"""corp-agent(agent.py)의 TOOL_DEFINITIONS 원문 그대로."""

TOOL_SCHEMAS = [
    {
        "name": "resolve_company",
        "description": (
            "회사명으로 DART 고유번호(corp_code) 후보를 찾는다. 결과가 여러 개면 "
            "상장사를 우선으로 정렬해 반환하므로, 모호하면 사용자에게 어느 회사인지 "
            "확인하거나 가장 그럴듯한 상장사 후보를 선택했음을 밝히고 다음 단계로 진행한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "회사명 (예: 삼성전자)"}
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_financials",
        "description": (
            "corp_code와 사업연도로 주요 재무계정(매출액/영업이익/당기순이익/자산총계/"
            "부채총계)을 조회한다. 연결재무제표(CFS)를 우선 시도하고 데이터가 없으면 "
            "개별재무제표(OFS)로 자동 폴백한다. 반환값의 rcept_no(접수번호)와 fs_div를 "
            "반드시 출처로 함께 제시한다. accounts의 값이 null이면 해당 연도 공시에서 "
            "그 계정을 찾지 못한 것이므로, 숫자를 지어내지 말고 모른다고 답한다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "corp_code": {"type": "string", "description": "8자리 DART 고유번호"},
                "bsns_year": {"type": "string", "description": "사업연도 (예: 2023)"},
                "reprt_code": {
                    "type": "string",
                    "description": "11011=사업보고서(연간, 기본값), 11012=반기, 11013=1분기, 11014=3분기",
                    "default": "11011",
                },
            },
            "required": ["corp_code", "bsns_year"],
        },
    },
    {
        "name": "compute_growth",
        "description": (
            "연도별 금액 목록을 받아 전년 대비 증감률(%)을 코드로 계산한다. 여러 연도의 "
            "매출/영업이익/순이익 추이나 증감률을 답할 때는 반드시 이 도구를 쓰고, "
            "직접 암산한 숫자를 답에 쓰지 않는다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "values": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "year": {"type": "string"},
                            "amount": {"type": "number"},
                        },
                        "required": ["year", "amount"],
                    },
                }
            },
            "required": ["values"],
        },
    },
]
