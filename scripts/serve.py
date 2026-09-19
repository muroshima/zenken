#!/usr/bin/env python3
"""監査結果を承認者の目線で見て、その場で判断を返す画面。

    uv run python scripts/serve.py        # http://localhost:8765

エージェントが人に渡した申請だけを並べる。承認者が最初に見るのは「見るべき数十件」で
あって、全188件ではない。

ここで返した判断は `runs/decisions.jsonl` に追記される。
README に書いた「人の判断が記録に戻る」の受け口がこれにあたる。

依存は標準ライブラリだけ。
"""

from __future__ import annotations

import argparse
import html
import json
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = ROOT / "runs" / "decisions.jsonl"

# 指摘をどこが出したか。コードが計算で出したものと、モデルが言っていることを見分けられるようにする。
SOURCE = {
    "rule": ("規程", "calc"),
    "duplicate_check": ("過去照合", "calc"),
    "outlier": ("分布", "calc"),
    "sanitizer": ("入力検査", "alert"),
    "model": ("モデル", "model"),
}

CODE_LABEL = {
    "over_limit": "規程の上限を超えている",
    "preapproval_required": "事前承認が必要な金額",
    "date_inconsistent": "利用日が申請日より後",
    "late_submission": "申請期限を過ぎている",
    "taxi_out_of_hours": "認められない時間帯のタクシー",
    "receipt_missing": "領収書がない",
    "attendees_missing": "参加人数の記載がない",
    "duplicate": "同じ支出が二度出ている",
    "vendor_alias_duplicate": "表記を変えた二重申請の疑い",
    "split_to_evade": "承認を避けるための分割の疑い",
    "amount_mismatch": "内容と金額が釣り合わない",
    "amount_outlier": "過去の分布から外れている",
    "prompt_injection": "領収書に指示文が埋め込まれている",
    "unusual_pattern": "説明のつかない点",
}

