#!/usr/bin/env bash
# Keychain から OrcaRouter のキーを読み、環境変数にセットする。
# 使い方: source scripts/orca_env.sh
_orca_key=$(security find-generic-password -a orcarouter -s orcarouter-api-key -w 2>/dev/null)
if [ -z "${_orca_key}" ]; then
  echo "Keychain に orcarouter-api-key がありません。次で登録してください:" >&2
  echo "  security add-generic-password -a orcarouter -s orcarouter-api-key -U -w" >&2
  unset _orca_key
  return 1 2>/dev/null || exit 1
fi
export ORCAROUTER_API_KEY="${_orca_key}"
export ORCAROUTER_BASE_URL="https://api.orcarouter.ai/v1"
unset _orca_key
echo "OrcaRouter の環境変数をセットしました（キーは表示しません）"
