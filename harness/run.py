#!/usr/bin/env python3
"""Run one agent session per question in Docker.

    harness/run.py run --question pe0-s1 --cond A1 --model local:qwen3.8:27b [--rep 1] [--label pe0]
    harness/run.py batch --questions pe0-s1,pe0-s5 --conds A1 --models local:qwen3.8:27b [--reps 1] [--label pe0]
    harness/run.py prompt --question pe0-s1 --cond A1
    harness/run.py plan                       条件 × 題材の当てはまりと、外した理由
    harness/run.py smoke --question <id> --cond A2 -- <cmd...>
                                              その条件の環境で Claude Code の代わりに
                                              <cmd> を動かす（道具と索引の確認）

A session is one question under one condition, model and repetition, written
to $ARE_RUNS (default ../agent-retrieval-eval-runs)/<label>/<question>-<cond>-<model>-r<rep>/:

  meta.json      question, condition, model, versions, commits, times, exit,
                 and the sha256 of every question file, so that a session can
                 be graded again from the record alone
  prompt.md      the prompt given to the agent
  stream.jsonl   Claude Code's stream-json output
  stderr.txt
  work/          /workspace, with answer.json
  corpus/        /corpus (and /work), a copy of the pinned corpus, deleted
                 after the session unless $ARE_KEEP_CORPUS=1
  bin/           logging wrappers of the shell commands, first on PATH
  log/           cmdlog.tsv of the wrappers
  idx/<tool>/    the writable upper layer over the tool's index (L2); what the
                 tool wrote is measured into meta.json and then removed unless
                 $ARE_KEEP_CORPUS=1
  mcp.json       the MCP servers of the condition, when it has any

A model is <backend>:<name>. "local" runs Claude Code against the Ollama
server's Anthropic-compatible API (pilot runs only); "anthropic" runs the
Claude API with the OAuth token in $ARE_TOKEN_FILE. The session's container
sits on an internal network whose only way out is the proxy container, which
lets through the Claude API and the Ollama server alone.

Conditions (L2)
---------------
A0 has no corpus at all (the ceiling of what the model remembers), A1 is the
standard environment, and A2-A7 add one family's representative tool to A1.
A tool is given the way it is meant to be used: a CLI on the PATH, an MCP
server through --mcp-config. Its index is the one PE1 (L0) built, mounted
through an overlay whose upper layer belongs to the session, so the tool can
write but the index itself never changes and no session builds one.

The copy of the corpus is mounted at /corpus (the path the prompt names) and
at /work (the path the indexes were built under, so that a tool's index and
its output resolve). Both are the same files.
"""
import argparse
import datetime
import hashlib
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
INDEXES = os.path.join(DATA, "indexes")
SESSION_TIMEOUT = int(os.environ.get("ARE_SESSION_TIMEOUT", 30 * 60))
# The same for every condition, so that no tool is starved and the cost and
# the time of the conditions stay comparable. Recorded in meta.json.
CPUS = os.environ.get("ARE_CPUS", "8")
MEMORY = os.environ.get("ARE_MEMORY", "24g")
# Per-session ceiling Claude Code itself enforces (a guard, not the budget).
BUDGET_USD = os.environ.get("ARE_BUDGET_USD", "5")
# The copy of the corpus is deleted after the session unless this is set.
KEEP_CORPUS = os.environ.get("ARE_KEEP_CORPUS") == "1"

ANTHROPIC_MODELS = {"sonnet": "claude-sonnet-5", "opus": "claude-opus-5", "haiku": "claude-haiku-4-5-20251001"}
LOGGED_COMMANDS = ["grep", "rg", "find", "fd", "tree", "cat", "sed", "git", "head", "tail", "wc", "ls", "awk"]
AGENT_TOOLS = ["Bash", "Read", "Glob", "Grep", "Write"]
DISALLOWED = ["WebFetch", "WebSearch"]