STYLE = """
:root{
  --bg:#fbfbfc; --card:#fff; --ink:#15181d; --dim:#666e7a; --faint:#8b93a0;
  --line:#e7e9ee; --line-soft:#f0f2f5;
  --alert:#c0392b; --alert-bg:#fdf3f2; --alert-line:#f3d5d1;
  --calc:#8a5a00; --calc-bg:#fdf8ec;
  --model:#5b6472; --model-bg:#f3f5f8;
  --ok:#1a7f52;
  --ease:cubic-bezier(.23,1,.32,1);
}
@media (prefers-color-scheme:dark){
  :root{
    --bg:#101215; --card:#171a1f; --ink:#e8eaed; --dim:#9aa2ae; --faint:#6e7783;
    --line:#252931; --line-soft:#1d2127;
    --alert:#ff8a7a; --alert-bg:#241816; --alert-line:#3d2723;
    --calc:#e0b15c; --calc-bg:#241f14;
    --model:#9aa2ae; --model-bg:#1e222a;
    --ok:#4ec08a;
  }
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:-apple-system,BlinkMacSystemFont,"Hiragino Sans","Noto Sans JP",sans-serif;
  font-size:15px;line-height:1.7;-webkit-font-smoothing:antialiased}
.wrap{max-width:820px;margin:0 auto;padding:40px 20px 120px}

header{margin-bottom:28px}
h1{font-size:19px;font-weight:650;margin:0 0 6px;letter-spacing:.01em}
.sub{color:var(--dim);font-size:13px;margin:0}

.bar{display:flex;flex-wrap:wrap;gap:20px 28px;padding:16px 0;margin-bottom:8px;
  border-top:1px solid var(--line);border-bottom:1px solid var(--line)}
.bar div{display:flex;align-items:baseline;gap:7px}
.bar .n{font-size:19px;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
.bar .n.warn{color:var(--calc)}
.bar .n.alert{color:var(--alert)}
.bar .l{font-size:12px;color:var(--dim)}

.filters{display:flex;gap:6px;margin:22px 0 14px;flex-wrap:wrap}
.chip{font:inherit;font-size:12.5px;padding:5px 13px;border-radius:99px;cursor:pointer;
  border:1px solid var(--line);background:var(--card);color:var(--dim);
  transition:transform 140ms var(--ease),border-color 140ms var(--ease),color 140ms var(--ease)}
.chip[aria-pressed="true"]{border-color:var(--ink);color:var(--ink)}
.chip:active{transform:scale(.97)}
@media (hover:hover) and (pointer:fine){ .chip:hover{border-color:var(--dim)} }

.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:18px 20px;margin-bottom:8px;
  transition:opacity 220ms var(--ease),transform 220ms var(--ease),border-color 160ms var(--ease)}
.card[data-attacked="1"]{border-color:var(--alert-line);background:var(--alert-bg)}
.card[hidden]{display:none}
.card.done{opacity:0;transform:translateY(-6px) scale(.985);pointer-events:none}

.top{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
.amt{font-size:21px;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.who{color:var(--dim);font-size:13px}
.eid{margin-left:auto;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  font-size:11.5px;color:var(--faint)}

.reasons{list-style:none;margin:14px 0 0;padding:0;display:flex;flex-direction:column;gap:2px}
.reasons li{display:flex;gap:10px;align-items:flex-start;padding:5px 0}
.src{flex:none;font-size:10.5px;font-weight:600;letter-spacing:.03em;padding:2px 7px;
  border-radius:4px;margin-top:3px;background:var(--model-bg);color:var(--model)}
.src.calc{background:var(--calc-bg);color:var(--calc)}
.src.alert{background:var(--alert-bg);color:var(--alert)}
.rtext b{font-weight:600}
.rtext span{color:var(--dim);font-size:13.5px}

.says{margin-top:12px;padding-left:12px;border-left:2px solid var(--line-soft);
  color:var(--dim);font-size:13.5px}

.acts{display:flex;align-items:center;gap:8px;margin-top:16px;
  padding-top:14px;border-top:1px solid var(--line-soft)}
.btn{font:inherit;font-size:13px;font-weight:550;padding:7px 15px;border-radius:8px;cursor:pointer;
  border:1px solid var(--line);background:transparent;color:var(--ink);
  transition:transform 140ms var(--ease),background-color 140ms var(--ease),border-color 140ms var(--ease)}
.btn:active{transform:scale(.97)}
.btn.primary{border-color:transparent;background:var(--ink);color:var(--card)}
@media (hover:hover) and (pointer:fine){
  .btn:hover{border-color:var(--dim)}
  .btn.primary:hover{opacity:.88}
}
.how{margin-left:auto;font-size:11.5px;color:var(--faint);font-variant-numeric:tabular-nums}

h2{font-size:13px;font-weight:600;color:var(--dim);margin:34px 0 12px;
  letter-spacing:.02em}
h2 em{font-style:normal;color:var(--faint);font-weight:400}

details{border-top:1px solid var(--line);padding-top:14px;margin-top:8px}
summary{cursor:pointer;font-size:13px;color:var(--dim);list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"▸ ";color:var(--faint)}
details[open] summary::before{content:"▾ "}
table{width:100%;border-collapse:collapse;margin-top:12px;font-size:12.5px}
td,th{padding:6px 10px 6px 0;border-bottom:1px solid var(--line-soft);text-align:left}
th{color:var(--faint);font-weight:500}
td.num{font-variant-numeric:tabular-nums}

.empty{padding:40px 0;text-align:center;color:var(--faint);font-size:13.5px}
.note{margin-top:36px;color:var(--faint);font-size:12px;line-height:1.8}
code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:11.5px}

@media (prefers-reduced-motion:reduce){
  *{transition-duration:0ms!important}
  .card.done{transform:none}
}
"""

