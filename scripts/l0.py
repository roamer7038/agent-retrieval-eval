#!/usr/bin/env python3
"""L0: build each tool's index over each corpus in a container with fixed
limits, and measure time, size, memory, failures and the incremental update.

    scripts/l0.py build <tool> [...]              docker build are-l0-base and the tools' images
    scripts/l0.py run <tool> <corpus> [--slot a|b|c] [--timeout 3600] [--variant NAME --env K=V ...]
    scripts/l0.py batch --tools t1,t2 --corpora c1,c2 [--slot a] [--timeout 3600] [--variant NAME --env K=V ...]
    scripts/l0.py env                             print the measuring environment

A tool is a directory docker/tools/<tool>/ with a Dockerfile (FROM
are-l0-base) that installs /opt/l0/run.sh. run.sh takes a phase:

  prepare  optional work that is not the tool's index (e.g. compile_commands.json);
           exit 0 when there is nothing to do
  index    build the index from nothing
  query    print the result of one search for $Q_SYMBOL / $Q_TEXT (a sanity check)
  update   bring the index up to date after the files below were changed
  (exit 3 from any phase means "not applicable to this corpus")

The container sees:

  /work    the corpus ($ARE_DATA/corpora/<corpus>, one directory per repository,
           $REPOS) through an overlay: the tool may write into the tree, the
           writes land in $ARE_DATA/indexes/<tool>/<corpus>/upper and the
           corpus itself is never changed
  /index   $ARE_DATA/indexes/<tool>/<corpus>/index, for indexes kept outside
           the tree; HOME is /index/home
  no network (models are part of the image)

Between index and update, the harness appends a function areL0Probe to one
code file and a paragraph with the word areL0Probe to one document
(CHANGES), then runs query with Q_SYMBOL=areL0Probe to see whether the
update took the change in. One JSON line per run is appended to
results/pe1-l0.jsonl.

A variant (--variant NAME with --env K=V passed to run.sh, e.g.
SERENA_C2_LANGS="go typescript") is recorded as its own row and keeps its
index in $ARE_DATA/indexes/<tool>/<corpus>.<variant>.

Only one measurement runs in a slot at a time: a run waits for the slot's
lock ($ARE_DATA/indexes/.slot-<slot>.lock), and the record keeps what ran in
the other slots when it started and the host's load average before and after.
"""
import argparse
import datetime
import fcntl
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ARE_DATA", os.path.join(ROOT, "data"))
CORPORA = os.path.join(DATA, "corpora")
INDEXES = os.path.join(DATA, "indexes")
TOOLS = os.path.join(ROOT, "docker", "tools")
RESULTS = os.path.join(ROOT, "results", "pe1-l0.jsonl")

# The measuring environment. Each slot is a set of eight vCPUs; runs in
# different slots may go at once, one run per slot. Under WSL2 the vCPUs are
# scheduled by the hypervisor, so a slot is not tied to physical cores. The
# GPU runs use slot c.
CPUS = "8"
MEMORY = "16g"
SLOTS = {"a": "0-7", "b": "8-15", "c": "16-23"}

GO_FUNC = "\nfunc areL0Probe() int { return 42 }\n"
C_FUNC = "\nint areL0Probe(void) { return 42; }\n"
DOC_PARA = "\n\nareL0Probe: a paragraph added by the L0 update test.\n"
CHANGES = {
    "c1": [("wikictl/internal/page/links.go", GO_FUNC), ("wiki/global/git-force-with-lease.md", DOC_PARA)],
    "c2": [("grafana/pkg/services/dashboards/dashboard.go", GO_FUNC), ("grafana/docs/sources/_index.md", DOC_PARA)],
    "c3": [("linux/kernel/fork.c", C_FUNC), ("linux/Documentation/admin-guide/README.rst", DOC_PARA)],
    "c4": [("website/content/en/docs/concepts/overview/_index.md", DOC_PARA),
           ("website/content/ja/docs/concepts/overview/_index.md", DOC_PARA)],
}
# One search per corpus for the sanity check: an identifier defined in the
# code (or a heading word of the documents) and a phrase.
QUERIES = {
    "c1": ("BacklinkCandidates", "frontmatter"),
    "c2": ("ProvideDashboardServiceImpl", "dashboard provisioning"),
    "c3": ("copy_process", "memory allocation"),
    "c4": ("PodSecurity", "pod security admission"),
}