# The representative tool of each family (統括の決定, PE3), and how L2 gives
# it. "cli" puts the commands of "cmds" on the PATH as logging wrappers, so
# that every call is counted and the session gets the commands the
# environment text names and no others (readtags but not ctags, zoekt but not
# zoekt-index: the index is the one L0 built and no session builds one);
# "mcp" registers an MCP server with --mcp-config. "index" is the directory of PE1's index, with the variants
# PE1 had to build with settings of their own. "corpora" is where the tool
# applies at all; "na" says why not, for the report.
TOOLS = {
    "ctags": dict(
        kind="cli", family="シンボル索引", index="ctags",
        cmds={"readtags": "/opt/ctags/bin/readtags"},
        corpora=["c1", "c2", "c3", "c4"]),
    "zoekt": dict(
        kind="cli", family="構文・字句", index="zoekt",
        cmds={"zoekt": "/opt/zoekt/bin/zoekt"},
        corpora=["c1", "c2", "c3", "c4"]),
    "semble": dict(
        kind="cli", family="BM25・混合（コード）", index="semble",
        cmds={"semble": "/opt/semble/bin/semble"},
        env={"SEMBLE_CACHE_LOCATION": "/idx/semble/semble"},
        variants={"c3": "mem32g"}, memory={"c3": "32g"},
        corpora=["c1", "c2", "c3", "c4"]),
    # qmd ships an MCP server (query/get/multi_get/status) and a Claude Code
    # plugin that installs it: that is its form for an agent.
    "qmd": dict(
        kind="mcp", family="BM25・混合（Markdown）", index="qmd", gpu=True, server="qmd",
        command="/opt/qmd/node_modules/.bin/qmd", args=["mcp"],
        env={"PATH": "/opt/qmd/node_modules/.bin:/opt/node/bin:/usr/local/bin:/usr/bin:/bin",
             "XDG_CACHE_HOME": "/idx/qmd/cache", "QMD_CONFIG_DIR": "/idx/qmd/config"},
        corpora=["c1", "c2", "c4"],
        na={"c3": "Markdown のみ（c3 の文書は reStructuredText）"}),
    "codebase-memory-mcp": dict(
        kind="mcp", family="グラフ", index="codebase-memory-mcp", server="codebase-memory",
        command="/usr/local/bin/codebase-memory-mcp", args=[],
        # 索引はセッションの中では作らせない（統括の決定）。壊す道具も外す。
        disallow=["index_repository", "delete_project", "ingest_traces", "manage_adr"],
        env={"HOME": "/idx/codebase-memory-mcp/home", "PATH": "/usr/local/bin:/usr/bin:/bin"},
        variants={"c3": "mem12g"}, extra_env={"c3": {"CBM_MEM_BUDGET_MB": "12288"}},
        corpora=["c1", "c2", "c3", "c4"]),
    "serena": dict(
        kind="mcp", family="LSP", index="serena", server="serena",
        command="/opt/serena/bin/serena",
        args=["start-mcp-server", "--context", "ide-assistant", "--transport", "stdio",
              "--project", "{PROJECT}"],
        # シェルの記録を迂回する道具だけ外す（編集の道具は Serena 本来の形として残す）。
        disallow=["execute_shell_command"],
        env={"HOME": "/idx/serena/home",
             "PATH": "/opt/serena/bin:/opt/go/bin:/opt/go-tools/bin:/opt/clangd/bin:/opt/node/bin:"
                     "/usr/local/bin:/usr/bin:/bin"},
        in_tree=True, projects={"c1": "wikictl", "c2": "grafana"},
        corpora=["c1", "c2"],
        na={"c3": "統括の決定: LSP を c3 から外す", "c4": "言語サーバの対象なし"}),
    "wikictl": dict(
        kind="cli", family="自作", index="wikictl",
        cmds={"wikictl": "/usr/local/bin/wikictl"},
        env={"XDG_CACHE_HOME": "/idx/wikictl/cache", "WIKICTL_CONFIG": "/idx/wikictl/wikictl.yaml"},
        corpora=["c1", "c4"],
        na={"c2": "文書の題材のみ", "c3": "文書の題材のみ"}),
}

