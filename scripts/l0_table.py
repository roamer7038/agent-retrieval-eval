#!/usr/bin/env python3
"""Print the L0 records (results/pe1-l0.jsonl) as a Markdown table, the last
record of each tool (and variant) and corpus.

    scripts/l0_table.py [results/pe1-l0.jsonl]
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The families of the plan, in the order the table lists them.
FAMILIES = [
    ("シンボル索引", ["ctags", "global", "cscope"]),
    ("構文・字句", ["ast-grep", "probe", "zoekt"]),
    ("BM25・混合", ["qmd", "semble", "ck"]),
    ("グラフ", ["codebase-memory-mcp", "codegraph", "graphify", "gitnexus"]),
    ("LSP", ["serena"]),
    ("自作", ["wikictl"]),
]
ORDER = {t: (i, j) for i, (_, ts) in enumerate(FAMILIES) for j, t in enumerate(ts)}
FAMILY = {t: f for f, ts in FAMILIES for t in ts}
CORPORA = ["c1", "c2", "c3", "c4"]


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
        tool = r["tool"] + (f"（{r['variant']}）" if r.get("variant") else "")
        last[(tool, r["corpus"])] = r
    rows = sorted(last.items(), key=lambda kv: (ORDER.get(kv[1]["tool"], (9, 9)), kv[0][0], CORPORA.index(kv[0][1])))
    print("| 系統 | 道具 | 題材 | 結果 | slot | 準備 s | 索引 s | 索引 CPU s | 容量 MiB | メモリ MiB | 更新 s | 更新の反映 | 検索 s | 検索の一致 |")
    print("|---|---|---|---|---|--:|--:|--:|--:|--:|--:|---|--:|---|")
    for (tool, corpus), r in rows:
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
        hit = "" if not q else ("あり" if q.get("hit_symbol") else "なし")
        print(f"| {FAMILY.get(r['tool'], '')} | {tool} | {corpus} | {status} | {r.get('slot', '')} | {prep} | {secs(idx)} | {idx.get('cpu_s', '') if idx else ''} | "
              f"{mib(r.get('size_bytes'))} | {mib(mem)} | {secs(upd)} | {refl} | {secs(q)} | {hit} |")


if __name__ == "__main__":
    main()
