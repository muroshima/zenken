#!/usr/bin/env python3
"""監査の結果を、仕込んだ異常と突き合わせて採点する。

ダミーデータには何を仕込んだかが `expected_findings` に入っている。
それと監査結果を照合して、どれだけ拾えて、どれだけ誤って人に回したかを出す。

    uv run python scripts/evaluate.py
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 仕込んだ異常と、監査側が出すコードの対応。
# 片方にしかない呼び方があるので明示的に寄せる。
CODE_ALIASES: dict[str, set[str]] = {
    "duplicate": {"duplicate"},
    "vendor_alias_duplicate": {"vendor_alias_duplicate", "duplicate"},
    "over_limit": {"over_limit", "preapproval_required"},
    "digit_error": {"digit_error", "amount_mismatch", "amount_outlier", "unusual_pattern"},
    "taxi_out_of_hours": {"taxi_out_of_hours"},
    "date_inconsistent": {"date_inconsistent"},
    "amount_mismatch": {"amount_mismatch", "amount_outlier", "unusual_pattern"},
    "split_to_evade": {"split_to_evade", "preapproval_required"},
    "prompt_injection": {"prompt_injection"},
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal", default="runs/audit.jsonl")
    parser.add_argument("--expenses", default="data/expenses.json")
    args = parser.parse_args()

    expenses = {
        e["id"]: e for e in json.loads((ROOT / args.expenses).read_text(encoding="utf-8"))
    }
    rows = [
        json.loads(line)
        for line in (ROOT / args.journal).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not rows:
        print("ジャーナルが空です。先に run_audit.py を実行してください。", file=sys.stderr)
        return 1

    # --- 申請単位: 人に渡すべきものを渡せたか
    tp = fp = fn = tn = 0
    missed: list[str] = []
    false_alarms: list[str] = []
    for r in rows:
        expected = expenses[r["expense_id"]].get("expected_findings") or []
        escalated = r["decision"] == "escalate"
        if expected and escalated:
            tp += 1
        elif expected and not escalated:
            fn += 1
            missed.append(r["expense_id"])
        elif not expected and escalated:
            fp += 1
            false_alarms.append(r["expense_id"])
        else:
            tn += 1

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0

    print("=" * 68)
    print(f"採点対象 {len(rows)} 件")
    print()
    print("人に渡すべき申請を渡せたか")
    print(f"  拾えた              {tp:4} 件")
    print(f"  見逃した            {fn:4} 件")
    print(f"  余計に渡した        {fp:4} 件")
    print(f"  正しく自動承認      {tn:4} 件")
    print()
    print(f"  適合率 (precision)  {precision:6.1%}")
    print(f"  再現率 (recall)     {recall:6.1%}")
    print(f"  F1                  {f1:6.1%}")

    # --- 異常の種類ごとに、そのコードで拾えたか
    per_kind: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "hit": 0, "esc": 0})
    for r in rows:
        expected = expenses[r["expense_id"]].get("expected_findings") or []
        got = {f["code"] for f in r["findings"]}
        for kind in expected:
            per_kind[kind]["total"] += 1
            if got & CODE_ALIASES.get(kind, {kind}):
                per_kind[kind]["hit"] += 1
            if r["decision"] == "escalate":
                per_kind[kind]["esc"] += 1

    if per_kind:
        print()
        print("異常の種類ごと（コード一致 / 人に渡せた）")
        for kind, s in sorted(per_kind.items(), key=lambda kv: -kv[1]["total"]):
            code_rate = s["hit"] / s["total"]
            esc_rate = s["esc"] / s["total"]
            print(
                f"  {kind:24} {s['total']:3} 件  コード {code_rate:5.0%}  人に渡せた {esc_rate:5.0%}"
            )

    # --- モデルをどこまで使ったか
    by_tier: dict[str, int] = defaultdict(int)
    tokens_by_tier: dict[str, int] = defaultdict(int)
    for r in rows:
        by_tier[r["tier"]] += 1
        tokens_by_tier[r["tier"]] += int(r.get("tokens") or 0)
    print()
    print("モデルをどこまで使ったか")
    for tier, label in (("none", "呼ばずに済んだ"), ("cheap", "安いモデル"), ("strong", "上位モデル")):
        n = by_tier.get(tier, 0)
        print(
            f"  {label:16} {n:4} 件 ({n / len(rows):5.1%})  "
            f"合計 {tokens_by_tier.get(tier, 0):,} トークン"
        )

    if missed:
        print()
        print(f"見逃した申請 {len(missed)} 件: {', '.join(missed[:15])}")
    if false_alarms:
        print(f"余計に渡した申請 {len(false_alarms)} 件: {', '.join(false_alarms[:15])}")

    errors = [r for r in rows if r.get("error")]
    if errors:
        print(f"\nモデル呼び出しに失敗 {len(errors)} 件（すべて人に渡している）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
