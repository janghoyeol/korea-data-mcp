"""corp-agent의 CorpAgent._dispatch를 MCP용 함수로 옮긴 것.

corp-agent는 웹 서버가 시작될 때 고유번호 TSV(scripts/download_corpcodes.py로 미리 받음)를
읽었다. MCP 서버는 설치 직후 바로 써야 하므로, 처음 resolve_company가 불릴 때
OpenDART에서 고유번호 전체자료를 받아 사용자 캐시 폴더에 저장하고 재사용한다.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from . import tools as corp_tools
from .corp_codes import CorpCode, load_tsv, parse_corp_code_xml, write_tsv
from .dart_client import DartApiError, DartClient, extract_corp_code_xml

CORP_CODES_MAX_AGE = 7 * 24 * 3600  # 고유번호는 거의 안 바뀜: 1주일마다 갱신


def _cache_path() -> Path:
    base = os.environ.get("KOREA_DATA_MCP_CACHE") or os.path.join(
        os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "korea-data-mcp")
    return Path(base) / "corp_codes.tsv"


_client: DartClient | None = None
_corp_codes: list[CorpCode] | None = None


def _dart() -> DartClient:
    global _client
    if _client is None:
        _client = DartClient(api_key=os.environ.get("OPENDART_API_KEY", ""))
    return _client


def _codes() -> list[CorpCode]:
    global _corp_codes
    if _corp_codes is None:
        path = _cache_path()
        if not path.exists() or time.time() - path.stat().st_mtime > CORP_CODES_MAX_AGE:
            xml = extract_corp_code_xml(_dart().download_corp_code_zip())
            path.parent.mkdir(parents=True, exist_ok=True)
            write_tsv(parse_corp_code_xml(xml), path)
        _corp_codes = load_tsv(path)
    return _corp_codes


def resolve_company(name: str):
    return corp_tools.resolve_company(name, _codes())


def get_financials(corp_code: str, bsns_year: str, reprt_code: str = "11011"):
    try:
        return corp_tools.get_financials(_dart(), corp_code, bsns_year, reprt_code)
    except DartApiError as e:
        return {"error": str(e), "status": e.status}


def compute_growth(values: list[dict]):
    return corp_tools.compute_growth(values)


TOOL_FUNCTIONS = {
    "resolve_company": resolve_company,
    "get_financials": get_financials,
    "compute_growth": compute_growth,
}
