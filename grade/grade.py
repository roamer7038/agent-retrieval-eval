#!/usr/bin/env python3
"""Grade sessions and extract their cost and time.

    grade/grade.py <session dir or label dir>... > scores.jsonl

One line per session: correctness of the answer, whether the evidence names a
gold file, tokens and USD from the result event, tool calls from the stream,
and the shell commands the agent ran (from cmdlog.tsv, without the ones Claude
Code runs itself).

Gold types:
  path      the answer names this file (relative to the repository in "repo")
  version   the answer is this version, with or without the leading "v"
  int       the answer is this integer
  keywords  the answer contains every keyword
  null      the corpus has no answer; the answer must be null
"""
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))
from run import load_questions  # noqa: E402

# USD per million tokens of input and output; cache reads and writes are
# multiples of the input price.
PRICES = json.load(open(os.path.join(ROOT, "grade", "prices.json")))


def norm_path(p, repo=None):
    p = str(p).strip().strip("`")
    p = re.sub(r"^(\./)+", "", p)
    p = re.sub(r"^/?corpus/", "", p)
    if repo and p.startswith(repo + "/"):
        p = p[len(repo) + 1:]
    return p


def correct(answer, gold):
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
            return int(str(answer).strip()) == gold["value"]
        except ValueError:
            return False
    if t == "keywords":
        return all(k in str(answer) for k in gold["value"])
    raise ValueError(f"unknown gold type {t}")


def stream_stats(path):
    tools, result = {}, None
    for line in open(path):
        try:
            j = json.loads(line)
        except ValueError:
            continue
        if j.get("type") == "assistant":
            for c in j["message"].get("content", []):
                if c.get("type") == "tool_use":
                    tools[c["name"]] = tools.get(c["name"], 0) + 1
        elif j.get("type") == "result":
            result = j
    return tools, result


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
                        "stdout_bytes": int(row["stdout_bytes"])})
    return out


def grade(d, qs):
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
    row["correct"] = row["answer_valid"] and correct(answer, q["gold"])
    evidence = {norm_path(e) for e in (ans.get("evidence") or [])} if row["answer_valid"] else set()
    gold_ev = set(q.get("evidence", []))
    row["evidence_hit"] = bool(evidence & gold_ev) if gold_ev else None
    tools, result = stream_stats(os.path.join(d, "stream.jsonl"))
    row["tool_calls"] = tools
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
    qs = load_questions()
    for d in sessions(sys.argv[1:]):
        print(json.dumps(grade(d, qs), ensure_ascii=False))


if __name__ == "__main__":
    main()
