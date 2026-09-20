#!/usr/bin/env python3
"""Grade sessions and extract their cost and time.

    grade/grade.py [--judgments judged.jsonl] <session dir or label dir>... > scores.jsonl

One line per session: correctness of the answer, whether the evidence names a
gold file, tokens and USD from the result event, tool calls and the bytes they
returned (from the stream), the shell commands the agent ran (from cmdlog.tsv,
without the ones Claude Code runs itself), and whether the session used the
tool its condition added (added_tool_used / added_tool_calls /
added_tool_bytes / per_tool, and mcp_servers for the state of the servers).

Gold types:
  path      the answer names this file (relative to the repository in "repo")
  version   the answer is this version, with or without the leading "v"
  int       the answer is this integer
  keywords  the answer contains every keyword
  null      the corpus has no answer; the answer must be null
  date      the answer starts with this date ("YYYY-MM" or "YYYY-MM-DD")
  set       the answer is a list; precision, recall and F1 against "value",
            correct when the sets are equal. "match" says how an item is
            compared: "name" (the string), "func" ("<path>:<name>", where the
            answer may leave out the path or the receiver when the name alone
            is unambiguous within the gold). Items in "optional" are
            accepted without being required
  files     the answer is a list of paths; precision, recall and F1. With
            "match": "any", correct when one gold file is named; otherwise
            correct when every gold file is named. Paths in "optional" are
            neither required nor counted against precision: S2 puts there the
            files a patch touched besides the main one (the main file is
            required, what came with it may be named or not), S9 the files
            that only carry the name in a list
  rubric    free text judged against "points" by the LLM judge (and people):
            correct is null here and filled from --judgments (lines of
            {"session", "correct"})

Evidence: "evidence" is a list of paths relative to /corpus. A path that ends
with "/" accepts every file under it, and a path may be a glob (fnmatch). A
question with an empty list has no evidence to check.
"""
import argparse
import collections
import csv
import fnmatch
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))
import run as R  # noqa: E402
from run import load_questions  # noqa: E402

# USD per million tokens of input and output; cache reads and writes are
# multiples of the input price.
PRICES = json.load(open(os.path.join(ROOT, "grade", "prices.json")))


def norm_path(p, repo=None):
    p = str(p).strip().strip("`")
    p = re.sub(r"^(\./)+", "", p)
    # The corpus is mounted at /corpus and at /work; a tool's output names the
    # second. No repository of the corpora is called corpus or work.
    p = re.sub(r"^/?(corpus|work)/", "", p)
    if repo and p.startswith(repo + "/"):
        p = p[len(repo) + 1:]
    return p


def as_list(answer):
    if answer is None:
        return []
    if isinstance(answer, list):
        return [str(a) for a in answer]
    return [a for a in re.split(r"[\n,]+", str(answer)) if a.strip()]


def func_key(item, repo):
    """(path or None, name) of "<path>:<name>" / "<name>"; the receiver of a
    method stays in the name."""
    item = str(item).strip().strip("`").strip()
    item = re.sub(r"\(\)$", "", item)
    if ":" in item:
        path, name = item.rsplit(":", 1)
        return norm_path(path, repo), name.strip()
    return None, item


def func_match(a, keys, repo, taken):
    path, name = func_key(a, repo)
    for i, (gp, gn) in enumerate(keys):
        if i in taken:
            continue
        same_name = name == gn or (("." in gn) and name == gn.split(".", 1)[1]
                                   and sum(1 for _, n in keys if n.split(".")[-1] == name) == 1)
        if same_name and (path is None or path == gp):
            return i
    return None


def set_scores(answer, gold):
    """Items in "optional" (callers through a function pointer or an
    interface, for S3) are neither required nor counted against precision."""
    got = as_list(answer)
    value = gold["value"]
    optional = gold.get("optional") or []
    if gold.get("match") == "func":
        keys = [func_key(v, gold.get("repo")) for v in value]
        okeys = [func_key(v, gold.get("repo")) for v in optional]
        matched, hit, extra = set(), 0, []
        for a in got:
            i = func_match(a, keys, gold.get("repo"), matched)
            if i is not None:
                matched.add(i)
                hit += 1
            elif func_match(a, okeys, gold.get("repo"), set()) is None:
                extra.append(a)
        tp = hit
        got = [None] * hit + extra
    else:
        gs = {str(v).strip() for v in value}
        norm = {a.strip().strip("`") for a in got} - {str(v).strip() for v in optional}
        tp = len(norm & gs)
        got = list(norm)
    p = tp / len(got) if got else 0.0
    r = tp / len(value) if value else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}, tp == len(value) == len(got)


