#!/usr/bin/env python3
"""Run one agent session per question in Docker.

    harness/run.py run --question pe0-s1 --cond A1 --model local:qwen3.8:27b [--rep 1] [--label pe0]
    harness/run.py batch --questions pe0-s1,pe0-s5 --conds A1 --models local:qwen3.8:27b [--reps 1] [--label pe0]
    harness/run.py prompt --question pe0-s1 --cond A1

A session is one question under one condition, model and repetition, written
to $ARE_RUNS (default ../agent-retrieval-eval-runs)/<label>/<question>-<cond>-<model>-r<rep>/:

  meta.json      question, condition, model, versions, commits, times, exit
  prompt.md      the prompt given to the agent
  stream.jsonl   Claude Code's stream-json output
  stderr.txt
  work/          /workspace, with answer.json
  corpus/        /corpus, a copy of the pinned corpus
  bin/           logging wrappers of the shell commands, first on PATH
  log/           cmdlog.tsv of the wrappers

A model is <backend>:<name>. "local" runs Claude Code against the Ollama
server's Anthropic-compatible API (pilot runs only); "anthropic" runs the
Claude API with the OAuth token in $ARE_TOKEN_FILE. The session's container
sits on an internal network whose only way out is the proxy container, which
lets through the Claude API and the Ollama server alone.
"""
import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNS = os.environ.get("ARE_RUNS", os.path.join(os.path.dirname(ROOT), "agent-retrieval-eval-runs"))
IMAGE = os.environ.get("ARE_IMAGE", "are-agent:dev")
PROXY_IMAGE = os.environ.get("ARE_PROXY_IMAGE", "are-proxy:dev")
TOKEN_FILE = os.environ.get("ARE_TOKEN_FILE", os.path.expanduser("~/.config/agent-retrieval-eval/oauth-token"))
LOCAL_URL = os.environ.get("ARE_LOCAL_URL", "http://192.168.1.240:8080")
NETWORK = "are-int"
PROXY = "are-proxy"
DATA = os.environ.get("ARE_DATA", os.path.join(ROOT, "data"))
CORPORA = os.path.join(DATA, "corpora")
SESSION_TIMEOUT = int(os.environ.get("ARE_SESSION_TIMEOUT", 30 * 60))
CPUS, MEMORY = "2", "6g"
BUDGET_USD = "5"

ANTHROPIC_MODELS = {"sonnet": "claude-sonnet-5", "opus": "claude-opus-5", "haiku": "claude-haiku-4-5-20251001"}
LOGGED_COMMANDS = ["grep", "rg", "find", "fd", "tree", "cat", "sed", "git", "head", "tail", "wc", "ls", "awk"]
TOOLS = ["Bash", "Read", "Glob", "Grep", "Write"]
DISALLOWED = ["WebFetch", "WebSearch"]

# The condition decides which tools the session gets beyond the standard ones
# and which environment text describes them. PE0 has the standard one only.
CONDS = {
    "A1": {"env": "A1.md", "path": []},
}


def read(path):
    with open(path) as f:
        return f.read()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sh(*args):
    return subprocess.run(list(args), check=True, capture_output=True, text=True).stdout.strip()


def load_questions():
    qs = {}
    for dirpath, _, files in os.walk(os.path.join(ROOT, "tasks")):
        for fn in sorted(files):
            if fn.endswith(".jsonl"):
                for line in read(os.path.join(dirpath, fn)).splitlines():
                    if line.strip():
                        q = json.loads(line)
                        qs[q["id"]] = q
    return qs


def load_corpora():
    return yaml.safe_load(read(os.path.join(ROOT, "corpora", "corpora.yaml")))


def parse_model(model):
    backend, _, name = model.partition(":")
    if backend == "anthropic":
        return backend, ANTHROPIC_MODELS.get(name, name)
    if backend == "local" and name:
        return backend, name
    raise SystemExit(f"model must be local:<name> or anthropic:<name>, got {model!r}")


def slug(text):
    return "".join(c if c.isalnum() or c in "-." else "-" for c in text)


# ---- infrastructure ----------------------------------------------------------

def ensure_network():
    """The internal network and the proxy, the session's only way out."""
    if subprocess.run(["docker", "network", "inspect", NETWORK], capture_output=True).returncode:
        sh("docker", "network", "create", "--internal", NETWORK)
    r = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", PROXY], capture_output=True, text=True)
    if r.stdout.strip() != "true":
        subprocess.run(["docker", "rm", "-f", PROXY], capture_output=True)
        sh("docker", "run", "-d", "--name", PROXY, "--network", "bridge", PROXY_IMAGE)
        sh("docker", "network", "connect", "--alias", "proxy", NETWORK, PROXY)
        time.sleep(1)


