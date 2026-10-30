"""korea-data-mcp: 한국 공공데이터 4종을 MCP 도구로 제공하는 서버.

키가 있는 도메인만 도구 목록에 올린다. 쓸 수 없는 도구를 모델에게 보여주면
호출했다가 실패하는 왕복만 늘기 때문이다. 어떤 도메인이 켜졌는지는 stderr에 남긴다.
"""
from __future__ import annotations

import json
import sys
from typing import Any

import anyio
import jsonschema
import mcp_types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

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


def build_server(specs: dict[str, ToolSpec]) -> Server:
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
        return await anyio.to_thread.run_sync(run_tool, spec, params.arguments or {})

    return Server(
        "korea-data-mcp",
        version="0.1.0",
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        get_tool_input_schema=lambda name: specs[name].input_schema if name in specs else None,
    )


def main() -> None:
    specs = load_specs(log=lambda m: print(m, file=sys.stderr))
    if not specs:
        print("[korea-data-mcp] 켜진 도메인이 없습니다. README의 환경변수를 설정하세요.", file=sys.stderr)
    server = build_server(specs)

    async def _run():
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    anyio.run(_run)


if __name__ == "__main__":
    main()
