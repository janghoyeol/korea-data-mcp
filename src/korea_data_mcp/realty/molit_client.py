"""국토교통부 아파트 매매 실거래가 상세 자료(공공데이터포털) OpenAPI 클라이언트.

stat-agent의 kosis_client.py와 같은 역할이지만, 이 API는 JSON이 아니라 XML을
돌려주므로 파싱 레이어가 새로 필요하다.

설계 원칙 (kosis_client와 동일)
  - 에러를 print로 삼키지 않고 예외로 올린다. 에이전트가 실패를 알아야
    다른 기간/지역을 시도하는 판단을 할 수 있기 때문.
  - 숫자 필드(거래금액 "82,500" 같은 문자열)는 여기서 int/float로 바꿔 둔다.
    어떤 필드를 모델에게 보여줄지는 다음 레이어(tools.py)에서 정한다.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, quote_plus, unquote

import requests

BASE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev"
ENDPOINT = f"{BASE_URL}/getRTMSDataSvcAptTradeDev"
PORTAL_URL = "https://www.data.go.kr/data/15126468/openapi.do"
SOURCE_NAME = "국토교통부 아파트 매매 실거래가 상세 자료"

# 한 페이지에 받을 최대 건수. 강남구처럼 거래가 많은 달은 수백 건이라 페이지네이션이 필요하다.
PAGE_SIZE = 1000

# 정상 응답 resultCode. 신규(Dev) API는 "000", 예전 API는 "00"을 썼다.
OK_CODES = {"00", "000"}

INT_FIELDS = {"dealYear", "dealMonth", "dealDay", "floor", "buildYear"}


class MolitError(RuntimeError):
    """API가 에러를 돌려주거나 응답을 해석할 수 없을 때."""


def normalize_service_key(key: str) -> str:
    """서비스키를 '디코딩' 버전으로 맞춘다.

    data.go.kr은 키를 인코딩/디코딩 두 버전으로 준다. requests의 params는
    값을 알아서 URL 인코딩하므로, 인코딩된 키(%2B, %3D 등 포함)를 그대로 넘기면
    이중 인코딩되어 SERVICE_KEY_IS_NOT_REGISTERED_ERROR가 난다.
    어느 쪽을 .env에 넣어도 동작하도록 '%'가 보이면 한 번 디코딩한다.
    """
    key = key.strip()
    return unquote(key) if "%" in key else key


def parse_amount(text: str | None) -> int | None:
    """거래금액 "  82,500" → 82500 (단위: 만원)."""
    if not text:
        return None
    digits = text.replace(",", "").strip()
    return int(digits) if digits else None


def parse_response(xml_text: str) -> tuple[list[dict[str, Any]], int]:
    """응답 XML을 (거래 dict 리스트, totalCount)로 바꾼다.

    에러는 두 가지 모양으로 온다.
      1) <response><header><resultCode>03</resultCode>... (정상 포맷 안의 에러 코드)
      2) <OpenAPI_ServiceResponse><cmmMsgHeader><returnAuthMsg>SERVICE_KEY_...  (게이트웨이 에러)
    """
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as e:
        raise MolitError(f"XML 해석 실패. 응답 앞부분: {xml_text[:200]!r}") from e

    if root.tag == "OpenAPI_ServiceResponse":
        auth_msg = root.findtext(".//returnAuthMsg") or ""
        err_msg = root.findtext(".//errMsg") or ""
        reason = root.findtext(".//returnReasonCode") or ""
        hint = ""
        if "SERVICE_KEY" in auth_msg:
            hint = " (서비스키 확인: 활용신청 직후라면 반영까지 시간이 걸릴 수 있고, 인코딩/디코딩 키 혼동도 흔한 원인)"
        raise MolitError(f"API 에러 {reason}: {err_msg} {auth_msg}{hint}".strip())

    code = (root.findtext("header/resultCode") or root.findtext(".//resultCode") or "").strip()
    if code and code not in OK_CODES:
        msg = (root.findtext("header/resultMsg") or root.findtext(".//resultMsg") or "").strip()
        raise MolitError(f"API 에러 {code}: {msg}")

    items = []
    for item in root.iterfind(".//items/item"):
        row: dict[str, Any] = {child.tag: (child.text or "").strip() for child in item}
        row["dealAmount"] = parse_amount(row.get("dealAmount"))
        row["excluUseAr"] = float(row["excluUseAr"]) if row.get("excluUseAr") else None
        for key in INT_FIELDS:
            value = row.get(key)
            row[key] = int(value) if value and value.lstrip("-").isdigit() else None
        items.append(row)

    total = root.findtext(".//totalCount")
    return items, int(total) if total and total.strip().isdigit() else len(items)


class MolitClient:
    def __init__(self, service_key: str | None = None, timeout: float = 20.0):
        key = service_key or os.environ.get("DATA_GO_KR_SERVICE_KEY")
        if not key:
            raise MolitError(
                "DATA_GO_KR_SERVICE_KEY가 없습니다. .env 파일에 공공데이터포털 서비스키를 넣었는지 확인하세요."
            )
        self.service_key = normalize_service_key(key)
        self.timeout = timeout
        self.session = requests.Session()

    def _get_page(self, lawd_cd: str, deal_ymd: str, page_no: int) -> tuple[list[dict[str, Any]], int]:
        params = {
            "serviceKey": self.service_key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "pageNo": page_no,
            "numOfRows": PAGE_SIZE,
        }
        try:
            res = self.session.get(ENDPOINT, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise MolitError(f"실거래가 API 요청 실패: {self._mask(str(e))}") from e
        if res.status_code in (401, 403):
            raise MolitError(
                f"실거래가 API가 {res.status_code}로 거절했습니다. 이 서비스키로 "
                "'국토교통부_아파트 매매 실거래가 상세 자료'(RTMSDataSvcAptTradeDev)를 활용신청했는지, "
                "신청 직후라면 키가 반영될 때까지(보통 1시간 안팎) 기다렸는지 확인하세요. "
                f"응답: {self._mask(res.text[:200])!r}"
            )
        if not res.ok:
            raise MolitError(f"실거래가 API HTTP {res.status_code}: {self._mask(res.text[:200])!r}")
        res.encoding = "utf-8"
        return parse_response(res.text)

    def _mask(self, text: str) -> str:
        """에러 메시지에 서비스키가 찍히지 않게 가린다 (원본/URL 인코딩 둘 다)."""
        for variant in {self.service_key, quote(self.service_key, safe=""), quote_plus(self.service_key)}:
            text = text.replace(variant, "***")
        return text

    def get_month(self, lawd_cd: str, deal_ymd: str) -> list[dict[str, Any]]:
        """시군구(LAWD_CD 5자리) 하나의 한 달치 거래를 페이지 끝까지 전부 가져온다."""
        if len(lawd_cd) != 5 or not lawd_cd.isdigit():
            raise ValueError(f"LAWD_CD는 법정동코드 앞 5자리 숫자여야 합니다: {lawd_cd!r}")
        if len(deal_ymd) != 6 or not deal_ymd.isdigit():
            raise ValueError(f"DEAL_YMD는 YYYYMM 6자리여야 합니다: {deal_ymd!r}")

        items, total = self._get_page(lawd_cd, deal_ymd, 1)
        page = 1
        while len(items) < total:
            page += 1
            more, _ = self._get_page(lawd_cd, deal_ymd, page)
            if not more:  # totalCount와 실제 건수가 어긋나도 무한루프에 빠지지 않게
                break
            items.extend(more)
        return items


def source_info(lawd_cd: str, months: list[str]) -> dict[str, str]:
    """모든 도구 결과에 붙일 출처 정보."""
    return {
        "name": SOURCE_NAME,
        "provider": "국토교통부 (공공데이터포털 OpenAPI)",
        "url": PORTAL_URL,
        "query": f"LAWD_CD={lawd_cd}, 계약년월 {months[0]}~{months[-1]}" if months else f"LAWD_CD={lawd_cd}",
        "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "note": "실거래 신고 자료 기준. 신고기한(계약 후 30일) 때문에 최근 1~2개월은 건수가 늘어날 수 있음.",
    }