# The condition decides which tools the session gets beyond the standard ones,
# which image carries them and which environment text describes them.
# 題材を読んで答える条件と、読めない条件（A0）とで、答え方の規則が違う。
RULE = "答えは題材の中から探し、題材の外の知識で推測しないでください。"
RULE_A0 = "題材は渡されていないので、知っている範囲で答えてください。分からないときは推測せず `null` にしてください。"

CONDS = {
    "A0": dict(env="A0.md", image="are-l2-base:pe4", tools=[], corpus=False, rule=RULE_A0),
    "A1": dict(env="A1.md", image="are-l2-base:pe4", tools=[]),
    "A2": dict(env="A2.md", image="are-l2-ctags:pe4", tools=["ctags"]),
    "A3": dict(env="A3.md", image="are-l2-zoekt:pe4", tools=["zoekt"]),
    "A4": dict(env="A4.md", image="are-l2-semble-qmd:pe4", tools=["semble", "qmd"]),
    "A5": dict(env="A5.md", image="are-l2-codebase-memory-mcp:pe4", tools=["codebase-memory-mcp"]),
    "A6": dict(env="A6.md", image="are-l2-serena:pe4", tools=["serena"]),
    "A7": dict(env="A7.md", image="are-l2-wikictl:pe4", tools=["wikictl"]),
}


def cond_tools(cond):
    return CONDS[cond].get("tools", [])


# The plan keeps Linux (c3) to L0 and L1; L2 runs on c1, c2 and c4.
L2_CORPORA = ["c1", "c2", "c4"]


def not_applicable(cond, corpus):
    """Why the condition does not apply to the corpus, or None."""
    if corpus not in L2_CORPORA:
        return "計画: Linux（c3）は L0・L1 のみ"
    for t in cond_tools(cond):
        if corpus not in TOOLS[t]["corpora"]:
            return f"{t}: {TOOLS[t].get('na', {}).get(corpus, '対象外')}"
    return None


def index_dir(tool, corpus):
    v = TOOLS[tool].get("variants", {}).get(corpus)
    return os.path.join(INDEXES, TOOLS[tool]["index"], corpus + (f".{v}" if v else ""))


def memory_for(cond, corpus):
    return max([MEMORY] + [TOOLS[t].get("memory", {}).get(corpus, MEMORY) for t in cond_tools(cond)],
               key=lambda m: int(m.rstrip("g")))


def read(path):
    with open(path) as f:
        return f.read()


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sh(*args):
    return subprocess.run(list(args), check=True, capture_output=True, text=True).stdout.strip()


def question_files():
    out = []
    for dirpath, _, files in os.walk(os.path.join(ROOT, "tasks")):
        out += [os.path.join(dirpath, fn) for fn in sorted(files) if fn.endswith(".jsonl")]
    return sorted(out)


def load_questions():
    qs = {}
    for path in question_files():
        for line in read(path).splitlines():
            if line.strip():
                q = json.loads(line)
                q["_file"] = os.path.relpath(path, ROOT)
                qs[q["id"]] = q
    return qs


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def question_versions():
    """問題ファイルの版。記録だけで採点し直せるように meta.json に入れる。

    PE3b の L1 では記録に問題の版が無く、前の測定を採点し直せなかった
    （results/pe3b-l1.md 第 3 節、第 11 節 #6）。
    """
    return {os.path.relpath(p, ROOT): sha256_of(p) for p in question_files()}


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
    env = read(os.path.join(ROOT, "tasks", "env", CONDS[cond]["env"]))
    env = env.replace("{CORPUS}", corpus).replace("{REPO}", next(iter(repos)))
    text = read(os.path.join(ROOT, "tasks", "prompt.md"))
    for k, v in {"RULE": CONDS[cond].get("rule", RULE), "ENV": env.strip(),
                 "QUESTION": q["question"], "ANSWER_FORMAT": q["answer_format"]}.items():
        text = text.replace("{" + k + "}", v)
    return text


