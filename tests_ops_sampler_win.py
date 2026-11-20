#!/usr/bin/env python3
"""Windows ops sampler: fixed-data helpers on every platform, APIs on Windows only."""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["SHELLFRAME_TESTS"] = "1"
os.environ["SHELLFRAME_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sf-ops-sampler-win-")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import ops_sampler as S  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


print("Windows pure helpers")
TASKLIST = '''"Demo.exe","101","Console","1","1,024 K"
"C:\\Tools\\Demo.exe","102","Console","1","2,048 K"
"Other.exe","103","Console","1","512 K"
"bad row"
'''
rows = S.parse_tasklist_csv(TASKLIST)
check("tasklist CSV: basename, memory bytes, same-name aggregation",
      [r["name"] for r in rows] == ["Demo.exe", "Other.exe"]
      and rows[0]["rss"] == 3 * 1024 * 1024 and rows[0]["count"] == 2
      and rows[0]["cpu"] is None, rows)
check("tasklist CSV: memory order", [r["name"] for r in S.parse_tasklist_csv(TASKLIST, by="mem")] == ["Demo.exe", "Other.exe"])

IFACES = [
    {"name": "Ethernet", "oper_status": 1, "if_type": 6, "tunnel_type": 0, "in_octets": 100, "out_octets": 200},
    {"name": "Wi-Fi", "oper_status": "up", "if_type": 71, "tunnel_type": 0, "in_octets": 300, "out_octets": 400},
    {"name": "Loopback", "oper_status": 1, "if_type": 24, "tunnel_type": 0, "in_octets": 1000, "out_octets": 1000},
    {"name": "Tunnel", "oper_status": 1, "if_type": 131, "tunnel_type": 1, "in_octets": 2000, "out_octets": 2000},
    {"name": "Down", "oper_status": 2, "if_type": 6, "tunnel_type": 0, "in_octets": 4000, "out_octets": 4000},
]
net = S.aggregate_win_interfaces(IFACES)
check("interfaces: only up, non-loopback, non-tunnel totals", net == {"in": 400, "out": 600, "iface": "Ethernet, Wi-Fi"}, net)
check("interfaces: no eligible row is unknown", S.aggregate_win_interfaces(IFACES[2:]) is None)

check("Windows pressure: free-memory thresholds",
      S.windows_pressure(4.9, 100) == "critical"
      and S.windows_pressure(9.9, 100) == "warn"
      and S.windows_pressure(10, 100) == "normal")
check("Windows pagefile: committed memory minus physical use",
      S.windows_swap(16 << 30, 8 << 30, 8 << 30, 6 << 30) == {"total": 8 << 30, "used": 2 << 30}
      and S.windows_swap(4 << 30, 1 << 30, 8 << 30, 7 << 30) == {"total": 0, "used": 0})

print("Windows structures (any platform: fixed-width types, 64-bit Windows layout)")
import ctypes  # noqa: E402
_R = S._WIN_IF_ROW2
check("MIB_IF_ROW2 is 1352 bytes", ctypes.sizeof(_R) == 1352, ctypes.sizeof(_R))
check("…NetworkGuid sits between MediaConnectState and ConnectionType (1168)", _R.NetworkGuid.offset == 1168, _R.NetworkGuid.offset)
check("…InOctets at 1208 and OutOctets at 1280", (_R.InOctets.offset, _R.OutOctets.offset) == (1208, 1280),
      (_R.InOctets.offset, _R.OutOctets.offset))
check("SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION is 48 bytes", ctypes.sizeof(S._WIN_PROCESSOR_INFO) == 48)
check("MEMORYSTATUSEX is 64 bytes", ctypes.sizeof(S._WIN_MEMORY_STATUS) == 64)
check("FILETIME is 8 bytes", ctypes.sizeof(S._WIN_FILETIME) == 8)

if sys.platform == "win32":
    print("Windows API sample")
    sampler = S.Sampler(kind="win")
    started = time.time()
    first = sampler.sample(full=True)
    second = sampler.sample(full=True, prime=False)
    elapsed = time.time() - started
    print(f"  one/two samples elapsed: {elapsed:.3f}s")
    check("platform kind", S.platform_kind() == "win" and S.supported())
    check("sample: CPU, memory, disk, network are numeric",
          isinstance(second["cpu"], (int, float)) and 0 <= second["cpu"] <= 100
          and isinstance(second["mem_pct"], (int, float)) and 0 <= second["mem_pct"] <= 100
          and isinstance(second["disk_free_pct"], (int, float)) and 0 <= second["disk_free_pct"] <= 100
          and isinstance(second["net_in"], (int, float)) and second["net_in"] >= 0, second)
    check("sample: Windows memory parts are intentionally absent",
          second["mem_app"] is None and second["mem_wired"] is None
          and second["mem_compressed"] is None and second["mem_cached"] is None)
    top = sampler.top()
    check("top: tasklist returns bounded named rows", len(top) <= 5 and all(t["name"] and t["rss"] >= 0 for t in top), top)
else:
    print("  [SKIP] Windows API sample (not sys.platform == 'win32')")

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
