"""에이전트가 호출할 도구 4개: search / meta / data / calculate.

kosis_client의 원시 API 래퍼를 모델이 바로 쓸 수 있는 형태로 가공한다.
핵심 원칙: 분류 코드(지역 등)가 수천 개까지 나올 수 있으므로, 메타는
통째로 넘기지 않고 name_filter로 코드 레이어에서 먼저 좁힌다.
"""

from __future__ import annotations

from typing import Any

from .kosis_client import KosisClient, PRD_MAP, split_item_meta

# name_filter 없이, 혹은 매칭이 하나도 없을 때 분류 코드를 몇 개까지 보여줄지
MAX_CODES_WITHOUT_FILTER = 20


def search_tables(keyword: str, limit: int = 5, client: KosisClient | None = None) -> list[dict[str, Any]]:
    """키워드로 통계표를 검색한다."""
    client = client or KosisClient()
    results = client.search_tables(keyword, limit=limit)
    return [
        {
            "org_id": r.get("ORG_ID"),
            "tbl_id": r.get("TBL_ID"),
            "title": r.get("TBL_NM"),
            "org_name": r.get("ORG_NM"),
            "period": f"{r.get('STRT_PRD_DE')}~{r.get('END_PRD_DE')}",
            "link": r.get("LINK_URL") or r.get("TBL_VIEW_URL"),
        }
        for r in results
    ]


def get_table_info(
    org_id: str,
    tbl_id: str,
    name_filter: str | None = None,
    client: KosisClient | None = None,
) -> dict[str, Any]:
    """표의 항목(ITEM)·분류·수록주기를 조회한다.

    분류 코드가 많으면(예: 행정구역 4천여개) name_filter로 이름을 좁혀서
    그 안에서 매칭되는 코드만 돌려준다. 매칭이 없으면 앞쪽 일부를 보여주고
    truncated=True로 표시해, 모델이 "코드를 다 못 봤다"는 걸 알 수 있게 한다.
    """
    client = client or KosisClient()
    item_rows, classes = split_item_meta(client.get_table_meta(org_id, tbl_id, "ITM"))

    items = [{"itm_id": r["ITM_ID"], "name": r.get("ITM_NM"), "unit": r.get("UNIT_NM")} for r in item_rows]

    classifications = []
    for obj_id, rows in classes:
        matched = [r for r in rows if name_filter and name_filter in (r.get("ITM_NM") or "")]
        if name_filter:
            codes, truncated = (matched, False) if matched else (rows[:MAX_CODES_WITHOUT_FILTER], True)
        else:
            codes, truncated = rows[:MAX_CODES_WITHOUT_FILTER], len(rows) > MAX_CODES_WITHOUT_FILTER

        classifications.append(
            {
                "obj_id": obj_id,
                "name": rows[0].get("OBJ_NM"),
                "codes": [{"code": r["ITM_ID"], "name": r.get("ITM_NM")} for r in codes],
                "total_codes": len(rows),
                "truncated": truncated,
            }
        )

    periods = [
        {
            "prd_se": PRD_MAP.get(row.get("PRD_SE", "Y"), row.get("PRD_SE", "Y")),
            "prd_se_name": row.get("PRD_SE"),
            "start": row.get("STRT_PRD_DE"),
            "end": row.get("END_PRD_DE"),
        }
        for row in client.get_table_meta(org_id, tbl_id, "PRD")
    ]

    return {"items": items, "classifications": classifications, "periods": periods}


def get_data(
    org_id: str,
    tbl_id: str,
    itm_id: str,
    obj_codes: list[str],
    prd_se: str,
    start: str | None = None,
    end: str | None = None,
    recent: int | None = None,
    client: KosisClient | None = None,
) -> list[dict[str, Any]]:
    """실제 수치를 조회한다. 시점 오름차순(오래된 것 먼저)으로 정렬해서 돌려준다."""
    client = client or KosisClient()
    rows = client.get_data(org_id, tbl_id, itm_id, obj_codes, prd_se, start=start, end=end, recent=recent)
    data = [
        {
            "period": r.get("PRD_DE"),
            "value": r.get("DT"),
            "unit": r.get("UNIT_NM"),
            "item_name": r.get("ITM_NM"),
            "labels": [r.get(f"C{i}_NM") for i in range(1, 9) if r.get(f"C{i}_NM")],
        }
        for r in rows
    ]
    return sorted(data, key=lambda row: row["period"] or "")