def mcp_config(cond, corpus, repos):
    """The MCP servers of the condition, the way each tool is meant to be run."""
    servers = {}
    for t in cond_tools(cond):
        spec = TOOLS[t]
        if spec["kind"] != "mcp":
            continue
        project = spec.get("projects", {}).get(corpus, next(iter(repos)))
        args = [a.replace("{PROJECT}", f"/work/{project}") for a in spec["args"]]
        env = dict(spec.get("env", {}))
        env.update(spec.get("extra_env", {}).get(corpus, {}))
        servers[spec["server"]] = {"command": spec["command"], "args": args, "env": env}
    return {"mcpServers": servers}


def index_volume(d, session, tool, corpus, image):
    """A writable view of PE1's index: an overlay whose lower layer is the
    index and whose upper layer is the session's own directory. The index
    itself is never written to, and the session builds none of its own. L0's
    own logs (index/l0) are removed from the session's view."""
    lower = os.path.join(index_dir(tool, corpus), "index")
    if not os.path.isdir(lower):
        raise SystemExit(f"{tool}/{corpus}: no index from L0 at {lower}")
    upper, work = os.path.join(d, "idx", tool, "upper"), os.path.join(d, "idx", tool, "ovl")
    os.makedirs(upper)
    os.makedirs(work)
    vol = f"{session}-{tool}"[:120]
    subprocess.run(["docker", "volume", "rm", "-f", vol], capture_output=True)
    sh("docker", "volume", "create", "--driver", "local", "--opt", "type=overlay",
       "--opt", "device=overlay", "--opt", f"o=lowerdir={lower},upperdir={upper},workdir={work}", vol)
    subprocess.run(["docker", "run", "--rm", "--user", "0:0", "-v", f"{vol}:/idx", image,
                    "rm", "-rf", "/idx/l0"], capture_output=True)
    return vol


def dir_bytes(path):
    n = 0
    for root, _, files in os.walk(path):
        for fn in files:
            try:
                n += os.lstat(os.path.join(root, fn)).st_size
            except OSError:
                pass
    return n


def tool_env(cond, corpus):
    """The container's environment for the tools. HOME and PATH stay out of it:
    an MCP server gets those from mcp.json, so that the shell the agent sees is
    the same in every condition. The rest (where a tool keeps its index and its
    configuration) is set here too, because the L0 images point those at L0's
    own paths (/index) while L2 mounts them at /idx/<tool>."""
    env = {}
    for t in cond_tools(cond):
        env.update({k: v for k, v in TOOLS[t].get("env", {}).items() if k not in ("HOME", "PATH")})
        env.update(TOOLS[t].get("extra_env", {}).get(corpus, {}))
    return env


def prepare(d, q, cond, corpora, session):
    """Everything the session's container mounts. Returns the index volumes."""
    spec = CONDS[cond]
    for sub in ("work", "bin", "log", "home", "corpus"):
        os.makedirs(os.path.join(d, sub))
    with open(os.path.join(d, "log", "cmdlog.tsv"), "w") as f:
        f.write("time\targv\tstdout_bytes\tstderr_bytes\texit\tms\tcallers\n")
    with open(os.path.join(d, "home", ".gitconfig"), "w") as f:
        f.write("[safe]\n\tdirectory = *\n[core]\n\tpager = cat\n")
    template = read(os.path.join(ROOT, "harness", "wrapper.sh.in"))
    wrapped = {name: {"fd": "/usr/bin/fdfind"}.get(name, f"/usr/bin/{name}") for name in LOGGED_COMMANDS}
    for t in cond_tools(cond):
        wrapped.update(TOOLS[t].get("cmds", {}))
    for name, real in sorted(wrapped.items()):
        path = os.path.join(d, "bin", name)
        with open(path, "w") as f:
            f.write(template.replace("@NAME@", name).replace("@REAL@", real).replace("@LOGDIR@", "/eval/log"))
        os.chmod(path, 0o755)
    volumes = []
    if spec.get("corpus", True):
        for name in corpora[q["corpus"]]["repos"]:
            src = os.path.join(CORPORA, q["corpus"], name)
            if not os.path.isdir(src):
                raise SystemExit(f"corpus {q['corpus']}/{name} is missing; run scripts/fetch_corpora.py {q['corpus']}")
            copy_tree(src, os.path.join(d, "corpus", name))
        for t in cond_tools(cond):
            if TOOLS[t].get("in_tree"):
                copy_in_tree_index(t, q["corpus"], os.path.join(d, "corpus"))
            volumes.append(index_volume(d, session, t, q["corpus"], spec["image"]))
    cfg = mcp_config(cond, q["corpus"], corpora[q["corpus"]]["repos"])
    if cfg["mcpServers"]:
        with open(os.path.join(d, "mcp.json"), "w") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    return volumes