def sh(*args, **kw):
    return subprocess.run(list(args), check=True, capture_output=True, text=True, **kw).stdout.strip()


def repos(corpus):
    with open(os.path.join(ROOT, "corpora", "corpora.yaml")) as f:
        return list(yaml.safe_load(f)[corpus]["repos"])


def du(path):
    if not os.path.exists(path):
        return 0
    return int(sh("du", "-sb", path).split()[0])


def image(tool):
    return f"are-l0-{tool}:pe1"


def build(tools):
    sh("docker", "build", "-q", "-t", "are-l0-base:pe1", os.path.join(TOOLS, "base"))
    for t in tools:
        print(f"building {t}", file=sys.stderr)
        subprocess.run(["docker", "build", "-t", image(t), os.path.join(TOOLS, t)], check=True)


def environment():
    gpu = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip()
    cpu = next((l.split(":", 1)[1].strip() for l in open("/proc/cpuinfo") if l.startswith("model name")), "")
    mem = next(l.split()[1] for l in open("/proc/meminfo") if l.startswith("MemTotal"))
    return {
        "host": platform.node(), "kernel": platform.release(), "cpu": cpu, "host_threads": os.cpu_count(),
        "host_mem_kib": int(mem), "gpu": gpu, "docker": sh("docker", "version", "-f", "{{.Server.Version}}"),
        "limit_cpus": CPUS, "limit_memory": MEMORY, "swap": "none (memory-swap = memory)",
    }


def docker_run(tool, corpus, vol, idx, phase, slot, timeout, gpu, extra_env=None, label=None, memory=MEMORY):
    label = label or phase
    env = {"CORPUS": corpus, "REPOS": " ".join(repos(corpus)), "HOME": "/index/home", "L0_TIMEOUT": str(timeout),
           "Q_SYMBOL": QUERIES[corpus][0], "Q_TEXT": QUERIES[corpus][1], "L0_LABEL": label,
           # Tools that size their worker pool by the host's CPU count (24) are
           # told the container's limit.
           "GRAPHIFY_MAX_WORKERS": CPUS, "CBM_WORKERS": CPUS}
    env.update(extra_env or {})
    cmd = ["docker", "run", "--rm", "--network", "none", "--cpus", CPUS, "--cpuset-cpus", SLOTS[slot],
           "--memory", memory, "--memory-swap", memory, "-v", f"{vol}:/work", "-v", f"{idx}:/index"]
    if gpu:
        cmd += ["--gpus", "all"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [image(tool), phase]
    log = os.path.join(idx, "l0", f"{label}.log")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    t0 = time.monotonic()
    with open(log, "w") as f:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=f, text=True, timeout=timeout + 600)
    out = p.stdout.rstrip("\n").split("\n")
    rec = {}
    if out and out[-1].startswith("{"):
        rec = json.loads(out[-1])
        out = out[:-1]
    else:
        rec = {"phase": phase, "exit": None, "error": f"no record (docker exit {p.returncode})"}
    rec["docker_wall_s"] = round(time.monotonic() - t0, 2)
    with open(os.path.join(idx, "l0", f"{label}.out"), "w") as f:
        f.write("\n".join(out) + "\n")
    rec["out_lines"] = len([l for l in out if l.strip()])
    rec["out_bytes"] = sum(len(l) + 1 for l in out)
    with open(log) as f:
        rec["stderr_tail"] = f.read()[-600:]
    return rec, out


