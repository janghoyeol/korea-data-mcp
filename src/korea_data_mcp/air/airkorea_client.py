"""한국환경공단 에어코리아 "측정소별 실시간 측정정보 조회" OpenAPI 클라이언트.

molit_client.py(realty-agent)와 같은 설계 원칙:
  - 에러를 print로 삼키지 않고 예외로 올린다.
  - 서비스키는 에러 메시지에 절대 노출하지 않는다.

이 API 고유의 특징: dataTerm=DAILY로 최근 24시간 자료를 '최신순'으로 돌려주는데,
방금 지난 시각은 아직 집계 전이라 수치가 "-"(측정불가)로 비어 있는 경우가 흔하다.
그래서 최신 행부터 내려가며 '숫자가 있는 첫 행'을 실제 최신값으로 쓴다.
"""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, quote_plus, unquote

import requests

BASE_URL = "https://apis.data.go.kr/B552584/ArpltnInforInqireSvc"
ENDPOINT = f"{BASE_URL}/getMsrstnAcctoRltmMesureDnsty"
PORTAL_URL = "https://www.data.go.kr/data/15073861/openapi.do"
SOURCE_NAME = "한국환경공단 에어코리아 대기오염정보 (측정소별 실시간 측정정보)"

OK_CODES = {"00", "000"}

# 등급 코드(1~4) → 사람이 읽는 말. "-"는 측정소 점검 등으로 자료가 없는 상태.
GRADE_LABELS = {"1": "좋음", "2": "보통", "3": "나쁨", "4": "매우나쁨"}

_NUMERIC_FIELDS = (
    "pm10Value", "pm10Value24", "pm25Value", "pm25Value24",
    "o3Value", "no2Value", "coValue", "so2Value", "khaiValue",
)


class AirkoreaError(RuntimeError):
    """API가 에러를 돌려주거나 응답을 해석할 수 없을 때."""


def normalize_service_key(key: str) -> str:
    """molit_client와 동일한 인코딩/디코딩 키 혼동 방지 처리."""
    key = key.strip()
    return unquote(key) if "%" in key else key


def _to_float(text: str | None) -> float | None:
    if not text or text.strip() in ("-", ""):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_response(xml_text: str) -> list[dict[str, Any]]:
    """응답 XML → 측정 기록 리스트 (최신순 그대로)."""
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as e:
        raise AirkoreaError(f"XML 해석 실패. 응답 앞부분: {xml_text[:200]!r}") from e

    if root.tag == "OpenAPI_ServiceResponse":
        auth_msg = root.findtext(".//returnAuthMsg") or ""
        err_msg = root.findtext(".//errMsg") or ""
        reason = root.findtext(".//returnReasonCode") or ""
        hint = " (서비스키 확인: 활용신청 직후면 반영까지 시간이 걸릴 수 있음)" if "SERVICE_KEY" in auth_msg else ""
        raise AirkoreaError(f"API 에러 {reason}: {err_msg} {auth_msg}{hint}".strip())

    code = (root.findtext(".//resultCode") or "").strip()
    if code and code not in OK_CODES:
        msg = (root.findtext(".//resultMsg") or "").strip()
        raise AirkoreaError(f"API 에러 {code}: {msg}")

    rows = []
    for item in root.iterfind(".//items/item"):
        row: dict[str, Any] = {child.tag: (child.text or "").strip() for child in item}
        for key in _NUMERIC_FIELDS:
            row[key] = _to_float(row.get(key))
        rows.append(row)
    return rows


class AirkoreaClient:
    def __init__(self, service_key: str | None = None, timeout: float = 15.0):
        key = service_key or os.environ.get("DATA_GO_KR_SERVICE_KEY")
        if not key:
            raise AirkoreaError(
                "DATA_GO_KR_SERVICE_KEY가 없습니다. .env 파일에 공공데이터포털 서비스키를 넣었는지 확인하세요."
            )
        self.service_key = normalize_service_key(key)
        self.timeout = timeout
        self.session = requests.Session()

    def get_latest(self, station_name: str) -> dict[str, Any]:
        """측정소의 최근 24시간 중, 값이 채워진 가장 최신 행을 돌려준다."""
        params = {
            "serviceKey": self.service_key,
            "returnType": "xml",
            "numOfRows": 24,
            "pageNo": 1,
            "stationName": station_name,
            "dataTerm": "DAILY",
            "ver": "1.3",
        }
        try:
            res = self.session.get(ENDPOINT, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise AirkoreaError(f"대기질 API 요청 실패: {self._mask(str(e))}") from e
        if res.status_code in (401, 403):
            raise AirkoreaError(
                f"대기질 API가 {res.status_code}로 거절했습니다. 이 서비스키로 "
                "'한국환경공단_에어코리아_대기오염정보 조회서비스'를 활용신청했는지 확인하세요. "
                f"응답: {self._mask(res.text[:200])!r}"
            )
        if not res.ok:
            raise AirkoreaError(f"대기질 API HTTP {res.status_code}: {self._mask(res.text[:200])!r}")
        res.encoding = "utf-8"
        rows = parse_response(res.text)
        if not rows:
            raise AirkoreaError(f"측정소 '{station_name}'의 자료가 없습니다. 측정소명을 확인하세요.")

        usable = [r for r in rows if r.get("pm10Value") is not None or r.get("pm25Value") is not None]
        if not usable:
            raise AirkoreaError(f"측정소 '{station_name}'의 최근 24시간 자료가 전부 비어 있습니다 (점검 중일 수 있음).")
        return usable[0]

    def _mask(self, text: str) -> str:
        for variant in {self.service_key, quote(self.service_key, safe=""), quote_plus(self.service_key)}:
            text = text.replace(variant, "***")
        return text


def source_info(station_name: str) -> dict[str, str]:
    return {
        "name": SOURCE_NAME,
        "provider": "한국환경공단 (공공데이터포털 OpenAPI)",
        "url": PORTAL_URL,
        "query": f"stationName={station_name}",
        "retrieved_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "note": "측정소 기준 1시간 평균값. 결측 시간대는 제외하고 가장 최근 유효 값을 썼음.",
    }
