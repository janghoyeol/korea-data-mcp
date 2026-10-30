"""에이전트가 호출할 도구 4개: resolve_address / get_transactions / compute_trend / find_comparables.

stat-agent의 4개 도구 패턴을 그대로 가져왔다. 다른 점은 데이터 성격이다.
KOSIS는 이미 집계된 숫자를 주지만, 실거래가는 '원시 거래 로그'라서
그냥 평균을 내면 왜곡된다.
  - 면적이 제각각 → 전용면적당 단가(만원/㎡, 만원/3.3㎡)로 정규화
  - 월별 거래 건수가 들쭉날쭉(1~2건인 달도 있음) → 평균 대신 중위값, 표본 수 표시
  - 해제된 거래(cdealType='O')는 실제로 성사되지 않은 거래라 제외
  - 직거래는 가족 간 저가 거래가 섞일 수 있어 건수를 따로 보여주고 제외 옵션 제공

원시 거래 수백 건을 모델에게 넘기면 토큰 낭비이자 '암산' 유혹이 생기므로,
compute_trend / find_comparables는 거래를 직접 다시 조회(캐시)해서 코드로 계산한다.
"""

from __future__ import annotations

import re
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any, Callable

from . import lawd
from .molit_client import MolitClient, source_info

PYEONG_M2 = 3.305785  # 1평 = 3.305785㎡
MAX_MONTHS = 36  # 한 번에 조회할 최대 개월 수 (API 일일 트래픽 보호)
LOW_SAMPLE = 3  # 이 건수 미만인 구간은 low_sample로 표시
SAMPLE_ROWS = 15  # get_transactions가 모델에게 보여줄 거래 예시 수

# (lawd_cd, YYYYMM) → (받은 시각, 거래 리스트). 여러 도구가 같은 달을 다시 조회하지 않도록.
# 지난 달 자료는 신고기한(30일) 때문에 계속 늘어나므로 최근 2개월치는 1시간만 캐시한다.
_CACHE: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
RECENT_TTL_SEC = 3600

FetchMonth = Callable[[str, str], list[dict[str, Any]]]