SCRIPT = """
const $$ = s => Array.from(document.querySelectorAll(s));

// 絞り込み
$$('.chip').forEach(chip => chip.addEventListener('click', () => {
  $$('.chip').forEach(c => c.setAttribute('aria-pressed', String(c === chip)));
  const f = chip.dataset.filter;
  let shown = 0;
  $$('.card').forEach(card => {
    const hit = f === 'all'
      || (f === 'attacked' && card.dataset.attacked === '1')
      || (f === 'calc' && card.dataset.calc === '1')
      || (f === 'model' && card.dataset.calc === '0');
    card.hidden = !hit;
    if (hit) shown++;
  });
  document.getElementById('empty').hidden = shown > 0;
}));

// 判断を返す
async function decide(card, decision) {
  const id = card.dataset.id;
  card.classList.add('done');
  try {
    await fetch('/decide', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({expense_id: id, decision})
    });
  } catch (e) { /* ローカル用途なので握りつぶす */ }
  setTimeout(() => {
    card.hidden = true;
    const left = $$('.card').filter(c => !c.hidden && !c.classList.contains('done')).length;
    document.getElementById('remain').textContent = left;
    document.getElementById('empty').hidden = left > 0;
  }, 220);
}

$$('[data-act]').forEach(btn => btn.addEventListener('click', e => {
  decide(e.target.closest('.card'), btn.dataset.act);
}));
"""


