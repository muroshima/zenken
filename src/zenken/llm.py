"""LLM 呼び出しの共通層。キャッシュ・使用量の記録・上限の強制をここに集約する。

自律ループを回すエージェントでは、条件分岐をひとつ間違えるとモデルを呼び続けて課金が止まらない。
「気をつける」では防げないので、次の3つを機械で強制する。

1. **キャッシュ** — 同じ入力を二度叩かない。開発中の作り直しが実質ただになる
2. **使用量の記録** — 呼び出し回数とトークンを常に数える
3. **上限** — 回数かトークンが上限に達したら例外で止める

呼び出しは必ずこのモジュール経由にする。SDK を直接触る箇所を作らない。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .orca import BASE_URL, FREE_MODEL, api_key

CACHE_DIR = Path(os.environ.get("ZENKEN_CACHE_DIR", ".cache/llm"))


class BudgetExceeded(RuntimeError):
    """上限に達したので処理を止めた。"""


@dataclass
class Ledger:
    """このプロセスで何をどれだけ使ったかの記録。"""

    calls: int = 0
    cache_hits: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    by_model: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    _lock: Any = field(default_factory=threading.Lock, repr=False)

    def record(self, model: str, usage: dict[str, Any] | None) -> None:
        with self._lock:
            self._record(model, usage)

    def _record(self, model: str, usage: dict[str, Any] | None) -> None:
        self.calls += 1
        p = int((usage or {}).get("prompt_tokens", 0))
        c = int((usage or {}).get("completion_tokens", 0))
        self.prompt_tokens += p
        self.completion_tokens += c
        m = self.by_model.setdefault(model, {"calls": 0, "prompt": 0, "completion": 0})
        m["calls"] += 1
        m["prompt"] += p
        m["completion"] += c

    def summary(self) -> str:
        lines = [
            f"呼び出し {self.calls} 回（キャッシュ命中 {self.cache_hits} 回）/ "
            f"入力 {self.prompt_tokens:,} トークン・出力 {self.completion_tokens:,} トークン"
        ]
        for model, m in sorted(self.by_model.items(), key=lambda kv: -kv[1]["calls"]):
            lines.append(
                f"  {model:40} {m['calls']:4} 回  入力 {m['prompt']:>8,} / 出力 {m['completion']:>8,}"
            )
        return "\n".join(lines)


@dataclass
class Budget:
    """これを超えたら止める、という線。"""

    max_calls: int = 300
    max_tokens: int = 1_000_000

    def check(self, ledger: Ledger) -> None:
        if ledger.calls >= self.max_calls:
            raise BudgetExceeded(
                f"呼び出しが上限 {self.max_calls} 回に達しました。"
                "ループが止まらなくなっていないか確認してください。"
            )
        if ledger.total_tokens >= self.max_tokens:
            raise BudgetExceeded(
                f"トークンが上限 {self.max_tokens:,} に達しました（実績 {ledger.total_tokens:,}）。"
            )


def _cache_key(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


class LLM:
    """OrcaRouter 経由の呼び出し口。

    model を渡さなければ無料枠に流す。有料モデルを使うのは、明示的に指定したときだけ。
    """

    def __init__(
        self,
        *,
        budget: Budget | None = None,
        use_cache: bool = True,
        cache_dir: Path | None = None,
    ) -> None:
        self.ledger = Ledger()
        self.budget = budget or Budget()
        self.use_cache = use_cache
        self.cache_dir = cache_dir or CACHE_DIR
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=api_key(), base_url=BASE_URL, max_retries=2)
        return self._client

    def complete(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = FREE_MODEL,
        temperature: float = 0.0,
        max_tokens: int = 1200,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """1回の補完。返すのは `{"content": str, "model": str, "cached": bool}`。"""
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            request["response_format"] = response_format

        key = _cache_key(request)
        path = self.cache_dir / f"{key}.json"
        if self.use_cache and path.exists():
            cached = json.loads(path.read_text(encoding="utf-8"))
            self.ledger.cache_hits += 1
            return {**cached, "cached": True}

        self.budget.check(self.ledger)

        started = time.monotonic()
        res = self.client.chat.completions.create(**request)
        elapsed = time.monotonic() - started

        usage = res.usage.model_dump() if res.usage else {}
        self.ledger.record(res.model or model, usage)

        out = {
            "content": res.choices[0].message.content or "",
            "model": res.model or model,
            "usage": usage,
            "elapsed_sec": round(elapsed, 3),
        }
        if self.use_cache:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**out, "cached": False}
