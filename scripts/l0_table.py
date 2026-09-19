#!/usr/bin/env python3
"""Print the L0 records (results/pe1-l0.jsonl) as a Markdown table, the last
record of each tool and corpus.

    scripts/l0_table.py [results/pe1-l0.jsonl]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def mib(b):
    return "" if b is None else f"{b / 2**20:,.0f}" if b >= 2**20 else f"{b / 2**20:.2f}"


def secs(p):
    if not p or p.get("wall_s") is None:
        return ""
    s = p["wall_s"]
    return f"{s:,.0f}" if s >= 100 else f"{s:.1f}" if s >= 1 else f"{s:.2f}"


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "results", "pe1-l0.jsonl")
    last = {}
    for line in open(path):
        r = json.loads(line)
        last[(r["tool"], r["corpus"])] = r
    print("| 道具 | 題材 | 結果 | 準備 s | 索引 s | 索引 CPU s | 容量 MiB | メモリ MiB | 更新 s | 更新の反映 | 検索 s |")
    print("|---|---|---|--:|--:|--:|--:|--:|--:|---|--:|")
    for (tool, corpus), r in last.items():
        ph = r["phases"]
        pre, idx, upd = ph.get("prepare"), ph.get("index"), ph.get("update")
        q, qa = ph.get("query"), ph.get("query_after_update")
        status = r.get("status")
        if status in ("failed", "timeout"):
            status += f"（{r.get('failed_phase')}）"
        # Anonymous memory of the container (sampled every 0.2 s), or the largest
        # process's RSS when the phase was too short to be sampled.
        mem = max(max(p.get("mem_anon_peak_bytes") or 0, p.get("maxrss_child_bytes") or 0) for p in (pre, idx) if p) if idx else None
        prep = secs(pre) if pre and pre.get("wall_s", 0) >= 1 else ""
        refl = "" if not qa else ("あり" if qa.get("hit_symbol") else "なし")
        print(f"| {tool} | {corpus} | {status} | {prep} | {secs(idx)} | {idx.get('cpu_s', '') if idx else ''} | "
              f"{mib(r.get('size_bytes'))} | {mib(mem)} | {secs(upd)} | {refl} | {secs(q)} |")


if __name__ == "__main__":
    main()
