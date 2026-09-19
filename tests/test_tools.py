"""計算で決まる部分のテスト。ここにモデルは関わらないので、結果は常に同じになる。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from zenken.tools import (  # noqa: E402
    _normalize_vendor,
    arithmetic_facts,
    check_rules,
    find_exact_duplicates,
    find_near_matches,
    load_rules,
)

RULES = load_rules()


def expense(**over):
    base = {
        "id": "EXP-9001",
        "employee_id": "E001",
        "employee_name": "テスト 太郎",
        "department": "営業部",
        "used_at": "2026-09-10T19:00:00",
        "submitted_at": "2026-09-12T10:00:00",
        "category": "会議費",
        "vendor": "カフェ ルミエール",
        "amount": 5000,
        "attendees": 2,
        "description": "打ち合わせ",
        "receipt_text": "領収書",
    }
    return {**base, **over}


def codes(findings):
    return {f["code"] for f in findings}


class TestCheckRules:
    def test_規程内なら何も出ない(self):
        assert check_rules(expense(amount=5000, attendees=2), RULES) == []

    def test_1人あたりの上限超過を出す(self):
        # 会議費は1人3,000円まで。2名で 10,000 円は1人5,000円
        found = check_rules(expense(amount=10000, attendees=2), RULES)
        assert "over_limit" in codes(found)

    def test_人数で割るので総額が大きくても超過にならない(self):
        # 4名で 11,000 円なら1人2,750円
        assert "over_limit" not in codes(check_rules(expense(amount=11000, attendees=4), RULES))

    def test_利用日が申請日より後なら出す(self):
        found = check_rules(
            expense(used_at="2026-09-20T10:00:00", submitted_at="2026-09-12T10:00:00"), RULES
        )
        assert "date_inconsistent" in codes(found)

    def test_事前承認が必要な金額を出す(self):
        found = check_rules(
            expense(category="消耗品費", amount=45000, attendees=1), RULES
        )
        assert "preapproval_required" in codes(found)

    def test_時間外のタクシーを出す(self):
        found = check_rules(
            expense(category="交通費", vendor="日本交通（タクシー）", used_at="2026-09-10T15:00:00"),
            RULES,
        )
        assert "taxi_out_of_hours" in codes(found)

    def test_深夜のタクシーは通す(self):
        found = check_rules(
            expense(category="交通費", vendor="日本交通（タクシー）", used_at="2026-09-10T23:30:00"),
            RULES,
        )
        assert "taxi_out_of_hours" not in codes(found)

    def test_期限を過ぎた申請を出す(self):
        found = check_rules(
            expense(used_at="2026-07-01T10:00:00", submitted_at="2026-09-12T10:00:00"), RULES
        )
        assert "late_submission" in codes(found)


class TestNormalizeVendor:
    def test_全角半角と空白の違いを吸収する(self):
        assert _normalize_vendor("ヨドバシ カメラ") == _normalize_vendor("ヨドバシカメラ")

    def test_法人格の有無を吸収する(self):
        assert _normalize_vendor("株式会社アスクル") == _normalize_vendor("アスクル")

    def test_表記が違う同一店舗までは吸収できない(self):
        # ここが吸収できないからこそ、モデルの判断が要る
        assert _normalize_vendor("Cafe Lumiere") != _normalize_vendor("カフェ ルミエール")


class TestDuplicates:
    def test_同一内容の二重申請を見つける(self):
        a = expense(id="EXP-1")
        b = expense(id="EXP-2")
        assert [d["id"] for d in find_exact_duplicates(a, [b])] == ["EXP-2"]

    def test_金額が違えば重複としない(self):
        a = expense(id="EXP-1", amount=5000)
        b = expense(id="EXP-2", amount=5100)
        assert find_exact_duplicates(a, [b]) == []

    def test_別の社員なら重複としない(self):
        a = expense(id="EXP-1", employee_id="E001")
        b = expense(id="EXP-2", employee_id="E002")
        assert find_exact_duplicates(a, [b]) == []

    def test_表記が違う店は自分自身では見つけられない(self):
        a = expense(id="EXP-1", vendor="カフェ ルミエール")
        b = expense(id="EXP-2", vendor="Cafe Lumiere")
        assert find_exact_duplicates(a, [b]) == []  # → モデルに渡す（find_near_matches）


class TestNearMatches:
    def test_金額が一致すれば日が離れていても拾う(self):
        a = expense(id="EXP-1", amount=28000, used_at="2026-09-10T12:00:00")
        b = expense(id="EXP-2", amount=28000, used_at="2026-08-01T12:00:00")
        assert [r["id"] for r in find_near_matches(a, [b])] == ["EXP-2"]

    def test_勘定科目が違えば近い日でも拾わない(self):
        a = expense(id="EXP-1", category="会議費", amount=5000)
        b = expense(id="EXP-2", category="交通費", amount=800, used_at="2026-09-11T12:00:00")
        assert find_near_matches(a, [b]) == []

    def test_同じ科目で日が近ければ拾う(self):
        a = expense(id="EXP-1", category="消耗品費", amount=28000, used_at="2026-09-10T12:00:00")
        b = expense(id="EXP-2", category="消耗品費", amount=27000, used_at="2026-09-11T12:00:00")
        assert [r["id"] for r in find_near_matches(a, [b])] == ["EXP-2"]


class TestArithmetic:
    def test_1人あたりを先に計算しておく(self):
        facts = arithmetic_facts(expense(amount=12000, attendees=3))
        assert facts["per_person"] == 4000

    def test_人数が0でも落ちない(self):
        assert arithmetic_facts(expense(amount=5000, attendees=0))["per_person"] == 5000
