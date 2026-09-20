#!/usr/bin/env python3
"""The resident part of L1, inside a tool's container.

Reads one JSON request per line on stdin and answers one JSON line:

    {"op": "start"}                      -> {"t_s": ..., "version": ..., "note": ...}
    {"op": "search", "q": {...}, "k": n} -> {"paths": [[path, score], ...],
                                             "t_s": ..., "n_raw": n, "err": null}

`q` is one of
    {"kind": "ident", "terms": [...]}    identifiers, in the order of the question
    {"kind": "terms", "terms": [...]}    identifiers and content words
    {"kind": "nat", "text": "...", "terms": [...]}   the question itself

A path is written as <repo>/<path within the repository>, the unit L1 grades.

Ranking rules (pre-registration)
--------------------------------
Every adapter returns an ordered list; the order *is* the ranking. The rules
that turn a tool without a ranking into one:

  definitions before references   ctags, GNU global, cscope, Serena. Within
                                  each part the tool's own output order is
                                  kept (all three print paths in order).
  matches, then path              grep, wikictl, ast-grep: the number of
                                  distinct query terms that a file matches
                                  (descending), then the number of matches
                                  (descending), then the path (ascending).
                                  This is the plan's rule for the standard
                                  family, used for every tool that only says
                                  "matched" or "did not match".
  the tool's own order            probe, zoekt, qmd, semble, ck,
                                  codebase-memory-mcp, codegraph (score),
                                  graphify (breadth-first order from the
                                  symbol), GitNexus (definitions, then the
                                  symbols of the processes it returns).
  several identifiers             a question may name more than one. Each is
                                  searched in the order it appears in the
                                  question and the answers are concatenated;
                                  a path already seen is not repeated.
  several repositories            a corpus can hold two (c1). A tool that is
                                  called once per repository and gives
                                  comparable scores is merged by score;
                                  a tool that gives none is interleaved
                                  round-robin in the order of $REPOS, so that
                                  neither repository is favoured.
  ties                            a tie on every key above is broken by the
                                  path ascending. The order is therefore
                                  deterministic and does not depend on the
                                  gold.
  cut-off                         at most k (50) paths are kept per search;
                                  the metrics are computed at 10.
"""
import json
import os
import re
import subprocess
import sys
import time

TOOL = os.environ["L1_TOOL"]
CORPUS = os.environ["CORPUS"]
REPOS = os.environ["REPOS"].split()
WORK = "/work"
INDEX = "/index"


def sh(cmd, cwd=None, env=None, timeout=600, check=False):
    e = dict(os.environ)
    e.update(env or {})
    p = subprocess.run(cmd, cwd=cwd, env=e, capture_output=True, text=True,
                       timeout=timeout, errors="replace")
    if check and p.returncode not in (0, 1):
        raise RuntimeError(f"{cmd[:3]} exit {p.returncode}: {p.stderr[-400:]}")
    return p.stdout


def rel(path, repo):
    """Make a tool's path into <repo>/<path within the repository>."""
    path = path.strip()
    if path.startswith(f"{WORK}/"):
        path = path[len(WORK) + 1:]
        return path
    path = path.lstrip("./")
    return f"{repo}/{path}" if repo and not path.startswith(f"{repo}/") else path


def by_count(counts):
    """{path: (terms, matches)} -> the plan's rule for the standard family."""
    return [[p, list(v)] for p, v in sorted(counts.items(), key=lambda kv: (-kv[1][0], -kv[1][1], kv[0]))]


def merge_score(parts):
    """Several repositories, comparable scores: one list by score."""
    out = [(p, s) for part in parts for p, s in part]
    return [[p, s] for p, s in sorted(out, key=lambda x: (-(x[1] or 0), x[0]))]


def merge_rr(parts):
    """Several repositories, no comparable score: round-robin in $REPOS order."""
    out = []
    for i in range(max((len(p) for p in parts), default=0)):
        for part in parts:
            if i < len(part):
                out.append(part[i])
    return out


def dedupe(pairs, k):
    seen, out = set(), []
    for p, s in pairs:
        if p in seen:
            continue
        seen.add(p)
        out.append([p, s])
        if len(out) >= k:
            break
    return out


# --------------------------------------------------------------------------
class Tool:
    note = None

    def start(self):
        return {}

    def version(self):
        return None

    def search(self, q, k):
        raise NotImplementedError


