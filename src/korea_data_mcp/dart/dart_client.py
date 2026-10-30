"""OpenDART(금융감독원 전자공시시스템) API 클라이언트.

https://opendart.fss.or.kr 의 단일회사 주요계정 API와 고유번호 전체자료
API를 감싼다. 이 모듈은 HTTP I/O와 응답 코드 처리만 책임지고, 계정과목
매칭 같은 도메인 로직은 tools.py에 둔다.
"""
from __future__ import annotations

import io
import json
import zipfile
from typing import Optional

import requests

BASE_URL = "https://opendart.fss.or.kr/api"
CORP_CODE_URL = f"{BASE_URL}/corpCode.xml"
FINANCIALS_URL = f"{BASE_URL}/fnlttSinglAcntAll.json"

REPRT_CODE_NAMES = {
    "11011": "사업보고서",
    "11012": "반기보고서",
    "11013": "1분기보고서",
    "11014": "3분기보고서",
}


class DartApiError(Exception):
    """DART API가 status "000"이 아닌 응답을 반환했을 때."""

    def __init__(self, status: str, message: str):
        self.status = status
        self.message = message
        super().__init__(f"DART API error {status}: {message}")


class DartNoDataError(DartApiError):
    """status 013 — 조회된 데이터가 없음. CFS→OFS 폴백 트리거로 쓰인다."""


class DartAuthError(DartApiError):
    """status 010/011/012/800/901 — 인증키 관련 오류."""


class DartRateLimitError(DartApiError):
    """status 020/021 — 호출 한도 초과."""


_AUTH_CODES = {"010", "011", "012", "800", "901"}
_RATE_LIMIT_CODES = {"020", "021"}


def _raise_for_status(payload: dict) -> None:
    status = payload.get("status")
    message = payload.get("message", "")
    if status == "000":
        return
    if status == "013":
        raise DartNoDataError(status, message)
    if status in _AUTH_CODES:
        raise DartAuthError(status, message)
    if status in _RATE_LIMIT_CODES:
        raise DartRateLimitError(status, message)
    raise DartApiError(status, message)


class DartClient:
    """OpenDART API 호출 클라이언트."""

    def __init__(
        self,
        api_key: str,
        session: Optional[requests.Session] = None,
        timeout: int = 15,
    ):
        if not api_key:
            raise ValueError("OPENDART_API_KEY가 필요합니다 (.env 또는 환경변수).")
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout

    def download_corp_code_zip(self) -> bytes:
        """고유번호 전체자료(zip)를 다운로드한다.

        거의 바뀌지 않는 정적 자료이므로, 호출부(scripts/download_corpcodes.py)
        에서 결과를 TSV 파일로 캐싱해 재사용할 것을 권장한다 — 매 요청마다
        부르면 일일 호출 한도를 낭비한다.
        """
        resp = self.session.get(
            CORP_CODE_URL, params={"crtfc_key": self.api_key}, timeout=self.timeout
        )
        resp.raise_for_status()
        content = resp.content
        # 키 오류 등은 zip이 아니라 JSON 에러 응답으로 내려온다.
        if not content.startswith(b"PK"):
            try:
                _raise_for_status(json.loads(content))
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass
            raise DartApiError("UNKNOWN", "corpCode.xml 응답이 zip 형식이 아닙니다.")
        return content

    def get_financials(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
        fs_div: str = "CFS",
    ) -> list[dict]:
        """단일회사 주요계정 조회.

        status 013(데이터 없음)이면 DartNoDataError를 그대로 던진다 —
        CFS→OFS 폴백 여부는 tools.get_financials에서 결정한다. 이 메서드는
        순수 HTTP 호출만 책임진다.
        """
        resp = self.session.get(
            FINANCIALS_URL,
            params={
                "crtfc_key": self.api_key,
                "corp_code": corp_code,
                "bsns_year": bsns_year,
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        _raise_for_status(payload)
        return payload["list"]


def extract_corp_code_xml(zip_bytes: bytes) -> bytes:
    """corpCode.xml(zip) 안의 단일 XML 파일 바이트를 꺼낸다."""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        if not names:
            raise ValueError("corpCode zip 안에 파일이 없습니다.")
        return zf.read(names[0])