def copy_in_tree_index(tool, corpus, dst):
    """A tool that keeps its index inside the tree (Serena's .serena) built it
    in L0's overlay; the directory is copied into the session's copy of the
    corpus. Only the tool's own directories are copied, never the files that
    L0's update test changed."""
    upper = os.path.join(index_dir(tool, corpus), "upper")
    name = {"serena": ".serena"}[tool]
    for repo in sorted(os.listdir(upper)):
        src = os.path.join(upper, repo, name)
        if os.path.isdir(src):
            shutil.copytree(src, os.path.join(dst, repo, name), symlinks=True, dirs_exist_ok=True)


def claude_args(d, cond, model_id):
    # --tools names built-in tools only; an MCP server's tools are enabled by
    # its being configured and are allowed by name in --allowedTools.
    allowed = list(AGENT_TOOLS) + [f"mcp__{TOOLS[t]['server']}" for t in cond_tools(cond)
                                   if TOOLS[t]["kind"] == "mcp"]
    denied = list(DISALLOWED) + [f"mcp__{TOOLS[t]['server']}__{x}" for t in cond_tools(cond)
                                 for x in TOOLS[t].get("disallow", [])]
    args = ["claude", "-p", "--model", model_id, "--output-format", "stream-json", "--verbose",
            "--tools", ",".join(AGENT_TOOLS), "--allowedTools", ",".join(allowed),
            "--disallowedTools", ",".join(denied), "--permission-mode", "dontAsk",
            "--strict-mcp-config", "--max-budget-usd", BUDGET_USD,
            "--settings", json.dumps({"autoMemoryEnabled": False})]
    if os.path.exists(os.path.join(d, "mcp.json")):
        args += ["--mcp-config", "/eval/mcp.json"]
    if CONDS[cond].get("corpus", True):
        args += ["--add-dir", "/corpus", "--add-dir", "/work"]
    return args


def docker_cmd(d, name, backend, model_id, cond, corpus, volumes, exec_argv=None):
    spec = CONDS[cond]
    proxy = "http://proxy:8888"
    tool_path = [p for t in cond_tools(cond) for p in TOOLS[t].get("path", [])]
    path = ":".join(["/eval/bin", *tool_path, "/usr/local/bin", "/usr/bin", "/bin"])
    env = {
        "HOME": "/home/agent", "PATH": path, "CLAUDE_CONFIG_DIR": "/home/agent/.claude-eval", "TZ": "UTC",
        "LANG": "C.UTF-8", "HTTPS_PROXY": proxy, "HTTP_PROXY": proxy, "https_proxy": proxy, "http_proxy": proxy,
        "NO_PROXY": "localhost,127.0.0.1", "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1",
        "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1", "GIT_TERMINAL_PROMPT": "0",
    }
    env.update(tool_env(cond, corpus))
    if backend == "local":
        env.update({"ANTHROPIC_BASE_URL": LOCAL_URL, "ANTHROPIC_AUTH_TOKEN": "ollama",
                    "ANTHROPIC_MODEL": model_id, "ANTHROPIC_SMALL_FAST_MODEL": model_id})
    cmd = ["docker", "run", "--rm", "-i", "--name", name, "--user", "1000:1000", "--cpus", CPUS,
           "--memory", memory_for(cond, corpus), "--network", NETWORK, "-w", "/workspace"]
    if any(TOOLS[t].get("gpu") for t in cond_tools(cond)):
        cmd += ["--gpus", "all"]
    for k, v in env.items():
        cmd += ["-e", f"{k}={v}"]
    if backend == "anthropic":
        cmd += ["-e", "CLAUDE_CODE_OAUTH_TOKEN"]
    cmd += ["-v", f"{d}/home:/home/agent:rw", "-v", f"{d}/work:/workspace:rw", "-v", f"{d}/bin:/eval/bin:ro",
            "-v", f"{d}/log:/eval/log:rw"]
    if spec.get("corpus", True):
        cmd += ["-v", f"{d}/corpus:/corpus:rw", "-v", f"{d}/corpus:/work:rw"]
    for t, vol in zip(cond_tools(cond), volumes):
        cmd += ["-v", f"{vol}:/idx/{t}:rw"]
    if os.path.exists(os.path.join(d, "mcp.json")):
        cmd += ["-v", f"{d}/mcp.json:/eval/mcp.json:ro"]
    cmd += [spec["image"]]
    return cmd + (exec_argv or claude_args(d, cond, model_id))


