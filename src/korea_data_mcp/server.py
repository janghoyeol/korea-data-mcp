"""korea-data-mcp: 한국 공공데이터 4종을 MCP 도구로 제공하는 서버.

키가 있는 도메인만 도구 목록에 올린다. 쓸 수 없는 도구를 모델에게 보여주면
호출했다가 실패하는 왕복만 늘기 때문이다. 어떤 도메인이 켜졌는지는 stderr에 남긴다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import anyio
import jsonschema
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.server.transport_security import TransportSecuritySettings
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Route

from .guard import Guard, RateLimit, parse_limits
from .registry import Domain, ToolSpec, build_specs, enabled

INSTRUCTIONS = (
    "한국 공공데이터(KOSIS 국가통계, DART 전자공시, 에어코리아·기상청, 국토교통부 아파트 실거래가)를 "
    "조회하는 도구들이다. 모든 숫자는 도구 결과에 담긴 출처(통계표·접수번호·측정 시각·조회 기간)와 함께 "
    "인용하고, 증감률·평균 같은 계산은 암산하지 말고 계산 도구(*_calculate_change, dart_compute_growth, "
    "realty_compute_trend, air_recommend)를 쓴다. 도구가 값을 못 찾으면 지어내지 말고 모른다고 답한다."
)


def domains() -> list[Domain]:
    from .air import tools as air
    from .dart import adapter as dart, schemas as dart_schemas
    from .kosis import tools as kosis
    from .realty import tools as realty

    return [
        Domain("kosis", "KOSIS 국가통계", ("KOSIS_API_KEY",),
               "kosis.kr/openapi 에서 발급", kosis.TOOL_SCHEMAS, kosis.TOOL_FUNCTIONS,
               "janghoyeol/stat-agent"),
        Domain("dart", "DART 전자공시 재무제표", ("OPENDART_API_KEY",),
               "opendart.fss.or.kr 에서 발급", dart_schemas.TOOL_SCHEMAS, dart.TOOL_FUNCTIONS,
               "janghoyeol/corp-agent"),
        Domain("air", "에어코리아 대기질 + 기상청 관측 (서울 25개 자치구)", ("DATA_GO_KR_SERVICE_KEY",),
               "data.go.kr 에서 '에어코리아 대기오염정보'와 '기상청 단기예보' 활용신청",
               air.TOOL_SCHEMAS, air.TOOL_FUNCTIONS, "janghoyeol/air-agent"),
        Domain("realty", "국토교통부 아파트 매매 실거래가", ("DATA_GO_KR_SERVICE_KEY",),
               "data.go.kr 에서 '국토교통부 아파트 매매 실거래가 상세 자료' 활용신청",
               realty.TOOL_SCHEMAS, realty.TOOL_FUNCTIONS, "janghoyeol/realty-agent"),
    ]


def load_specs(env: dict[str, str] | None = None, log=None) -> dict[str, ToolSpec]:
    specs: dict[str, ToolSpec] = {}
    for d in domains():
        if enabled(d, env):
            for s in build_specs(d):
                specs[s.name] = s
            status = "on"
        else:
            status = f"off ({', '.join(d.env_keys)} 없음 — {d.key_help})"
        if log:
            log(f"[korea-data-mcp] {d.prefix}: {status}")
    return specs


def run_tool(spec: ToolSpec, arguments: dict[str, Any]) -> types.CallToolResult:
    """원래 에이전트의 _run_tool과 같은 규칙: 실패는 숨기지 않고 에러 결과로 돌려준다."""
    try:
        jsonschema.validate(arguments, spec.input_schema)
    except jsonschema.ValidationError as e:
        return _error(f"인자 오류: {e.message}")
    try:
        output = spec.fn(**arguments)
    except Exception as e:  # noqa: BLE001 — 모델이 실패를 보고 다른 방법을 시도할 수 있게
        return _error(f"에러: {e}")
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(output, ensure_ascii=False))])


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=True)


def build_server(specs: dict[str, ToolSpec], guard: Guard | None = None) -> Server:
    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(name=s.name, description=s.description, input_schema=s.input_schema,
                       annotations=types.ToolAnnotations(read_only_hint=True, open_world_hint=True))
            for s in specs.values()
        ])

    async def call_tool(ctx, params) -> types.CallToolResult:
        spec = specs.get(params.name)
        if spec is None:
            return _error(f"알 수 없는 도구: {params.name}")
        # 공공 API 호출은 동기(requests)라서 이벤트 루프를 막지 않게 스레드에서 실행
        run = guard.run if guard else run_tool
        return await anyio.to_thread.run_sync(run, spec, params.arguments or {})

    return Server(
        "korea-data-mcp",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        get_tool_input_schema=lambda name: specs[name].input_schema if name in specs else None,
    )


def build_http_app(specs: dict[str, ToolSpec], guard: Guard, rate_per_min: int, public_host: str | None):
    """원격 모드 (ADR 0002): stateless + JSON 응답. 도구가 전부 요청 하나로 끝나는 조회라
    세션 상태가 필요 없고, Render 무료 서버가 잠들었다 깨도 클라이언트 세션이 깨지지 않는다."""
    async def root(request):
        # 상태 확인(Render·showcase 깨우기)과 연결 안내를 겸한다
        mcp_url = str(request.url_for("mcp"))
        return JSONResponse({
            "name": "korea-data-mcp", "tools": len(specs), "mcp": mcp_url,
            "connect": f"claude mcp add --transport http korea-data {mcp_url}",
            "source": "https://github.com/janghoyeol/korea-data-mcp",
        })

    security = None
    if public_host:  # 외부 주소로 열 때는 SDK가 Host 검사를 자동으로 켜 주지 않는다
        security = TransportSecuritySettings(allowed_hosts=[public_host, "127.0.0.1:*", "localhost:*"],
                                             allowed_origins=[f"https://{public_host}"])
    app = build_server(specs, guard).streamable_http_app(
        stateless_http=True, json_response=True, host="0.0.0.0", transport_security=security,
        max_request_body_size=64 * 1024, custom_starlette_routes=[Route("/", root)])
    # streamable_http_app이 붙인 MCP 라우트에 이름을 줘서 url_for로 안내 주소를 만든다
    for r in app.routes:
        if getattr(r, "path", None) == "/mcp":
            r.name = "mcp"
    app.user_middleware.insert(0, Middleware(RateLimit, per_min=rate_per_min))
    return app


def main() -> None:
    ap = argparse.ArgumentParser(prog="korea-data-mcp")
    ap.add_argument("--http", action="store_true", help="원격 모드 (Streamable HTTP). 기본은 stdio")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    a = ap.parse_args()

    specs = load_specs(log=lambda m: print(m, file=sys.stderr))
    if not specs:
        print("[korea-data-mcp] 켜진 도메인이 없습니다. README의 환경변수를 설정하세요.", file=sys.stderr)

    if a.http:
        import uvicorn
        guard = Guard(parse_limits(os.environ.get("KOREA_DATA_MCP_DAILY_LIMITS")))
        app = build_http_app(specs, guard, int(os.environ.get("KOREA_DATA_MCP_RATE_PER_MIN", 60)),
                             os.environ.get("RENDER_EXTERNAL_HOSTNAME"))  # Render가 자동으로 넣어 주는 값
        print(f"[korea-data-mcp] http :{a.port}/mcp · 하루 상한 {guard.limits}", file=sys.stderr)
        uvicorn.run(app, host="0.0.0.0", port=a.port, proxy_headers=True, forwarded_allow_ips="*")
        return

    server = build_server(specs)

    async def _run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(_run)


if __name__ == "__main__":
    main()
