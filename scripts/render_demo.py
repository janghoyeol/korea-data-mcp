"""claude_check.py --save 로 남긴 대화 기록을 '작동 예시' 화면(HTML)으로 그린다.

MCP 서버는 웹 화면이 없어서, showcase 카드의 작동 예시는 실제 Claude ↔ MCP 대화 기록을 그린 화면을 쓴다.
사용법: python scripts/render_demo.py docs/demo/kakao.json out.html [--font Pretendard.woff2]
캡처는 Playwright로 (폭 880px, 전체 페이지).
"""
import argparse
import html
import json
import re

DOMAIN_COLOR = {"kosis": "#2f6fdb", "dart": "#0f7a5c", "air": "#0e8aa8", "realty": "#c2621b"}


def won(amount: int) -> str:
    """8099147815086 → '8조 991억 원' (억 단위 반올림)."""
    sign = "-" if amount < 0 else ""
    eok = round(abs(amount) / 1e8)
    jo, rest = divmod(eok, 10_000)
    parts = ([f"{jo}조"] if jo else []) + ([f"{rest:,}억"] if rest else [])
    return sign + " ".join(parts or ["0"]) + " 원"


def summarize(name: str, result: str) -> str:
    try:
        r = json.loads(result)
    except ValueError:
        return result[:90]
    if name == "dart_resolve_company":
        c = r[0]
        return f"{c['corp_name']} → corp_code {c['corp_code']} (종목 {c['stock_code']}) · 후보 {len(r)}개"
    if name == "realty_today":
        return f"오늘 {r['today']}"
    if name == "dart_get_financials":
        sales = r["accounts"]["매출액"]["amount"]
        return f"{r['reprt_name']}({r['fs_div_name']}) · 매출 {won(sales)} · 접수번호 {r['rcept_no']}"
    if name == "dart_compute_growth":
        return " · ".join(f"{v['year']} {v['yoy_growth_pct']:+.2f}%" for v in r if v["yoy_growth_pct"] is not None)
    return result[:90]


def args_text(name: str, args: dict) -> str:
    if name == "dart_compute_growth":
        return f"values=[{len(args['values'])}개 연도 매출]"
    return ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in args.items())


def inline(text: str) -> str:
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html.escape(text))


def markdown(md: str) -> str:
    """답변에 나오는 만큼만: 문단, 목록, 표, 굵게."""
    out, lines, i = [], md.strip().splitlines(), 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("|"):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                    rows.append(cells)
                i += 1
            head = "".join(f"<th>{inline(c)}</th>" for c in rows[0])
            body = "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in rows[1:])
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
            continue
        if line.startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(f"<li>{inline(lines[i].strip()[2:])}</li>")
                i += 1
            out.append(f"<ul>{''.join(items)}</ul>")
            continue
        if line:
            out.append(f"<p>{inline(line.lstrip('#').strip())}</p>")
        i += 1
    return "\n".join(out)


def render(d: dict, font: str | None) -> str:
    tools = [e for e in d["log"] if e["type"] == "tool"]
    answer = "\n\n".join(e["text"] for e in d["log"] if e["type"] == "text")
    rows = []
    for n, t in enumerate(tools, 1):
        prefix = t["name"].split("_")[0]
        rows.append(f"""
      <li style="--c:{DOMAIN_COLOR.get(prefix, '#555')}">
        <span class="n">{n}</span>
        <div><code class="name">{html.escape(t['name'])}</code><code class="args">({html.escape(args_text(t['name'], t['input']))})</code>
        <p class="res">→ {html.escape(summarize(t['name'], t['result']))}</p></div>
      </li>""")
    font_face = f"@font-face {{ font-family: Pretendard; src: url('{font}') format('woff2'); font-weight: 100 900; }}" if font else ""
    tin, tout = d["tokens"]
    return f"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><title>korea-data-mcp 작동 예시</title>
