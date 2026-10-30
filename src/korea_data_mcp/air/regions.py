"""지역 해석: 구 이름 → (에어코리아 측정소명, 기상청 격자 nx/ny).

realty-agent의 lawd.py와 같은 역할이지만 데이터 소스가 다르다. 법정동코드처럼
전국을 아우르는 공식 좌표 테이블을 구하려면 공공데이터포털 활용가이드 안의 첨부
엑셀을 받아야 하는데, 그건 사용자의 직접 다운로드가 필요해서 오늘 안에 끝내기
어렵다. 그래서 v1은 **서울특별시 25개 자치구**로 범위를 좁히고, 구청 좌표 +
기상청 공식 격자 변환 공식(아래 latlon_to_grid)으로 nx/ny를 직접 계산한다.

알려진 리스크: 구청 좌표가 격자 경계(5km 칸) 바로 근처면 옆 동네 날씨가 조용히
나올 수 있다 (API가 에러를 내지 않는다). latlon_to_grid 자체는 기상청이 공개한
공식을 그대로 구현한 것이라 공식의 정확성은 이미 여러 독립 구현체로 검증됐지만,
구청 위경도 값은 이 프로젝트에서 직접 교차 검증하지 않았다. 전국 확장이나
정확도를 더 높이려면 기상자료개방포털의 '동네예보 지점 좌표' 공식 자료로
이 테이블을 교체하는 게 다음 단계다.

에어코리아 측정소명은 서울은 대부분 구 이름과 그대로 같다(예: '강남구' 측정소).
다르게 측정소명이 없는 구가 있다면 get_air_quality 호출 시 API가 바로 에러를
던지므로, 여기서 예외 테이블을 미리 만들어두지 않았다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# 기상청 단기예보 API 격자 변환 공식 상수 (활용가이드에 실린 Lambert Conformal Conic 파라미터).
_RE = 6371.00877  # 지구 반경(km)
_GRID = 5.0  # 격자 간격(km)
_SLAT1 = 30.0  # 투영 위도1(deg)
_SLAT2 = 60.0  # 투영 위도2(deg)
_OLON = 126.0  # 기준점 경도(deg)
_OLAT = 38.0  # 기준점 위도(deg)
_XO = 43.0  # 기준점 X좌표(GRID 단위)
_YO = 136.0  # 기준점 Y좌표(GRID 단위)


def latlon_to_grid(lat: float, lon: float) -> tuple[int, int]:
    """위경도 → 기상청 단기예보 격자(nx, ny).

    검증: 서울시청(37.5665, 126.9780) → (60, 127) — 거의 모든 기상청 API
    튜토리얼이 쓰는 표준 예시값이라 회귀 테스트 기준으로 썼다 (tests/test_regions.py).
    """
    degrad = math.pi / 180.0
    re = _RE / _GRID
    slat1, slat2 = _SLAT1 * degrad, _SLAT2 * degrad
    olon, olat = _OLON * degrad, _OLAT * degrad

    sn = math.tan(math.pi * 0.25 + slat2 * 0.5) / math.tan(math.pi * 0.25 + slat1 * 0.5)
    sn = math.log(math.cos(slat1) / math.cos(slat2)) / math.log(sn)
    sf = math.tan(math.pi * 0.25 + slat1 * 0.5)
    sf = (sf ** sn) * math.cos(slat1) / sn
    ro = math.tan(math.pi * 0.25 + olat * 0.5)
    ro = re * sf / (ro ** sn)

    ra = math.tan(math.pi * 0.25 + lat * degrad * 0.5)
    ra = re * sf / (ra ** sn)
    theta = lon * degrad - olon
    if theta > math.pi:
        theta -= 2.0 * math.pi
    if theta < -math.pi:
        theta += 2.0 * math.pi
    theta *= sn

    x = ra * math.sin(theta) + _XO + 0.5
    y = ro - ra * math.cos(theta) + _YO + 0.5
    return int(x), int(y)


@dataclass(frozen=True)
class Region:
    gu: str  # "강남구" — 에어코리아 측정소명으로도 그대로 쓴다
    lat: float
    lon: float

    @property
    def sido(self) -> str:
        return "서울"

    @property
    def station(self) -> str:
        return self.gu

    @property
    def grid(self) -> tuple[int, int]:
        return latlon_to_grid(self.lat, self.lon)


# 서울 25개 자치구 구청 좌표. 실제 측정소 위치가 아니라 행정구역 대표점(구청)이라,
# 구청에서 멀리 떨어진 동네는 다른 격자/측정소 쪽이 더 맞을 수 있다는 점을 답변에서 짚어준다.
REGIONS: tuple[Region, ...] = (
    Region("종로구", 37.5735, 126.9790),
    Region("중구", 37.5636, 126.9975),
    Region("용산구", 37.5323, 126.9900),
    Region("성동구", 37.5634, 127.0369),
    Region("광진구", 37.5385, 127.0823),
    Region("동대문구", 37.5744, 127.0396),
    Region("중랑구", 37.6063, 127.0925),
    Region("성북구", 37.5894, 127.0167),
    Region("강북구", 37.6396, 127.0257),
    Region("도봉구", 37.6688, 127.0471),
    Region("노원구", 37.6542, 127.0568),
    Region("은평구", 37.6027, 126.9291),
    Region("서대문구", 37.5791, 126.9368),
    Region("마포구", 37.5663, 126.9019),
    Region("양천구", 37.5170, 126.8664),
    Region("강서구", 37.5509, 126.8495),
    Region("구로구", 37.4954, 126.8874),
    Region("금천구", 37.4568, 126.8955),
    Region("영등포구", 37.5263, 126.8966),
    Region("동작구", 37.5124, 126.9393),
    Region("관악구", 37.4784, 126.9516),
    Region("서초구", 37.4837, 127.0324),
    Region("강남구", 37.5172, 127.0473),
    Region("송파구", 37.5145, 127.1059),
    Region("강동구", 37.5301, 127.1238),
)

_BY_NAME = {r.gu: r for r in REGIONS}


def resolve_region(query: str) -> dict:
    """구 이름(또는 일부) → 후보 지역 목록.

    "강남"처럼 '구'를 뺀 입력, "강"처럼 여러 구에 걸치는 입력(강남/강북/강서/강동)
    둘 다 처리한다. 후보가 2개 이상이면 ambiguous=True — 짐작으로 고르지 말 것.
    """
    q = query.strip().replace(" ", "")
    if not q:
        return {"query": query, "candidates": [], "ambiguous": False,
                "message": "지역 이름이 비어 있습니다."}

    exact = _BY_NAME.get(q) or _BY_NAME.get(q + "구")
    matches = [exact] if exact else [r for r in REGIONS if q in r.gu]

    candidates = [
        {"gu": r.gu, "sido": r.sido, "station": r.station, "nx": r.grid[0], "ny": r.grid[1]}
        for r in matches
    ]
    result = {
        "query": query,
        "candidates": candidates,
        "ambiguous": len(candidates) > 1,
        "coverage": "v1은 서울특별시 25개 자치구만 지원합니다.",
    }
    if not candidates:
        result["message"] = (
            f"'{query}'를 서울 25개 구 중에서 찾지 못했습니다. "
            "서울 바깥 지역이거나 이름이 달라서일 수 있으니, 사용자에게 구 이름을 다시 물어보세요."
        )
    return result