def run_session(qid, cond, model, rep, label):
    qs, corpora = load_questions(), load_corpora()
    if qid not in qs:
        raise SystemExit(f"unknown question: {qid}")
    if cond not in CONDS:
        raise SystemExit(f"unknown condition: {cond}")
    q = qs[qid]
    why = not_applicable(cond, q["corpus"])
    if why:
        raise SystemExit(f"{cond} does not apply to {q['corpus']} ({why})")
    backend, model_id = parse_model(model)
    token = read(TOKEN_FILE).strip() if backend == "anthropic" else None
    image = CONDS[cond]["image"]
    ensure_network()
    session = f"{qid}-{cond}-{slug(model)}-r{rep}"
    d = os.path.join(RUNS, label or "default", session)
    if os.path.exists(d):
        raise SystemExit(f"{d} exists")
    os.makedirs(d)
    volumes = prepare(d, q, cond, corpora, session)
    text = prompt_text(q, cond, corpora)
    with open(os.path.join(d, "prompt.md"), "w") as f:
        f.write(text)
    name = f"are-{slug(label or 'default')}-{session}"[:120]
    cmd = docker_cmd(d, name, backend, model_id, cond, q["corpus"], volumes)
    meta = {"session": session, "label": label, "question": qid, "corpus": q["corpus"], "scenario": q["scenario"],
            "phrasing": q["phrasing"], "cond": cond, "tools": cond_tools(cond), "backend": backend,
            "model": model_id, "rep": rep,
            "image": image, "image_id": sh("docker", "image", "inspect", "-f", "{{.Id}}", image),
            "claude_version": sh("docker", "run", "--rm", "--entrypoint", "claude", image, "--version"),
            "cpus": CPUS, "memory": memory_for(cond, q["corpus"]),
            "question_file": q.get("_file"), "question_versions": question_versions(),
            "indexes": {t: index_dir(t, q["corpus"]) for t in cond_tools(cond)},
            "commits": {n: s["commit"] for n, s in corpora[q["corpus"]]["repos"].items()},
            "cmd": cmd, "started_at": now()}
    start = time.time()
    try:
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
    finally:
        # The container is gone (--rm); its overlay volumes go with it, while
        # the upper layer stays in the session's directory as a record.
        for vol in volumes:
            subprocess.run(["docker", "volume", "rm", "-f", vol], capture_output=True)
        if volumes:
            # How much each tool wrote into its index (overlayfs copies a file
            # up whole on the first write: Serena's cache is 65 MB, qmd's
            # SQLite on c4 is 356 MB), then the writes themselves go: they are
            # the tool's own state, not a record of the session.
            meta["index_upper_bytes"] = {t: dir_bytes(os.path.join(d, "idx", t, "upper"))
                                         for t in cond_tools(cond)}
            # The overlay's work directory belongs to root (the kernel makes
            # it), which would stop the session's directory from ever being
            # removed; root in a container can take it away.
            subprocess.run(["docker", "run", "--rm", "--user", "0:0", "-v", f"{d}/idx:/idx", image,
                            "sh", "-c", "rm -rf /idx/*/ovl" + ("" if KEEP_CORPUS else " /idx/*/upper")],
                           capture_output=True)
        # The copy of the corpus is the largest part of a session (c2 is 2.3 GB)
        # and can be made again from the pinned commit, so it goes unless
        # ARE_KEEP_CORPUS is set.
        if not KEEP_CORPUS:
            shutil.rmtree(os.path.join(d, "corpus"), ignore_errors=True)
    meta.update({"wall_ms": int((time.time() - start) * 1000), "exit": rc, "timed_out": timed_out,
                 "finished_at": now()})
    with open(os.path.join(d, "meta.json"), "w") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return d, meta


