"""What this computer is doing right now: CPU per core, memory, swap, memory pressure, network speed,
whether there is a route out, free disk, and the five programs using the most (the office's ops TV).

Leaf module: stdlib, `wake_watch.memory_pressure` and nothing else. No `import main`, no network, no
psutil, and no `top` (0.8 s of CPU per call). Read only: nothing here writes, and nothing reads a
file's content or a process's arguments (a program is its basename only).

Platforms
  macOS   CPU per core: `host_processor_info` through ctypes (two readings subtracted; the same call
          Activity Monitor uses, about 0.01 ms). Memory: `vm_stat` + `sysctl hw.memsize`. Swap:
          `sysctl vm.swapusage`. Pressure: `wake_watch.memory_pressure()`. Network: `netstat -ibn` twice (`-n` matters: without it netstat looks up host names when its output is a pipe, and waited 5 s).
          Route: `route -n get default`. Top five: `ps -Ao pid,pcpu,rss,comm -r`.
  Linux   /proc/stat, /proc/meminfo, /proc/pressure/memory, /proc/net/dev, /proc/net/route, `ps`.
  other   (Windows) `supported()` is False and the collector reports level "none".

Every subprocess has a timeout; any failure leaves that field None and never raises. A sample is a
flat dict (see SAMPLE_KEYS); a field that could not be read is None. `Sampler.sample(full=False)`
reads only what the rules need (CPU, pressure, swap, disk, route: no `vm_stat`, no `netstat`) and is
what the 30-second watch uses.
"""
import ctypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time

import wake_watch

TIMEOUT = 3.0
PRIME_GAP = 0.25            # the first sample reads CPU and network twice this far apart, so it has a speed

SAMPLE_KEYS = ("t", "cpu", "cores", "cores_p", "cores_e", "mem_total", "mem_used", "mem_pct", "mem_app",
               "mem_wired", "mem_compressed", "mem_cached", "swap_total", "swap_used", "pressure",
               "net_in", "net_out", "iface", "online", "disk_total", "disk_free", "disk_free_pct")


def platform_kind() -> str:
    if sys.platform == "darwin":
        return "mac"
    if sys.platform.startswith("linux"):
        return "linux"
    return "none"


def supported() -> bool:
    return platform_kind() != "none"