# ---------------------------------------------------------------------- #
# 공통 유틸
# ---------------------------------------------------------------------- #
def month_range(start_ym: str, end_ym: str) -> list[str]:
    """'202401', '202403' → ['202401', '202402', '202403']"""
    for ym in (start_ym, end_ym):
        if not re.fullmatch(r"\d{6}", str(ym)) or not 1 <= int(str(ym)[4:]) <= 12:
            raise ValueError(f"기간은 YYYYMM 6자리여야 합니다: {ym!r}")
    y, m = int(start_ym[:4]), int(start_ym[4:])
    ey, em = int(end_ym[:4]), int(end_ym[4:])
    if (y, m) > (ey, em):
        raise ValueError(f"시작({start_ym})이 끝({end_ym})보다 늦습니다.")
    months = []
    while (y, m) <= (ey, em):
        months.append(f"{y}{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    if len(months) > MAX_MONTHS:
        raise ValueError(f"한 번에 최대 {MAX_MONTHS}개월까지 조회할 수 있습니다 (요청: {len(months)}개월).")
    return months


def normalize_apt_name(name: str) -> str:
    """'은마아파트', '은마 (아파트)', '래미안 블레스티지' → 비교용 키."""
    name = re.sub(r"\(.*?\)", "", name or "")
    name = re.sub(r"아파트$", "", re.sub(r"\s+", "", name))
    return name.lower()


def _default_fetch() -> FetchMonth:
    client = MolitClient()
    return client.get_month


def fetch_months(lawd_cd: str, months: list[str], fetch: FetchMonth | None = None) -> list[dict[str, Any]]:
    """여러 달을 병렬로 조회해 합친다. 이미 받아둔 달은 캐시에서 꺼낸다."""
    missing = [ym for ym in months if not _cache_fresh(lawd_cd, ym)]
    if missing:
        fetch = fetch or _default_fetch()
        with ThreadPoolExecutor(max_workers=6) as pool:
            for ym, rows in zip(missing, pool.map(lambda ym: fetch(lawd_cd, ym), missing)):
                _CACHE[(lawd_cd, ym)] = (time.time(), rows)
    return [row for ym in months for row in _CACHE[(lawd_cd, ym)][1]]


def _cache_fresh(lawd_cd: str, ym: str) -> bool:
    entry = _CACHE.get((lawd_cd, ym))
    if entry is None:
        return False
    d = date.today()
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    is_recent = ym >= f"{y}{m:02d}"
    return not is_recent or time.time() - entry[0] < RECENT_TTL_SEC


def _filter(
    rows: list[dict[str, Any]],
    umd_name: str | None = None,
    apt_name: str | None = None,
    area_min: float | None = None,
    area_max: float | None = None,
    exclude_direct: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """조건에 맞는 '유효한' 거래만 남기고, 무엇을 몇 건 뺐는지도 함께 돌려준다."""
    apt_key = normalize_apt_name(apt_name) if apt_name else None
    kept, dropped = [], {"cancelled": 0, "direct_deal": 0, "invalid": 0}
    for r in rows:
        if umd_name and r.get("umdNm") != umd_name:
            continue
        if apt_key and apt_key not in normalize_apt_name(r.get("aptNm", "")):
            continue
        area = r.get("excluUseAr")
        if area_min is not None and (area is None or area < area_min):
            continue
        if area_max is not None and (area is None or area > area_max):
            continue
        if (r.get("cdealType") or "").upper() == "O":
            dropped["cancelled"] += 1
            continue
        if not r.get("dealAmount") or not area:
            dropped["invalid"] += 1
            continue
        if (r.get("dealingGbn") or "") == "직거래":
            if exclude_direct:
                dropped["direct_deal"] += 1
                continue
        kept.append(r)
    return kept, dropped


def _unit_price(r: dict[str, Any]) -> float:
    """만원/㎡ (전용면적 기준)."""
    return r["dealAmount"] / r["excluUseAr"]


def _deal_date(r: dict[str, Any]) -> str:
    return f"{r.get('dealYear')}-{(r.get('dealMonth') or 0):02d}-{(r.get('dealDay') or 0):02d}"


def _period_key(r: dict[str, Any], period: str) -> str:
    y, m = r.get("dealYear"), r.get("dealMonth") or 1
    if period == "quarter":
        return f"{y}Q{(m - 1) // 3 + 1}"
    if period == "half":
        return f"{y}H{1 if m <= 6 else 2}"
    if period == "year":
        return f"{y}"
    return f"{y}-{m:02d}"


def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    prices = [_unit_price(r) for r in rows]
    amounts = [r["dealAmount"] for r in rows]
    med = statistics.median(prices)
    return {
        "count": len(rows),
        "median_price_per_m2_manwon": round(med, 1),
        "median_price_per_pyeong_manwon": round(med * PYEONG_M2),
        "median_deal_amount_manwon": round(statistics.median(amounts)),
        "min_deal_amount_manwon": min(amounts),
        "max_deal_amount_manwon": max(amounts),
        "low_sample": len(rows) < LOW_SAMPLE,
    }


# ---------------------------------------------------------------------- #
# 도구 1: resolve_address
# ---------------------------------------------------------------------- #
def resolve_address(query: str) -> dict[str, Any]:
    """주소/단지명 문자열 → 법정동코드 후보.

    동 이름이 여러 시군구에 있으면(예: 송도동, 중구) 후보를 전부 돌려주고
    ambiguous=True로 표시한다. 지역으로 해석되지 않은 나머지 단어는
    apt_name_hint로 돌려줘서 단지명으로 쓸 수 있게 한다.
    """
    candidates, region_tokens, rest = lawd.split_query(query)
    result: dict[str, Any] = {
        "query": query,
        "region_tokens": region_tokens,
        "apt_name_hint": " ".join(rest) or None,
        "candidates": [
            {
                "lawd_cd": row.lawd_cd,
                "full_name": row.name,
                "sigungu": lawd.sigungu_name(row.lawd_cd),
                # 동 단위까지 특정됐으면 실거래 데이터의 umdNm 필터로 쓴다
                "umd_name": row.parts[-1] if row.code[5:] != "00000" else None,
                "bjdong_code": row.code,
            }
            for row in candidates[:20]
        ],
        "ambiguous": len({row.lawd_cd for row in candidates}) > 1,
        "source": {"name": "법정동코드 전체자료 (행정표준코드관리시스템 code.go.kr)", "file": "data/lawd_codes.tsv"},
    }
    if not candidates:
        result["message"] = (
            "법정동을 찾지 못했습니다. 단지명만으로는 지역을 특정할 수 없으니 "
            "동 이름이나 시군구를 함께 알려달라고 사용자에게 요청하세요."
        )
    return result


# ---------------------------------------------------------------------- #
# 도구 2: get_transactions
# ---------------------------------------------------------------------- #
def get_transactions(
    lawd_cd: str,
    start_ym: str,
    end_ym: str,
    umd_name: str | None = None,
    apt_name: str | None = None,
    area_min: float | None = None,
    area_max: float | None = None,
    fetch: FetchMonth | None = None,
) -> dict[str, Any]:
    """기간 내 원시 거래를 조회해 요약 + 최근 거래 예시 + 단지별 건수를 돌려준다.

    단지명이 실제 데이터에서 어떻게 표기되는지(예: '은마' vs '은마아파트') 확인하는 데도 쓴다.
    """
    months = month_range(start_ym, end_ym)
    rows = fetch_months(lawd_cd, months, fetch)
    kept, dropped = _filter(rows, umd_name, apt_name, area_min, area_max)

    apt_counts: dict[str, int] = {}
    for r in kept:
        key = f"{r.get('aptNm')} ({r.get('umdNm')})"
        apt_counts[key] = apt_counts.get(key, 0) + 1
    top_apts = sorted(apt_counts.items(), key=lambda kv: -kv[1])[:20]

    recent = sorted(kept, key=_deal_date, reverse=True)[:SAMPLE_ROWS]
    return {
        "lawd_cd": lawd_cd,
        "sigungu": lawd.sigungu_name(lawd_cd),
        "period": f"{months[0]}~{months[-1]}",
        "filters": {"umd_name": umd_name, "apt_name": apt_name, "area_min": area_min, "area_max": area_max},
        "raw_count_in_sigungu": len(rows),
        "matched_count": len(kept),
        "excluded": dropped,
        "apartments": [{"name": k, "count": v} for k, v in top_apts],
        "recent_transactions": [
            {
                "date": _deal_date(r),
                "apt": r.get("aptNm"),
                "umd": r.get("umdNm"),
                "area_m2": r.get("excluUseAr"),
                "floor": r.get("floor"),
                "amount_manwon": r.get("dealAmount"),
                "price_per_pyeong_manwon": round(_unit_price(r) * PYEONG_M2),
                "deal_type": r.get("dealingGbn") or None,
                "build_year": r.get("buildYear"),
            }
            for r in recent
        ],
        "source": source_info(lawd_cd, months),
    }


# ---------------------------------------------------------------------- #
# 도구 3: compute_trend
# ---------------------------------------------------------------------- #
def compute_trend(
    lawd_cd: str,
    start_ym: str,
    end_ym: str,
    umd_name: str | None = None,
    apt_name: str | None = None,
    area_min: float | None = None,
    area_max: float | None = None,
    period: str = "quarter",
    exclude_direct_deals: bool = False,
    fetch: FetchMonth | None = None,
) -> dict[str, Any]:
    """기간별 전용면적당 중위 단가와 첫 구간 대비 마지막 구간 변동률을 코드로 계산한다.

    변동률은 표본이 LOW_SAMPLE건 이상인 구간 중 첫 구간과 마지막 구간을 비교한다.
    (거래 1~2건짜리 달끼리 비교하면 시세가 아니라 특정 매물의 가격 차이가 나오기 때문)
    """
    if period not in {"month", "quarter", "half", "year"}:
        raise ValueError("period는 month / quarter / half / year 중 하나여야 합니다.")
    months = month_range(start_ym, end_ym)
    rows = fetch_months(lawd_cd, months, fetch)
    kept, dropped = _filter(rows, umd_name, apt_name, area_min, area_max, exclude_direct_deals)

    groups: dict[str, list[dict[str, Any]]] = {}
    for r in kept:
        groups.setdefault(_period_key(r, period), []).append(r)
    # 조회 기간이 분기/반기 중간에서 시작·끝나면 그 구간은 일부 달만 들어있다 → partial로 표시
    covered: dict[str, int] = {}
    for ym in months:
        key = _period_key({"dealYear": int(ym[:4]), "dealMonth": int(ym[4:])}, period)
        covered[key] = covered.get(key, 0) + 1
    full_len = {"month": 1, "quarter": 3, "half": 6, "year": 12}[period]
    series = [
        {"period": key, **_stats(groups[key]), "partial": covered.get(key, 0) < full_len}
        for key in sorted(groups)
    ]

    result: dict[str, Any] = {
        "lawd_cd": lawd_cd,
        "sigungu": lawd.sigungu_name(lawd_cd),
        "filters": {
            "umd_name": umd_name, "apt_name": apt_name, "area_min": area_min, "area_max": area_max,
            "exclude_direct_deals": exclude_direct_deals,
        },
        "period_unit": period,
        "matched_count": len(kept),
        "excluded": dropped,
        "overall": _stats(kept) if kept else None,
        "series": series,
        "change": None,
        "method": (
            "전용면적당 단가(거래금액/전용면적)의 구간별 중위값. 해제 거래 제외. "
            f"변동률은 표본 {LOW_SAMPLE}건 이상이고 조회 기간에 온전히 들어간(partial=false) 첫 구간과 마지막 구간 비교."
        ),
        "source": source_info(lawd_cd, months),
    }

    usable = [s for s in series if not s["low_sample"] and not s["partial"]]
    if len(usable) >= 2:
        first, last = usable[0], usable[-1]
        a, b = first["median_price_per_m2_manwon"], last["median_price_per_m2_manwon"]
        result["change"] = {
            "from_period": first["period"],
            "to_period": last["period"],
            "from_price_per_pyeong_manwon": first["median_price_per_pyeong_manwon"],
            "to_price_per_pyeong_manwon": last["median_price_per_pyeong_manwon"],
            "pct_change": round((b - a) / a * 100, 2),
        }
    else:
        result["change_note"] = f"표본 {LOW_SAMPLE}건 이상이면서 온전한 구간이 2개 미만이라 변동률을 계산하지 않았습니다. 기간을 넓히거나 period를 키우세요."
    return result


# ---------------------------------------------------------------------- #
# 도구 4: find_comparables
# ---------------------------------------------------------------------- #
def find_comparables(
    lawd_cd: str,
    start_ym: str,
    end_ym: str,
    area_min: float,
    area_max: float,
    umd_name: str | None = None,
    exclude_apt_name: str | None = None,
    limit: int = 10,
    fetch: FetchMonth | None = None,
) -> dict[str, Any]:
    """같은 시군구(umd_name을 주면 같은 동 우선)에서 비슷한 면적대 단지들의 중위 단가를 비교한다.

    LAWD_CD가 시군구 단위라 '인근'은 같은 시군구 안의 단지로 정의한다.
    """
    months = month_range(start_ym, end_ym)
    rows = fetch_months(lawd_cd, months, fetch)
    kept, _ = _filter(rows, None, None, area_min, area_max)
    exclude_key = normalize_apt_name(exclude_apt_name) if exclude_apt_name else None

    by_apt: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in kept:
        by_apt.setdefault((r.get("aptNm", ""), r.get("umdNm", "")), []).append(r)

    comps = []
    for (apt, umd), group in by_apt.items():
        if exclude_key and exclude_key in normalize_apt_name(apt):
            continue
        s = _stats(group)
        comps.append({
            "apt": apt,
            "umd": umd,
            "same_dong": bool(umd_name) and umd == umd_name,
            "build_year": group[0].get("buildYear"),
            **s,
        })
    # 같은 동 → 거래 많은 순 (표본이 많은 단지가 비교 기준으로 믿을 만함)
    comps.sort(key=lambda c: (not c["same_dong"], -c["count"]))
    comps = comps[:limit]

    return {
        "lawd_cd": lawd_cd,
        "sigungu": lawd.sigungu_name(lawd_cd),
        "area_range_m2": [area_min, area_max],
        "period": f"{months[0]}~{months[-1]}",
        "comparables": comps,
        "sigungu_overall": _stats(kept) if kept else None,
        "method": "같은 시군구·같은 전용면적대 거래의 단지별 중위 단가. 해제 거래 제외.",
        "source": source_info(lawd_cd, months),
    }


def today() -> dict[str, str]:
    """오늘 날짜 (모델이 '최근'을 YYYYMM으로 바꿀 때 기준)."""
    d = date.today()
    return {"today": d.isoformat(), "this_month": f"{d.year}{d.month:02d}"}


_PERIOD_PROPS = {
    "lawd_cd": {"type": "string", "description": "resolve_address가 돌려준 lawd_cd (법정동코드 앞 5자리)"},
    "start_ym": {"type": "string", "description": "시작 계약년월 YYYYMM (예: '202401')"},
    "end_ym": {"type": "string", "description": "끝 계약년월 YYYYMM. 최대 36개월"},
}
_FILTER_PROPS = {
    "umd_name": {"type": "string", "description": "법정동 이름으로 좁히기 (resolve_address의 umd_name, 예: '대치동')"},
    "apt_name": {"type": "string", "description": "단지명 일부 (예: '은마'). '아파트'·공백은 무시하고 부분 일치"},
    "area_min": {"type": "number", "description": "전용면적 최소 ㎡ (예: 84㎡대를 보려면 80)"},
    "area_max": {"type": "number", "description": "전용면적 최대 ㎡ (예: 85)"},
}

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "resolve_address",
        "description": (
            "주소/동/단지명이 섞인 문자열을 법정동코드로 바꾼다. 실거래 조회 전에 반드시 먼저 호출한다. "
            "ambiguous=true면 같은 이름의 지역이 여러 곳이라는 뜻이니 짐작으로 고르지 말 것. "
            "지역으로 해석되지 않은 단어는 apt_name_hint로 돌려준다(단지명 후보)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "예: '대치동 은마아파트', '분당구 정자동'"}},
            "required": ["query"],
        },
    },
    {
        "name": "get_transactions",
        "description": (
            "시군구의 기간 내 아파트 매매 실거래를 조회해 건수, 단지별 거래 수, 최근 거래 예시를 돌려준다. "
            "단지명이 데이터에 실제로 어떻게 적혀 있는지 확인할 때도 쓴다. 금액 단위는 만원."
        ),
        "input_schema": {
            "type": "object",
            "properties": {**_PERIOD_PROPS, **_FILTER_PROPS},
            "required": ["lawd_cd", "start_ym", "end_ym"],
        },
    },
    {
        "name": "compute_trend",
        "description": (
            "시세 추이를 코드로 계산한다: 구간별(월/분기/반기/연) 전용면적당 중위 단가(만원/㎡, 만원/3.3㎡)와 "
            "첫 구간 대비 마지막 구간 변동률. 평균·변동률은 절대 암산하지 말고 이 도구 결과를 그대로 인용할 것. "
            "면적이 다른 거래가 섞이면 왜곡되므로, 가능하면 area_min/area_max로 면적대를 좁혀라."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_PERIOD_PROPS,
                **_FILTER_PROPS,
                "period": {"type": "string", "enum": ["month", "quarter", "half", "year"], "description": "집계 구간 (기본 quarter)"},
                "exclude_direct_deals": {"type": "boolean", "description": "직거래 제외 여부 (기본 false)"},
            },
            "required": ["lawd_cd", "start_ym", "end_ym"],
        },
    },
    {
        "name": "find_comparables",
        "description": (
            "같은 시군구 안에서 비슷한 전용면적대 단지들의 중위 단가를 비교한다. "
            "umd_name을 주면 같은 동 단지를 먼저 보여준다. 대상 단지는 exclude_apt_name으로 뺄 수 있다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                **_PERIOD_PROPS,
                "area_min": _FILTER_PROPS["area_min"],
                "area_max": _FILTER_PROPS["area_max"],
                "umd_name": _FILTER_PROPS["umd_name"],
                "exclude_apt_name": {"type": "string", "description": "비교에서 뺄 대상 단지명"},
                "limit": {"type": "integer", "description": "최대 단지 수 (기본 10)"},
            },
            "required": ["lawd_cd", "start_ym", "end_ym", "area_min", "area_max"],
        },
    },
    {
        "name": "today",
        "description": "오늘 날짜와 이번 달(YYYYMM). '최근', '작년' 같은 표현을 기간으로 바꿀 때 먼저 호출한다.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

TOOL_FUNCTIONS = {
    "resolve_address": resolve_address,
    "get_transactions": get_transactions,
    "compute_trend": compute_trend,
    "find_comparables": find_comparables,
    "today": today,
}
