"""도메인별 도구를 MCP 도구로 묶는 등록부.

각 도메인(kosis/dart/air/realty)은 원래 에이전트의 스키마와 함수를 그대로 쓰고,
여기서는 이름 앞에 도메인 접두어만 붙인다. 여러 MCP 서버가 같이 꽂힌 클라이언트에서
`get_data`, `today` 같은 일반적인 이름이 다른 서버 도구와 헷갈리지 않게 하기 위해서다.
설명문 안에서 같은 도메인의 다른 도구를 가리키는 이름도 함께 바꿔 일관성을 유지한다.
"""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Domain:
    prefix: str             # 도구 이름 접두어 (예: "kosis")
    title: str              # 사람용 이름
    env_keys: tuple[str, ...]  # 필요한 환경변수 (하나라도 없으면 비활성)
    key_help: str           # 키 발급처 안내
    schemas: list[dict[str, Any]]
    functions: dict[str, Callable[..., Any]]
    source: str             # 원본 에이전트 레포


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., Any] = field(repr=False)
    domain: str = ""


def _rename_refs(text: str, names: list[str], prefix: str) -> str:
    # 긴 이름부터 바꿔서 부분 일치(get_data vs get_data_x)를 피한다
    for n in sorted(names, key=len, reverse=True):
        text = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(n)}(?![A-Za-z0-9_])", f"{prefix}_{n}", text)
    return text


def build_specs(domain: Domain) -> list[ToolSpec]:
    names = [s["name"] for s in domain.schemas]
    missing = set(names) ^ set(domain.functions)
    if missing:
        raise ValueError(f"{domain.prefix}: 스키마와 함수가 맞지 않음 {sorted(missing)}")
    specs = []
    for s in domain.schemas:
        schema = copy.deepcopy(s["input_schema"])
        for prop in schema.get("properties", {}).values():
            if "description" in prop:
                prop["description"] = _rename_refs(prop["description"], names, domain.prefix)
        specs.append(ToolSpec(
            name=f"{domain.prefix}_{s['name']}",
            description=_rename_refs(s["description"], names, domain.prefix),
            input_schema=schema,
            fn=domain.functions[s["name"]],
            domain=domain.prefix,
        ))
    return specs


def enabled(domain: Domain, env: dict[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return all(env.get(k) for k in domain.env_keys)