def calculate_change(
    start_period: str, start_value: float, end_period: str, end_value: float
) -> dict[str, Any]:
    """두 시점 사이의 증감(량/률)을 계산한다. 모델이 암산하지 않고 코드가 계산한다.

    get_data 결과에서 비교하고 싶은 두 시점(보통 가장 오래된 것과 가장 최신)을
    모델이 직접 골라 숫자로 넘기면 된다.
    """
    start_value, end_value = float(start_value), float(end_value)
    abs_change = end_value - start_value

    return {
        "start_period": start_period,
        "start_value": start_value,
        "end_period": end_period,
        "end_value": end_value,
        "abs_change": abs_change,
        "pct_change": (abs_change / start_value * 100) if start_value else None,
    }


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "search_tables",
        "description": "키워드로 KOSIS 통계표를 검색한다. 어떤 통계표를 써야 할지 모를 때 가장 먼저 호출한다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "keyword": {"type": "string", "description": "검색어 (예: '주민등록인구', '고용률')"},
                "limit": {"type": "integer", "description": "최대 결과 수 (기본 5)"},
            },
            "required": ["keyword"],
        },
    },
    {
        "name": "get_table_info",
        "description": (
            "통계표의 항목(ITEM)·분류(지역/성별/연령 등)·수록주기를 조회한다. "
            "get_data를 호출하려면 먼저 이걸로 itm_id, 분류 코드, prd_se를 알아내야 한다. "
            "분류 코드가 수천 개인 표도 있으므로(예: 행정구역), name_filter로 원하는 이름을 좁혀서 찾는다. "
            "결과의 어떤 분류에 truncated=true가 있으면 코드 전체를 못 본 것이니, "
            "필요한 코드를 못 찾았다면 name_filter를 바꿔 다시 호출한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string", "description": "search_tables가 돌려준 org_id"},
                "tbl_id": {"type": "string", "description": "search_tables가 돌려준 tbl_id"},
                "name_filter": {
                    "type": "string",
                    "description": "분류 코드 중 이름에 이 문자열이 포함된 것만 찾기 (예: '평택')",
                },
            },
            "required": ["org_id", "tbl_id"],
        },
    },
    {
        "name": "get_data",
        "description": "실제 통계 수치를 조회한다. get_table_info로 알아낸 itm_id/분류 코드/prd_se가 필요하다.",
        "input_schema": {
            "type": "object",
            "properties": {
                "org_id": {"type": "string"},
                "tbl_id": {"type": "string"},
                "itm_id": {"type": "string", "description": "get_table_info의 items[].itm_id"},
                "obj_codes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "분류별 코드 목록. get_table_info의 classifications 순서대로 하나씩",
                },
                "prd_se": {"type": "string", "description": "수록주기 코드 (Y/H/Q/M/D)"},
                "start": {"type": "string", "description": "시작 시점 (recent 대신 쓸 때, 예: '2022')"},
                "end": {"type": "string", "description": "끝 시점 (recent 대신 쓸 때)"},
                "recent": {"type": "integer", "description": "최근 N개 시점만 조회 (start/end 대신 쓸 때)"},
            },
            "required": ["org_id", "tbl_id", "itm_id", "obj_codes", "prd_se"],
        },
    },
    {
        "name": "calculate_change",
        "description": "두 시점 사이의 증감량·증감률을 계산한다. 직접 암산하지 말고 반드시 이 도구로 계산할 것.",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_period": {"type": "string"},
                "start_value": {"type": "number"},
                "end_period": {"type": "string"},
                "end_value": {"type": "number"},
            },
            "required": ["start_period", "start_value", "end_period", "end_value"],
        },
    },
]

# 도구 이름 → 실제로 호출할 함수
TOOL_FUNCTIONS = {
    "search_tables": search_tables,
    "get_table_info": get_table_info,
    "get_data": get_data,
    "calculate_change": calculate_change,
}
