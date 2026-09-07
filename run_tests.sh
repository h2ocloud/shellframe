#!/usr/bin/env bash
# 全套回歸測試。
#
# 成敗**只看子程序的退出碼**。上一版是拿最後一行做字串比對，兩個後果：
# 子程序 exit 7 但最後一行含 "ALL PASS" 會被判成通過；"Results: 1 passed,
# 10 failed" 因為含有子字串 "0 failed" 也算通過。輸出只用來顯示摘要。
#
# 收集三種命名：`tests_*.py`、`test_*.py`（單數，之前整個被漏掉）、`tests_*.js`
# （純前端邏輯的測試，函式直接從 web/index.html 抽出來，不留會走樣的副本）。
# 注意 `test_*.py` 這個 glob 也會命中 `tests_*.py`，所以要去重。
#
# SKIP 與 PASS 分開呈現：缺 runtime（playwright / node）而跳過的測試退出碼是 0，
# 那是正確行為，但不能讓它在總計裡看起來像「守到了」。
set -uo pipefail
cd "$(dirname "$0")"

PY=".venv/bin/python"
[ -x "$PY" ] || PY="python3"
fails=0
skips=0
total=0
failed_names=""

run() {
  local name="$(basename "${@: -1}")"
  total=$((total + 1))
  local out rc last
  out=$("$@" 2>&1)
  rc=$?
  last=$(printf '%s\n' "$out" | tail -1)
  if [ "$rc" -ne 0 ]; then
    fails=$((fails + 1))
    failed_names="$failed_names $name"
    printf '  ✗ %-34s exit=%s  %s\n' "$name" "$rc" "$last"
    printf '%s\n' "$out" | tail -20 | sed 's/^/      /'
    return
  fi
  # 退出碼 0：再看它是不是自己說跳過了
  if printf '%s\n' "$out" | grep -q '^SKIP\b\|^  *SKIP\b'; then
    skips=$((skips + 1))
    printf '  ○ %-34s %s\n' "$name" "$(printf '%s\n' "$out" | grep -m1 'SKIP')"
    return
  fi
  printf '  ✓ %-34s %s\n' "$name" "$last"
}

seen=""
collect() {
  local t
  for t in $1; do
    [ -e "$t" ] || continue
    case " $seen " in *" $t "*) continue ;; esac
    seen="$seen $t"
    printf '%s\n' "$t"
  done
}

for t in $(collect 'tests_*.py'; collect 'test_*.py'); do run "$PY" "$t"; done
if command -v node >/dev/null 2>&1; then
  for t in $(collect 'tests_*.js'); do run node "$t"; done
else
  echo "  ! 跳過 tests_*.js（找不到 node）——JS 測試沒有被執行"
  fails=$((fails + 1))
  failed_names="$failed_names tests_*.js(no-node)"
fi

echo
if [ "$total" -eq 0 ]; then
  echo "沒有收集到任何測試 — 當成失敗（glob 或工作目錄不對）"
  exit 1
fi
printf '%s/%s 通過' "$((total - fails - skips))" "$total"
[ "$skips" -gt 0 ] && printf '，%s 個跳過' "$skips"
[ "$fails" -gt 0 ] && printf '，%s 個失敗：%s' "$fails" "$failed_names"
printf '\n'
exit $((fails > 0))
