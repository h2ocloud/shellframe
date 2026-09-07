#!/usr/bin/env python3
"""測試入口自己要先可信（v0.35.7）。

舊版 run_tests.sh 把 `out=$(...)` 的退出碼丟掉，通過與否全看最後一行有沒有
PASS／"0 failed"／"all green"。兩個後果都能重現：

  1. 子程序 exit 7、最後一行 "ALL PASS" → 判定通過
  2. 最後一行 "Results: 1 passed, 10 failed" → 因為含子字串 "0 failed" 也通過

還漏收 `test_*.py`（單數）這一種命名，`test_init_prompt.py` 因此從來沒被跑過。

這支把真正的 run_tests.sh 拿到一個乾淨的臨時目錄裡跑，塞進上面兩種假測試，
驗它現在會紅燈；同時驗收集規則涵蓋三種命名、SKIP 不被算成通過、以及零測試
不能報全綠。

跑法：.venv/bin/python tests_runner_self.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
RUNNER = HERE / "run_tests.sh"

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


def sandbox(files):
    """把 runner 複製到臨時目錄，只放指定的假測試，回傳 (returncode, stdout)."""
    tmp = Path(tempfile.mkdtemp(prefix="sf-runner-self-"))
    try:
        shutil.copy2(RUNNER, tmp / "run_tests.sh")
        for fname, body in files.items():
            p = tmp / fname
            p.write_text(body, encoding="utf-8")
            p.chmod(0o755)
        r = subprocess.run(["bash", str(tmp / "run_tests.sh")],
                           capture_output=True, text=True, timeout=120,
                           cwd=str(tmp))
        return r.returncode, r.stdout + r.stderr
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


PASS_TEXT_EXIT_7 = '''#!/usr/bin/env python3
print("ALL PASS")
raise SystemExit(7)
'''
TEN_FAILED = '''#!/usr/bin/env python3
print("Results: 1 passed, 10 failed")
raise SystemExit(1)
'''
GENUINE_PASS = '''#!/usr/bin/env python3
print("Results: 3 passed, 0 failed")
print("ALL PASS")
'''
SKIPPED = '''#!/usr/bin/env python3
print("SKIP  tests_fake_skip.py（沒裝 playwright）")
print("ALL PASS")
'''

rc, out = sandbox({"tests_fake_exit7.py": PASS_TEXT_EXIT_7})
check("退出碼非零一律失敗（即使最後一行寫 ALL PASS）", rc != 0, f"rc={rc}\n{out}")
check("失敗的檔名有列出來", "tests_fake_exit7.py" in out, out)

rc, out = sandbox({"tests_fake_ten_failed.py": TEN_FAILED})
check('"10 failed" 不會被 "0 failed" 的子字串放過', rc != 0, f"rc={rc}\n{out}")

rc, out = sandbox({"tests_fake_ok.py": GENUINE_PASS})
check("真的通過就綠燈", rc == 0 and "1/1 通過" in out, f"rc={rc}\n{out}")

rc, out = sandbox({"tests_fake_ok.py": GENUINE_PASS,
                   "tests_fake_exit7.py": PASS_TEXT_EXIT_7})
check("一支失敗就整體失敗", rc != 0, f"rc={rc}\n{out}")

rc, out = sandbox({"tests_fake_skip.py": SKIPPED})
check("SKIP 不算通過（分開計數）",
      rc == 0 and "跳過" in out and "0/1 通過" in out, f"rc={rc}\n{out}")

rc, out = sandbox({})
check("零測試不得報全綠", rc != 0 and "沒有收集到" in out, f"rc={rc}\n{out}")

# 三種命名都要收，且 tests_*.py 不能因為也命中 test_*.py 而被跑兩次
rc, out = sandbox({"tests_fake_ok.py": GENUINE_PASS,
                   "test_fake_singular.py": GENUINE_PASS,
                   "tests_fake_ok.js": 'console.log("Results: 1 passed, 0 failed");'})
check("tests_*.py / test_*.py / tests_*.js 三種都收到",
      "tests_fake_ok.py" in out and "test_fake_singular.py" in out
      and "tests_fake_ok.js" in out, out)
check("同一支不會跑兩次（3 支就是 3 支）",
      rc == 0 and "3/3 通過" in out, f"rc={rc}\n{out}")

# 真 repo 裡確實存在單數命名的測試——這就是之前漏掉的那支
singular = sorted(p.name for p in HERE.glob("test_*.py")
                  if not p.name.startswith("tests_"))
check("repo 裡的單數命名測試會被收集", "test_init_prompt.py" in singular,
      str(singular))

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