def run(cmd, timeout=TIMEOUT):
    """stdout of `cmd` as text, or None (not found, timeout, non-zero exit). The C locale keeps numbers plain."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors="replace", stdin=subprocess.DEVNULL,
                           env=dict(os.environ, LC_ALL="C", LANG="C"))
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    return p.stdout if p.returncode == 0 else None


def _num(text, default=None):
    try:
        return float(text)
    except (TypeError, ValueError):
        return default


# ── pure parsers (tested on fixed text) ───────────────────────────────────────
def parse_vm_stat(text) -> dict:
    """`vm_stat` → {"page": bytes, "<field>": pages}. Missing fields are absent."""
    out = {}
    m = re.search(r"page size of (\d+) bytes", text or "")
    out["page"] = int(m.group(1)) if m else 16384
    for line in (text or "").splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            v = val.strip().rstrip(".")
            if v.isdigit():
                out[key.strip().strip('"').lower()] = int(v)
    return out


def memory_parts(vm: dict, total: int) -> dict:
    """Bytes for the four parts of the memory bar, as Activity Monitor draws them:
      App         = anonymous (internal) pages - purgeable pages      (what programs hold)
      Wired       = pages wired down                                  (the kernel's, cannot be paged out)
      Compressed  = pages occupied by the compressor                  (what the compressor's output takes)
      Cached      = file-backed pages + purgeable pages               (a program can give it back)
    Memory used = App + Wired + Compressed; the rest of the total is cached or free. This is the usual
    reading of how Activity Monitor builds "Memory Used"; the numbers were NOT compared side by side with
    Activity Monitor, so treat the split as an estimate."""
    page = vm.get("page", 16384)
    purge = vm.get("pages purgeable", 0)
    app = max(0, vm.get("anonymous pages", 0) - purge) * page
    wired = vm.get("pages wired down", 0) * page
    comp = vm.get("pages occupied by compressor", 0) * page
    cached = (vm.get("file-backed pages", 0) + purge) * page
    used = min(total, app + wired + comp) if total else app + wired + comp
    return {"app": app, "wired": wired, "compressed": comp, "cached": cached, "used": used}


_UNIT = {"K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


def parse_swapusage(text):
    """`vm.swapusage`: `total = 11264.00M  used = 9763.69M  free = 1500.31M  (encrypted)` → (total, used) bytes."""
    got = {}
    for key, val, unit in re.findall(r"(total|used|free)\s*=\s*([\d.]+)\s*([KMGT])", text or ""):
        got[key] = int(float(val) * _UNIT[unit])
    if "total" in got and "used" in got:
        return got["total"], got["used"]
    return None


def parse_netstat_ib(text) -> dict:
    """`netstat -ib` → {iface: (in_bytes, out_bytes)} from each interface's `<Link#n>` row (the row that
    has the interface's whole count). The Address column is empty for some interfaces, so the counters
    are taken from the right: ... Ibytes Opkts Oerrs Obytes Coll."""
    out = {}
    for line in (text or "").splitlines()[1:]:
        f = line.split()
        if len(f) < 10 or not f[2].startswith("<Link#"):
            continue
        try:
            out[f[0].rstrip("*")] = (int(f[-5]), int(f[-2]))
        except ValueError:
            continue
    return out


def parse_route_get_default(text):
    """`route -n get default` → the interface name, or ''."""
    m = re.search(r"^\s*interface:\s*(\S+)", text or "", re.M)
    return m.group(1) if m else ""


def parse_ps(text, own=("ps",)) -> list:
    """`ps -Ao pid,pcpu,rss,comm` → [{"name", "cpu", "rss", "count"}] by program name (the basename
    of `comm`, nothing else), several processes of one program added up, biggest CPU first."""
    agg = {}
    for line in (text or "").splitlines()[1:]:
        f = line.strip().split(None, 3)
        if len(f) < 4:
            continue
        cpu, rss = _num(f[1]), _num(f[2])
        if cpu is None or rss is None:
            continue
        name = os.path.basename(f[3].strip()) or f[3].strip()
        if name in own:
            continue
        a = agg.setdefault(name, {"name": name, "cpu": 0.0, "rss": 0, "count": 0})
        a["cpu"] += cpu
        a["rss"] += int(rss) * 1024
        a["count"] += 1
    return sorted(agg.values(), key=lambda a: (-a["cpu"], -a["rss"]))


def parse_proc_stat(text) -> list:
    """/proc/stat → per core (busy, 0, idle, 0) ticks, the layout `cpu_usage` takes. Idle = idle + iowait."""
    out = []
    for line in (text or "").splitlines():
        if re.match(r"cpu\d+\s", line):
            v = [int(x) for x in line.split()[1:9] if x.isdigit()]
            if len(v) >= 4:
                idle = v[3] + (v[4] if len(v) > 4 else 0)
                out.append((sum(v) - idle, 0, idle, 0))
    return out


def parse_meminfo(text) -> dict:
    out = {}
    for line in (text or "").splitlines():
        m = re.match(r"(\w+):\s+(\d+)\s*kB", line)
        if m:
            out[m.group(1)] = int(m.group(2)) * 1024
    return out


def parse_psi_memory(text):
    """/proc/pressure/memory → normal / warn / critical. Not Apple's levels: `some` is the share of
    the last 10 s in which a task waited for memory. 10 % → warn; `full` (everything waited) 5 % or
    `some` 40 % → critical. None when the file has no such lines."""
    some = full = None
    for line in (text or "").splitlines():
        m = re.match(r"(some|full)\s.*avg10=([\d.]+)", line)
        if m:
            if m.group(1) == "some":
                some = _num(m.group(2))
            else:
                full = _num(m.group(2))
    if some is None:
        return None
    if (full or 0) >= 5 or some >= 40:
        return "critical"
    return "warn" if some >= 10 else "normal"


def parse_proc_net_dev(text) -> dict:
    out = {}
    for line in (text or "").splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 9 and f[0].isdigit() and f[8].isdigit():
            out[name.strip()] = (int(f[0]), int(f[8]))
    return out


def parse_proc_net_route(text):
    """/proc/net/route → (online, default interface). A default route (destination 0) that is up."""
    seen = False
    for line in (text or "").splitlines()[1:]:
        f = line.split()
        if len(f) >= 4 and f[1] == "00000000":
            seen = True
            try:
                if int(f[3], 16) & 1:
                    return True, f[0]
            except ValueError:
                continue
    return (False, "") if seen or text else (None, "")


def cpu_usage(prev, cur) -> list:
    """Per core percent from two readings of per core (user, system, idle, nice) ticks. Each state is
    subtracted on its own, modulo 2**32 (Apple's counters are 32 bit and wrap)."""
    out = []
    for a, b in zip(prev or [], cur or []):
        d = [(y - x) & 0xFFFFFFFF for x, y in zip(a, b)]
        total = sum(d)
        out.append(round(max(0.0, min(100.0, 100.0 * (total - d[2]) / total)), 1) if total > 0 else 0.0)
    return out


# ── macOS: host_processor_info ────────────────────────────────────────────────
_PROCESSOR_CPU_LOAD_INFO = 2        # flavor: per cpu {user, system, idle, nice} ticks, 32-bit counters
_mach = {}


def _mach_lib():
    if "lib" not in _mach:
        lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        lib.mach_host_self.restype = ctypes.c_uint
        lib.host_processor_info.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint),
                                            ctypes.POINTER(ctypes.POINTER(ctypes.c_int)),
                                            ctypes.POINTER(ctypes.c_uint)]
        lib.host_processor_info.restype = ctypes.c_int
        lib.vm_deallocate.argtypes = [ctypes.c_uint, ctypes.c_void_p, ctypes.c_size_t]
        lib.vm_deallocate.restype = ctypes.c_int
        _mach["lib"] = lib
        _mach["task"] = ctypes.c_uint.in_dll(lib, "mach_task_self_")
    return _mach["lib"], _mach["task"]


def read_cpu_ticks_mac():
    """Per core (user, system, idle, nice) ticks since boot, or None. Apple's counters are 32 bit and wrap;
    `cpu_usage` subtracts modulo 2**32."""
    try:
        lib, task = _mach_lib()
        count, info, n = ctypes.c_uint(), ctypes.POINTER(ctypes.c_int)(), ctypes.c_uint()
        if lib.host_processor_info(lib.mach_host_self(), _PROCESSOR_CPU_LOAD_INFO, ctypes.byref(count),
                                   ctypes.byref(info), ctypes.byref(n)) != 0:
            return None
        try:
            out = [tuple(info[i * 4 + j] & 0xFFFFFFFF for j in range(4)) for i in range(count.value)]
        finally:
            lib.vm_deallocate(task.value, ctypes.cast(info, ctypes.c_void_p), n.value * 4)
        return out
    except Exception:
        return None


def _mac_core_layout():
    """(performance cores, efficiency cores) on Apple Silicon, else (0, 0). On the one Apple M4 this was
    tried on, the efficiency cores were the first logical CPUs and the performance cores the last ones
    (seen under load, once); other Apple Silicon parts were not checked."""
    p = _num((run(["sysctl", "-n", "hw.perflevel0.logicalcpu"], 2) or "").strip())
    e = _num((run(["sysctl", "-n", "hw.perflevel1.logicalcpu"], 2) or "").strip())
    return (int(p), int(e)) if p and e is not None else (0, 0)


class Sampler:
    """Reads the computer. Keeps the previous CPU and network readings so each sample is a speed over
    the time since the last one. Not thread safe: one collector thread uses it."""

    def __init__(self, kind=None, clock=time.time, runner=None, read_ticks=None):
        self.kind = kind or platform_kind()
        self._clock = clock
        self._run = runner or run
        self._read_ticks = read_ticks
        self._prev_cpu = None          # (t, ticks)
        self._prev_net = None          # (t, iface, in, out)
        self._static = None            # memsize and core layout: do not change while the app runs

    # ── helpers ──
    def _ticks(self):
        if self._read_ticks:
            return self._read_ticks()
        if self.kind == "mac":
            return read_cpu_ticks_mac()
        return parse_proc_stat(self._read("/proc/stat"))

    @staticmethod
    def _read(path):
        """A /proc file's text, or None (a test replaces this to feed a Linux computer's files)."""
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError:
            return None

    def _cpu(self, now):
        cur = self._ticks()
        if not cur:
            return None
        prev = self._prev_cpu
        self._prev_cpu = (now, cur)
        if prev is None or len(prev[1]) != len(cur):
            return None
        return cpu_usage(prev[1], cur)

    def _statics(self):
        if self._static is None:
            total, p, e = None, 0, 0
            if self.kind == "mac":
                total = _num((self._run(["sysctl", "-n", "hw.memsize"], 2) or "").strip())
                if self._run is run:
                    p, e = _mac_core_layout()
            self._static = (int(total) if total else None, p, e)
        return self._static

    def _mac_memory(self, out):
        total, _, _ = self._statics()
        vm = parse_vm_stat(self._run(["vm_stat"], 2))
        if total and vm.get("pages wired down") is not None:
            parts = memory_parts(vm, total)
            out.update(mem_total=total, mem_used=parts["used"], mem_pct=round(100.0 * parts["used"] / total, 1),
                       mem_app=parts["app"], mem_wired=parts["wired"], mem_compressed=parts["compressed"],
                       mem_cached=parts["cached"])

    def _linux_memory(self, out):
        info = parse_meminfo(self._read("/proc/meminfo"))
        total, avail = info.get("MemTotal"), info.get("MemAvailable")
        if total and avail is not None:
            used = total - avail
            cached = info.get("Cached", 0) + info.get("Buffers", 0) + info.get("SReclaimable", 0)
            out.update(mem_total=total, mem_used=used, mem_pct=round(100.0 * used / total, 1), mem_app=used,
                       mem_wired=0, mem_compressed=0, mem_cached=cached)

    def _swap(self, out):
        if self.kind == "mac":
            got = parse_swapusage(self._run(["sysctl", "-n", "vm.swapusage"], 2))
            if got:
                out.update(swap_total=got[0], swap_used=got[1])
        else:
            info = parse_meminfo(self._read("/proc/meminfo"))
            if "SwapTotal" in info:
                out.update(swap_total=info["SwapTotal"], swap_used=info["SwapTotal"] - info.get("SwapFree", 0))

    def _pressure(self, out):
        if self.kind == "mac":
            level = wake_watch.memory_pressure()
            out["pressure"] = level if level in ("normal", "warn", "critical") else None
        else:
            level = parse_psi_memory(self._read("/proc/pressure/memory"))
            if level is None and out.get("mem_total"):        # a kernel without PSI: free memory decides
                left = 100.0 - (out.get("mem_pct") or 0)
                level = "critical" if left < 5 else "warn" if left < 10 else "normal"
            out["pressure"] = level

    def _route(self):
        """(online, interface). macOS: `route -n get default` exits 0 with a route; "not in table" (exit 1) is
        offline; anything else (timeout, no tool) is unknown (None). That the offline reply is "not in table" is
        the BSD route(8) wording, not something this Mac was made to show."""
        if self.kind == "mac":
            try:
                p = subprocess.run(["route", "-n", "get", "default"], capture_output=True, text=True, timeout=2,
                                   stdin=subprocess.DEVNULL,
                                   env=dict(os.environ, LC_ALL="C"))
            except (subprocess.SubprocessError, OSError):
                return None, ""
            if p.returncode == 0:
                return True, parse_route_get_default(p.stdout)
            return (False, "") if "not in table" in (p.stdout + p.stderr) else (None, "")
        return parse_proc_net_route(self._read("/proc/net/route"))

    def _net_counters(self):
        if self.kind == "mac":
            return parse_netstat_ib(self._run(["netstat", "-ibn"], 3))
        return parse_proc_net_dev(self._read("/proc/net/dev"))

    def _network(self, out, iface, now):
        """Speed of the default route's interface; with no route, of all real interfaces added up (lo, utun,
        bridges and so on left out). A changed interface gives no speed for that sample."""
        counters = self._net_counters()
        if not counters:
            return

        def pick(c):
            if iface and iface in c:
                return iface, c[iface]
            real = [v for k, v in c.items() if re.match(r"(en|eth|wl|ww)", k)]
            return (iface or "*"), (sum(v[0] for v in real), sum(v[1] for v in real)) if real else None

        name, cur = pick(counters)
        if cur is None:
            return
        out["iface"] = name
        prev = self._prev_net
        self._prev_net = (now, name, cur[0], cur[1])
        if prev and prev[1] == name and now > prev[0] and cur[0] >= prev[2] and cur[1] >= prev[3]:
            dt = now - prev[0]
            out["net_in"] = round((cur[0] - prev[2]) / dt, 1)
            out["net_out"] = round((cur[1] - prev[3]) / dt, 1)

    # ── one sample ──
    def sample(self, full=True, prime=True) -> dict:
        """A sample dict (SAMPLE_KEYS). `full=False` skips memory parts and network speed."""
        out = dict.fromkeys(SAMPLE_KEYS)
        if self.kind == "none":
            out["t"] = self._clock()
            return out
        if prime and self._prev_cpu is None:         # the very first sample: read once, wait a moment, read again
            self.sample(full, prime=False)
            time.sleep(PRIME_GAP)
        now = self._clock()
        out["t"] = now
        _, p, e = self._statics()
        try:
            cores = self._cpu(now)
            if cores:
                out["cores"] = cores
                out["cpu"] = round(sum(cores) / len(cores), 1)
                if p and e and p + e == len(cores):
                    out["cores_p"], out["cores_e"] = p, e
        except Exception:
            pass
        for step in ((self._mac_memory if self.kind == "mac" else self._linux_memory) if full else None,
                     self._swap):
            if step:
                try:
                    step(out)
                except Exception:
                    pass
        try:
            self._pressure(out)
        except Exception:
            pass
        try:
            du = shutil.disk_usage("/")
            total, free = du.total, du.free
            out.update(disk_total=total, disk_free=free, disk_free_pct=round(100.0 * free / total, 1) if total else None)
        except OSError:
            pass
        online, iface = None, ""
        try:
            online, iface = self._route()
            out["online"] = online
        except Exception:
            pass
        if full:
            try:
                self._network(out, iface, now)
            except Exception:
                pass
        return out

    def top(self, n=5) -> list:
        """The `n` programs using the most CPU: [{"name", "cpu", "rss", "count"}]. [] when it cannot be read."""
        if self.kind == "mac":
            text = self._run(["ps", "-Ao", "pid,pcpu,rss,comm", "-r"], 3)
        elif self.kind == "linux":
            text = self._run(["ps", "-Ao", "pid,pcpu,rss,comm", "--sort=-pcpu"], 3)
        else:
            return []
        return parse_ps(text)[:n]
