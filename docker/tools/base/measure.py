#!/usr/bin/env python3
"""Run one phase of a tool's /opt/l0/run.sh and measure it.

    measure.py <phase> [args ...]      phase: prepare | index | update | query

The container is the unit of measurement: memory is read from its cgroup
(anonymous memory, i.e. without the page cache of the files read, sampled
every 0.2 s, plus the kernel's memory.peak which includes the cache), CPU
time from cpu.stat, GPU memory from nvidia-smi when the container has a GPU.
The phase is killed with its process group after L0_TIMEOUT seconds
(default 3600). One JSON line is printed last on stdout and written to
/index/l0/<$L0_LABEL or phase>.json.
"""
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import threading
import time

CG = "/sys/fs/cgroup"


def read_kv(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                k, _, v = line.partition(" ")
                out[k] = int(v)
    except OSError:
        pass
    return out


def read_int(path):
    try:
        with open(path) as f:
            return int(f.read().split()[0])
    except (OSError, ValueError):
        return None


class Sampler(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.stop = threading.Event()
        self.anon_peak = 0
        self.current_peak = 0
        self.gpu_peak = None
        # /dev/nvidiactl on Linux, /dev/dxg under WSL2.
        has_gpu = os.path.exists("/dev/nvidiactl") or os.path.exists("/dev/dxg")
        self.gpu = shutil.which("nvidia-smi") if has_gpu else None

    def gpu_used(self):
        try:
            out = subprocess.run([self.gpu, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=5).stdout
            return sum(int(x) for x in out.split()) * 1024 * 1024
        except Exception:
            return None

    def run(self):
        n = 0
        while not self.stop.is_set():
            st = read_kv(f"{CG}/memory.stat")
            self.anon_peak = max(self.anon_peak, st.get("anon", 0))
            self.current_peak = max(self.current_peak, read_int(f"{CG}/memory.current") or 0)
            if self.gpu and n % 5 == 0:
                g = self.gpu_used()
                if g is not None:
                    self.gpu_peak = max(self.gpu_peak or 0, g)
            n += 1
            self.stop.wait(0.2)


def main():
    phase = sys.argv[1]
    limit = int(os.environ.get("L0_TIMEOUT", 3600))
    os.makedirs("/index/l0", exist_ok=True)
    cpu0 = read_kv(f"{CG}/cpu.stat").get("usage_usec", 0)
    gpu_base = None
    s = Sampler()
    if s.gpu:
        gpu_base = s.gpu_used()
    s.start()
    t0 = time.monotonic()
    started = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    p = subprocess.Popen(["/opt/l0/run.sh", *sys.argv[1:]], start_new_session=True)
    timed_out = False
    try:
        rc = p.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        timed_out = True
        os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
        rc = p.returncode
    wall = time.monotonic() - t0
    s.stop.set()
    s.join()
    cpu1 = read_kv(f"{CG}/cpu.stat").get("usage_usec", 0)
    rec = {
        "phase": phase,
        "started": started,
        "wall_s": round(wall, 2),
        "cpu_s": round((cpu1 - cpu0) / 1e6, 2),
        "exit": rc,
        "timed_out": timed_out,
        "timeout_s": limit,
        "mem_anon_peak_bytes": s.anon_peak,
        "mem_cgroup_peak_bytes": read_int(f"{CG}/memory.peak"),
        "mem_current_peak_bytes": s.current_peak,
        "maxrss_child_bytes": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss * 1024,
        "oom_kill": read_kv(f"{CG}/memory.events").get("oom_kill"),
        "gpu_mem_peak_bytes": s.gpu_peak,
        "gpu_mem_base_bytes": gpu_base,
    }
    line = json.dumps(rec)
    with open(f"/index/l0/{os.environ.get('L0_LABEL', phase)}.json", "w") as f:
        f.write(line + "\n")
    print(line, flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
