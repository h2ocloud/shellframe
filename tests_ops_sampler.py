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
PS2 = """  PID  %CPU    RSS COMM
 1  3.0  1048576 /usr/bin/demo-agent
 2  2.0  1048576 /usr/bin/demo-agent
 3  1.0  1048576 /usr/bin/demo-agent
 4 40.0   204800 /usr/bin/demo-busy
 5  9.0  2097152 /usr/bin/demo-editor
 6  0.0   102400 /usr/bin/demo-idle
"""
by_cpu, by_mem = S.parse_ps(PS2), S.parse_ps(PS2, by="mem")
check("ps by CPU: the busy one first", [t["name"] for t in by_cpu] == ["demo-busy", "demo-editor", "demo-agent", "demo-idle"], by_cpu)
check("ps by memory: the processes of one program are added up (3 x 1 GB beats one 2 GB)",
      [t["name"] for t in by_mem] == ["demo-agent", "demo-editor", "demo-busy", "demo-idle"] and by_mem[0]["rss"] == 3 * 1048576 * 1024
      and by_mem[0]["count"] == 3, by_mem)
check("a tie in memory is broken by CPU, a tie in CPU by memory",
      [t["name"] for t in S.sort_programs([{"name": "a", "cpu": 1, "rss": 5, "count": 1}, {"name": "b", "cpu": 2, "rss": 5, "count": 1}], "mem")] == ["b", "a"]
      and [t["name"] for t in S.sort_programs([{"name": "a", "cpu": 1, "rss": 5, "count": 1}, {"name": "b", "cpu": 1, "rss": 9, "count": 1}])] == ["b", "a"])
ps_calls = []
sm = S.Sampler(kind="mac", runner=lambda cmd, timeout=3: (ps_calls.append(cmd), PS2)[1])
both = sm.top_both(2)
check("top_both: one ps call gives both orders, five (here two) each",
      len(ps_calls) == 1 and ps_calls[0][0] == "ps" and [t["name"] for t in both["cpu"]] == ["demo-busy", "demo-editor"]
      and [t["name"] for t in both["mem"]] == ["demo-agent", "demo-editor"], (ps_calls, both))
check("top(n, by=...) follows the same order", [t["name"] for t in sm.top(1)] == ["demo-busy"] and [t["name"] for t in sm.top(1, "mem")] == ["demo-agent"])
check("top_both where nothing can be read", S.Sampler(kind="none").top_both() == {"cpu": [], "mem": []}
      and S.Sampler(kind="mac", runner=lambda *a, **k: None).top_both() == {"cpu": [], "mem": []})
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

print("temperatures: CPU from a fake sysfs, GPU from nvidia-smi text")


def sysfs(root, hwmon=(), zones=()):
    """A fake /sys: hwmon = [(name, {n: (millidegrees, label or None)})], zones = [(type, millidegrees)]."""
    root = Path(root)
    for i, (name, temps) in enumerate(hwmon):
        d = root / "class" / "hwmon" / f"hwmon{i}"
        d.mkdir(parents=True)
        (d / "name").write_text(name + "\n")
        for n, (v, label) in temps.items():
            (d / f"temp{n}_input").write_text(f"{v}\n")
            if label:
                (d / f"temp{n}_label").write_text(label + "\n")
    for i, (typ, v) in enumerate(zones):
        d = root / "class" / "thermal" / f"thermal_zone{i}"
        d.mkdir(parents=True)
        (d / "type").write_text(typ + "\n")
        (d / "temp").write_text(f"{v}\n")
    return str(root)


def cpu_temp(**kw):
    tmp = tempfile.mkdtemp(prefix="sf-sysfs-")
    sm = S.Sampler(kind="linux", runner=lambda *a, **k: None, sysfs=sysfs(tmp, **kw))
    out = {}
    sm._temps(out, False, 1000.0)
    return out["cpu_temp"], out["cpu_temp_src"]


DGX_ZONES = [("acpitz", 43000), ("acpitz", 44500), ("acpitz", 45800), ("acpitz", 44000), ("acpitz", 43500), ("acpitz", 45000), ("acpitz", 43200)]
DGX_HW = [("acpitz", {1: (45800, None)}), ("nvme", {1: (39850, "Composite")}), ("mt7925_phy0", {1: (42000, None)}), ("acpi_fan", {})]
check("the DGX Spark: only acpitz is a CPU-ish sensor; the hottest zone wins, nvme and Wi-Fi are left out", cpu_temp(hwmon=DGX_HW, zones=DGX_ZONES) == (45.8, "acpitz"),
      cpu_temp(hwmon=DGX_HW, zones=DGX_ZONES))
