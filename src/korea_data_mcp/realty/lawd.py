"""법정동코드 테이블 (주소 → LAWD_CD).

data/lawd_codes.tsv 에 법정동코드 전체자료를 정적 파일로 캐싱해 두고 읽는다.
거의 안 바뀌는 데이터라 매번 조회할 필요가 없다.

행정표준코드관리시스템(code.go.kr)의 "법정동코드 전체자료.txt"(탭 구분,
법정동코드/법정동명/폐지여부, cp949)를 그대로 data/lawd_codes.tsv 자리에
넣어도 읽히도록, 인코딩과 '폐지여부' 열을 둘 다 처리한다.
"""

from __future__ import annotations

import csv
import itertools
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_PATH = Path(__file__).resolve().parent / "data" / "lawd_codes.tsv"

# 행정구역 단위로 끝나는 이름 (예: 강남구, 대치동, 을지로1가, 진접읍, 서면)
REGION_SUFFIXES = ("시", "군", "구", "동", "가", "읍", "면", "리")

# 단위 없이 쓴 지역명에 붙여볼 단위 (앞쪽이 우선)
BARE_UNITS = ("구", "시", "군", "동", "읍", "면")

# 시도 약칭 → 정식 명칭
SIDO_ALIASES = {
    "서울": "서울특별시", "부산": "부산광역시", "대구": "대구광역시", "인천": "인천광역시",
    "광주": "광주광역시", "대전": "대전광역시", "울산": "울산광역시", "세종": "세종특별자치시",
    "경기": "경기도", "강원": "강원특별자치도", "충북": "충청북도", "충남": "충청남도",
    "전북": "전북특별자치도", "전남": "전라남도", "경북": "경상북도", "경남": "경상남도",
    "제주": "제주특별자치도",
}


@dataclass(frozen=True)
class LawdRow:
    code: str  # 10자리
    name: str  # "서울특별시 강남구 대치동"

    @property
    def lawd_cd(self) -> str:
        """실거래가 API의 LAWD_CD (시군구 단위, 앞 5자리)."""
        return self.code[:5]

    @property
    def parts(self) -> list[str]:
        return self.name.split()


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "cp949"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"{path}의 인코딩을 알 수 없습니다 (utf-8/cp949 아님)")


@lru_cache(maxsize=1)
def load_rows(path: Path = DATA_PATH) -> tuple[LawdRow, ...]:
    lines = [line for line in _read_text(path).splitlines() if line and not line.startswith("#")]
    reader = csv.reader(lines, delimiter="\t")
    header = next(reader)
    has_status = len(header) >= 3  # code.go.kr 원본: 법정동코드 / 법정동명 / 폐지여부
    rows = []
    for rec in reader:
        if len(rec) < 2 or not rec[0].strip().isdigit():
            continue
        if has_status and len(rec) >= 3 and rec[2].strip() == "폐지":
            continue
        rows.append(LawdRow(rec[0].strip(), rec[1].strip()))
    return tuple(rows)


def sigungu_name(lawd_cd: str) -> str | None:
    """LAWD_CD(5자리) → "서울특별시 강남구"."""
    target = lawd_cd + "00000"
    for row in load_rows():
        if row.code == target:
            return row.name
    return None


def dongs_in_sigungu(lawd_cd: str) -> list[str]:
    """시군구 안의 읍면동 이름 목록 (리 단위 제외)."""
    names = []
    for row in load_rows():
        if row.lawd_cd == lawd_cd and row.code[5:] != "00000" and row.code[8:] == "00":
            names.append(row.parts[-1])
    return names


def split_query(query: str) -> tuple[list[LawdRow], list[str], list[str]]:
    """입력을 (매칭된 법정동 후보, 지역으로 쓰인 토큰, 남은 토큰=단지명 후보)로 나눈다.

    "대치동 은마아파트" → 대치동 후보 1개 + ["은마아파트"]
    "강남구 대치동"     → 강남구로 좁힌 대치동 후보
    "중구 신당동"       → 서울 중구 신당동 (다른 시도 중구엔 신당동이 없음)
    "중구"              → 시군구 단위 후보 여러 개 (서울/부산/대구/인천/대전/울산)
    """
    rows = load_rows()
    tokens = [t for t in re.split(r"[\s,]+", query.strip()) if t]

    # 각 토큰이 법정동명의 어떤 '조각'과 정확히 일치하는지 본다 (부분 문자열 매칭은 오탐이 많음).
    # "강남", "대치"처럼 단위를 뺀 토큰은 붙일 수 있는 단위를 전부 후보로 둔다.
    all_parts = {p for row in rows for p in row.parts}
    options: list[list[str]] = []
    rest: list[str] = []
    for tok in tokens:
        tok = SIDO_ALIASES.get(tok, tok)
        if tok in all_parts:
            options.append([tok])
        elif len(tok) >= 2 and not tok.endswith(REGION_SUFFIXES):
            expanded = [tok + unit for unit in BARE_UNITS if tok + unit in all_parts]
            if expanded:
                options.append(expanded)
            else:
                rest.append(tok)
        else:
            rest.append(tok)

    if not options:
        return [], [], rest

    # 단위 조합 중 실제로 존재하는 지역이 나오는 첫 조합을 쓴다 ("강남 개포동" → 강남구+개포동)
    for region_tokens in itertools.product(*options):
        candidates = [row for row in rows if all(tok in row.parts for tok in region_tokens)]
        # 가장 구체적인 토큰(마지막 지역 토큰)으로 끝나는 행만 남긴다 (그 아래 리 단위 행 제외)
        candidates = [row for row in candidates if row.parts[-1] == region_tokens[-1]]
        if candidates:
            return candidates, list(region_tokens), rest
    return [], list(options[0]), rest
