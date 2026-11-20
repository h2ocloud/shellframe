#!/usr/bin/env python3
"""The ops sampler (ops_sampler.py): the parsers on fixed text, then one real reading of this computer.

  - vm_stat, swapusage, netstat, route, ps, /proc files parse (a missing field is absent, not an error)
  - the memory parts add up as Activity Monitor draws them; per core CPU wraps at 32 bits
  - a failing or hanging subprocess leaves a field None and raises nothing
  - on this computer: every field has its type and a sane range, a light sample skips memory and network,
    the top five are program names only (no path, no arguments); the time of each is printed
Run: .venv/bin/python tests_ops_sampler.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["SHELLFRAME_TESTS"] = "1"
os.environ["SHELLFRAME_CONFIG_DIR"] = tempfile.mkdtemp(prefix="sf-ops-sampler-")
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


VM = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                     7427.
Pages wired down:                             235751.
Pages purgeable:                               28858.
"Translation faults":                     4037282891.
File-backed pages:                            405454.
Anonymous pages:                              968921.
Pages occupied by compressor:                 440878.
"""
print("parsers")
vm = S.parse_vm_stat(VM)
check("vm_stat: page size and fields", vm["page"] == 16384 and vm["pages wired down"] == 235751 and vm["anonymous pages"] == 968921, vm)
parts = S.memory_parts(vm, 32 << 30)
page = 16384
check("app = anonymous - purgeable, wired, compressed, cached = file-backed + purgeable",
      parts["app"] == (968921 - 28858) * page and parts["wired"] == 235751 * page and parts["compressed"] == 440878 * page
      and parts["cached"] == (405454 + 28858) * page, parts)
check("used = app + wired + compressed", parts["used"] == parts["app"] + parts["wired"] + parts["compressed"])
check("used never passes the total", S.memory_parts(vm, 1 << 20)["used"] == 1 << 20)
check("vm_stat of nothing: a page size, no fields", S.parse_vm_stat(None) == {"page": 16384})
check("swapusage in M and G", S.parse_swapusage("total = 11264.00M  used = 9763.69M  free = 1500.31M  (encrypted)") == (11264 << 20, int(9763.69 * 1024 ** 2))
      and S.parse_swapusage("total = 2.00G  used = 0.00M  free = 2.00G") == (2 << 30, 0) and S.parse_swapusage("junk") is None)
NET = """Name       Mtu   Network       Address            Ipkts Ierrs     Ibytes    Opkts Oerrs     Obytes  Coll
lo0        16384 <Link#1>                       4036247     0 10373096740  4036247     0 10373096740     0
lo0        16384 127           localhost        4036247     - 10373096740  4036247     - 10373096740     -
en0        1500  <Link#14>   aa:bb:cc:00:11:22  5000     0       123456     4000     0        65432     0
utun10*    1380  <Link#20>                        900     0        99000      800     0        88000     0
"""
net = S.parse_netstat_ib(NET)
check("netstat: one row per interface (its <Link#> row), the counters taken from the right even with no Address column",
      net == {"lo0": (10373096740, 10373096740), "en0": (123456, 65432), "utun10": (99000, 88000)}, net)
check("route: the interface", S.parse_route_get_default("   route to: default\ninterface: utun10\n flags: <UP>") == "utun10"
      and S.parse_route_get_default("") == "")
PS = """  PID  %CPU    RSS COMM
10137  79.0 434064 /Applications/Google Chrome.app/Contents/Frameworks/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
10138  21.0 100000 /Applications/Google Chrome.app/Contents/Frameworks/Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)
  850   8.2 242976 /usr/libexec/mds_stores
  851   1.0   1000 ps
  bad line
"""
top = S.parse_ps(PS)
check("ps: the basename only, several processes of one program added up, ps itself left out, biggest first",
      [t["name"] for t in top] == ["Google Chrome Helper (Renderer)", "mds_stores"] and top[0]["count"] == 2
      and round(top[0]["cpu"]) == 100 and top[0]["rss"] == (434064 + 100000) * 1024, top)