class Grep(Tool):
    """The standard family: git grep over the working tree of each repository."""
    note = "git grep -I -c -F -i, one call per term and repository"

    def search(self, q, k):
        counts = {}
        for repo in REPOS:
            for t in q["terms"]:
                out = sh(["git", "grep", "-I", "-c", "-F", "-i", "-e", t], cwd=f"{WORK}/{repo}")
                for line in out.splitlines():
                    path, _, n = line.rpartition(":")
                    if not path or not n.isdigit():
                        continue
                    c = counts.setdefault(f"{repo}/{path}", [0, 0])
                    c[0] += 1
                    c[1] += int(n)
        return by_count(counts), len(counts)


class Ctags(Tool):
    note = "readtags -n -e -, definitions only (ctags has no references)"

    def search(self, q, k):
        out, n = [], 0
        for t in q["terms"]:
            for repo in REPOS:
                tags = f"{INDEX}/{repo}.tags"
                if not os.path.exists(tags):
                    continue
                for line in sh(["readtags", "-t", tags, "-n", "-e", "-", t]).splitlines():
                    f = line.split("\t")
                    if len(f) > 1:
                        n += 1
                        out.append([rel(f[1], repo), None])
        return out, n


class Global(Tool):
    note = "global -x (definitions), then global -rx (references)"

    def search(self, q, k):
        out, n = [], 0
        for phase, flag in (("def", "-x"), ("ref", "-rx")):
            for t in q["terms"]:
                for repo in REPOS:
                    db = f"{INDEX}/{repo}"
                    if not os.path.isdir(db):
                        continue
                    env = {"GTAGSDBPATH": db, "GTAGSROOT": f"{WORK}/{repo}"}
                    for line in sh(["global", flag, "--", t], cwd=f"{WORK}/{repo}", env=env).splitlines():
                        f = line.split(None, 3)
                        if len(f) >= 3:
                            n += 1
                            out.append([f"{repo}/{f[2]}", phase])
        return out, n


class Cscope(Tool):
    note = "cscope -L -1 (definitions), then -L -3 (callers)"

    def search(self, q, k):
        out, n = [], 0
        repo = REPOS[0]
        for phase, flag in (("def", "-1"), ("ref", "-3")):
            for t in q["terms"]:
                for line in sh(["cscope", "-d", "-L", flag, t, "-f", f"{INDEX}/cscope.out"],
                               cwd=f"{WORK}/{repo}").splitlines():
                    f = line.split(None, 3)
                    if len(f) >= 3:
                        n += 1
                        out.append([rel(f[0], repo), phase])
        return out, n


class AstGrep(Tool):
    note = "ast-grep run --pattern <identifier>, ranked by the number of matches"

    def search(self, q, k):
        counts, n = {}, 0
        for t in q["terms"]:
            for repo in REPOS:
                out = sh(["ast-grep", "run", "--pattern", t, "--heading", "never", f"{WORK}/{repo}"],
                         timeout=900)
                for line in out.splitlines():
                    m = re.match(r"^(/work/[^:]+):(\d+):", line)
                    if m:
                        n += 1
                        c = counts.setdefault(rel(m.group(1), repo), [0, 0])
                        c[0], c[1] = 1, c[1] + 1
        return by_count(counts), n


class Probe(Tool):
    note = "probe search <terms>, BM25, one call per repository, merged by score"

    def search(self, q, k):
        parts, n = [], 0
        query = " ".join(q["terms"])
        for repo in REPOS:
            out = sh(["probe", "search", query, f"{WORK}/{repo}", "--max-results", str(k),
                      "--format", "json"], timeout=900)
            i = out.find("{")
            if i < 0:
                continue
            try:
                d = json.loads(out[i:])
            except ValueError:
                continue
            part = [(rel(r["file"], repo), r.get("score")) for r in d.get("results", [])]
            n += len(part)
            parts.append(part)
        return merge_score(parts), n


class Zoekt(Tool):
    note = 'zoekt -r "t1 or t2 or ...", the tool\'s own ranking'

    def search(self, q, k):
        if not q["terms"]:
            return [], 0
        query = " or ".join('"%s"' % t.replace('"', "") for t in q["terms"])
        out = sh(["zoekt", "-index_dir", f"{INDEX}/shards", "-r", query], timeout=900)
        pairs, n = [], 0
        for line in out.splitlines():
            m = re.match(r"^([^\s:]+):(\d+):", line)
            if m:
                n += 1
                pairs.append([m.group(1), None])
        return pairs, n