def copy_tree(src, dst):
    """Copy a repository: git objects as hard links (git never rewrites them
    in place), everything else as copies, so a session cannot change the
    pinned corpus."""
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target = os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        in_objects = rel == os.path.join(".git", "objects") or rel.startswith(os.path.join(".git", "objects") + os.sep)
        for fn in files:
            s, t = os.path.join(root, fn), os.path.join(target, fn)
            if os.path.islink(s):
                os.symlink(os.readlink(s), t)
            elif in_objects:
                os.link(s, t)
            else:
                shutil.copy2(s, t)


# ---- sessions ----------------------------------------------------------------

def prompt_text(q, cond, corpora):
    repos = corpora[q["corpus"]]["repos"]
    corpus = "\n".join(f"- `/corpus/{name}`: {spec['summary_ja']}" for name, spec in repos.items())
    env = read(os.path.join(ROOT, "tasks", "env", CONDS[cond]["env"])).replace("{CORPUS}", corpus)
    text = read(os.path.join(ROOT, "tasks", "prompt.md"))
    for k, v in {"ENV": env.strip(), "QUESTION": q["question"], "ANSWER_FORMAT": q["answer_format"]}.items():
        text = text.replace("{" + k + "}", v)
    return text


def prepare(d, q, cond, corpora):
    for sub in ("work", "bin", "log", "home", "corpus"):
        os.makedirs(os.path.join(d, sub))
    with open(os.path.join(d, "log", "cmdlog.tsv"), "w") as f:
        f.write("time\targv\tstdout_bytes\tstderr_bytes\texit\tms\tcallers\n")
    with open(os.path.join(d, "home", ".gitconfig"), "w") as f:
        f.write("[safe]\n\tdirectory = *\n[core]\n\tpager = cat\n")
    template = read(os.path.join(ROOT, "harness", "wrapper.sh.in"))
    for name in LOGGED_COMMANDS:
        real = {"fd": "/usr/bin/fdfind"}.get(name, f"/usr/bin/{name}")
        path = os.path.join(d, "bin", name)
        with open(path, "w") as f:
            f.write(template.replace("@NAME@", name).replace("@REAL@", real).replace("@LOGDIR@", "/eval/log"))
        os.chmod(path, 0o755)
    for name in corpora[q["corpus"]]["repos"]:
        src = os.path.join(CORPORA, q["corpus"], name)
        if not os.path.isdir(src):
            raise SystemExit(f"corpus {q['corpus']}/{name} is missing; run scripts/fetch_corpora.py {q['corpus']}")
        copy_tree(src, os.path.join(d, "corpus", name))


def claude_args(model_id):
    return ["claude", "-p", "--model", model_id, "--output-format", "stream-json", "--verbose",
            "--tools", ",".join(TOOLS), "--allowedTools", ",".join(TOOLS),
            "--disallowedTools", ",".join(DISALLOWED), "--permission-mode", "dontAsk",
            "--strict-mcp-config", "--max-budget-usd", BUDGET_USD,
            "--settings", json.dumps({"autoMemoryEnabled": False}), "--add-dir", "/corpus"]


