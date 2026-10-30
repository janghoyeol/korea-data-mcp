"""Claude가 이 MCP 서버의 도구를 받아 실제로 골라 쓰는지 확인한다 (MCP stdio 경유).

사용법: python scripts/claude_check.py "질문" [--model claude-haiku-5-5]
키: ANTHROPIC_API_KEY (또는 TREND_ANTHROPIC_API_KEY) + 쓰려는 도메인의 공공 API 키
"""
import argparse
import os
import sys

import anthropic
import anyio
from mcp import Client, StdioServerParameters

PRICES = {"claude-haiku-5-5": (0.10, 0.50), "claude-sonnet-5-5": (2.0, 10.0)}  # $/1M (input, output)


async def main(question: str, model: str) -> None:
    key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("TREND_ANTHROPIC_API_KEY")
    claude = anthropic.Anthropic(api_key=key)
    params = StdioServerParameters(command=sys.executable, args=["-m", "korea_data_mcp.server"],
                                   env=dict(os.environ))
    async with Client(params) as mcp:
        listed = (await mcp.list_tools()).tools
        tools = [{"name": t.name, "description": t.description, "input_schema": t.input_schema} for t in listed]
        messages = [{"role": "user", "content": question}]
        tokens_in = tokens_out = 0
        for _ in range(8):
            resp = claude.messages.create(model=model, max_tokens=4000, tools=tools, messages=messages)
            tokens_in += resp.usage.input_tokens
            tokens_out += resp.usage.output_tokens
            messages.append({"role": "assistant", "content": resp.content})
            if resp.stop_reason != "tool_use":
                break
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                r = await mcp.call_tool(block.name, block.input)
                print(f"  [tool] {block.name}({block.input}) -> {r.content[0].text[:150]}")
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": r.content[0].text, "is_error": bool(r.is_error)})
            messages.append({"role": "user", "content": results})
        print("\n" + "".join(b.text for b in resp.content if b.type == "text"))
        p_in, p_out = PRICES.get(model, (0, 0))
        print(f"\n도구 {len(tools)}개 · 토큰 {tokens_in}+{tokens_out} · ${(tokens_in * p_in + tokens_out * p_out) / 1e6:.4f}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question")
    ap.add_argument("--model", default="claude-haiku-5-5")
    a = ap.parse_args()
    anyio.run(main, a.question, a.model)