check("no path and no argument in a name", all("/" not in t["name"] for t in top))
STAT = "cpu  1 2 3 4\ncpu0 100 0 50 800 50 0 0 0\ncpu1 10 0 10 900 80 0 0 0\nintr 1\n"
ticks = S.parse_proc_stat(STAT)
check("/proc/stat: per core, idle = idle + iowait", ticks == [(150, 0, 850, 0), (20, 0, 980, 0)], ticks)
check("cpu usage: two readings subtracted", S.cpu_usage([(0, 0, 0, 0)], [(25, 25, 50, 0)]) == [50.0]
      and S.cpu_usage([(0, 0, 100, 0)], [(0, 0, 100, 0)]) == [0.0])
check("a 32-bit counter that wrapped still subtracts", S.cpu_usage([(2 ** 32 - 10, 0, 0, 0)], [(10, 0, 20, 0)]) == [50.0])
MEMINFO = "MemTotal:       16000000 kB\nMemAvailable:    4000000 kB\nCached:  2000000 kB\nBuffers: 100000 kB\nSwapTotal: 2000000 kB\nSwapFree: 1500000 kB\n"
mi = S.parse_meminfo(MEMINFO)
check("/proc/meminfo in bytes", mi["MemTotal"] == 16000000 * 1024 and mi["SwapFree"] == 1500000 * 1024)
check("/proc/pressure/memory: normal / warn / critical",
      S.parse_psi_memory("some avg10=0.50 avg60=0 avg300=0 total=1\nfull avg10=0.00 avg60=0 avg300=0 total=0") == "normal"
      and S.parse_psi_memory("some avg10=12.00 avg60=0 avg300=0 total=1\nfull avg10=1.00") == "warn"
      and S.parse_psi_memory("some avg10=12.00 avg60=0 avg300=0 total=1\nfull avg10=6.00") == "critical"
      and S.parse_psi_memory("") is None)
check("/proc/net/dev", S.parse_proc_net_dev("hdr\nhdr\n  eth0: 1000 1 0 0 0 0 0 0 2000 1 0 0 0 0 0 0\n    lo: 5 1 0 0 0 0 0 0 5 1 0 0 0 0 0 0\n")
      == {"eth0": (1000, 2000), "lo": (5, 5)})
check("/proc/net/route: a default route that is up, down, or none",
      S.parse_proc_net_route("Iface Destination Gateway Flags\neth0 00000000 0102A8C0 0003\n") == (True, "eth0")
      and S.parse_proc_net_route("Iface Destination Gateway Flags\neth0 00000000 0102A8C0 0002\n") == (False, "")
      and S.parse_proc_net_route("Iface Destination Gateway Flags\neth0 0002A8C0 00000000 0001\n") == (False, "")
      and S.parse_proc_net_route(None) == (None, ""))

print("a Linux computer, from fixed files")
FILES = {"/proc/stat": STAT, "/proc/meminfo": MEMINFO, "/proc/net/route": "Iface Destination Gateway Flags\neth0 00000000 0102A8C0 0003\n",
         "/proc/pressure/memory": "some avg10=12.00 avg60=0 avg300=0 total=1\nfull avg10=1.00\n"}
NETFILES = ["hdr\nhdr\n  eth0: 1000 1 0 0 0 0 0 0 2000 1 0 0 0 0 0 0\n", "hdr\nhdr\n  eth0: 51000 1 0 0 0 0 0 0 12000 1 0 0 0 0 0 0\n"]
STATS = [STAT, "cpu0 200 0 100 850 50 0 0 0\ncpu1 10 0 10 1000 80 0 0 0\n"]


class LinuxSampler(S.Sampler):
    reads = 0

    def _read(self, path):
        if path == "/proc/net/dev":
            return NETFILES[min(self.reads, 1)]
        if path == "/proc/stat":
            return STATS[min(self.reads, 1)]
        return FILES.get(path)


clock = iter([1000.0, 1001.0, 1002.0, 1003.0, 1004.0, 1005.0])
ls = LinuxSampler(kind="linux", clock=lambda: next(clock))
ls._prev_cpu = (999.0, ls._ticks())
ls._prev_net = (999.0, "eth0", 1000, 2000)
ls.reads = 1
got = ls.sample(prime=False)
check("Linux: per core CPU from /proc/stat (busy / all, idle + iowait idle)", got["cores"] == [75.0, 0.0], got["cores"])
check("Linux: memory used = total - available, cached from the files", got["mem_total"] == 16000000 * 1024
      and got["mem_used"] == 12000000 * 1024 and got["mem_cached"] == 2100000 * 1024, got)
