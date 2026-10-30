"""KOSIS(국가통계포털) 공유서비스 OpenAPI 클라이언트.

나중에 에이전트의 '도구'가 될 함수 3개를 여기서 만든다.
  - search_tables : 키워드로 통계표 찾기        (KOSIS 통합검색)
  - get_table_meta: 통계표의 항목/분류/기간 확인  (통계표설명)
  - get_data      : 실제 수치 조회             (통계자료)

설계 원칙
  - 에러를 print로 삼키지 않고 예외로 올린다. 에이전트가 실패를 알아야
    "다른 표를 찾아보자" 같은 판단을 할 수 있기 때문.
  - 응답은 가공하지 않은 dict 리스트로 돌려준다. 어떤 필드를 모델에게
    보여줄지는 다음 단계(도구 레이어)에서 정한다.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any

import requests

BASE_URL = "https://kosis.kr/openapi"

# 메타(PRD)에서 수록주기가 한글로 오는 경우를 대비한 변환표
PRD_MAP = {"년": "Y", "반기": "H", "분기": "Q", "월": "M", "일": "D"}

ENDPOINTS = {
    "search": f"{BASE_URL}/statisticsSearch.do",
    "meta": f"{BASE_URL}/statisticsData.do",
    "data": f"{BASE_URL}/Param/statisticsParameterData.do",
}

# 통계표설명(getMeta)의 type 값
META_TYPES = {
    "TBL": "통계표 명칭",
    "ORG": "기관 명칭",
    "PRD": "수록 주기/기간",
    "ITM": "항목 및 분류",
    "CMMT": "주석",
    "UNIT": "단위",
    "SOURCE": "출처",
    "NCD": "자료 갱신일",
}


class KosisError(RuntimeError):
    """KOSIS가 에러를 돌려주거나 응답을 해석할 수 없을 때."""


def split_item_meta(
    meta: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[tuple[str, list[dict[str, Any]]]]]:
    """ITM 메타를 항목(OBJ_ID='ITEM')과 분류(그 외, OBJ_ID_SN 순)로 나눈다."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in meta:
        groups[row.get("OBJ_ID", "?")].append(row)
    item_rows = groups.pop("ITEM", [])
    classes = sorted(groups.items(), key=lambda kv: int(kv[1][0].get("OBJ_ID_SN") or 0))
    return item_rows, classes


class KosisClient:
    def __init__(self, api_key: str | None = None, timeout: float = 15.0):
        self.api_key = api_key or os.environ.get("KOSIS_API_KEY")
        if not self.api_key:
            raise KosisError(
                "KOSIS_API_KEY가 없습니다. .env 파일에 키를 넣었는지 확인하세요."
            )
        self.timeout = timeout
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    # 내부 공통 요청 함수
    # ------------------------------------------------------------------ #
    def _get(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        query = {
            "apiKey": self.api_key,
            "format": "json",
            "jsonVD": "Y",  # 키에 따옴표가 붙은 '정상' JSON으로 받기
            **{k: v for k, v in params.items() if v is not None},
        }
        try:
            res = self.session.get(ENDPOINTS[endpoint], params=query, timeout=self.timeout)
            res.raise_for_status()
        except requests.RequestException as e:
            raise KosisError(f"KOSIS 요청 실패: {e}") from e

        try:
            body = res.json()
        except ValueError as e:
            raise KosisError(f"JSON 해석 실패. 응답 앞부분: {res.text[:200]!r}") from e

        # 정상이면 리스트, 에러면 {"err": "...", "errMsg": "..."} 형태의 dict
        if isinstance(body, dict):
            if body.get("errMsg") or body.get("err"):
                raise KosisError(f"KOSIS 에러 {body.get('err')}: {body.get('errMsg')}")
            return [body]
        return body

    # ------------------------------------------------------------------ #
    # 도구가 될 함수들
    # ------------------------------------------------------------------ #
    def search_tables(self, keyword: str, limit: int = 10) -> list[dict[str, Any]]:
        """키워드로 통계표를 검색한다."""
        return self._get(
            "search",
            {
                "method": "getList",
                "searchNm": keyword,
                "startCount": 1,
                "resultCount": limit,
                "sort": "RANK",
            },
        )

    def get_table_meta(self, org_id: str, tbl_id: str, meta_type: str = "ITM") -> list[dict[str, Any]]:
        """통계표 설명을 조회한다. meta_type은 META_TYPES의 키 중 하나."""
        if meta_type not in META_TYPES:
            raise ValueError(f"meta_type은 {list(META_TYPES)} 중 하나여야 합니다.")
        return self._get(
            "meta",
            {"method": "getMeta", "type": meta_type, "orgId": org_id, "tblId": tbl_id},
        )

    def get_data(
        self,
        org_id: str,
        tbl_id: str,
        itm_id: str,
        obj_codes: list[str],
        prd_se: str,
        start: str | None = None,
        end: str | None = None,
        recent: int | None = None,
    ) -> list[dict[str, Any]]:
        """통계 수치를 조회한다.

        obj_codes: 분류별 코드. [objL1, objL2, ...] 순서. 'ALL'이면 전체.
        prd_se   : 수록주기 코드 (Y=년, H=반기, Q=분기, M=월, D=일)
        기간은 start/end(예: '2020', '2024') 또는 recent(최근 N개 시점) 중 하나.

        주의: 한 번에 4만 셀까지만 받을 수 있으니 'ALL'을 남발하지 말 것.
        """
        if not (start and end) and not recent:
            raise ValueError("start/end 또는 recent 중 하나는 지정해야 합니다.")
        if len(obj_codes) > 8:
            raise ValueError("분류는 최대 8개(objL1~objL8)까지입니다.")

        params: dict[str, Any] = {
            "method": "getList",
            "orgId": org_id,
            "tblId": tbl_id,
            "itmId": itm_id,
            "prdSe": prd_se,
        }
        for i, code in enumerate(obj_codes, start=1):
            params[f"objL{i}"] = code
        if recent:
            params["newEstPrdCnt"] = recent
        else:
            params["startPrdDe"] = start
            params["endPrdDe"] = end
        return self._get("data", params)