class Wikictl(Tool):
    note = "wikictl grep -n -i -F, one call per term; the standard family's rule"

    def start(self):
        repo = {"c1": "wiki", "c4": "website"}[CORPUS]
        os.environ["XDG_CACHE_HOME"] = f"{INDEX}/cache"
        os.environ["WIKICTL_CONFIG"] = f"{INDEX}/wikictl.yaml"
        self.repo = repo
        return {"repo": repo}

    def search(self, q, k):
        counts = {}
        for t in q["terms"]:
            hits = {}
            for line in sh(["wikictl", "grep", "-n", "-i", "-F", "--", t]).splitlines():
                m = re.match(r"^([^:]+):(\d+):", line)
                if m:
                    hits[f"{self.repo}/{m.group(1)}"] = hits.get(f"{self.repo}/{m.group(1)}", 0) + 1
            for p, c in hits.items():
                v = counts.setdefault(p, [0, 0])
                v[0] += 1
                v[1] += c
        return by_count(counts), len(counts)


class Semble(Tool):
    note = "semble search --format json (code only, semble's default content)"

    def start(self):
        os.environ["SEMBLE_CACHE_LOCATION"] = f"{INDEX}/semble"
        lst = f"{INDEX}/semble-repos"
        self.repos = [r.strip() for r in open(lst)] if os.path.exists(lst) else REPOS
        self.repos = [r for r in self.repos if r]
        # one search to load the model and the index (the resident part)
        sh(["semble", "search", "areL1Warmup"] + [f"{WORK}/{r}" for r in self.repos] +
           ["-k", "1", "--format", "json", "--max-snippet-lines", "0"], timeout=1800)
        return {"repos": self.repos}

    def search(self, q, k):
        out = sh(["semble", "search", q["text"]] + [f"{WORK}/{r}" for r in self.repos] +
                 ["-k", str(k), "--format", "json", "--max-snippet-lines", "0"], timeout=1800)
        i = out.find("{")
        if i < 0:
            return [], 0
        d = json.loads(out[i:])
        pairs = []
        for r in d.get("results", []):
            p = r["file_path"]
            if not any(p == x or p.startswith(x + "/") for x in self.repos):
                owner = next((x for x in self.repos if os.path.exists(f"{WORK}/{x}/{p}")), self.repos[0])
                p = f"{owner}/{p}"
            pairs.append([p, r.get("score")])
        return pairs, len(pairs)


class Ck(Tool):
    note = "ck --hybrid --topk k --jsonl (regex and semantic fused with RRF)"

    def start(self):
        os.environ["XDG_CACHE_HOME"] = "/opt/ck-cache"
        os.environ["NO_COLOR"] = "1"
        self.repos = [r for r in REPOS if os.path.isdir(f"{WORK}/{r}/.ck")]
        for r in self.repos:
            sh(["ck", "--hybrid", "--topk", "1", "--jsonl", "--no-snippet", "areL1Warmup", "."],
               cwd=f"{WORK}/{r}", timeout=1800)
        return {"repos": self.repos}

    def search(self, q, k):
        parts, n = [], 0
        for r in self.repos:
            out = sh(["ck", "--hybrid", "--topk", str(k), "--jsonl", "--no-snippet", q["text"], "."],
                     cwd=f"{WORK}/{r}", timeout=1800)
            part = []
            for line in out.splitlines():
                if not line.startswith("{"):
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if "path" in d:
                    part.append((rel(d["path"], r), d.get("score")))
            n += len(part)
            parts.append(part)
        return merge_score(parts), n


