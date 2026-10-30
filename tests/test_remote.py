"""원격(Streamable HTTP) 모드 오프라인 테스트: 공용 키 보호층(guard)과 HTTP 배선.

네트워크 도구는 가짜 함수로 바꿔 끼워 공공 API를 부르지 않는다.
"""
import json
import socket
import threading
import time
import unittest
import urllib.error
import urllib.request

import anyio
import uvicorn
from mcp import Client

from korea_data_mcp import server
from korea_data_mcp.guard import Guard, NETWORK_TOOLS, RateLimit, parse_limits
from korea_data_mcp.registry import ToolSpec

ALL_KEYS = {"KOSIS_API_KEY": "x", "OPENDART_API_KEY": "x", "DATA_GO_KR_SERVICE_KEY": "x"}


class FakeClock:
    def __init__(self, t=1_800_000_000.0):  # 2027-01-15 KST 근처, 하루 경계와 무관한 시각
        self.t = t

    def __call__(self):
        return self.t


def counting_spec(name="dart_get_financials", fail=False):
    calls = []

    def fn(**kw):
        calls.append(kw)
        if fail:
            raise RuntimeError("API 응답 없음")
        return {"n": len(calls)}

    return ToolSpec(name, "", {"type": "object"}, fn, name.split("_")[0]), calls


def text(result):
    return result.content[0].text


class TestGuard(unittest.TestCase):
    def test_network_tools_are_real_tool_names(self):
        self.assertLessEqual(NETWORK_TOOLS, set(server.load_specs(ALL_KEYS)))

    def test_same_call_is_served_from_cache(self):
        spec, calls = counting_spec()
        g = Guard({"dart": 10}, clock=FakeClock())
        a = g.run(spec, {"corp_code": "1", "bsns_year": "2025"})
        b = g.run(spec, {"bsns_year": "2025", "corp_code": "1"})  # 인자 순서만 다름
        self.assertEqual(text(a), text(b))
        self.assertEqual(len(calls), 1)
        self.assertEqual(g.used("dart"), 1)

    def test_cache_expires_by_domain_ttl(self):
        clock = FakeClock()
        spec, calls = counting_spec("air_get_air_quality")
        g = Guard({"air": 10}, clock=clock)
        g.run(spec, {"station": "강남구"})
        clock.t += 601  # 대기질은 10분
        g.run(spec, {"station": "강남구"})
        self.assertEqual(len(calls), 2)

    def test_daily_limit_blocks_with_tool_error(self):
        spec, calls = counting_spec()
        g = Guard({"dart": 2}, clock=FakeClock())
        for year in ("2023", "2024"):
            self.assertFalse(g.run(spec, {"bsns_year": year}).is_error)
        r = g.run(spec, {"bsns_year": "2025"})
        self.assertTrue(r.is_error)
        self.assertIn("공용 한도", text(r))
        self.assertEqual(len(calls), 2)
        # 이미 캐시된 질문은 한도가 차도 답한다
        self.assertFalse(g.run(spec, {"bsns_year": "2023"}).is_error)

    def test_limit_resets_on_next_kst_day(self):
        clock = FakeClock()
        spec, _ = counting_spec()
        g = Guard({"dart": 1}, clock=clock)
        g.run(spec, {"bsns_year": "2023"})
        self.assertTrue(g.run(spec, {"bsns_year": "2024"}).is_error)
        clock.t += 86_400
        self.assertFalse(g.run(spec, {"bsns_year": "2024"}).is_error)

    def test_errors_are_counted_but_not_cached(self):
        spec, calls = counting_spec(fail=True)
        g = Guard({"dart": 10}, clock=FakeClock())
        self.assertTrue(g.run(spec, {"bsns_year": "2025"}).is_error)
        self.assertTrue(g.run(spec, {"bsns_year": "2025"}).is_error)
        self.assertEqual(len(calls), 2)
        self.assertEqual(g.used("dart"), 2)

    def test_local_tools_bypass_guard(self):
        spec, calls = counting_spec("dart_compute_growth")
        g = Guard({"dart": 0}, clock=FakeClock())
        self.assertFalse(g.run(spec, {}).is_error)
        self.assertFalse(g.run(spec, {}).is_error)
        self.assertEqual(len(calls), 2)

    def test_parse_limits(self):
        self.assertEqual(parse_limits("dart=50, air=7")["dart"], 50)
        self.assertEqual(parse_limits("dart=50, air=7")["air"], 7)
        self.assertEqual(parse_limits("")["kosis"], parse_limits(None)["kosis"])
        with self.assertRaises(ValueError):
            parse_limits("dart")


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class TestHttp(unittest.TestCase):
    """실제 uvicorn을 띄우고 mcp Client가 URL로 붙는다 (원격 클라이언트와 같은 경로)."""

    @classmethod
    def setUpClass(cls):
        specs = server.load_specs(ALL_KEYS)
        cls.port = free_port()
        app = server.build_http_app(specs, Guard(parse_limits(None)), rate_per_min=20,
                                    public_host=None)
        cls.srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=cls.port, log_level="warning"))
        cls.thread = threading.Thread(target=cls.srv.run, daemon=True)
        cls.thread.start()
        for _ in range(100):
            if cls.srv.started:
                break
            time.sleep(0.05)
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.srv.should_exit = True
        cls.thread.join(5)

    def test_tools_over_streamable_http(self):
        async def go():
            async with Client(self.base + "/mcp") as c:
                tools = (await c.list_tools()).tools
                r = await c.call_tool("dart_compute_growth",
                                      {"values": [{"year": "2023", "amount": 100}, {"year": "2024", "amount": 110}]})
                return tools, r
        tools, r = anyio.run(go)
        self.assertEqual(len(tools), 16)
        self.assertEqual(json.loads(text(r))[1]["yoy_growth_pct"], 10.0)

    def test_root_is_health_and_usage(self):
        with urllib.request.urlopen(self.base + "/") as resp:
            body = json.loads(resp.read())
        self.assertEqual(body["tools"], 16)
        self.assertTrue(body["mcp"].endswith("/mcp"))