<style>
{font_face}
:root {{ --bg:#f5f7fb; --card:#fff; --line:#e3e8f0; --text:#1b2333; --muted:#6b7588; --accent:#0f7a5c; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--text); font-family:Pretendard, "Noto Sans KR", sans-serif; width:880px; }}
.wrap {{ padding:28px 20px 24px; }}
header {{ text-align:center; margin-bottom:22px; }}
.pill {{ display:inline-block; font-size:13px; font-weight:600; color:var(--accent); border:1px solid #9fd3c2; background:#e8f6f1; padding:3px 12px; border-radius:99px; }}
h1 {{ font-size:30px; margin:12px 0 6px; letter-spacing:-0.02em; }}
h1 span {{ color:var(--accent); }}
.sub {{ color:var(--muted); margin:0; font-size:15px; line-height:1.6; }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:14px; box-shadow:0 1px 3px rgba(20,30,60,.06); margin-top:14px; }}
.label {{ font-size:12.5px; font-weight:700; color:var(--muted); letter-spacing:.02em; margin:0 0 10px; }}
.q {{ display:flex; justify-content:flex-end; }}
.q p {{ margin:0; background:var(--accent); color:#fff; font-size:17px; padding:11px 18px; border-radius:16px 16px 4px 16px; }}
.tools {{ padding:16px 20px 8px; }}
.tools ol {{ list-style:none; margin:0; padding:0; }}
.tools li {{ display:flex; gap:12px; padding:9px 0; border-top:1px solid var(--line); }}
.tools li:first-child {{ border-top:0; }}
.n {{ flex:none; width:22px; height:22px; border-radius:50%; background:var(--c); color:#fff; font-size:12px; font-weight:700; display:grid; place-items:center; margin-top:1px; }}
code {{ font-family:"DejaVu Sans Mono", monospace; font-size:13px; }}
.name {{ color:var(--c); font-weight:700; }}
.args {{ color:var(--muted); }}
.res {{ margin:3px 0 0; font-size:14px; }}
.answer {{ padding:18px 22px 8px; font-size:15.5px; line-height:1.7; }}
.answer p {{ margin:0 0 12px; }}
.answer ul {{ margin:0 0 12px; padding-left:20px; }}
table {{ border-collapse:collapse; width:100%; margin:4px 0 14px; font-size:14.5px; }}
th, td {{ border:1px solid var(--line); padding:7px 10px; text-align:right; }}
th:first-child, td:first-child {{ text-align:left; }}
th {{ background:#f0f4f9; font-weight:700; }}
footer {{ display:flex; flex-wrap:wrap; gap:6px 16px; justify-content:center; color:var(--muted); font-size:13px; margin-top:16px; }}
</style></head>
<body><div class="wrap">
  <header>
    <span class="pill">MCP 서버 · Claude가 직접 도구를 고름</span>
    <h1><span>korea-data-mcp</span> 작동 예시</h1>
    <p class="sub">Claude에 이 MCP 서버만 연결하고 질문했을 때의 실제 대화 기록.<br>
    공개된 도구 {d['tools_listed']}개(통계·공시·대기질·실거래가) 중 Claude가 필요한 것만 골라 {len(tools)}번 호출했다.</p>
  </header>
  <div class="q"><p>{html.escape(d['question'])}</p></div>
  <section class="card tools">
    <p class="label">MCP 도구 호출 {len(tools)}회</p>
    <ol>{''.join(rows)}
    </ol>
  </section>
  <section class="card answer">
    <p class="label">Claude 답변</p>
    {markdown(answer)}
  </section>
  <footer>
    <span>{html.escape(d['model'])}</span><span>토큰 {tin:,} + {tout:,}</span><span>${d['cost_usd']:.4f}</span>
    <span>{d['date']} 실제 실행 · MCP stdio</span>
  </footer>
</div></body></html>"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript")
    ap.add_argument("out")
    ap.add_argument("--font", help="Pretendard woff2 경로 (없으면 시스템 글꼴)")
    a = ap.parse_args()
    with open(a.transcript, encoding="utf-8") as f:
        data = json.load(f)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(render(data, a.font))
