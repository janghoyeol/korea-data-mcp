"""기상청 단기예보 조회서비스 중 "초단기실황조회"(getUltraSrtNcst) 클라이언트.

실시간 데이터 특유의 타이밍 문제: 초단기실황은 매시 정시 자료를 "매시 40분"에
발표한다. 조회 시각이 그 시(時)의 40분 이전이면 아직 최신 자료가 없으므로,
한 시간 전 base_time으로 조회해야 한다. 그래도 비어 있으면(서버 반영 지연 등)
한 번 더 과거로 물러나 재시도한다 — 실패를 숨기지 않고 그래도 안 되면 예외를 올린다.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote, quote_plus, unquote
from zoneinfo import ZoneInfo

# 기상청 API는 한국 표준시(KST) 기준으로 base_date/base_time을 받는다. 로컬 개발 환경(KST)에서는
# datetime.now()가 우연히 맞아떨어지지만, 서버 시계가 UTC인 배포 환경(Render 등)에서는 9시간
# 어긋난 시각으로 요청하게 돼 조용히 다른(과거) 관측값을 돌려받는다 — 반드시 KST로 고정해서 써야 한다.
KST = ZoneInfo("Asia/Seoul")

import requests

BASE_URL = "https://apis.data.go.kr/1360000/VilageFcstInfoService_2.0"
ENDPOINT = f"{BASE_URL}/getUltraSrtNcst"
PORTAL_URL = "https://www.data.go.kr/data/15084084/openapi.do"
SOURCE_NAME = "기상청 단기예보 조회서비스 (초단기실황조회)"

OK_CODES = {"00", "000"}

# 강수형태(PTY) 코드 → 사람이 읽는 말.
PTY_LABELS = {
    "0": "없음", "1": "비", "2": "비/눈", "3": "눈",
    "5": "빗방울", "6": "빗방울눈날림", "7": "눈날림",
}

MAX_RETRIES = 2  # 발표 지연에 대비해 최대 이만큼 더 과거 시각으로 재시도


class KmaError(RuntimeError):
    """API가 에러를 돌려주거나 응답을 해석할 수 없을 때."""


def normalize_service_key(key: str) -> str:
    key = key.strip()
    return unquote(key) if "%" in key else key


def latest_base_datetime(now: datetime | None = None) -> datetime:
    """조회에 쓸 정시(:00) 기준시각. 40분 이전이면 전 시간을 쓴다.

    now를 넘기지 않으면 서버의 로컬 시계가 아니라 KST로 고정한 '지금'을 쓴다.
    """
    now = now or datetime.now(KST)
    base = now.replace(minute=0, second=0, microsecond=0)
    if now.minute < 40:
        base -= timedelta(hours=1)
    return base


def parse_response(xml_text: str) -> list[dict[str, str]]:
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as e:
        raise KmaError(f"XML 해석 실패. 응답 앞부분: {xml_text[:200]!r}") from e

    if root.tag == "OpenAPI_ServiceResponse":
        auth_msg = root.findtext(".//returnAuthMsg") or ""
        err_msg = root.findtext(".//errMsg") or ""
        reason = root.findtext(".//returnReasonCode") or ""
        hint = " (서비스키 확인: 활용신청 직후면 반영까지 시간이 걸릴 수 있음)" if "SERVICE_KEY" in auth_msg else ""
        raise KmaError(f"API 에러 {reason}: {err_msg} {auth_msg}{hint}".strip())

    code = (root.findtext(".//resultCode") or "").strip()
    if code and code not in OK_CODES and code != "03":  # 03 = NODATA, 아래서 빈 리스트로 처리
        msg = (root.findtext(".//resultMsg") or "").strip()
        raise KmaError(f"API 에러 {code}: {msg}")

    return [
        {"category": item.findtext("category") or "", "obsrValue": (item.findtext("obsrValue") or "").strip()}
        for item in root.iterfind(".//items/item")
    ]


def to_observation(rows: list[dict[str, str]]) -> dict[str, Any]:
    """category/obsrValue 행 목록 → {T1H, RN1, REH, PTY, WSD} 딱 필요한 값만."""
    by_cat = {r["category"]: r["obsrValue"] for r in rows}
    pty_code = by_cat.get("PTY", "0")
    return {
        "temperature_c": float(by_cat["T1H"]) if "T1H" in by_cat else None,
        "humidity_pct": float(by_cat["REH"]) if "REH" in by_cat else None,
        "wind_speed_ms": float(by_cat["WSD"]) if "WSD" in by_cat else None,
        "precip_1h_mm": by_cat.get("RN1"),  # "강수없음" 같은 문자열이 올 수 있어 그대로 둔다
        "precip_type_code": pty_code,
        "precip_type": PTY_LABELS.get(pty_code, pty_code),
    }


class KmaClient:
    def __init__(self, service_key: str | None = None, timeout: float = 15.0):
        key = service_key or os.environ.get("DATA_GO_KR_SERVICE_KEY")
        if not key:
            raise KmaError(
                "DATA_GO_KR_SERVICE_KEY가 없습니다. .env 파일에 공공데이터포털 서비스키를 넣었는지 확인하세요."
            )
        self.service_key = normalize_service_key(key)
        self.timeout = timeout
        self.session = requests.Session()

    def _get(self, nx: int, ny: int, base_dt: datetime) -> list[dict[str, str]]:
        params = {
            "serviceKey": self.service_key,
            "pageNo": 1,
            "numOfRows": 10,
            "dataType": "XML",
            "base_date": base_dt.strftime("%Y%m%d"),
            "base_time": base_dt.strftime("%H%M"),
            "nx": nx,
            "ny": ny,
        }
        try:
            res = self.session.get(ENDPOINT, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise KmaError(f"날씨 API 요청 실패: {self._mask(str(e))}") from e
        if res.status_code in (401, 403):
            raise KmaError(
                f"날씨 API가 {res.status_code}로 거절했습니다. 이 서비스키로 "
                "'기상청_단기예보 조회서비스'를 활용신청했는지 확인하세요. "
                f"응답: {self._mask(res.text[:200])!r}"
            )
        if not res.ok:
            raise KmaError(f"날씨 API HTTP {res.status_code}: {self._mask(res.text[:200])!r}")
        res.encoding = "utf-8"
        return parse_response(res.text)

    def get_current(self, nx: int, ny: int, now: datetime | None = None) -> dict[str, Any]:
        """현재 관측값. 아직 발표 전이면 최대 MAX_RETRIES시간 전까지 물러나며 재시도."""
        base_dt = latest_base_datetime(now)
        last_rows: list[dict[str, str]] = []
        for attempt in range(MAX_RETRIES + 1):
            rows = self._get(nx, ny, base_dt - timedelta(hours=attempt))
            if rows:
                last_rows = rows
                base_dt = base_dt - timedelta(hours=attempt)
                break
        if not last_rows:
            raise KmaError(f"nx={nx}, ny={ny} 격자의 실황 자료를 찾지 못했습니다 (최근 {MAX_RETRIES + 1}시간 모두 비어 있음).")
        obs = to_observation(last_rows)
        obs["base_date"] = base_dt.strftime("%Y%m%d")
        obs["base_time"] = base_dt.strftime("%H%M")
        return obs

    def _mask(self, text: str) -> str:
        for variant in {self.service_key, quote(self.service_key, safe=""), quote_plus(self.service_key)}:
            text = text.replace(variant, "***")
        return text


def source_info(nx: int, ny: int) -> dict[str, str]:
    return {
        "name": SOURCE_NAME,
        "provider": "기상청 (공공데이터포털 OpenAPI)",
        "url": PORTAL_URL,
        "query": f"nx={nx}, ny={ny}",
        "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "note": "초단기실황(매시 정시 관측, 40분경 발표). 예보가 아니라 관측값.",
    }
