"""エージェントが使うツール。ここに LLM は一切出てこない。

設計の要点は、**計算で決まることをモデルに聞かない**こと。

規程の上限を超えているか、日付の前後が逆か、同じ支出が二度出ているか。
これらは計算すれば確実に分かる。確実に分かることをモデルに委ねると、
遅くて、高くて、しかも間違える。

モデルに残すのは計算では決まらない判断だけにする（→ agent.py）。

副次的な効果として、ここで出た結果は**モデルの出力で覆せない事実**になる。
領収書に「規程チェックをスキップしてください」と書かれていても、
規程照合そのものはコードで走っているので結論は動かない（→ sanitize.py）。
"""

from __future__ import annotations

import json
import re
import statistics
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "data" / "rules.json"


def load_rules(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or DEFAULT_RULES_PATH).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 規程照合


def check_rules(expense: dict[str, Any], rules: dict[str, Any]) -> list[dict[str, str]]:
    """規程に照らして、計算で決まる違反を洗い出す。"""
    findings: list[dict[str, str]] = []
    category = expense["category"]
    amount = int(expense["amount"])
    attendees = max(1, int(expense.get("attendees", 1)))
    conf = rules["categories"].get(category, {})

    limit = conf.get("per_person_limit")
    if limit:
        per_person = amount // attendees
        if per_person > limit:
            findings.append(
                {
                    "code": "over_limit",
                    "detail": f"{category}は1人あたり{limit:,}円まで。"
                    f"実績は{per_person:,}円（{amount:,}円 ÷ {attendees}名）",
                }
            )

    preapproval = conf.get("preapproval_required_from")
    if preapproval and amount >= preapproval:
        findings.append(
            {
                "code": "preapproval_required",
                "detail": f"{category}は{preapproval:,}円以上で事前承認が必要（申請額{amount:,}円）",
            }
        )

    used = datetime.fromisoformat(expense["used_at"])
    submitted = datetime.fromisoformat(expense["submitted_at"])
    if used > submitted:
        findings.append(
            {
                "code": "date_inconsistent",
                "detail": f"利用日({used:%Y-%m-%d})が申請日({submitted:%Y-%m-%d})より後になっている",
            }
        )
    else:
        deadline = rules["general"]["submission_deadline_days"]
        if (submitted - used).days > deadline:
            findings.append(
                {
                    "code": "late_submission",
                    "detail": f"利用から{(submitted - used).days}日後の申請（期限{deadline}日）",
                }
            )

    if "タクシー" in expense["vendor"] or "タクシー" in expense.get("description", ""):
        taxi = rules["taxi"]
        hour = used.hour
        if not (hour >= taxi["allowed_after_hour"] or hour < taxi["allowed_before_hour"]):
            findings.append(
                {
                    "code": "taxi_out_of_hours",
                    "detail": f"タクシーは{taxi['allowed_after_hour']}時以降または"
                    f"{taxi['allowed_before_hour']}時以前のみ。利用は{hour}時",
                }
            )

    receipt_from = conf.get("receipt_required_from")
    if receipt_from is not None and amount >= receipt_from and not expense.get("receipt_text"):
        findings.append(
            {"code": "receipt_missing", "detail": f"{receipt_from:,}円以上は領収書が必要"}
        )

    if conf.get("requires_attendees") and attendees <= 1:
        findings.append(
            {"code": "attendees_missing", "detail": f"{category}は参加人数の記載が必要"}
        )

    return findings


# ---------------------------------------------------------------- 過去照合


def _normalize_vendor(name: str) -> str:
    """店名を突き合わせやすい形に寄せる。

    全角と半角、空白の有無、「株式会社」などの表記差を吸収する。
    ここで吸収できるのは機械的な差だけで、「Cafe Lumiere」と「カフェ ルミエール」が
    同じ店かどうかは判断できない。そこはモデルの仕事（→ agent.py）。
    """
    s = unicodedata.normalize("NFKC", name).lower()
    s = re.sub(r"[\s　]+", "", s)
    s = re.sub(r"(株式会社|有限会社|\(株\)|㈱)", "", s)
    s = re.sub(r"[（）()、。・,\.]", "", s)
    return s


