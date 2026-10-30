"""원격 모드에서 공용 키(운영자 키)를 지키는 층 (ADR 0002).

원격 서버는 누구나 쓸 수 있고 공공 API는 운영자 키로 부른다. 같은 키를 원래 에이전트들도 쓰므로
한도를 다 써 버리면 그쪽까지 멈춘다. 그래서:
- 공공 API를 부르는 도구만 (도구, 인자) 단위로 TTL 캐시한다 — 같은 질문 반복은 한도를 안 쓴다.
- 도메인별 하루(KST) 호출 상한. 넘으면 지어내지 말라는 원칙대로 에러 결과로 알린다.
- IP별 분당 요청 수 제한은 HTTP 층(RateLimit)에서.
로컬(stdio)은 사용자 자기 키라 이 층을 쓰지 않는다.
"""
from __future__ import annotations

import json
import time
from collections import OrderedDict, defaultdict, deque
from datetime import datetime
from typing import Any, Callable
from zoneinfo import ZoneInfo

import mcp_types as types

KST = ZoneInfo("Asia/Seoul")

# 공공 API를 실제로 부르는 도구. 나머지(주소·구 이름 해석, 계산, 오늘 날짜)는 로컬에서 끝난다.
NETWORK_TOOLS = {
    "kosis_search_tables", "kosis_get_table_info", "kosis_get_data",
    "dart_resolve_company", "dart_get_financials",
    "air_get_air_quality", "air_get_weather",
    "realty_get_transactions", "realty_compute_trend", "realty_find_comparables",
}

# 하루 상한 기본값: 각 포털 개발계정 한도보다 넉넉히 낮게. 환경변수 KOREA_DATA_MCP_DAILY_LIMITS로 덮어쓴다.
DEFAULT_LIMITS = {"kosis": 1000, "dart": 1000, "air": 300, "realty": 500}
# 캐시 수명(초): 대기질·기상은 매시 관측이라 짧게, 통계·공시는 하루 안에 거의 안 바뀐다.
CACHE_TTL = {"kosis": 6 * 3600, "dart": 6 * 3600, "air": 600, "realty": 3600}
CACHE_MAX = 2000


def parse_limits(text: str | None) -> dict[str, int]:
    """'dart=500,air=100' → 기본값에 덮어쓴 상한 표."""
    limits = dict(DEFAULT_LIMITS)
    for part in filter(None, (p.strip() for p in (text or "").split(","))):
        domain, sep, n = part.partition("=")
        if not sep:
            raise ValueError(f"상한 형식은 도메인=숫자: {part!r}")
        limits[domain.strip()] = int(n)
    return limits


class Guard:
    def __init__(self, limits: dict[str, int], clock: Callable[[], float] = time.time):
        self.limits = limits
        self.clock = clock
        self.cache: OrderedDict[str, tuple[float, types.CallToolResult]] = OrderedDict()
        self.counts: dict[tuple[str, str], int] = defaultdict(int)

    def _day(self) -> str:
        return datetime.fromtimestamp(self.clock(), KST).date().isoformat()

    def used(self, domain: str) -> int:
        return self.counts[(self._day(), domain)]

    def run(self, spec, arguments: dict[str, Any], call: Callable | None = None) -> types.CallToolResult:
        from .server import _error, run_tool
        call = call or run_tool
        if spec.name not in NETWORK_TOOLS:
            return call(spec, arguments)

        key = spec.name + json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        hit = self.cache.get(key)
        if hit and self.clock() - hit[0] < CACHE_TTL.get(spec.domain, 3600):
            return hit[1]

        day_key = (self._day(), spec.domain)
        if self.counts[day_key] >= self.limits.get(spec.domain, 0):
            return _error(f"오늘 이 원격 서버의 {spec.domain} 공용 한도({self.limits.get(spec.domain, 0)}회)를 다 썼습니다. "
                          "내일 다시 쓰거나, 자기 키로 로컬 설치(README)해서 쓰세요.")
        self.counts[day_key] += 1  # 실패한 호출도 공공 API 한도는 쓴다
        result = call(spec, arguments)
        if not result.is_error:
            self.cache[key] = (self.clock(), result)
            self.cache.move_to_end(key)
            while len(self.cache) > CACHE_MAX:
                self.cache.popitem(last=False)
        return result


class RateLimit:
    """IP별 분당 요청 수 제한 (ASGI 미들웨어). MCP 엔드포인트 POST만 센다."""

    def __init__(self, app, per_min: int, path: str = "/mcp", clock: Callable[[], float] = time.time):
        self.app = app
        self.per_min = per_min
        self.path = path
        self.clock = clock
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    @staticmethod
    def client_ip(scope) -> str:
        # Render 같은 프록시 뒤에서는 X-Forwarded-For 첫 값이 원래 클라이언트.
        # 위조로 이 제한을 피할 수는 있지만, 한도 보호의 최종선은 Guard의 하루 상한이다.
        for name, value in scope.get("headers", []):
            if name == b"x-forwarded-for":
                return value.decode("latin-1").split(",")[0].strip()
        client = scope.get("client")
        return client[0] if client else "unknown"

    def allow(self, ip: str) -> bool:
        now = self.clock()
        q = self.hits[ip]
        while q and now - q[0] >= 60:
            q.popleft()
        if len(q) >= self.per_min:
            return False
        q.append(now)
        if len(self.hits) > 10_000:  # 오래된 IP 정리
            for k in [k for k, v in self.hits.items() if not v or now - v[-1] >= 60]:
                del self.hits[k]
        return True

    async def __call__(self, scope, receive, send):
        if (scope["type"] == "http" and scope["method"] == "POST" and scope["path"].rstrip("/") == self.path
                and not self.allow(self.client_ip(scope))):
            body = json.dumps({"error": "요청이 너무 많습니다. 1분 뒤 다시 시도하세요."}, ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": 429,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"retry-after", b"60")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