class Qmd(Tool):
    note = "qmd query --no-rerank (BM25 and vectors, RRF). qmd expands the query with its own local LLM: that LLM is part of the tool"

    def start(self):
        os.makedirs(f"{INDEX}/cache/qmd", exist_ok=True)
        os.makedirs(os.environ.get("QMD_CONFIG_DIR", f"{INDEX}/config"), exist_ok=True)
        link = f"{INDEX}/cache/qmd/models"
        if not os.path.exists(link):
            os.symlink("/opt/qmd-models", link)
        # L0 noted that qmd keeps the expansions in llm_cache, which makes a
        # repeated question look fast: start from nothing.
        cache = f"{INDEX}/cache/qmd/llm_cache"
        removed = os.path.isdir(cache)
        if removed:
            subprocess.run(["rm", "-rf", cache])
        sh(["qmd", "status"], timeout=900)
        return {"llm_cache_cleared": removed}

    def search(self, q, k):
        out = sh(["qmd", "query", "--no-rerank", "-n", str(k), q["text"]], timeout=900)
        pairs = []
        for line in out.splitlines():
            m = re.match(r"^qmd://([^/]+)/(\S+?)(?::\d+(?:-\d+)?)?\s*(?:#\w+)?\s*$", line.strip())
            if m:
                pairs.append([f"{m.group(1)}/{m.group(2)}", None])
        return pairs, len(pairs)


class Cbm(Tool):
    note = "codebase-memory-mcp over MCP (its own way in), search_graph, resident"

    def start(self):
        self.p = subprocess.Popen(["codebase-memory-mcp"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                  text=True, bufsize=1)
        self.n = 0
        self.rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                "clientInfo": {"name": "are-l1", "version": "1"}})
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.p.stdin.flush()
        projects = self.rpc("tools/call", {"name": "list_projects", "arguments": {}})
        self.projects = [f"work-{r}" for r in REPOS]
        return {"projects": self.projects, "listed": str(projects)[:200]}

    def rpc(self, method, params):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method,
                                       "params": params}) + "\n")
        self.p.stdin.flush()
        while True:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("MCP server closed")
            line = line.strip()
            if line.startswith("{"):
                d = json.loads(line)
                if d.get("id") == self.n:
                    return d.get("result")

    def search(self, q, k):
        parts, n = [], 0
        for proj in self.projects:
            r = self.rpc("tools/call", {"name": "search_graph",
                                        "arguments": {"project": proj, "query": q["text"], "limit": k}})
            text = "".join(c.get("text", "") for c in (r or {}).get("content", []))
            repo = proj[len("work-"):]
            part = []
            for line in text.splitlines():
                if not line.startswith("  "):
                    continue
                f = line.split()
                if len(f) >= 5 and ("/" in f[2] or "." in f[2]):
                    part.append((f"{repo}/{f[2]}", f[4]))
            n += len(part)
            parts.append(part)
        return merge_rr(parts), n


class Codegraph(Tool):
    note = "codegraph query <identifier> --json, the tool's own score"

    def search(self, q, k):
        parts, n = [], 0
        for t in q["terms"]:
            for repo in REPOS:
                out = sh(["codegraph", "query", t, "-p", f"{WORK}/{repo}", "--limit", str(k), "--json"],
                         timeout=900)
                i = out.find("[")
                if i < 0:
                    continue
                try:
                    d = json.loads(out[i:])
                except ValueError:
                    continue
                part = [(f"{repo}/{x['node']['filePath']}", x.get("score"))
                        for x in d if x.get("node", {}).get("filePath")]
                n += len(part)
                parts.append(part)
        return merge_score(parts), n


class Gitnexus(Tool):
    note = "gitnexus query <concept> (its own BM25 and vector search over the graph); definitions, then the symbols of the processes"

    def start(self):
        os.makedirs(os.environ["HOME"], exist_ok=True)
        link = os.path.join(os.environ["HOME"], ".lbdb")
        if not os.path.exists(link):
            os.symlink("/opt/lbdb/.lbdb", link)
        return {}

    def search(self, q, k):
        parts, n = [], 0
        for repo in REPOS:
            out = sh(["gitnexus", "query", q["text"], "-r", repo, "-l", "10"], timeout=900)
            i = out.find("{")
            if i < 0:
                continue
            try:
                d, _ = json.JSONDecoder().raw_decode(out[i:])
            except ValueError:
                continue
            part = []
            for key in ("definitions", "process_symbols"):
                for x in d.get(key, []):
                    if x.get("filePath"):
                        part.append((f"{repo}/{x['filePath']}", key))
            n += len(part)
            parts.append(part)
        return merge_rr(parts), n


