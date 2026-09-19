#!/usr/bin/env python3
"""OrcaRouter への疎通を確認する。キーは一切表示しない。

    python3 scripts/check_orca.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from zenken.orca import BASE_URL, api_key  # noqa: E402


def main() -> int:
    try:
        key = api_key()
    except RuntimeError as e:
        print(e, file=sys.stderr)
        return 1

    req = urllib.request.Request(
        f"{BASE_URL}/models", headers={"Authorization": f"Bearer {key}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            body = json.load(res)
    except urllib.error.HTTPError as e:
        print(f"HTTP {e.code}: 認証か権限を確認してください", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"接続に失敗しました: {e.reason}", file=sys.stderr)
        return 1

    models = body.get("data", [])
    print(f"疎通OK。利用できるモデル: {len(models)} 件")
    for m in models[:5]:
        print(f"  - {m.get('id')}")
    if len(models) > 5:
        print(f"  ... 他 {len(models) - 5} 件")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