def smoke(qid, cond, argv):
    """The environment of one session, with a command of one's own instead of
    Claude Code: used to check that a condition's tool and its index work."""
    qs, corpora = load_questions(), load_corpora()
    q = qs[qid]
    session = f"smoke-{qid}-{cond}"
    d = os.path.join(RUNS, "smoke", f"{session}-{int(time.time())}")
    os.makedirs(d)
    volumes = prepare(d, q, cond, corpora, os.path.basename(d))
    with open(os.path.join(d, "prompt.md"), "w") as f:
        f.write(prompt_text(q, cond, corpora))
    name = f"are-{os.path.basename(d)}"[:120]
    cmd = docker_cmd(d, name, "local", "none", cond, q["corpus"], volumes,
                     exec_argv=argv or ["bash", "-lc", "echo no command"])
    try:
        rc = subprocess.run(cmd).returncode
    finally:
        for vol in volumes:
            subprocess.run(["docker", "volume", "rm", "-f", vol], capture_output=True)
        if volumes:
            # The overlay's work directory belongs to root (the kernel makes
            # it), which would stop the session's directory from ever being
            # removed; root in a container can take it away.
            subprocess.run(["docker", "run", "--rm", "--user", "0:0", "-v", f"{d}/idx:/idx",
                            CONDS[cond]["image"],
                            "sh", "-c", "rm -rf /idx/*/ovl" + ("" if KEEP_CORPUS else " /idx/*/upper")],
                           capture_output=True)
    print(f"# session dir: {d}", file=sys.stderr)
    return rc


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("smoke")
    s.add_argument("--question", required=True)
    s.add_argument("--cond", required=True)
    s.add_argument("argv", nargs=argparse.REMAINDER)
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
    sub.add_parser("plan")
    a = ap.parse_args()
    if a.cmd == "smoke":
        return smoke(a.question, a.cond, [x for x in a.argv if x != "--"])
    if a.cmd == "plan":
        for cond, spec in CONDS.items():
            tools = ",".join(spec["tools"]) or "―"
            for c in ("c1", "c2", "c3", "c4"):
                why = not_applicable(cond, c)
                print(f"{cond} {c} {tools:24s} {spec['image']:34s} {'n/a: ' + why if why else 'ok'}")
    elif a.cmd == "run":
        d, meta = run_session(a.question, a.cond, a.model, a.rep, a.label)
        print(json.dumps({"dir": d, "wall_ms": meta["wall_ms"], "exit": meta["exit"], "timed_out": meta["timed_out"]}))
    elif a.cmd == "batch":
        ids = sorted(load_questions()) if a.questions == "all" else a.questions.split(",")
        qs = load_questions()
        for rep in range(1, a.reps + 1):
            for qid in ids:
                for cond in a.conds.split(","):
                    for model in a.models.split(","):
                        d = os.path.join(RUNS, a.label or "default", f"{qid}-{cond}-{slug(model)}-r{rep}")
                        if os.path.exists(os.path.join(d, "meta.json")):
                            print(f"skip {d}", flush=True)
                            continue
                        why = not_applicable(cond, qs[qid]["corpus"])
                        if why:
                            print(f"n/a {qid} {cond} ({why})", flush=True)
                            continue
                        _, meta = run_session(qid, cond, model, rep, a.label)
                        print(f"done {qid} {cond} {model} r{rep} exit={meta['exit']} "
                              f"wall={meta['wall_ms'] / 1000:.0f}s timed_out={meta['timed_out']}", flush=True)
    else:
        qs = load_questions()
        print(prompt_text(qs[a.question], a.cond, load_corpora()))


if __name__ == "__main__":
    sys.exit(main())
