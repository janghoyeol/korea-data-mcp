"""에이전트가 호출할 도구 4개: resolve_region / get_air_quality / get_weather / recommend.

stat-agent·realty-agent와 같은 4-tool 패턴이지만, 실시간 데이터라 두 가지가 다르다.

1. **캐싱**: 두 공공 API 다 매시간 갱신이라, 같은 구를 여러 번 물어봐도 매번 새로
   호출할 필요가 없다. TTL_SEC(기본 10분) 동안은 캐시를 그대로 돌려준다 — 짧은
   시간 안에 같은 질문이 반복돼도 API 호출량과 응답 시간을 아낀다.
2. **recommend는 암산이 아니라 '등급 재사용'이다**: compute_trend처럼 숫자를
   새로 계산하는 게 아니라, 에어코리아가 이미 공식 기준으로 매긴 pm10Grade/
   pm25Grade(1=좋음~4=매우나쁨)를 그대로 비교·조합한다. 우리가 PM 임계값을
   새로 지어내는 것보다, 환경부가 쓰는 등급 체계를 그대로 인용하는 게 더
   근거 있는 답변이 된다.
"""

from __future__ import annotations

import time
from typing import Any

from . import regions
from .airkorea_client import AirkoreaClient, GRADE_LABELS
from .airkorea_client import source_info as air_source_info
from .kma_client import KmaClient
from .kma_client import source_info as weather_source_info

TTL_SEC = 600  # 10분

_air_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_weather_cache: dict[tuple[int, int], tuple[float, dict[str, Any]]] = {}


def _cached(cache: dict, key, fetch):
    hit = cache.get(key)
    if hit and time.time() - hit[0] < TTL_SEC:
        return hit[1]
    value = fetch()
    cache[key] = (time.time(), value)
    return value


# ---------------------------------------------------------------------- #
# 도구 1: resolve_region
# ---------------------------------------------------------------------- #
def resolve_region(query: str) -> dict[str, Any]:
    """구 이름 문자열 → 서울 25개 구 후보 (regions.resolve_region 그대로 노출)."""
    return regions.resolve_region(query)


# ---------------------------------------------------------------------- #
# 도구 2: get_air_quality
# ---------------------------------------------------------------------- #
def get_air_quality(station: str, fetch: Any = None) -> dict[str, Any]:
    """측정소의 최신 유효 대기질 측정값 + 등급."""
    client_fetch = fetch or (lambda: AirkoreaClient().get_latest(station))
    row = _cached(_air_cache, station, client_fetch)

    def grade(key: str) -> dict[str, Any]:
        code = row.get(key)
        return {"code": code, "label": GRADE_LABELS.get(code or "", "알수없음")}

    return {
        "station": station,
        "data_time": row.get("dataTime"),
        "pm10": {"value_ugm3": row.get("pm10Value"), "grade": grade("pm10Grade")},
        "pm25": {"value_ugm3": row.get("pm25Value"), "grade": grade("pm25Grade")},
        "o3_ppm": row.get("o3Value"),
        "no2_ppm": row.get("no2Value"),
        "khai": {"value": row.get("khaiValue"), "grade": grade("khaiGrade")},
        "source": air_source_info(station),
    }


# ---------------------------------------------------------------------- #
# 도구 3: get_weather
# ---------------------------------------------------------------------- #
def get_weather(nx: int, ny: int, fetch: Any = None) -> dict[str, Any]:
    """격자의 최신 초단기실황(관측값, 예보 아님)."""
    client_fetch = fetch or (lambda: KmaClient().get_current(nx, ny))
    obs = _cached(_weather_cache, (nx, ny), client_fetch)
    return {
        "nx": nx,
        "ny": ny,
        "base_date": obs.get("base_date"),
        "base_time": obs.get("base_time"),
        "temperature_c": obs.get("temperature_c"),
        "humidity_pct": obs.get("humidity_pct"),
        "wind_speed_ms": obs.get("wind_speed_ms"),
        "precip_1h_mm": obs.get("precip_1h_mm"),
        "precip_type_code": obs.get("precip_type_code"),
        "precip_type": obs.get("precip_type"),
        "source": weather_source_info(nx, ny),
    }


# ---------------------------------------------------------------------- #
# 도구 4: recommend
# ---------------------------------------------------------------------- #
# 체감상 "바람이 강하다"고 안내할 기준(m/s). 기상청 강풍주의보(지상 10m 평균 14m/s)보다
# 낮게 잡았다 — 특보 기준이 아니라 "우산이 뒤집히거나 걷기 불편한 수준"이라는 참고용 멘트.
_STRONG_WIND_MS = 9.0