def load() -> tuple[list[dict], dict[str, dict]]:
    rows = [
        json.loads(line)
        for line in (ROOT / "runs" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    expenses = {
        e["id"]: e
        for e in json.loads((ROOT / "data" / "expenses.json").read_text(encoding="utf-8"))
    }
    return rows, expenses


def render_card(row: dict, exp: dict) -> str:
    esc = html.escape
    codes = {f["code"] for f in row["findings"]}
    attacked = "prompt_injection" in codes
    has_calc = any(f.get("source") in ("rule", "duplicate_check", "outlier") for f in row["findings"])

    reasons = []
    # 計算で確定したものを先に出す。モデルの推測より重い情報なので。
    order = {"sanitizer": 0, "rule": 1, "duplicate_check": 2, "outlier": 3, "model": 4}
    for f in sorted(row["findings"], key=lambda f: order.get(f.get("source", ""), 9)):
        label, kind = SOURCE.get(f.get("source", ""), ("—", "model"))
        reasons.append(
            f'<li><span class="src {kind}">{esc(label)}</span>'
            f'<span class="rtext"><b>{esc(CODE_LABEL.get(f["code"], f["code"]))}</b><br>'
            f'<span>{esc(f.get("detail", ""))}</span></span></li>'
        )

    says = ""
    if row.get("error"):
        says = (
            '<p class="says">モデルの判定が失敗したため、自動では判断できていません。</p>'
        )
    elif row.get("assessment"):
        says = f'<p class="says">{esc(row["assessment"])}</p>'

    tier = {"none": "計算のみ", "cheap": "安いモデル", "strong": "上位モデル"}.get(row.get("tier", ""), "")
    model = f" · {esc(row['model'])}" if row.get("model") else ""

    return f"""
<div class="card" data-id="{esc(row["expense_id"])}" data-attacked="{1 if attacked else 0}"
     data-calc="{1 if has_calc else 0}">
  <div class="top">
    <span class="amt">{int(exp["amount"]):,}<span style="font-size:13px;font-weight:400">円</span></span>
    <span class="who">{esc(exp["category"])} · {esc(exp["vendor"])} ·
      {esc(exp["employee_name"])}（{esc(exp["department"])}）· 利用 {esc(exp["used_at"][:10])}</span>
    <span class="eid">{esc(row["expense_id"])}</span>
  </div>
  <ul class="reasons">{"".join(reasons)}</ul>
  {says}
  <div class="acts">
    <button class="btn primary" data-act="reject">差し戻す</button>
    <button class="btn" data-act="approve">問題なし</button>
    <span class="how">{esc(tier)}{model} · 確信度 {row.get("confidence", 0):.2f}</span>
  </div>
</div>"""


def render_page() -> str:
    rows, expenses = load()
    esc_list = [r for r in rows if r["decision"] == "escalate"]
    approved = [r for r in rows if r["decision"] == "auto_approve"]
    no_call = [r for r in rows if r.get("tier") == "none"]
    attacked = [r for r in esc_list if any(f["code"] == "prompt_injection" for f in r["findings"])]

    # 攻撃を受けたものを先頭に。次は金額の大きい順（承認者が見たい順）。
    esc_list.sort(
        key=lambda r: (
            0 if any(f["code"] == "prompt_injection" for f in r["findings"]) else 1,
            -int(expenses[r["expense_id"]]["amount"]),
        )
    )
    cards = "".join(render_card(r, expenses[r["expense_id"]]) for r in esc_list)
    total = sum(int(expenses[r["expense_id"]]["amount"]) for r in esc_list)

    approved_rows = "".join(
        f'<tr><td>{html.escape(r["expense_id"])}</td>'
        f'<td class="num">{int(expenses[r["expense_id"]]["amount"]):,}円</td>'
        f'<td>{html.escape(expenses[r["expense_id"]]["category"])}</td>'
        f'<td>{"計算のみ" if r["tier"] == "none" else html.escape(r.get("model") or "")}</td></tr>'
        for r in sorted(approved, key=lambda r: -int(expenses[r["expense_id"]]["amount"]))[:50]
    )

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>確認待ちの経費申請 — zenken</title><style>{STYLE}</style></head>
<body><div class="wrap">

<header>
  <h1>確認待ちの経費申請</h1>
  <p class="sub">{len(rows)} 件を監査して、説明のつかなかったものだけ残しました。{datetime.now():%Y-%m-%d %H:%M} 時点</p>
</header>

<div class="bar">
  <div><span class="n warn" id="remain">{len(esc_list)}</span><span class="l">確認する</span></div>
  <div><span class="n">{len(approved)}</span><span class="l">自動で承認した</span></div>
  <div><span class="n">{len(no_call)}</span><span class="l">モデルを呼ばずに判定</span></div>
  <div><span class="n alert">{len(attacked)}</span><span class="l">指示文の埋め込み</span></div>
  <div><span class="n">{total:,}</span><span class="l">円（確認対象の合計）</span></div>
</div>

<div class="filters">
  <button class="chip" data-filter="all" aria-pressed="true">すべて</button>
  <button class="chip" data-filter="attacked" aria-pressed="false">攻撃を検出</button>
  <button class="chip" data-filter="calc" aria-pressed="false">計算で確定</button>
  <button class="chip" data-filter="model" aria-pressed="false">モデルの指摘のみ</button>
</div>

{cards}
<p class="empty" id="empty" hidden>確認するものはありません。</p>

<h2>自動で承認した申請 <em>{len(approved)} 件</em></h2>
<details>
  <summary>内訳を見る（金額の大きい順に50件）</summary>
  <table>
    <tr><th>申請</th><th>金額</th><th>科目</th><th>判定に使ったもの</th></tr>
    {approved_rows}
  </table>
</details>

<p class="note">
  <b>計算で確定</b>（規程・過去照合・分布）はコードが出した指摘で、モデルの出力では取り消せません。<br>
  <b>モデル</b> は計算では決まらない判断で、根拠を書けたものだけ載せています。<br>
  ここで返した判断は <code>runs/decisions.jsonl</code> に追記されます。<br>
  表示しているのは <code>src/zenken/dummy.py</code> で生成した架空の申請です。実在の企業・個人のデータは使っていません。
</p>

</div><script>{SCRIPT}</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        body = render_page().encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        if self.path != "/decide":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self.send_error(400)
            return

        record = {
            "expense_id": payload.get("expense_id"),
            "decision": payload.get("decision"),
            "decided_at": datetime.now().isoformat(timespec="seconds"),
            "by": "human",
        }
        DECISIONS.parent.mkdir(parents=True, exist_ok=True)
        with DECISIONS.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(f"  {record['expense_id']} → {record['decision']}", flush=True)

        body = b'{"ok":true}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()

    if not (ROOT / "runs" / "audit.jsonl").exists():
        print("runs/audit.jsonl がありません。先に run_audit.py を実行してください。")
        return 1

    url = f"http://localhost:{args.port}"
    print(f"{url}（Ctrl+C で終了）")
    print(f"返した判断は {DECISIONS.relative_to(ROOT)} に追記されます")
    if not args.no_open:
        webbrowser.open(url)
    try:
        HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    except KeyboardInterrupt:
        print("\n終了しました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
