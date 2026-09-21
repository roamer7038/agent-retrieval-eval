#!/usr/bin/env python3
"""L1: one search per (tool, corpus, question, query form), with the tool
resident.

    l1/run.py start <tool> <corpus>        bring up the tool's container
    l1/run.py sh <tool> <corpus> [cmd...]  a shell in a running container
    l1/run.py stop [<tool> <corpus>]       stop it (no argument: all of L1's)
    l1/run.py ps                           what is up
    l1/run.py run --tools t1,t2 --corpora c1,c2 [--langs ja,en] [--slot a]
    l1/run.py plan                         the (tool, corpus) cells and why

The container of a cell (tool, corpus) is started once, kept alive, and asked
one question at a time through a worker (l1/worker.py, mounted at /opt/l1).
Two times are recorded:

  startup_s  bringing the container up and the tool's resident part with it
             (Serena's language servers, the MCP server of
             codebase-memory-mcp). Reported apart from the searches.
  t_s        the search itself, measured inside the container around the
             tool's own call, so that neither `docker exec` nor the worker's
             own parsing is counted. exec_s (outside) is kept as well.

The index of a cell is the one PE1 (L0) built, reused as it stands:
$ARE_DATA/indexes/<tool>/<corpus>{,.<variant>}. The overlay is remounted on
the same upper directory, so the indexes that a tool keeps inside the tree
(codegraph, ck, gitnexus, graphify, Serena) are there. Those indexes also
hold L0's update test (a function areL0Probe appended to one code file and a
paragraph to one document): two files of the corpus differ from the pinned
commit by that much, in every cell alike.

Only one cell runs at a time in a slot ($ARE_DATA/indexes/.slot-<slot>.lock,
the same lock L0 used), so a search is never timed against another search.
"""
import argparse
import datetime
import fcntl
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks as T  # noqa: E402

ROOT = T.ROOT
DATA = T.DATA
CORPORA = T.CORPORA
INDEXES = os.path.join(DATA, "indexes")
L1 = os.path.join(ROOT, "l1")
RESULTS = os.environ.get("ARE_L1_RESULTS", os.path.join(ROOT, "results", "pe3-l1.jsonl"))
CPUS = "8"
SLOTS = {"a": "0-7", "b": "8-15", "c": "16-23"}