check("…also from the zones alone", cpu_temp(zones=DGX_ZONES) == (45.8, "acpitz"))
check("a hot nvme or Wi-Fi never becomes the CPU", cpu_temp(hwmon=[("nvme", {1: (88000, "Composite")}), ("mt7925_phy0", {1: (91000, None)}),
                                                                   ("iwlwifi_1", {1: (95000, None)}), ("BAT0", {1: (60000, None)})]) == (None, None))
check("coretemp is preferred over acpitz, and its Package id over the hottest core", cpu_temp(
    hwmon=[("acpitz", {1: (70000, None)}), ("coretemp", {1: (52000, "Package id 0"), 2: (61000, "Core 0"), 3: (58000, "Core 1")})]) == (52.0, "coretemp"))
check("coretemp without a Package label: the hottest core", cpu_temp(hwmon=[("coretemp", {1: (50000, "Core 0"), 2: (64500, "Core 1")})]) == (64.5, "coretemp"))
check("k10temp: Tdie before Tctl (Tctl can carry an offset)", cpu_temp(
    hwmon=[("k10temp", {1: (75000, "Tctl"), 2: (55000, "Tdie"), 3: (50000, "Tccd1")})]) == (55.0, "k10temp"))
check("k10temp with only Tctl: Tctl", cpu_temp(hwmon=[("k10temp", {1: (61000, "Tctl")})]) == (61.0, "k10temp"))
check("an ARM board: cpu_thermal", cpu_temp(hwmon=[("cpu_thermal", {1: (48250, None)})]) == (48.2, "cpu_thermal") or cpu_temp(hwmon=[("cpu_thermal", {1: (48250, None)})]) == (48.3, "cpu_thermal"))
check("a CPU hwmon beats a thermal zone, a CPU zone beats acpitz", cpu_temp(hwmon=[("zenpower", {1: (40000, "Tdie")})], zones=[("x86_pkg_temp", 99000)]) == (40.0, "zenpower")
      and cpu_temp(zones=[("acpitz", 80000), ("x86_pkg_temp", 50000), ("iwlwifi_1", 90000)]) == (50.0, "x86_pkg_temp")
      and cpu_temp(zones=[("cpu-thermal", 47000), ("gpu-thermal", 80000), ("soc-thermal", 52000)]) == (52.0, "soc-thermal"))
check("a nonsense value (0, or over 150 C) is ignored", cpu_temp(hwmon=[("coretemp", {1: (0, "Package id 0"), 2: (300000, "Core 0"), 3: (47000, "Core 1")})]) == (47.0, "coretemp"))
check("no sysfs at all: None, no error", cpu_temp() == (None, None))
bad = S.Sampler(kind="linux", runner=lambda *a, **k: None, sysfs="/nonexistent-sysfs-root")
bo = {}
bad._temps(bo, True, 1.0)
check("a missing /sys leaves None and still says Linux can read one", bo["cpu_temp"] is None and bo["temp_supported"] is True and bo.get("gpus") == [])

opened = []
class Spy(S.Sampler):
    def _read(self, path):
        opened.append(path)
        return S.Sampler._read(path)
spy_root = sysfs(tempfile.mkdtemp(prefix="sf-sysfs-"), hwmon=DGX_HW + [("coretemp", {1: (50000, "Package id 0")})], zones=DGX_ZONES + [("iwlwifi_1", 55000)])
Spy(kind="linux", runner=lambda *a, **k: None, sysfs=spy_root)._temps({}, False, 1.0)
check("the sensors of NVMe and Wi-Fi are not even opened (only a CPU hwmon, acpitz, and a CPU-ish zone are)",
      not any("hwmon1" in o and "temp" in o.rsplit("/", 1)[1] for o in opened) and not any("hwmon2" in o and "temp" in o.rsplit("/", 1)[1] for o in opened)
      and not any("thermal_zone7/temp" in o for o in opened) and any("hwmon0/temp1_input" in o for o in opened), [o.split("sf-sysfs-")[-1] for o in opened][:40])

SMI = "NVIDIA GB10, 43, 0, [N/A], [N/A], 11.03\n"
g = S.parse_nvidia_smi(SMI)
check("nvidia-smi on the DGX Spark: [N/A] memory is None, the rest are numbers", g == [{"name": "NVIDIA GB10", "temp": 43.0, "util": 0.0, "mem_used": None,
                                                                                      "mem_total": None, "power": 11.03}], g)
