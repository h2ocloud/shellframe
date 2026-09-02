#!/usr/bin/env bash
# 全套回歸測試。`tests_*.py` 加 `tests_*.js` 一起跑——JS 測試（IME 去重那種
# 純前端邏輯）用 python 的 glob 掃不到，會靜靜變成沒人跑的孤兒。
set -uo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
fails=0
total=0

run() {
  total=$((total + 1))
  local out last
  out=$("$@" 2>&1)
  last=$(printf '%s\n' "$out" | tail -1)
  case "$last" in
    *PASS*|*"0 failed"*|*"all green"*)
      printf '  ✓ %-32s %s\n' "$(basename "${@: -1}")" "$last" ;;
    *)
      fails=$((fails + 1))
      printf '  ✗ %-32s %s\n' "$(basename "${@: -1}")" "$last"
      printf '%s\n' "$out" | tail -20 | sed 's/^/      /' ;;
  esac
}

for t in tests_*.py; do [ -e "$t" ] && run "$PY" "$t"; done
if command -v node >/dev/null 2>&1; then
  for t in tests_*.js; do [ -e "$t" ] && run node "$t"; done
else
  echo "  ! 跳過 tests_*.js（找不到 node）"
fi

echo
echo "$((total - fails))/$total 通過"
exit $((fails > 0))