# tool -> how L1 calls it.
#   family     the seven families of the plan
#   form       the query form the tool's interface takes: ident | terms | nat
#   corpora    the corpora whose index L0 built and that L1 uses, with the
#              variant of the index and the settings PE1 asked to record
#   memory     the container's memory limit
#   resident   what has to stay alive between searches
TOOLS = {
    "grep":    dict(family="標準", form="terms", image="are-l0-base:pe1", no_index=True,
                    corpora={"c1": {}, "c2": {}, "c3": {}, "c4": {}}),
    "ctags":   dict(family="シンボル索引", form="ident",
                    corpora={"c1": {}, "c2": {}, "c3": {}, "c4": {}}),
    "global":  dict(family="シンボル索引", form="ident",
                    corpora={"c1": {}, "c2": {}, "c3": {}, "c4": {}}),
    "cscope":  dict(family="シンボル索引", form="ident", corpora={"c3": {}}),
    "ast-grep": dict(family="構文・字句", form="ident",
                     corpora={"c1": {}, "c2": {}, "c3": {}}),
    "probe":   dict(family="構文・字句", form="terms",
                    corpora={"c1": {}, "c2": {}, "c3": {}, "c4": {}}),
    "zoekt":   dict(family="構文・字句", form="terms",
                    corpora={"c1": {}, "c2": {}, "c3": {}, "c4": {}}),
    "qmd":     dict(family="BM25・混合", form="nat", gpu=True, resident="container",
                    corpora={"c1": {}, "c2": {}, "c4": {}}),
    "semble":  dict(family="BM25・混合", form="nat", resident="container",
                    corpora={"c1": {}, "c2": {}, "c4": {},
                             "c3": {"variant": "mem32g", "memory": "32g",
                                    "note": "統括の決定: c3 の混合系は 32GiB で semble"}}),
    "ck":      dict(family="BM25・混合", form="nat", resident="container",
                    corpora={"c1": {"note": "c2・c3・c4 は L0 で 60 分の時間切れ"}}),
    "codebase-memory-mcp": dict(family="グラフ", form="nat", resident="mcp",
                                corpora={"c1": {}, "c2": {}, "c4": {},
                                         "c3": {"variant": "mem12g", "memory": "16g",
                                                "env": {"CBM_MEM_BUDGET_MB": "12288"}}}),
    "codegraph": dict(family="グラフ", form="ident",
                      corpora={"c1": {}, "c2": {}, "c3": {}, "c4": {}}),
    "graphify": dict(family="グラフ", form="ident",
                     corpora={"c1": {}, "c2": {}, "c4": {},
                              "c3": {"env": {"GRAPHIFY_MAX_GRAPH_BYTES": "4GB"},
                                     "memory": "32g"}}),
    "gitnexus": dict(family="グラフ", form="nat",
                     corpora={"c1": {}, "c2": {}, "c4": {}}),
    "serena":  dict(family="LSP", form="ident", resident="agent", memory="24g",
                    corpora={"c1": {}, "c2": {"note": "統括の決定: c2 は Go のみを主とする"}}),
    "wikictl": dict(family="自作", form="terms", corpora={"c1": {}, "c4": {}}),
}
# Why a corpus is not a cell of a tool (for the report and for the analysis's
# list of estimable contrasts).
NOT_APPLICABLE = {
    ("cscope", "c1"): "C のみ", ("cscope", "c2"): "C のみ", ("cscope", "c4"): "C のみ",
    ("ast-grep", "c4"): "コードのみ（c4 は文書）",
    ("qmd", "c3"): "Markdown のみ（c3 の文書は reStructuredText）",
    ("ck", "c2"): "L0 で索引が 60 分で終わらない", ("ck", "c3"): "L0 で索引が 60 分で終わらない",
    ("ck", "c4"): "L0 で索引が 60 分で終わらない",
    ("gitnexus", "c3"): "L0 で 16GiB の制限で OOM",
    ("serena", "c3"): "統括の決定: LSP を c3 から外す（L0 で約 12 時間の見込み）",
    ("serena", "c4"): "言語サーバの対象なし",
    ("wikictl", "c2"): "文書の題材のみ", ("wikictl", "c3"): "文書の題材のみ",
}
# Cells that are measured but left out of the comparisons: the tool's index
# holds no file of the kind the corpus's gold is made of, so the 0.000 there
# says what the index covers and not how good the tool is (the decision of
# 2026-09-20). c4's gold is Markdown throughout; the counts are of the index
# PE1 built and are quoted in results/pe3b-l1.md.
NOT_COMPARABLE = {
    ("global", "c4"): "記号索引に文書が入らない（GTAGS が記号を持つ md は 4 件。GPATH の md 9,120 件には記号が付かない）",
    ("semble", "c4"): "索引の塊 7,379 件に .md が 0 件（js・css・py・scss・go のみ）",
    ("codegraph", "c4"): "索引したファイル 1,661 件に .md が 0 件",
}
# The same three tools index no Markdown in c1 either (semble 1,195/1,198 が
# .go、codegraph 0/66、global の GTAGS は 4 件）, so c1 の S4・S5（文書の質問）も
# 同じ理由で届かない。ここは統括の決定が c4 に限られているため対比からは外さず、
# 報告で副次の集計として示す。
DOC_BLIND = ("global", "semble", "codegraph")


def repos(corpus):
    import yaml
    with open(os.path.join(ROOT, "corpora", "corpora.yaml")) as f:
        return list(yaml.safe_load(f)[corpus]["repos"])


def cell(tool, corpus):
    return TOOLS[tool]["corpora"][corpus]


def name(tool, corpus):
    return re.sub(r"[^A-Za-z0-9_.-]", "-", f"are-l1-{tool}-{corpus}")


def index_dir(tool, corpus):
    v = cell(tool, corpus).get("variant")
    return os.path.join(INDEXES, tool, corpus + (f".{v}" if v else ""))


def sh(*args, **kw):
    return subprocess.run(list(args), check=True, capture_output=True, text=True, **kw).stdout.strip()