check("Linux: swap, pressure (PSI) and the route", got["swap_used"] == 500000 * 1024 and got["pressure"] == "warn"
      and got["online"] is True, got)
check("Linux: network speed of the default route's interface (50000 bytes in 1 s)",
      got["iface"] == "eth0" and got["net_in"] == 50000.0 and got["net_out"] == 10000.0, got)

print("a broken subprocess")
t0 = time.time()
check("a command that does not exist: None", S.run(["/nonexistent/tool"]) is None)
check("a command that hangs is given up on at its timeout", S.run(["sleep", "5"], timeout=0.3) is None and time.time() - t0 < 2)
check("a command that fails: None", S.run(["false"]) is None)
fake = S.Sampler(kind="mac", runner=lambda cmd, timeout=0: None, read_ticks=lambda: None)
got = fake.sample(prime=False)
check("every tool failing gives a dict of None (and the time), no exception",
      isinstance(got, dict) and got["t"] and got["cpu"] is None and got["mem_pct"] is None and got["swap_used"] is None, got)
none = S.Sampler(kind="none").sample()
check("a system with no sampler: all None", none["cpu"] is None and none["t"])
check("top on such a system: nothing", S.Sampler(kind="none").top() == [] and S.Sampler(kind="mac", runner=lambda *a, **k: None).top() == [])

print("this computer")
s = S.Sampler()
check("this platform is supported", S.supported() and s.kind in ("mac", "linux"), s.kind)
t0 = time.time()
first = s.sample()
t_first = time.time() - t0
time.sleep(1.0)
t0 = time.time()
full = s.sample()
t_full = time.time() - t0
t0 = time.time()
light = s.sample(full=False)
t_light = time.time() - t0
t0 = time.time()
top = s.top()
t_top = time.time() - t0
print(f"  timings: first sample {t_first * 1000:.0f} ms (includes a {S.PRIME_GAP * 1000:.0f} ms wait), full {t_full * 1000:.0f} ms, "
      f"light {t_light * 1000:.0f} ms, top five {t_top * 1000:.0f} ms")
check("the first sample already has a CPU and a network speed", first["cpu"] is not None and first["net_in"] is not None, first)
check("a full sample takes well under a second", t_full < 1.0, t_full)
check("a light sample is quicker than a full one", t_light <= t_full + 0.02, (t_light, t_full))
check("CPU: a percent, one per core", 0 <= full["cpu"] <= 100 and full["cores"] and all(0 <= c <= 100 for c in full["cores"])
      and abs(sum(full["cores"]) / len(full["cores"]) - full["cpu"]) < 0.1, full["cores"])
if s.kind == "mac" and full["cores_p"]:
    check("Apple Silicon: performance + efficiency cores = the cores read", full["cores_p"] + full["cores_e"] == len(full["cores"]), full)
check("memory: total, used %, parts that add up",
      full["mem_total"] > 1 << 30 and 0 < full["mem_pct"] <= 100 and full["mem_used"] <= full["mem_total"]
      and full["mem_app"] >= 0 and full["mem_wired"] >= 0 and full["mem_compressed"] >= 0 and full["mem_cached"] >= 0, full)
check("swap: used within total", 0 <= full["swap_used"] <= full["swap_total"], full)
check("pressure is a known word", full["pressure"] in ("normal", "warn", "critical"), full["pressure"])
check("network: speeds are not negative, an interface name is given", full["net_in"] >= 0 and full["net_out"] >= 0 and full["iface"], full)
check("route: a bool", isinstance(full["online"], bool), full["online"])
check("disk: free percent within 0..100, free <= total", 0 <= full["disk_free_pct"] <= 100 and full["disk_free"] <= full["disk_total"], full)
check("a light sample has no memory parts and no network speed, but the rules' fields",
      light["mem_pct"] is None and light["net_in"] is None and light["cpu"] is not None and light["pressure"] and light["disk_free_pct"] is not None
      and light["swap_used"] is not None, light)
check("top five: at most five, a name, CPU and memory, no slash in a name", 0 < len(top) <= 5 and all(
    t["name"] and "/" not in t["name"] and t["cpu"] >= 0 and t["rss"] >= 0 and t["count"] >= 1 for t in top), top)
check("sorted by CPU", [t["cpu"] for t in top] == sorted((t["cpu"] for t in top), reverse=True))
check("every key of a sample is there", set(full) == set(S.SAMPLE_KEYS))

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