def apply_changes(corpus, vol):
    for path, text in CHANGES[corpus]:
        subprocess.run(["docker", "run", "--rm", "--network", "none", "-u", "1000:1000", "-v", f"{vol}:/work",
                        "--entrypoint", "python3", "are-l0-base:pe1", "-c",
                        "import sys; open(sys.argv[1], 'a').write(sys.argv[2])", f"/work/{path}", text], check=True)


def tool_version(tool):
    return sh("docker", "image", "inspect", "-f", '{{index .Config.Labels "l0.version"}}', image(tool))


def slot_file(slot, ext):
    return os.path.join(INDEXES, f".slot-{slot}.{ext}")


def others_running(slot):
    out = {}
    for s in SLOTS:
        if s == slot:
            continue
        try:
            with open(slot_file(s, "running")) as f:
                out[s] = f.read().strip()
        except OSError:
            pass
    return out


def run(args):
    os.makedirs(INDEXES, exist_ok=True)
    gpu = os.path.exists(os.path.join(TOOLS, args.tool, "gpu"))
    slot = args.slot or ("c" if gpu else "a")
    with open(slot_file(slot, "lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"waiting for slot {slot}", file=sys.stderr, flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        name = f"{args.tool}{'.' + args.variant if args.variant else ''} {args.corpus}"
        with open(slot_file(slot, "running"), "w") as f:
            f.write(f"{name} since {datetime.datetime.now().astimezone().isoformat(timespec='seconds')}\n")
        try:
            return run_locked(args, slot, gpu)
        finally:
            os.remove(slot_file(slot, "running"))


def run_locked(args, slot, gpu):
    tool, corpus = args.tool, args.corpus
    extra = dict(kv.split("=", 1) for kv in args.env)
    base = os.path.join(INDEXES, tool, corpus + (f".{args.variant}" if args.variant else ""))
    upper, work, idx = (os.path.join(base, d) for d in ("upper", "ovl", "index"))
    # A docker volume name takes only [a-zA-Z0-9][a-zA-Z0-9_.-]*.
    vol = re.sub(r"[^A-Za-z0-9_.-]", "-", f"are-l0-{tool}-{corpus}" + (f"-{args.variant}" if args.variant else ""))
    subprocess.run(["docker", "volume", "rm", "-f", vol], capture_output=True)
    if os.path.exists(base):
        # overlay's work dir holds a root-owned directory; remove it in a container.
        subprocess.run(["docker", "run", "--rm", "-u", "0", "-v", f"{base}:/b", "--entrypoint", "rm", "are-l0-base:pe1",
                        "-rf", "/b/upper", "/b/ovl", "/b/index"], check=True)
    for d in (upper, work, os.path.join(idx, "home")):
        os.makedirs(d)
    sh("docker", "volume", "create", "--driver", "local", "--opt", "type=overlay", "--opt", "device=overlay",
       "--opt", f"o=lowerdir={os.path.join(CORPORA, corpus)},upperdir={upper},workdir={work}", vol)
    def size():
        return du(upper) + du(idx) - du(os.path.join(idx, "l0"))

    rec = {"tool": tool, "version": tool_version(tool), "corpus": corpus, "slot": slot, "gpu": gpu,
           "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds"), "env": environment(),
           "timeout_s": args.timeout, "memory": args.memory, "others_running_at_start": others_running(slot),
           "loadavg_start": os.getloadavg(), "phases": {}}
    if args.variant:
        rec["variant"] = args.variant
        rec["variant_env"] = extra
    try:
        phases = rec["phases"]
        for phase in ("prepare", "index"):
            r, _ = docker_run(tool, corpus, vol, idx, phase, slot, args.timeout, gpu, extra, memory=args.memory)
            phases[phase] = r
            if r.get("exit") == 3:
                rec["status"] = "n/a"
                return rec
            if r.get("exit") != 0:
                rec["status"] = "timeout" if r.get("timed_out") else "failed"
                rec["failed_phase"] = phase
                return rec
            if phase == "prepare":
                rec["size_after_prepare_bytes"] = size()
        rec["size_upper_bytes"] = du(upper)
        rec["size_index_bytes"] = du(idx) - du(os.path.join(idx, "l0"))
        # What the index phase added: the tree's writes and /index, less the
        # measuring logs and what prepare had written.
        rec["size_bytes"] = size() - rec["size_after_prepare_bytes"]
        r, out = docker_run(tool, corpus, vol, idx, "query", slot, 600, gpu, extra, memory=args.memory)
        phases["query"] = r
        r["hit_symbol"] = any(QUERIES[corpus][0] in l for l in out)
        apply_changes(corpus, vol)
        r, _ = docker_run(tool, corpus, vol, idx, "update", slot, args.timeout, gpu, extra, memory=args.memory)
        phases["update"] = r
        r, out = docker_run(tool, corpus, vol, idx, "query", slot, 600, gpu, {**extra, "Q_SYMBOL": "areL0Probe", "Q_TEXT": "areL0Probe"}, "query_after_update", memory=args.memory)
        r["hit_symbol"] = any("areL0Probe" in l for l in out)
        phases["query_after_update"] = r
        rec["status"] = "ok"
        return rec
    finally:
        subprocess.run(["docker", "volume", "rm", "-f", vol], capture_output=True)
        rec["loadavg_end"] = os.getloadavg()
        if not args.dry:
            os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
            with open(RESULTS, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        summary = {k: rec.get(k) for k in ("tool", "variant", "corpus", "status", "failed_phase", "size_bytes")}
        summary["hits"] = [rec["phases"].get(p, {}).get("hit_symbol") for p in ("query", "query_after_update")]
        summary.update({p: (v.get("exit"), v.get("wall_s"), v.get("mem_anon_peak_bytes")) for p, v in rec["phases"].items()})
        print(json.dumps(summary), flush=True)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("tools", nargs="*")
    r = sub.add_parser("run")
    r.add_argument("tool")
    r.add_argument("corpus")
    r.add_argument("--slot", choices=SLOTS)
    r.add_argument("--timeout", type=int, default=3600)
    r.add_argument("--memory", default=MEMORY, help=f"the container's memory limit (default {MEMORY})")
    r.add_argument("--variant", help="name of a variant of the tool's settings (a row of its own)")
    r.add_argument("--env", action="append", default=[], metavar="K=V", help="environment for run.sh (with --variant)")
    r.add_argument("--dry", action="store_true", help="do not append to results/pe1-l0.jsonl")
    bt = sub.add_parser("batch")
    bt.add_argument("--tools", required=True)
    bt.add_argument("--corpora", default="c1,c2,c4,c3")
    bt.add_argument("--slot", choices=SLOTS)
    bt.add_argument("--timeout", type=int, default=3600)
    bt.add_argument("--memory", default=MEMORY, help=f"the container's memory limit (default {MEMORY})")
    bt.add_argument("--variant", help="name of a variant of the tool's settings (a row of its own)")
    bt.add_argument("--env", action="append", default=[], metavar="K=V", help="environment for run.sh (with --variant)")
    bt.add_argument("--dry", action="store_true")
    sub.add_parser("env")
    args = ap.parse_args()
    if args.cmd == "build":
        build(args.tools)
    elif args.cmd == "run":
        run(args)
    elif args.cmd == "batch":
        for t in args.tools.split(","):
            for c in args.corpora.split(","):
                args.tool, args.corpus = t, c
                try:
                    run(args)
                except Exception as e:  # keep going; the record says what failed
                    print(json.dumps({"tool": t, "corpus": c, "error": repr(e)}), flush=True)
    else:
        print(json.dumps(environment(), indent=1))


if __name__ == "__main__":
    main()
