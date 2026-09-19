"""OrcaRouter への接続。

API キーは macOS Keychain だけを正とする。リポジトリにも環境設定ファイルにもログにも置かない。

登録（ターミナルで一度だけ。値はプロンプトに入力する。画面にはエコーされない）:
    security add-generic-password -a orcarouter -s orcarouter-api-key -U -w
"""

from __future__ import annotations

import subprocess

ACCOUNT = "orcarouter"
SERVICE = "orcarouter-api-key"
BASE_URL = "https://api.orcarouter.ai/v1"

# 開発中はこれだけを使う。無料枠に自動ルーティングされるのでクレジットを消費しない。
# 利用には OrcaRouter 側で、ある程度の期間使われている GitHub アカウントの連携が要る。
FREE_MODEL = "orcarouter/free"


def api_key() -> str:
    """Keychain から API キーを取り出す。無ければ登録手順を添えて落とす。"""
    proc = subprocess.run(
        ["security", "find-generic-password", "-a", ACCOUNT, "-s", SERVICE, "-w"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Keychain に {SERVICE} がありません。次を実行して登録してください:\n"
            f"  security add-generic-password -a {ACCOUNT} -s {SERVICE} -U -w"
        )
    return proc.stdout.strip()


def client():
    """OpenAI 互換クライアントを返す。OrcaRouter は OpenAI SDK でそのまま叩ける。"""
    from openai import OpenAI

    return OpenAI(api_key=api_key(), base_url=BASE_URL)