class TestRateLimit(unittest.TestCase):
    def test_per_ip_window(self):
        clock = FakeClock()
        rl = RateLimit(app=None, per_min=2, clock=clock)
        self.assertTrue(rl.allow("1.1.1.1"))
        self.assertTrue(rl.allow("1.1.1.1"))
        self.assertFalse(rl.allow("1.1.1.1"))
        self.assertTrue(rl.allow("2.2.2.2"))
        clock.t += 61
        self.assertTrue(rl.allow("1.1.1.1"))

    def test_client_ip_prefers_forwarded_for(self):
        scope = {"headers": [(b"x-forwarded-for", b"9.9.9.9, 10.0.0.1")], "client": ("10.0.0.2", 1)}
        self.assertEqual(RateLimit.client_ip(scope), "9.9.9.9")
        self.assertEqual(RateLimit.client_ip({"headers": [], "client": ("10.0.0.2", 1)}), "10.0.0.2")

    def test_http_429_after_limit(self):
        port = free_port()
        app = server.build_http_app(server.load_specs(ALL_KEYS), Guard(parse_limits(None)), rate_per_min=2,
                                    public_host=None)
        srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
        t = threading.Thread(target=srv.run, daemon=True)
        t.start()
        while not srv.started:
            time.sleep(0.05)
        try:
            codes = []
            for _ in range(3):
                req = urllib.request.Request(f"http://127.0.0.1:{port}/mcp", data=b"{}", method="POST",
                                             headers={"content-type": "application/json",
                                                      "accept": "application/json, text/event-stream"})
                try:
                    with urllib.request.urlopen(req) as r:
                        codes.append(r.status)
                except urllib.error.HTTPError as e:
                    codes.append(e.code)
            self.assertNotEqual(codes[0], 429)
            self.assertEqual(codes[2], 429)
            # 상태 확인(GET /)은 제한 대상이 아니다
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/") as r:
                self.assertEqual(r.status, 200)
        finally:
            srv.should_exit = True
            t.join(5)


if __name__ == "__main__":
    unittest.main()