def files_scores(answer, gold):
    """Correct when every path of "value" is named (or one of them with
    "match": "any"). A path in "optional" is neither required nor counted
    against precision, and a path in neither is counted against precision but
    does not by itself make the answer wrong."""
    got = {norm_path(a, gold.get("repo")) for a in as_list(answer)}
    value = set(gold["value"])
    optional = {norm_path(o, gold.get("repo")) for o in (gold.get("optional") or [])}
    tp = len(got & value)
    got = got - (optional - value)
    p = tp / len(got) if got else 0.0
    r = tp / len(value) if value else 0.0
    f1 = 2 * p * r / (p + r) if p + r else 0.0
    ok = tp >= 1 if gold.get("match") == "any" else tp == len(value)
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4)}, ok


def correct(answer, gold):
    """True/False, or None when a judge has to decide (rubric)."""
    t = gold["type"]
    if t == "null":
        return answer is None
    if answer is None:
        return False
    if t == "path":
        return norm_path(answer, gold.get("repo")) == gold["value"]
    if t == "version":
        return str(answer).strip().lstrip("vV") == str(gold["value"]).lstrip("vV")
    if t == "int":
        try:
            return int(str(answer).strip().lstrip("#")) == gold["value"]
        except ValueError:
            return False
    if t == "keywords":
        return all(k in str(answer) for k in gold["value"])
    if t == "date":
        return str(answer).strip().startswith(gold["value"])
    if t == "set":
        return set_scores(answer, gold)[1]
    if t == "files":
        return files_scores(answer, gold)[1]
    if t == "rubric":
        return None
    raise ValueError(f"unknown gold type {t}")


def partial(answer, gold):
    if gold["type"] == "set":
        return set_scores(answer, gold)[0]
    if gold["type"] == "files":
        return files_scores(answer, gold)[0]
    return None


def evidence_hit(evidence, accept):
    """Whether one of the answer's evidence paths falls in the accepted range."""
    for e in evidence:
        for a in accept:
            if a.endswith("/") and e.startswith(a):
                return True
            if e == a or fnmatch.fnmatchcase(e, a):
                return True
    return False


def stream_stats(path):
    """Tool calls by name, the bytes each tool gave back, the MCP servers of
    the session and their state, and the result event.

    An MCP call is a tool_use whose name is mcp__<server>__<tool>; the bytes
    are the tool_result that carries the same tool_use_id (the text the agent
    was given, which is what a session pays for)."""
    tools, bytes_, ids, mcp, result = {}, {}, {}, None, None
    for line in open(path):
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if j.get("type") == "system" and j.get("subtype") == "init" and mcp is None:
            mcp = {s.get("name"): s.get("status") for s in j.get("mcp_servers", [])}
        elif j.get("type") == "assistant":
            for c in j["message"].get("content", []):
                if c.get("type") == "tool_use":
                    tools[c["name"]] = tools.get(c["name"], 0) + 1
                    ids[c.get("id")] = c["name"]
        elif j.get("type") == "user":
            for c in (j.get("message") or {}).get("content", []) or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    name = ids.get(c.get("tool_use_id"))
                    if name is None:
                        continue
                    body = c.get("content")
                    if isinstance(body, list):
                        n = sum(len(x.get("text", "").encode()) for x in body if isinstance(x, dict))
                    else:
                        n = len(str(body or "").encode())
                    bytes_[name] = bytes_.get(name, 0) + n
        elif j.get("type") == "result":
            result = j
    return tools, bytes_, mcp or {}, result


def usd(model, u, share_1h):
    """share_1h is the fraction of cache writes kept for 1 hour, from the
    result's usage; the rest are 5-minute writes."""
    p = PRICES.get(model)
    if p is None:
        return None
    write = 2.0 * share_1h + 1.25 * (1 - share_1h)
    return (u.get("inputTokens", 0) * p["input"] + u.get("outputTokens", 0) * p["output"]
            + u.get("cacheReadInputTokens", 0) * p["input"] * 0.1
            + u.get("cacheCreationInputTokens", 0) * p["input"] * write) / 1e6


def share_1h(result):
    cc = ((result or {}).get("usage") or {}).get("cache_creation") or {}
    h1, m5 = cc.get("ephemeral_1h_input_tokens", 0), cc.get("ephemeral_5m_input_tokens", 0)
    return h1 / (h1 + m5) if h1 + m5 else 0.0


def commands(path):
    """The agent's shell commands: those whose nearest callers include a shell.
    Claude Code runs git itself (no shell among the callers), and on the
    first Bash call it runs three bare `cat` while preparing the shell; bare
    `cat` before the agent's first other command is left out."""
    out = []
    with open(path) as f:
        for row in csv.DictReader(f, delimiter="\t"):
            callers = (row.get("callers") or "").split(",")
            if not any(c in ("bash", "sh", "dash", "zsh") for c in callers):
                continue
            if row["argv"] == "cat" and not out:
                continue
            out.append({"cmd": row["argv"].split(" ", 1)[0], "ms": int(row["ms"]),
                        "stdout_bytes": int(row["stdout_bytes"]), "exit": int(row.get("exit") or 0)})
    return out


