#!/usr/bin/env python3
"""Summarize grade.py's output as Markdown tables.

    grade/summarize.py scores.jsonl

Per model and condition: sessions, correct, evidence hits, sessions without a
valid answer.json, how many sessions used the tool the condition added and how
often, and the medians of wall time, turns, tool calls, input and output
tokens, and USD (when the model has a price). Then one row per session.
"""
import json
import statistics
import sys


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def fmt(x, nd=0):
    if x is None:
        return "-"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def per_tool(r):
    """"<tool>:<calls>" for the tools the condition added."""
    return ",".join(f"{k}:{v['calls']}" for k, v in (r.get("per_tool") or {}).items()) or "-"


def used(rs):
    """How many of the sessions called the tool their condition added."""
    with_tool = [r for r in rs if r.get("added_tools")]
    if not with_tool:
        return "-"
    return f"{sum(1 for r in with_tool if r.get('added_tool_used'))}/{len(with_tool)}"


def tokens(r, key):
    return sum(u.get(key, 0) for u in r["tokens"].values()) if r["tokens"] else None


def main():
    rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
    groups = {}
    for r in rows:
        groups.setdefault((r["model"], r["cond"]), []).append(r)
    print("| model | cond | n | correct | evidence | no answer.json | used tool | tool calls | tool KB | "
          "wall s (median) | turns | tool calls (all) | input tok | output tok | USD |")
    print("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for (m, c), rs in sorted(groups.items()):
        ev = [r for r in rs if r["evidence_hit"] is not None]
        print(f"| {m} | {c} | {len(rs)} | {sum(1 for r in rs if r['correct'])}{'' if all(r['correct'] is not None for r in rs) else ' (+' + str(sum(1 for r in rs if r['correct'] is None)) + ' to judge)'} | "
              f"{sum(bool(r['evidence_hit']) for r in ev)}/{len(ev)} | {sum(not r['answer_valid'] for r in rs)} | "
              f"{used(rs)} | {fmt(med([r.get('added_tool_calls') for r in rs]))} | "
              f"{fmt(med([(r.get('added_tool_bytes') or 0) / 1024 for r in rs]), 1)} | "
              f"{fmt(med([r['wall_ms'] / 1000 for r in rs]))} | {fmt(med([r['turns'] for r in rs]))} | "
              f"{fmt(med([sum(r['tool_calls'].values()) for r in rs]))} | "
              f"{fmt(med([tokens(r, 'inputTokens') + tokens(r, 'cacheReadInputTokens') + tokens(r, 'cacheCreationInputTokens') if r['tokens'] else None for r in rs]))} | "
              f"{fmt(med([tokens(r, 'outputTokens') for r in rs]))} | {fmt(med([r['usd'] for r in rs]), 3)} |")
    print()
    print("| session | correct | answer | evidence | wall s | turns | tools | shell cmds | added tool | result |")
    print("|---|---|---|---|---:|---:|---|---:|---|---|")
    for r in rows:
        ans = json.dumps(r["answer"], ensure_ascii=False)
        ans = ans if len(ans) <= 40 else ans[:37] + "..."
        tools = ",".join(f"{k}{v}" for k, v in sorted(r["tool_calls"].items()))
        print(f"| {r['session']} | {'?' if r['correct'] is None else '✓' if r['correct'] else '✗'} | {ans} | {fmt(r['evidence_hit'])} | "
              f"{r['wall_ms'] / 1000:.0f} | {fmt(r['turns'])} | {tools} | {r['shell_commands']} | "
              f"{per_tool(r)} | "
              f"{r['result_subtype']}{' timeout' if r['timed_out'] else ''} |")


if __name__ == "__main__":
    main()
