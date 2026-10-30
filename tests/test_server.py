"""오프라인 테스트: 네트워크 없이 도는 도구와 서버 배선만 검사한다.

공공 API를 실제로 부르는 확인은 scripts/live_check.py (키 필요).
"""
import json
import unittest

import anyio
from mcp import Client

from korea_data_mcp import server
from korea_data_mcp.registry import build_specs

ALL_KEYS = {"KOSIS_API_KEY": "x", "OPENDART_API_KEY": "x", "DATA_GO_KR_SERVICE_KEY": "x"}


def call(specs, name, args):
    async def go():
        async with Client(server.build_server(specs)) as c:
            return await c.call_tool(name, args)
    return anyio.run(go)


def text(result):
    return result.content[0].text


class TestRegistry(unittest.TestCase):
    def test_all_16_tools_with_prefix(self):
        specs = server.load_specs(ALL_KEYS)
        self.assertEqual(len(specs), 16)
        self.assertEqual({s.split("_")[0] for s in specs}, {"kosis", "dart", "air", "realty"})

    def test_schemas_unchanged_except_tool_names(self):
        # 원래 에이전트 스키마에서 바뀌는 건 같은 도메인 도구 이름 앞의 접두어뿐이어야 한다
        for d in server.domains():
            names = [t["name"] for t in d.schemas]

            def strip(text):
                for n in names:
                    text = text.replace(f"{d.prefix}_{n}", n)
                return text

            for orig, spec in zip(d.schemas, build_specs(d)):
                self.assertEqual(strip(spec.description), orig["description"])
                self.assertEqual(json.loads(strip(json.dumps(spec.input_schema, ensure_ascii=False))),
                                 orig["input_schema"])

    def test_description_refs_renamed(self):
        specs = server.load_specs(ALL_KEYS)
        self.assertIn("kosis_search_tables가 돌려준", specs["kosis_get_table_info"].input_schema
                      ["properties"]["org_id"]["description"])
        self.assertIn("realty_resolve_address", specs["realty_get_transactions"].input_schema
                      ["properties"]["lawd_cd"]["description"])

    def test_domains_without_keys_are_hidden(self):
        specs = server.load_specs({"OPENDART_API_KEY": "x"})
        self.assertEqual(sorted(specs), ["dart_compute_growth", "dart_get_financials", "dart_resolve_company"])
        self.assertEqual(server.load_specs({}), {})


class TestCalls(unittest.TestCase):
    specs = server.load_specs(ALL_KEYS)

    def test_list_tools_over_mcp(self):
        async def go():
            async with Client(server.build_server(self.specs)) as c:
                return await c.list_tools()
        tools = anyio.run(go).tools
        self.assertEqual(len(tools), 16)
        self.assertTrue(all(t.annotations.read_only_hint for t in tools))

    def test_kosis_calculate_change(self):
        r = call(self.specs, "kosis_calculate_change",
                 {"start_period": "2022", "start_value": 578529, "end_period": "2023", "end_value": 591022})
        self.assertFalse(r.is_error)
        out = json.loads(text(r))
        self.assertEqual(out["abs_change"], 12493)

    def test_dart_compute_growth(self):
        r = call(self.specs, "dart_compute_growth",
                 {"values": [{"year": "2023", "amount": 100}, {"year": "2024", "amount": 110}]})
        self.assertFalse(r.is_error)
        self.assertEqual(json.loads(text(r))[1]["yoy_growth_pct"], 10.0)

    def test_air_resolve_region_offline(self):
        out = json.loads(text(call(self.specs, "air_resolve_region", {"query": "강남"})))
        self.assertIn("강남구", json.dumps(out, ensure_ascii=False))

    def test_realty_resolve_address_uses_packaged_data(self):
        out = json.loads(text(call(self.specs, "realty_resolve_address", {"query": "대치동 은마아파트"})))
        self.assertIn("11680", json.dumps(out, ensure_ascii=False))

    def test_invalid_arguments_are_tool_errors(self):
        r = call(self.specs, "kosis_calculate_change", {"start_period": "2022"})
        self.assertTrue(r.is_error)
        self.assertIn("인자 오류", text(r))

    def test_unknown_tool(self):
        r = call(self.specs, "nope", {})
        self.assertTrue(r.is_error)


if __name__ == "__main__":
    unittest.main()
