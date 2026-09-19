#!/usr/bin/env python3
"""経費申請を全件監査する。

    uv run python scripts/run_audit.py                 # 全件（途中まで終わっていれば続きから）
    uv run python scripts/run_audit.py --limit 20      # 先頭20件だけ
    uv run python scripts/run_audit.py --fresh         # 最初からやり直す

モデルは既定で無料枠に流れる。有料モデルを使うときだけ環境変数で指す。

    ZENKEN_MODEL_CHEAP=... ZENKEN_MODEL_STRONG=... uv run python scripts/run_audit.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from zenken.agent import MODEL_CHEAP, MODEL_STRONG, AuditAgent, Journal, Verdict  # noqa: E402
from zenken.llm import LLM, Budget  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=0, help="先頭 N 件だけ処理する")
    parser.add_argument("--fresh", action="store_true", help="ジャーナルを捨てて最初から")
    parser.add_argument("--no-cache", action="store_true", help="キャッシュを使わない")
    parser.add_argument("--journal", default="runs/audit.jsonl")
    parser.add_argument("--expenses", default="data/expenses.json")
    parser.add_argument("--max-calls", type=int, default=400)
    args = parser.parse_args()

    expenses = json.loads((ROOT / args.expenses).read_text(encoding="utf-8"))
    if args.limit:
        expenses = expenses[: args.limit]

    journal_path = ROOT / args.journal
    if args.fresh and journal_path.exists():
        journal_path.unlink()
    journal = Journal(journal_path)

    llm = LLM(
        budget=Budget(max_calls=args.max_calls, max_tokens=4_000_000),
        use_cache=not args.no_cache,
        cache_dir=ROOT / ".cache" / "llm",
    )
    agent = AuditAgent(llm)

    print(f"対象 {len(expenses)} 件 / 安いモデル: {MODEL_CHEAP} / 上位モデル: {MODEL_STRONG}")
    already = len(journal.processed_ids())
    if already and not args.fresh:
        print(f"すでに {already} 件は処理済みなので飛ばします")

    def progress(i: int, total: int, v: Verdict) -> None:
        mark = "→人" if v.decision == "escalate" else "承認"
        codes = ",".join(sorted({f["code"] for f in v.findings})) or "-"
        print(f"[{i:4}/{total}] {v.expense_id} {mark} tier={v.tier:6} {codes}")

    try:
        agent.audit_all(expenses, journal, resume=not args.fresh, on_progress=progress)
    except KeyboardInterrupt:
        print("\n中断しました。同じコマンドで続きから再開できます。")
        return 130

    rows = journal.read_all()
    escalated = [r for r in rows if r["decision"] == "escalate"]
    by_tier: dict[str, int] = {}
    for r in rows:
        by_tier[r["tier"]] = by_tier.get(r["tier"], 0) + 1

    print("\n" + "=" * 68)
    print(f"監査した申請      {len(rows)} 件")
    print(f"自動で承認        {len(rows) - len(escalated)} 件")
    print(f"人に渡した        {len(escalated)} 件")
    print()
    print("モデルをどこまで使ったか")
    for tier, label in (("none", "呼ばずに済んだ"), ("cheap", "安いモデル"), ("strong", "上位モデル")):
        n = by_tier.get(tier, 0)
        share = n / len(rows) * 100 if rows else 0
        print(f"  {label:16} {n:4} 件 ({share:5.1f}%)")
    print()
    print(llm.ledger.summary())

    errors = [r for r in rows if r.get("error")]
    if errors:
        print(f"\nモデル呼び出しに失敗した申請 {len(errors)} 件（すべて人に渡しています）")
        for r in errors[:5]:
            print(f"  {r['expense_id']}: {r['error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
