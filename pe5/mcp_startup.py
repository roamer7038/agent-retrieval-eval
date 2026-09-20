#!/usr/bin/env python3
"""MCP のサーバの立ち上げに、1 セッションあたり何秒かかるかを測る（LLM を使わない）。

    pe5/mcp_startup.py [--reps 3] > results/pe5-mcp-startup.jsonl

条件の環境（イメージ・索引の overlay・`mcp.json`）をそのまま作り（`harness/run.py`
の smoke）、Claude Code の代わりに小さな JSON-RPC の客を動かして、
`initialize` の応答までと `tools/list` の応答までの時間を測る。
Claude Code はセッションの最初に同じ握手をするので、これが実時間に乗る分である。
"""
import argparse
import base64
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "harness"))
import run as R  # noqa: E402

CLIENT = r'''
import json, os, subprocess, sys, time

cfg = json.load(open("/eval/mcp.json"))["mcpServers"]
out = {}
for name, spec in cfg.items():
    env = dict(os.environ)
    env.update(spec.get("env", {}))
    t0 = time.time()
    p = subprocess.Popen([spec["command"], *spec.get("args", [])], stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=env, text=True)

    def send(msg):
        p.stdin.write(json.dumps(msg) + "\n")
        p.stdin.flush()

    def wait(mid):
        while True:
            line = p.stdout.readline()
            if not line:
                return None
            try:
                j = json.loads(line)
            except ValueError:
                continue
            if j.get("id") == mid:
                return j

    send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
          "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                     "clientInfo": {"name": "are-pe5", "version": "1"}}})
    init = wait(1)
    t_init = time.time() - t0
    send({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
    send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tl = wait(2)
    t_tools = time.time() - t0
    tools = ((tl or {}).get("result") or {}).get("tools") or []
    out[name] = {"init_s": round(t_init, 3), "tools_s": round(t_tools, 3),
                 "n_tools": len(tools),
                 "tool_def_bytes": len(json.dumps(tools).encode()),
                 "instructions_bytes": len(((init or {}).get("result") or {}).get("instructions", "").encode()),
                 "ok": bool(init and tl)}
    p.kill()
print("@@" + json.dumps(out))
'''


def measure(cond, qid, rep):
    b64 = base64.b64encode(CLIENT.encode()).decode()
    argv = ["sh", "-c", f"echo {b64} | base64 -d | /usr/bin/python3 -"]
    out = subprocess.run([sys.executable, os.path.join(ROOT, "harness", "run.py"),
                          "smoke", "--question", qid, "--cond", cond, "--", *argv],
                         capture_output=True, text=True)
    line = [l for l in out.stdout.splitlines() if l.startswith("@@")]
    if not line:
        return {"cond": cond, "question": qid, "rep": rep, "error": (out.stdout + out.stderr)[-400:]}
    return {"cond": cond, "question": qid, "rep": rep, "servers": json.loads(line[-1][2:])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3)
    a = ap.parse_args()
    spec = json.load(open(os.path.join(ROOT, "pe5", "questions.json")))
    qs = R.load_questions()
    # MCP を持つ条件と、比べるための CLI・標準の条件（握手が無いことの確認）。
    conds = ["A1", "A2", "A4", "A5", "A6"]
    for cond in conds:
        for corpus in R.L2_CORPORA:
            if R.not_applicable(cond, corpus):
                continue
            qid = next(q for q, v in spec["questions"].items() if v["corpus"] == corpus)
            for rep in range(1, a.reps + 1):
                row = measure(cond, qid, rep)
                row["corpus"] = corpus
                print(json.dumps(row, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