def docker_cmd(d, name, backend, model_id, cond):
    proxy = "http://proxy:8888"
    path = ":".join(["/eval/bin", *CONDS[cond]["path"], "/usr/local/bin", "/usr/bin", "/bin"])
    env = {
        "HOME": "/home/agent", "PATH": path, "CLAUDE_CONFIG_DIR": "/home/agent/.claude-eval", "TZ": "UTC",
        "LANG": "C.UTF-8", "HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy,
        "NO_PROXY": "localhost,127.0.0.1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "GIT_TERMINAL_PROMPT": "0",
    }
    if backend == "local":
        env.update({"ANTHROPIC_BASE_URL": LOCAL_URL, "ANTHROPIC_AUTH_TOKEN": "ollama",
                    "ANTHROPIC_MODEL": model_id, "ANTHROPIC_SMALL_FAST_MODEL": model_id})
    cmd = ["docker", "run", "--rm", "-i", "--name", name, "--user", "1000:1000", "--cpus", CPUS, "--memory", MEMORY,
           "--network", NETWORK, "-w", "/workspace"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    if backend == "anthropic":
        cmd += ["-e", "CLAUDE_CODE_OAUTH_TOKEN"]
    cmd += ["-v", f"{d}/home:/home/agent:rw", "-v", f"{d}/work:/workspace:rw", "-v", f"{d}/bin:/eval/bin:ro",
            "-v", f"{d}/log:/eval/log:rw", "-v", f"{d}/corpus:/corpus:rw", IMAGE]
    return cmd + claude_args(model_id)


def run_session(qid, cond, model, rep, label):
    qs, corpora = load_questions(), load_corpora()
    if qid not in qs:
        raise SystemExit(f"unknown question: {qid}")
    if cond not in CONDS:
        raise SystemExit(f"unknown condition: {cond}")
    q = qs[qid]
    backend, model_id = parse_model(model)
    token = read(TOKEN_FILE).strip() if backend == "anthropic" else None
    ensure_network()
    session = f"{qid}-{cond}-{slug(model)}-r{rep}"
    d = os.path.join(RUNS, label or "default", session)
    if os.path.exists(d):
        raise SystemExit(f"{d} exists")
    os.makedirs(d)
    prepare(d, q, cond, corpora)
    text = prompt_text(q, cond, corpora)
    with open(os.path.join(d, "prompt.md"), "w") as f:
        f.write(text)
    name = f"are-{slug(label or 'default')}-{session}"[:120]
    cmd = docker_cmd(d, name, backend, model_id, cond)
    meta = {"session": session, "label": label, "question": qid, "corpus": q["corpus"], "scenario": q["scenario"],
            "phrasing": q["phrasing"], "cond": cond, "backend": backend, "model": model_id, "rep": rep,
            "image": IMAGE, "image_id": sh("docker", "image", "inspect", "-f", "{{.Id}}", IMAGE),
            "claude_version": sh("docker", "run", "--rm", IMAGE, "claude", "--version"),
            "commits": {n: s["commit"] for n, s in corpora[q["corpus"]]["repos"].items()},
            "cmd": cmd, "started_at": now()}
    start = time.time()
    with open(os.path.join(d, "stream.jsonl"), "w") as out, open(os.path.join(d, "stderr.txt"), "w") as err:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=out, stderr=err, text=True,
                             env={**os.environ, **({"CLAUDE_CODE_OAUTH_TOKEN": token} if token else {})})
        p.stdin.write(text)
        p.stdin.close()
        try:
            rc, timed_out = p.wait(timeout=SESSION_TIMEOUT), False
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "kill", name], capture_output=True)
            rc, timed_out = p.wait(), True
    meta.update({"wall_ms": int((time.time() - start) * 1000), "exit": rc, "timed_out": timed_out,
                 "finished_at": now()})
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return d, meta


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--question", required=True)
    r.add_argument("--cond", default="A1")
    r.add_argument("--model", required=True)
    r.add_argument("--rep", type=int, default=1)
    r.add_argument("--label", default="")
    b = sub.add_parser("batch")
    b.add_argument("--questions", required=True, help="comma-separated ids, or 'all'")
    b.add_argument("--conds", default="A1")
    b.add_argument("--models", required=True)
    b.add_argument("--reps", type=int, default=1)
    b.add_argument("--label", default="")
    p = sub.add_parser("prompt")
    p.add_argument("--question", required=True)
    p.add_argument("--cond", default="A1")
    a = ap.parse_args()
    if a.cmd == "run":
        d, meta = run_session(a.question, a.cond, a.model, a.rep, a.label)
        print(json.dumps({"dir": d, "wall_ms": meta["wall_ms"], "exit": meta["exit"], "timed_out": meta["timed_out"]}))
    elif a.cmd == "batch":
        ids = sorted(load_questions()) if a.questions == "all" else a.questions.split(",")
        for rep in range(1, a.reps + 1):
            for qid in ids:
                for cond in a.conds.split(","):
                    for model in a.models.split(","):
                        d = os.path.join(RUNS, a.label or "default", f"{qid}-{cond}-{slug(model)}-r{rep}")
                        if os.path.exists(os.path.join(d, "meta.json")):
                            print(f"skip {d}", flush=True)
                            continue
                        _, meta = run_session(qid, cond, model, rep, a.label)
                        print(f"done {qid} {cond} {model} r{rep} exit={meta['exit']} "
                              f"wall={meta['wall_ms'] / 1000:.0f}s timed_out={meta['timed_out']}", flush=True)
    else:
        qs = load_questions()
        print(prompt_text(qs[a.question], a.cond, load_corpora()))


if __name__ == "__main__":
    sys.exit(main())