def recommend(
    pm10_grade: str | None,
    pm25_grade: str | None,
    precip_type_code: str = "0",
    temperature_c: float | None = None,
    wind_speed_ms: float | None = None,
) -> dict[str, Any]:
    """대기질 등급 + 날씨 관측값으로 외출/마스크/우산 조언을 코드로 조합한다."""
    grades = [int(g) for g in (pm10_grade, pm25_grade) if g and g.isdigit()]
    worst = max(grades) if grades else None
    air_label = GRADE_LABELS.get(str(worst), "알수없음") if worst else "알수없음"

    advice: list[str] = []
    if worst is None:
        advice.append("대기질 등급 정보가 없어 외출 가능 여부를 판단할 수 없습니다.")
    elif worst >= 3:
        advice.append(f"미세먼지 '{air_label}' 수준 — 마스크를 쓰고, 민감군(어린이·노약자)은 장시간 외출을 자제하세요.")
    else:
        advice.append(f"미세먼지 '{air_label}' 수준 — 환기·외출에 무리 없습니다.")

    if precip_type_code and precip_type_code != "0":
        advice.append("강수가 있어 우산이 필요합니다.")

    if wind_speed_ms is not None and wind_speed_ms >= _STRONG_WIND_MS:
        advice.append(f"바람이 강합니다(풍속 {wind_speed_ms}m/s) — 우산이 뒤집히기 쉬우니 주의하세요.")

    if temperature_c is not None and temperature_c <= 0:
        advice.append(f"기온 {temperature_c}°C로 낮습니다 — 보온에 유의하세요.")

    return {
        "air_quality": {"grade": worst, "label": air_label, "basis": "pm10Grade/pm25Grade 중 더 나쁜 등급(환경부 공식 등급 그대로 사용)"},
        "advice": advice,
        "method": "등급·관측값 비교는 전부 코드로 처리했고, 모델이 새로 수치를 판단하지 않았습니다.",
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "resolve_region",
        "description": (
            "구 이름(또는 일부)을 서울 25개 자치구 후보로 바꾼다. 대기질/날씨 조회 전에 반드시 먼저 호출한다. "
            "ambiguous=true면 같은 글자로 시작하는 구가 여럿이라는 뜻이니 짐작으로 고르지 말 것. "
            "candidates가 비어 있으면 서울 밖 지역이거나 이름이 다른 경우이니 사용자에게 다시 물어라."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "예: '강남', '강남구', '중'"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_air_quality",
        "description": "측정소(=구 이름)의 최신 대기질(PM10/PM2.5/오존/통합대기환경지수)과 공식 등급을 조회한다.",
        "input_schema": {
            "type": "object",
            "properties": {"station": {"type": "string", "description": "resolve_region이 돌려준 station 값"}},
            "required": ["station"],
        },
    },
    {
        "name": "get_weather",
        "description": "격자(nx, ny)의 최신 기상 관측값(기온/습도/풍속/강수형태)을 조회한다. 예보가 아니라 현재 관측값이다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "nx": {"type": "integer", "description": "resolve_region이 돌려준 nx"},
                "ny": {"type": "integer", "description": "resolve_region이 돌려준 ny"},
            },
            "required": ["nx", "ny"],
        },
    },
    {
        "name": "recommend",
        "description": (
            "대기질 등급과 날씨 관측값을 코드로 조합해 마스크/외출/우산 조언을 만든다. "
            "pm10_grade/pm25_grade는 get_air_quality 결과의 grade.code, precip_type_code는 get_weather의 "
            "관측 코드(없으면 '0')를 그대로 넣는다. 이 조합 판단은 암산하지 말고 반드시 이 도구를 호출해서 얻어라."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pm10_grade": {"type": "string", "description": "get_air_quality의 pm10.grade.code ('1'~'4')"},
                "pm25_grade": {"type": "string", "description": "get_air_quality의 pm25.grade.code ('1'~'4')"},
                "precip_type_code": {"type": "string", "description": "get_weather의 precip_type_code. 없으면 '0'"},
                "temperature_c": {"type": "number", "description": "get_weather의 temperature_c"},
                "wind_speed_ms": {"type": "number", "description": "get_weather의 wind_speed_ms"},
            },
            "required": ["pm10_grade", "pm25_grade"],
        },
    },
]

TOOL_FUNCTIONS = {
    "resolve_region": resolve_region,
    "get_air_quality": get_air_quality,
    "get_weather": get_weather,
    "recommend": recommend,
}