def tool_usage(cond, tools, tool_bytes, cmds):
    """Whether the session used the tool the condition added, and how much.

    A CLI tool is counted from the shell commands it is called by (the
    logging wrapper sees every call), an MCP tool from the tool_use events of
    its server. "bytes" is what came back: the stdout of the commands, or the
    text of the tool results."""
    calls, byts, per = 0, 0, {}
    for t in R.cond_tools(cond):
        spec = R.TOOLS[t]
        if spec["kind"] == "cli":
            names = set(spec.get("cmds", {}))
            n = sum(1 for c in cmds if c["cmd"] in names)
            b = sum(c["stdout_bytes"] for c in cmds if c["cmd"] in names)
        else:
            prefix = f"mcp__{spec['server']}__"
            n = sum(v for k, v in tools.items() if k.startswith(prefix))
            b = sum(v for k, v in tool_bytes.items() if k.startswith(prefix))
        per[t] = {"calls": n, "bytes": b}
        calls, byts = calls + n, byts + b
    return {"added_tools": R.cond_tools(cond), "added_tool_calls": calls,
            "added_tool_bytes": byts, "added_tool_used": calls > 0, "per_tool": per}


def grade(d, qs, judged=None):
    meta = json.load(open(os.path.join(d, "meta.json")))
    q = qs[meta["question"]]
    row = {k: meta[k] for k in ("session", "label", "question", "corpus", "scenario", "phrasing", "cond",
                                "backend", "model", "rep", "wall_ms", "exit", "timed_out")}
    try:
        ans = json.load(open(os.path.join(d, "work", "answer.json")))
        row["answer_valid"] = isinstance(ans, dict) and "answer" in ans
    except (FileNotFoundError, ValueError):
        ans, row["answer_valid"] = {}, False
    answer = ans.get("answer") if row["answer_valid"] else None
    row["answer"] = answer
    c = correct(answer, q["gold"]) if row["answer_valid"] else False
    if c is None and judged is not None:
        c = judged.get(meta["session"])
    row["correct"] = c
    row["partial"] = partial(answer, q["gold"]) if row["answer_valid"] else None
    evidence = {norm_path(e) for e in (ans.get("evidence") or [])} if row["answer_valid"] else set()
    gold_ev = q.get("evidence", [])
    row["evidence_hit"] = evidence_hit(evidence, gold_ev) if gold_ev else None
    tools, tool_bytes, mcp, result = stream_stats(os.path.join(d, "stream.jsonl"))
    row["tool_calls"] = tools
    row["tool_bytes"] = tool_bytes
    row["mcp_servers"] = mcp
    row["turns"] = result.get("num_turns") if result else None
    row["result_subtype"] = result.get("subtype") if result else None
    usage = (result or {}).get("modelUsage", {})
    row["tokens"] = {m: {k: u.get(k, 0) for k in ("inputTokens", "outputTokens", "cacheReadInputTokens",
                                                  "cacheCreationInputTokens")} for m, u in usage.items()}
    costs = [usd(m, u, share_1h(result)) for m, u in usage.items()]
    row["usd"] = None if not costs or any(c is None for c in costs) else round(sum(costs), 6)
    cmds = commands(os.path.join(d, "log", "cmdlog.tsv"))
    row["shell_commands"] = len(cmds)
    row["shell_ms"] = sum(c["ms"] for c in cmds)
    row["shell_stdout_bytes"] = sum(c["stdout_bytes"] for c in cmds)
    row["shell_by_cmd"] = dict(collections.Counter(c["cmd"] for c in cmds))
    row.update(tool_usage(meta["cond"], tools, tool_bytes, cmds))
    return row


def sessions(paths):
    for p in paths:
        if os.path.exists(os.path.join(p, "meta.json")):
            yield p
        else:
            for n in sorted(os.listdir(p)):
                if os.path.exists(os.path.join(p, n, "meta.json")):
                    yield os.path.join(p, n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judgments", help="JSON lines of {session, correct} for rubric questions")
    ap.add_argument("paths", nargs="+")
    a = ap.parse_args()
    judged = None
    if a.judgments:
        judged = {j["session"]: j["correct"] for j in map(json.loads, open(a.judgments)) if "session" in j}
    qs = load_questions()
    for d in sessions(a.paths):
        print(json.dumps(grade(d, qs, judged), ensure_ascii=False))


if __name__ == "__main__":
    main()
