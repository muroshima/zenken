#!/usr/bin/env python3
"""ルーティングした場合としない場合で、実際にいくら違うのかを突き合わせる。

同じ188件に対して、

  A. 全件を上位モデルに投げる（ルーティングなし）
  B. 引っかからないものはモデルを呼ばず、必要なものだけ上位モデルに上げる（zenken）

を両方走らせ、トークンと金額を並べる。推定値ではなく、どちらも実際に走らせた実績。

    uv run python scripts/compare_cost.py --journal-a runs/all_strong.jsonl --journal-b runs/audit.jsonl

金額は data/pricing.json の単価で計算する。単価は各提供元の公表値で、
OrcaRouter はトークンの上乗せをしないため同額になる（計測日は pricing.json に記録）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRICING_PATH = ROOT / "data" / "pricing.json"


def load_pricing() -> dict:
    if not PRICING_PATH.exists():
        return {}
    return json.loads(PRICING_PATH.read_text(encoding="utf-8"))


def read_journal(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def tally(rows: list[dict], pricing: dict) -> dict:
    """ジャーナルからトークンと金額を集計する。

    ジャーナルには合計トークンしか残していないので、入出力の内訳は
    usage_detail があればそれを、無ければ合計だけを使う。
    """
    per_model: dict[str, dict[str, float]] = {}
    for r in rows:
        model = r.get("model")
        if not model or not r.get("tokens"):
            continue
        m = per_model.setdefault(model, {"calls": 0, "tokens": 0, "prompt": 0, "completion": 0})
        m["calls"] += 1
        m["tokens"] += int(r["tokens"])
        detail = r.get("usage_detail") or {}
        m["prompt"] += int(detail.get("prompt_tokens", 0))
        m["completion"] += int(detail.get("completion_tokens", 0))

    total_cost = 0.0
    priced = True
    for model, m in per_model.items():
        rate = pricing.get("models", {}).get(model)
        if not rate:
            priced = False
            continue
        if m["prompt"] or m["completion"]:
            cost = (
                m["prompt"] * rate["input_per_1m"] + m["completion"] * rate["output_per_1m"]
            ) / 1_000_000
        else:
            # 内訳が無い場合は合計に平均単価をかける（控えめに出ないよう出力単価寄りにする）
            avg = (rate["input_per_1m"] + rate["output_per_1m"] * 2) / 3
            cost = m["tokens"] * avg / 1_000_000
        m["cost"] = cost
        total_cost += cost

    return {
        "rows": len(rows),
        "calls": sum(m["calls"] for m in per_model.values()),
        "tokens": sum(m["tokens"] for m in per_model.values()),
        "per_model": per_model,
        "cost": total_cost,
        "priced": priced,
        "no_call": sum(1 for r in rows if r.get("tier") == "none"),
        "escalated": sum(1 for r in rows if r.get("decision") == "escalate"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--journal-a", default="runs/all_strong.jsonl", help="ルーティングなし")
    parser.add_argument("--journal-b", default="runs/audit.jsonl", help="zenken（ルーティングあり）")
    args = parser.parse_args()

    pricing = load_pricing()
    paths = {"A. 全件を上位モデル": ROOT / args.journal_a, "B. zenken": ROOT / args.journal_b}
    stats = {}
    for label, path in paths.items():
        if not path.exists():
            print(f"{path} がありません。先に実行してください。", file=sys.stderr)
            return 1
        stats[label] = tally(read_journal(path), pricing)

    print("=" * 74)
    for label, s in stats.items():
        print(f"{label}")
        print(f"  申請 {s['rows']} 件 / モデル呼び出し {s['calls']} 回 / {s['tokens']:,} トークン")
        print(f"  呼ばずに済んだ申請 {s['no_call']} 件 / 人に渡した {s['escalated']} 件")
        for model, m in sorted(s["per_model"].items(), key=lambda kv: -kv[1]["calls"]):
            cost = m.get("cost")
            money = f" / ${cost:.4f}" if cost is not None else ""
            print(f"    {model:34} {m['calls']:4} 回  {m['tokens']:>8,} トークン{money}")
        if s["priced"]:
            print(f"  合計 ${s['cost']:.4f}")
        print()

    a, b = stats["A. 全件を上位モデル"], stats["B. zenken"]
    print("=" * 74)
    if a["tokens"] and b["tokens"]:
        print(f"トークン        {a['tokens']:,} → {b['tokens']:,}  （{b['tokens'] / a['tokens']:.1%}）")
    if a["calls"]:
        print(f"呼び出し回数    {a['calls']} → {b['calls']}  （{b['calls'] / a['calls']:.1%}）")
    if a["priced"] and b["priced"] and a["cost"]:
        print(f"金額            ${a['cost']:.4f} → ${b['cost']:.4f}  （{b['cost'] / a['cost']:.1%}）")
        print(f"削減率          {1 - b['cost'] / a['cost']:.1%}")
    print()
    print(f"人に渡した件数  {a['escalated']} 件 → {b['escalated']} 件")
    print("（削れたのはコストであって、拾うべき申請ではないことを確認する）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