g2 = S.parse_nvidia_smi("NVIDIA GeForce RTX 4090, 71, 98, 20480, 24564, 312.45\nNVIDIA GeForce RTX 3080, [Not Supported], 5, 1024, 10240, [Not Supported]\n")
check("two cards: MiB become bytes, [Not Supported] is None", len(g2) == 2 and g2[0]["mem_used"] == 20480 * 1048576 and g2[0]["mem_total"] == 24564 * 1048576
      and g2[1]["temp"] is None and g2[1]["power"] is None and g2[1]["util"] == 5.0, g2)
check("a name with a comma stays whole; junk lines are skipped", S.parse_nvidia_smi("Tesla, K80, 60, 10, 100, 200, 90\nnot a gpu\n\n")[0]["name"] == "Tesla, K80"
      and len(S.parse_nvidia_smi("not a gpu\n")) == 1 - 1 and S.parse_nvidia_smi(None) == [] and S.parse_nvidia_smi("") == [])
check("the summary: hottest and the mean utilization; None where no card has one", S.gpu_summary(g2) == (71.0, 51.5) and S.gpu_summary([{"temp": None, "util": None}]) == (None, None)
      and S.gpu_summary([]) == (None, None))

calls = []
clock = [1000.0]
sm = S.Sampler(kind="linux", clock=lambda: clock[0], runner=lambda cmd, timeout=3: (calls.append((cmd, timeout)), SMI if cmd[0] == "nvidia-smi" else None)[1],
               sysfs=sysfs(tempfile.mkdtemp(prefix="sf-sysfs-"), zones=DGX_ZONES))
out = {}
sm._temps(out, True, 1000.0)
check("a full sample asks nvidia-smi with the query, a 5 s timeout; gpus, gpu_temp and gpu_util are set",
      calls and calls[0][0] == ["nvidia-smi", "--query-gpu=name,temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw", "--format=csv,noheader,nounits"]
      and calls[0][1] == 5.0 and out["gpu_temp"] == 43.0 and out["gpu_util"] == 0.0 and out["gpus"][0]["name"] == "NVIDIA GB10" and out["cpu_temp"] == 45.8, (calls, out))
out2 = {}
sm._temps(out2, True, 1002.0)
check("two seconds later (the detail screen): no new nvidia-smi, the answer is reused", len(calls) == 1 and out2["gpu_temp"] == 43.0, calls)
sm._temps({}, True, 1010.5)
check("ten seconds on: asked again", len(calls) == 2)
out3 = {}
sm._temps(out3, False, 1030.0)
check("a light sample does not ask (and carries no GPU)", len(calls) == 2 and out3.get("gpu_temp") is None and out3["cpu_temp"] == 45.8)
sm.watch_gpu = True
out4 = {}
sm._temps(out4, False, 1031.0)
check("…unless a GPU rule is building up: then the 30 s round asks too", len(calls) == 3 and out4["gpu_temp"] == 43.0)
check("macOS: never asks, never reads: temp_supported False", (lambda m, o: (m._temps(o, True, 1.0), o)[1])(
    S.Sampler(kind="mac", runner=lambda *a, **k: calls.append("mac") or None), {}) == {"temp_supported": False} and "mac" not in calls)
wcalls = []
wo = {}
S.Sampler(kind="win", runner=lambda cmd, timeout=3: (wcalls.append(cmd[0]), SMI)[1])._temps(wo, True, 5.0)
check("Windows: no CPU temperature (temp_supported False) but nvidia-smi is read", wo["temp_supported"] is False and wo.get("cpu_temp") is None
      and wcalls == ["nvidia-smi"] and wo["gpu_temp"] == 43.0, wo)
nogpu = {}
S.Sampler(kind="linux", runner=lambda *a, **k: None, sysfs="/nonexistent")._temps(nogpu, True, 1.0)
check("no nvidia-smi: asked and nothing came: gpus is [] (the cards that were there are gone), no temperature, nothing raised", nogpu.get("gpus") == [] and nogpu.get("gpu_temp") is None)
boom = S.Sampler(kind="linux", runner=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")), sysfs="/nonexistent")
check("a runner that raises still gives a sample", boom.sample(full=True, prime=False)["t"] is not None)
check("the sample has the new keys and a Mac says temp_supported False", {"cpu_temp", "cpu_temp_src", "gpus", "gpu_temp", "gpu_util", "temp_supported"} <= set(S.SAMPLE_KEYS))
real_run = S.run
S.run = lambda cmd, timeout=3.0: real_run([sys.executable, "-c", "import time; time.sleep(30)"], 0.3)
t0 = time.time()
slow = {}
S.Sampler(kind="linux", runner=S.run, sysfs="/nonexistent")._temps(slow, True, 1.0)
S.run = real_run
check("a hanging nvidia-smi is cut by its timeout: gpus [], no temperature", time.time() - t0 < 3 and slow.get("gpus") == [] and slow.get("gpu_temp") is None)

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