def start(tool, corpus, slot="a"):
    """Bring the cell's container up; return the seconds it took."""
    spec = TOOLS[tool]
    base = index_dir(tool, corpus)
    upper, work = os.path.join(base, "upper"), os.path.join(base, "ovl")
    idx = os.path.join(base, "index")
    if spec.get("no_index"):
        for d in (upper, work, os.path.join(idx, "home")):
            os.makedirs(d, exist_ok=True)
    for d in (upper, work, idx):
        if not os.path.isdir(d):
            raise SystemExit(f"{tool}/{corpus}: no index from L0 at {d}")
    vol = name(tool, corpus)
    subprocess.run(["docker", "rm", "-f", vol], capture_output=True)
    subprocess.run(["docker", "volume", "rm", "-f", vol], capture_output=True)
    sh("docker", "volume", "create", "--driver", "local", "--opt", "type=overlay", "--opt", "device=overlay",
       "--opt", f"o=lowerdir={os.path.join(CORPORA, corpus)},upperdir={upper},workdir={work}", vol)
    mem = cell(tool, corpus).get("memory") or spec.get("memory") or "16g"
    env = {"CORPUS": corpus, "REPOS": " ".join(repos(corpus)), "HOME": "/index/home",
           "L1_TOOL": tool, "PYTHONUNBUFFERED": "1"}
    env.update(cell(tool, corpus).get("env") or {})
    cmd = ["docker", "run", "-d", "--name", vol, "--network", "none", "--cpus", CPUS,
           "--cpuset-cpus", SLOTS[slot], "--memory", mem, "--memory-swap", mem,
           "-v", f"{vol}:/work", "-v", f"{idx}:/index", "-v", f"{L1}:/opt/l1:ro",
           "--entrypoint", "sleep"]
    if spec.get("gpu"):
        cmd += ["--gpus", "all"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    cmd += [spec.get("image", f"are-l0-{tool}:pe1"), "infinity"]
    t0 = time.monotonic()
    sh(*cmd)
    return round(time.monotonic() - t0, 3), vol, mem


def stop(tool=None, corpus=None):
    if tool:
        names = [name(tool, corpus)]
    else:
        names = [n for n in sh("docker", "ps", "-aq", "--filter", "name=are-l1-",
                               "--format", "{{.Names}}").split("\n") if n]
    for n in names:
        subprocess.run(["docker", "rm", "-f", n], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "-f", n], capture_output=True)
    return names


