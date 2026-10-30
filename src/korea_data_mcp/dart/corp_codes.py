"""고유번호(corp_code) 전체자료 캐싱 및 회사명 → corp_code 변환.

DART는 종목코드가 아니라 8자리 고유번호(corp_code)로 회사를 식별한다.
고유번호 전체자료는 법정동코드처럼 거의 바뀌지 않는 정적 자료라서, 한 번
받아 TSV로 캐싱해두고 재사용한다 (scripts/download_corpcodes.py).
"""
from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CorpCode:
    corp_code: str
    corp_name: str
    stock_code: str  # 비상장사는 빈 문자열
    modify_date: str

    @property
    def is_listed(self) -> bool:
        return bool(self.stock_code.strip())


TSV_HEADER = ["corp_code", "corp_name", "stock_code", "modify_date"]


def parse_corp_code_xml(xml_bytes: bytes) -> list[CorpCode]:
    root = ET.fromstring(xml_bytes)
    result = []
    for item in root.iter("list"):
        result.append(
            CorpCode(
                corp_code=(item.findtext("corp_code") or "").strip(),
                corp_name=(item.findtext("corp_name") or "").strip(),
                stock_code=(item.findtext("stock_code") or "").strip(),
                modify_date=(item.findtext("modify_date") or "").strip(),
            )
        )
    return result


def write_tsv(corp_codes: list[CorpCode], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(TSV_HEADER)
        for c in corp_codes:
            writer.writerow([c.corp_code, c.corp_name, c.stock_code, c.modify_date])


def load_tsv(path: Path) -> list[CorpCode]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        return [
            CorpCode(
                corp_code=row["corp_code"],
                corp_name=row["corp_name"],
                stock_code=row["stock_code"],
                modify_date=row["modify_date"],
            )
            for row in reader
        ]


def resolve_company(name: str, corp_codes: list[CorpCode], limit: int = 10) -> list[CorpCode]:
    """회사명(부분 문자열)으로 후보를 찾는다.

    정확히 일치하는 이름을 최우선으로 두고, 그다음은 상장사를 우선 정렬한다 —
    모호한 입력("삼성"처럼 여러 계열사에 걸치는 경우)에서 먼저 보여줄 후보
    순서로 삼는다. 상장사 우선 정렬은 "면접관이 물어볼 법한 회사는 대개
    상장사"라는 실용적 가정에 기반한 것으로, 정답을 보장하지는 않는다 —
    모호하면 agent가 후보 목록 전체를 사용자에게 확인시켜야 한다.
    """
    name = name.strip()
    if not name:
        return []

    exact = [c for c in corp_codes if c.corp_name == name]
    partial = [c for c in corp_codes if name in c.corp_name and c.corp_name != name]

    def sort_key(c: CorpCode):
        return (not c.is_listed, c.corp_name)

    exact.sort(key=sort_key)
    partial.sort(key=sort_key)
    return (exact + partial)[:limit]