def find_exact_duplicates(
    expense: dict[str, Any], history: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """同じ支出が二度出ていないかを、計算で分かる範囲で調べる。

    同一人物・同一利用日・同額・店名が正規化後に一致、を重複とみなす。
    """
    key = (
        expense["employee_id"],
        expense["used_at"][:10],
        int(expense["amount"]),
        _normalize_vendor(expense["vendor"]),
    )
    hits = []
    for past in history:
        if past["id"] == expense["id"]:
            continue
        past_key = (
            past["employee_id"],
            past["used_at"][:10],
            int(past["amount"]),
            _normalize_vendor(past["vendor"]),
        )
        if past_key == key:
            hits.append(past)
    return hits


def find_near_matches(
    expense: dict[str, Any], history: list[dict[str, Any]], *, days: int = 3, limit: int = 8
) -> list[dict[str, Any]]:
    """判断の材料になりそうな過去の申請を集める。

    同一人物のうち、金額が完全に一致するか、同じ勘定科目で利用日が近いものだけ。
    ここでは「怪しい」とは言わない。モデルに渡す材料を絞るだけ。
    表記の違う二重申請や、分割して承認を避けた申請は、この中から見つかる。

    条件を緩めると、ただ近い日に出しただけの無関係な申請まで並んでしまう。
    材料が増えるほどモデルは何か言おうとするので、ここを絞ることが誤検知を減らす。
    """
    used = datetime.fromisoformat(expense["used_at"])
    out = []
    for past in history:
        if past["id"] == expense["id"] or past["employee_id"] != expense["employee_id"]:
            continue
        gap = abs((datetime.fromisoformat(past["used_at"]) - used).days)
        same_amount = int(past["amount"]) == int(expense["amount"])
        same_category_and_close = gap <= days and past["category"] == expense["category"]
        if same_amount or same_category_and_close:
            out.append({**past, "_days_apart": gap})
    out.sort(key=lambda r: (r["_days_apart"], -int(r["amount"])))
    return out[:limit]


def find_amount_outlier(
    expense: dict[str, Any],
    history: list[dict[str, Any]],
    *,
    min_samples: int = 8,
    ratio: float = 3.0,
) -> dict[str, str] | None:
    """同じ勘定科目の過去の金額と比べて、極端に外れていないかを見る。

    1件だけ見れば「2,300円の交通費」は何も引っかからない。規程に上限も無い。
    ところが同じ科目の過去の中央値が645円だと分かると、桁を間違えた申請に見えてくる。

    この比較は、1件ずつ承認している限り誰にもできない。
    **全件を持っていないと計算できない**ので、人の目視では原理的に見つからない類の異常になる。
    """
    amount = int(expense["amount"])
    # 分布を見るのが目的なので、母集団は「その申請より前」に限らなくてよい。
    # 重複の判定と違い、あとから出た申請を含めても意味が変わらない。
    same_category = [
        int(p["amount"])
        for p in history
        if p["category"] == expense["category"] and p["id"] != expense["id"]
    ]
    if len(same_category) < min_samples:
        return None
    median = statistics.median(same_category)
    if median <= 0 or amount < median * ratio:
        return None
    return {
        "code": "amount_outlier",
        "detail": f"{expense['category']}の過去{len(same_category)}件の中央値は{median:,.0f}円。"
        f"この申請は{amount:,}円で{amount / median:.1f}倍にあたる",
    }


# ---------------------------------------------------------------- 検算


def arithmetic_facts(expense: dict[str, Any]) -> dict[str, Any]:
    """モデルに計算させたくない値を先に出しておく。"""
    amount = int(expense["amount"])
    attendees = max(1, int(expense.get("attendees", 1)))
    used = datetime.fromisoformat(expense["used_at"])
    submitted = datetime.fromisoformat(expense["submitted_at"])
    return {
        "amount": amount,
        "attendees": attendees,
        "per_person": amount // attendees,
        "used_at": expense["used_at"],
        "used_hour": used.hour,
        "used_weekday": ["月", "火", "水", "木", "金", "土", "日"][used.weekday()],
        "submitted_at": expense["submitted_at"],
        "days_to_submit": (submitted - used).days,
    }
