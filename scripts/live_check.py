"""실제 키로 각 도메인의 공공 API를 MCP(stdio)를 거쳐 한 번씩 호출해 본다.

사용법: KOSIS_API_KEY=... OPENDART_API_KEY=... DATA_GO_KR_SERVICE_KEY=... python scripts/live_check.py
      원격 서버 확인: python scripts/live_check.py --url https://.../mcp  (키는 서버 쪽에 있음)
질문은 모음집의 '작동 예시'와 같은 것을 쓴다 (원래 에이전트에서 실제로 성공한 경로).
"""
import argparse
import json
import os
import sys

import anyio
from mcp import Client, StdioServerParameters

CHECKS = [
    ("kosis", [
        ("kosis_search_tables", {"keyword": "주민등록인구", "limit": 3}),
    ]),
    ("dart", [
        ("dart_resolve_company", {"name": "카카오"}),
        ("dart_get_financials", {"corp_code": "{dart_resolve_company[0].corp_code}", "bsns_year": "2025"}),
    ]),
    ("air", [
        ("air_get_air_quality", {"station": "강남구"}),
        ("air_get_weather", {"nx": 61, "ny": 126}),
    ]),
    ("realty", [
        ("realty_get_transactions", {"lawd_cd": "11680", "start_ym": "202510", "end_ym": "202609",
                                     "umd_name": "대치동", "apt_name": "은마"}),
    ]),
]


def fill(args, results):
    # "{도구[0].필드}" 형태를 앞 단계 결과로 채운다
    out = {}
    for k, v in args.items():
        if isinstance(v, str) and v.startswith("{") and v.endswith("}"):
            tool, path = v[1:-1].split("[0].")
            v = results[tool][0][path]
        out[k] = v
    return out


async def main(url: str | None):
    target = url or StdioServerParameters(command=sys.executable, args=["-m", "korea_data_mcp.server"],
                                          env=dict(os.environ))
    ok = True
    async with Client(target) as c:
        names = {t.name for t in (await c.list_tools()).tools}
        for domain, steps in CHECKS:
            if not any(n.startswith(domain + "_") for n in names):
                print(f"- {domain}: 건너뜀 (키 없음)")
                continue
            results = {}
            for tool, args in steps:
                r = await c.call_tool(tool, fill(args, results))
                text = r.content[0].text
                if r.is_error or '"error"' in text[:50]:
                    ok = False
                    print(f"X {tool}: {text[:300]}")
                    break
                results[tool] = json.loads(text)
                print(f"O {tool}: {text[:200]}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", help="원격 서버의 /mcp 주소 (없으면 로컬 stdio)")
    sys.exit(0 if anyio.run(main, ap.parse_args().url) else 1)