class Graphify(Tool):
    note = "graphify query <identifier> --budget 4000; the breadth-first order of the nodes"

    def search(self, q, k):
        out_pairs, n = [], 0
        for t in q["terms"]:
            for repo in REPOS:
                g = f"{WORK}/{repo}/graphify-out/graph.json"
                if not os.path.exists(g):
                    continue
                out = sh(["graphify", "query", t, "--graph", g, "--budget", "4000"], timeout=900)
                for line in out.splitlines():
                    m = re.match(r"^NODE .*?\[src=([^\s\]]*)", line)
                    if m and m.group(1):
                        n += 1
                        out_pairs.append([f"{repo}/{m.group(1)}", None])
        return out_pairs, n


class Serena(Tool):
    note = "Serena's own tools in process (find_symbol, then find_referencing_symbols); the language servers stay up"

    def start(self):
        from serena.agent import SerenaAgent
        from serena.config.serena_config import LanguageBackend, SerenaConfig
        from serena.tools import FindReferencingSymbolsTool, FindSymbolTool
        os.makedirs(os.path.join(os.environ["HOME"], ".serena"), exist_ok=True)
        cfg = os.path.join(os.environ["HOME"], ".serena", "serena_config.yml")
        if not os.path.exists(cfg):
            subprocess.run(["cp", "/opt/l0/serena_config.yml", cfg], check=True)
        config = SerenaConfig.from_config_file()
        config.web_dashboard = False
        config.gui_log_window = False
        config.language_backend = LanguageBackend.LSP
        self.agents = {}
        for repo in REPOS:
            if not os.path.isdir(f"{WORK}/{repo}/.serena"):
                continue
            a = SerenaAgent(project=f"{WORK}/{repo}", serena_config=config)
            self.agents[repo] = (a, a.get_tool(FindSymbolTool), a.get_tool(FindReferencingSymbolsTool))
            # warm the language server up
            a.execute_task(lambda a=a: a.get_tool(FindSymbolTool).apply(name_path_pattern="main", include_info=False))
        return {"repos": list(self.agents)}

    def search(self, q, k):
        defs, refs, n = [], [], 0
        for t in q["terms"]:
            for repo, (a, find, findrefs) in self.agents.items():
                try:
                    r = a.execute_task(lambda: find.apply(name_path_pattern=t, include_info=False))
                    found = json.loads(r)
                except Exception:
                    continue
                if not isinstance(found, list):
                    continue
                for x in found[:k]:
                    if x.get("relative_path"):
                        n += 1
                        defs.append([f"{repo}/{x['relative_path']}", "def"])
                if found:
                    first = found[0]
                    try:
                        r2 = a.execute_task(lambda: findrefs.apply(name_path=first.get("name_path", t),
                                                                   relative_path=first.get("relative_path", "")))
                        for x in json.loads(r2)[:k]:
                            if x.get("relative_path"):
                                n += 1
                                refs.append([f"{repo}/{x['relative_path']}", "ref"])
                    except Exception:
                        pass
        return defs + refs, n


TOOLS = {"grep": Grep, "ctags": Ctags, "global": Global, "cscope": Cscope,
         "ast-grep": AstGrep, "probe": Probe, "zoekt": Zoekt, "wikictl": Wikictl,
         "semble": Semble, "ck": Ck, "qmd": Qmd, "codebase-memory-mcp": Cbm,
         "codegraph": Codegraph, "gitnexus": Gitnexus, "graphify": Graphify,
         "serena": Serena}


def main():
    tool = TOOLS[TOOL]()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        t0 = time.monotonic()
        try:
            if msg["op"] == "start":
                note = tool.start()
                out = {"t_s": round(time.monotonic() - t0, 3), "note": tool.note, "start": note}
            else:
                q = msg["q"]
                if not (q.get("terms") or q.get("text")):
                    out = {"paths": [], "t_s": 0.0, "n_raw": 0, "err": "empty_query"}
                else:
                    pairs, n = tool.search(q, msg.get("k", 50))
                    out = {"paths": dedupe(pairs, msg.get("k", 50)),
                           "t_s": round(time.monotonic() - t0, 3), "n_raw": n, "err": None}
        except Exception as e:  # one question failing must not lose the cell
            out = {"paths": [], "t_s": round(time.monotonic() - t0, 3), "n_raw": 0,
                   "err": f"{type(e).__name__}: {e}"[:400]}
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
