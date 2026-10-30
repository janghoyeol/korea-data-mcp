"""에이전트가 호출하는 3개 tool의 실제 구현.

resolve_company / get_financials / compute_growth 모두 "모델이 암산하지
않고, 못 찾으면 지어내지 않는다"는 원칙을 지키도록 설계했다.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .corp_codes import CorpCode
from .corp_codes import resolve_company as _resolve_company
from .dart_client import REPRT_CODE_NAMES, DartNoDataError

# 계정과목(account_nm) 표기가 회사/업종마다 달라서, 허용 표기 목록 중
# 첫 매치를 쓴다. 리스트 순서가 우선순위다.
ACCOUNT_ALIASES: dict[str, list[str]] = {
    "매출액": ["매출액", "수익(매출액)", "영업수익"],
    "영업이익": ["영업이익", "영업이익(손실)"],
    "당기순이익": [
        "당기순이익",
        "당기순이익(손실)",
        "분기순이익",
        "분기순이익(손실)",
        "반기순이익",
        "반기순이익(손실)",
    ],
    "자산총계": ["자산총계"],
    "부채총계": ["부채총계"],
}

# 재무상태표(BS) 계정은 sj_div == "BS" 행에서만 찾는다. 그 외(손익계산서
# 계정 등)는 BS가 아닌 행에서 찾는다 — 동명이의 계정과목 오매칭을 막는다.
_BS_ACCOUNTS = {"자산총계", "부채총계"}


@dataclass
class FinancialFact:
    canonical_name: str
    account_nm: str  # 실제 DART 응답에 찍힌 표기
    amount: Optional[int]
    bsns_year: str
    reprt_code: str
    fs_div: str
    rcept_no: str  # 접수번호 — 출처 추적용


def _find_account(accounts: list[dict], canonical_name: str) -> Optional[dict]:
    aliases = ACCOUNT_ALIASES[canonical_name]
    want_bs = canonical_name in _BS_ACCOUNTS
    for alias in aliases:
        for row in accounts:
            if row.get("account_nm", "").strip() != alias:
                continue
            is_bs_row = row.get("sj_div", "") == "BS"
            if want_bs != is_bs_row:
                continue
            return row
    return None


def _parse_amount(raw: Optional[str]) -> Optional[int]:
    if raw is None:
        return None
    raw = raw.replace(",", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def get_financials(
    client,
    corp_code: str,
    bsns_year: str,
    reprt_code: str = "11011",
    accounts: Optional[list[str]] = None,
) -> dict:
    """주요계정 금액 조회.

    연결재무제표(CFS)로 먼저 시도하고, status 013(데이터 없음 — 보통 연결
    대상 자회사가 없는 회사)이면 개별재무제표(OFS)로 자동 폴백한다.
    반환값에는 항상 실제 사용된 fs_div와 rcept_no(접수번호)를 담아 출처를
    밝힐 수 있게 한다. 계정을 찾지 못하면 None으로 남기고 지어내지 않는다.

    `client`는 DartClient와 같은 인터페이스(get_financials 메서드)를
    구현한 객체면 된다 — 테스트에서는 FakeDartClient로 대체한다.
    """
    accounts = accounts or list(ACCOUNT_ALIASES.keys())
    fs_div_used = "CFS"
    try:
        rows = client.get_financials(corp_code, bsns_year, reprt_code, fs_div="CFS")
    except DartNoDataError:
        fs_div_used = "OFS"
        rows = client.get_financials(corp_code, bsns_year, reprt_code, fs_div="OFS")

    rcept_no = rows[0].get("rcept_no", "") if rows else ""
    facts: dict[str, Optional[FinancialFact]] = {}
    for name in accounts:
        row = _find_account(rows, name)
        if row is None:
            facts[name] = None
            continue
        facts[name] = FinancialFact(
            canonical_name=name,
            account_nm=row.get("account_nm", ""),
            amount=_parse_amount(row.get("thstrm_amount")),
            bsns_year=bsns_year,
            reprt_code=reprt_code,
            fs_div=fs_div_used,
            rcept_no=rcept_no,
        )

    return {
        "corp_code": corp_code,
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
        "reprt_name": REPRT_CODE_NAMES.get(reprt_code, reprt_code),
        "fs_div": fs_div_used,
        "fs_div_name": "연결재무제표" if fs_div_used == "CFS" else "개별재무제표",
        "rcept_no": rcept_no,
        "accounts": {
            name: (
                {"account_nm": f.account_nm, "amount": f.amount} if f is not None else None
            )
            for name, f in facts.items()
        },
    }


def compute_growth(values: list[dict]) -> list[dict]:
    """연도별 금액에서 전년 대비 증감률을 코드로 계산한다 (모델 암산 금지).

    values: [{"year": "2022", "amount": 1000}, {"year": "2023", "amount": 1200}, ...]
    연도 오름차순이 아니어도 내부에서 정렬한다. 기준연도 금액이 0이면
    증감률을 계산할 수 없으므로(0으로 나눌 수 없음) None으로 남긴다.
    """
    sorted_values = sorted(values, key=lambda v: v["year"])
    result = []
    prev = None
    for v in sorted_values:
        entry = {"year": v["year"], "amount": v["amount"], "yoy_growth_pct": None}
        if (
            prev is not None
            and prev["amount"] not in (None, 0)
            and v["amount"] is not None
        ):
            growth = (v["amount"] - prev["amount"]) / abs(prev["amount"]) * 100
            entry["yoy_growth_pct"] = round(growth, 2)
        result.append(entry)
        prev = v
    return result


def resolve_company(name: str, corp_codes: list[CorpCode], limit: int = 10) -> list[dict]:
    candidates = _resolve_company(name, corp_codes, limit=limit)
    return [
        {
            "corp_code": c.corp_code,
            "corp_name": c.corp_name,
            "stock_code": c.stock_code or None,
            "is_listed": c.is_listed,
        }
        for c in candidates
    ]