def worker(tool, corpus):
    """A resident worker process inside the cell's container."""
    py = "/opt/serena/bin/python3" if tool == "serena" else "python3"
    return subprocess.Popen(["docker", "exec", "-i", name(tool, corpus), py, "/opt/l1/worker.py"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)


def ask(p, msg, timeout=600):
    p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
    p.stdin.flush()
    line = p.stdout.readline()
    if not line:
        raise RuntimeError("worker died")
    return json.loads(line)


def run(args):
    todo = []
    for tool in args.tools.split(","):
        for corpus in args.corpora.split(","):
            if corpus in TOOLS[tool]["corpora"]:
                todo.append((tool, corpus))
    all_tasks = T.load()
    if args.scenarios:
        keep = set(args.scenarios.split(","))
        all_tasks = [t for t in all_tasks if t["scenario"] in keep]
    langs = args.langs.split(",")
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)
    with open(os.path.join(INDEXES, f".slot-{args.slot}.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(f"waiting for slot {args.slot}", file=sys.stderr, flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        for tool, corpus in todo:
            with open(os.path.join(INDEXES, f".slot-{args.slot}.running"), "w") as f:
                f.write(f"l1 {tool} {corpus}\n")
            try:
                run_cell(tool, corpus, all_tasks, langs, args)
            except Exception as e:
                print(json.dumps({"tool": tool, "corpus": corpus, "error": repr(e)}), flush=True)
            finally:
                stop(tool, corpus)
        try:
            os.remove(os.path.join(INDEXES, f".slot-{args.slot}.running"))
        except OSError:
            pass


def run_cell(tool, corpus, all_tasks, langs, args):
    spec = TOOLS[tool]
    form = spec["form"]
    qs = [t for t in all_tasks if t["corpus"] == corpus]
    if args.limit:
        qs = qs[:args.limit]
    cell_langs = ["id"] if form == "ident" else langs
    t_start, vol, mem = start(tool, corpus, args.slot)
    p = worker(tool, corpus)
    try:
        hello = ask(p, {"op": "start"})
        startup = {"container_s": t_start, "tool_s": hello.get("t_s"), "note": hello.get("note"),
                   "version": hello.get("version"), "memory": mem}
        print(json.dumps({"cell": f"{tool}/{corpus}", "startup": startup}), flush=True)
        for lang in cell_langs:
            for t in qs:
                q = query_for(t, form, lang)
                t0 = time.monotonic()
                r = ask(p, {"op": "search", "q": q, "k": args.k, "id": t["id"]})
                exec_s = round(time.monotonic() - t0, 3)
                rec = {"tool": tool, "corpus": corpus, "family": spec["family"], "form": form,
                       "query_lang": lang, "id": t["id"], "scenario": t["scenario"],
                       "phrasing": t["phrasing"], "task_lang": t["lang"], "query": q,
                       "paths": r.get("paths", []), "t_s": r.get("t_s"), "exec_s": exec_s,
                       "n_raw": r.get("n_raw"), "err": r.get("err"), "startup": startup,
                       "slot": args.slot, "k": args.k,
                       "date": datetime.datetime.now().astimezone().isoformat(timespec="seconds")}
                if not args.dry:
                    with open(RESULTS, "a") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(json.dumps({"cell": f"{tool}/{corpus}", "lang": lang, "n": len(qs)}), flush=True)
    finally:
        try:
            p.stdin.close()
            p.wait(timeout=30)
        except Exception:
            p.kill()


def query_for(task, form, lang):
    f = task["forms"]
    if form == "ident":
        return {"kind": "ident", "terms": f["ident"]}
    if form == "terms":
        return {"kind": "terms", "terms": f[f"terms_{lang}"]}
    return {"kind": "nat", "text": f[f"nat_{lang}"], "terms": f[f"terms_{lang}"]}


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("start", "stop", "sh"):
        s = sub.add_parser(c)
        s.add_argument("tool", nargs="?")
        s.add_argument("corpus", nargs="?")
        if c == "start":
            s.add_argument("--slot", choices=SLOTS, default="a")
        if c == "sh":
            s.add_argument("argv", nargs=argparse.REMAINDER)
    sub.add_parser("ps")
    sub.add_parser("plan").add_argument("--md", action="store_true", help="表の形で出す")
    r = sub.add_parser("run")
    r.add_argument("--tools", required=True)
    r.add_argument("--corpora", default="c1,c2,c3,c4")
    r.add_argument("--langs", default="ja,en")
    r.add_argument("--slot", choices=SLOTS, default="a")
    r.add_argument("--scenarios", help="S3,S7 のように絞る（診断用）")
    r.add_argument("--k", type=int, default=50)
    r.add_argument("--limit", type=int)
    r.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    if a.cmd == "start":
        print(start(a.tool, a.corpus, a.slot))
    elif a.cmd == "stop":
        print(" ".join(stop(a.tool, a.corpus) or ["(none)"]))
    elif a.cmd == "sh":
        os.execvp("docker", ["docker", "exec", "-it", name(a.tool, a.corpus)] + (a.argv or ["bash"]))
    elif a.cmd == "ps":
        subprocess.run(["docker", "ps", "--filter", "name=are-l1-", "--format",
                        "table {{.Names}}\t{{.Status}}\t{{.Image}}"])
    elif a.cmd == "plan":
        if a.md:
            print("| 道具 | 形 | c1 | c2 | c3 | c4 | 外した理由 |")
            print("|---|---|:-:|:-:|:-:|:-:|---|")
            for tool, spec in TOOLS.items():
                marks, why = [], []
                for c in ("c1", "c2", "c3", "c4"):
                    if (tool, c) in NOT_COMPARABLE:
                        marks.append("△")
                        why.append(f"{c}: {NOT_COMPARABLE[(tool, c)]}")
                    elif c in spec["corpora"]:
                        marks.append("○")
                    else:
                        marks.append("―")
                        why.append(f"{c}: {NOT_APPLICABLE.get((tool, c), '?')}")
                print(f"| {tool} | {spec['form']} | " + " | ".join(marks) + " | " +
                      "；".join(why) + " |")
            print()
            print("○ 測って対比に使う／△ 測るが対比から外す（索引に題材の答えの種類が無い）／― 組まない")
            return
        for tool, spec in TOOLS.items():
            for c in ("c1", "c2", "c3", "c4"):
                if c in spec["corpora"]:
                    x = spec["corpora"][c]
                    na = NOT_COMPARABLE.get((tool, c))
                    print(f"{tool:20s} {c} form={spec['form']:5s} {x.get('variant') or '':8s} "
                          f"{'対比から外す: ' + na if na else (x.get('note') or '')}")
                else:
                    print(f"{tool:20s} {c} n/a   {NOT_APPLICABLE.get((tool, c), '?')}")
    else:
        run(a)


if __name__ == "__main__":
    main()
