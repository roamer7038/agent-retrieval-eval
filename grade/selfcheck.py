#!/usr/bin/env python3
"""Check that grade/grade.py scores every question of a split, by making one
synthetic session with the gold answer and one with a wrong answer for each
question and comparing the verdicts.

    grade/selfcheck.py tasks/dev/pe2.jsonl <a directory to write the sessions to>

It exits 0 when, for every question, the gold answer is graded correct, an
answer that is wrong for that gold type is graded incorrect, evidence inside
the accepted range counts and evidence outside it does not. Rubric questions
are graded from a judgments file, so the check writes one with the expected
verdicts. Nothing here talks to a model; it only exercises the grader.
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WRONG = {"path": "no/such/file.go", "version": "v99.99.99", "int": -1, "date": "1970-01",
         "null": "存在する", "set": ["no/such/file.go:nope"], "files": ["no/such/file.go"],
         "keywords": "まったく違う答え", "rubric": "まったく違う答え"}


def gold_answer(q):
    g = q["gold"]
    t = g["type"]
    if t == "null":
        return None
    if t == "rubric":
        return "要点をすべて述べた回答（判定は judgments から）"
    if t in ("set", "files"):
        return list(g["value"])
    return g["value"]


def session(d, q, answer, evidence):
    os.makedirs(os.path.join(d, "work"), exist_ok=True)
    os.makedirs(os.path.join(d, "log"), exist_ok=True)
    json.dump({"session": os.path.basename(d), "label": "gradecheck", "question": q["id"],
               "corpus": q["corpus"], "scenario": q["scenario"], "phrasing": q["phrasing"],
               "cond": "A1", "backend": "local", "model": "none", "rep": 1, "wall_ms": 1,
               "exit": 0, "timed_out": False}, open(os.path.join(d, "meta.json"), "w"))
    json.dump({"answer": answer, "evidence": evidence}, open(os.path.join(d, "work", "answer.json"), "w"))
    with open(os.path.join(d, "stream.jsonl"), "w") as f:
        f.write(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}}) + "\n")
        f.write(json.dumps({"type": "result", "subtype": "success", "num_turns": 2,
                            "modelUsage": {}, "usage": {}}) + "\n")
    with open(os.path.join(d, "log", "cmdlog.tsv"), "w") as f:
        f.write("argv\tms\tstdout_bytes\tcallers\n")
        f.write("git grep x\t10\t100\tbash,claude\n")


def main():
    tasks, tmp = sys.argv[1], sys.argv[2]
    qs = [json.loads(l) for l in open(tasks) if l.strip()]
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    judged = []
    for q in qs:
        ev = (q["evidence"] or ["x/y.md"])[0]
        ev = ev + "any.md" if ev.endswith("/") else ev
        session(os.path.join(tmp, q["id"] + "--gold"), q, gold_answer(q), [ev])
        session(os.path.join(tmp, q["id"] + "--bad"), q, WRONG[q["gold"]["type"]], ["no/such/file.go"])
        if q["gold"]["type"] == "rubric":
            judged.append({"session": q["id"] + "--gold", "correct": True})
            judged.append({"session": q["id"] + "--bad", "correct": False})
    jpath = os.path.join(tmp, "judgments.jsonl")
    with open(jpath, "w") as f:
        for j in judged:
            f.write(json.dumps(j) + "\n")
    out = subprocess.run([os.path.join(ROOT, "grade", "grade.py"), "--judgments", jpath, tmp],
                         capture_output=True, text=True)
    if out.returncode != 0:
        print(out.stderr[-3000:])
        sys.exit(1)
    rows = [json.loads(l) for l in out.stdout.splitlines() if l.strip()]
    by = {r["session"]: r for r in rows}
    bad = []
    for q in qs:
        g, b = by.get(q["id"] + "--gold"), by.get(q["id"] + "--bad")
        if g is None or b is None:
            bad.append((q["id"], "missing"))
            continue
        if g["correct"] is not True:
            bad.append((q["id"], f"gold answer scored {g['correct']}"))
        if b["correct"] is not False:
            bad.append((q["id"], f"wrong answer scored {b['correct']}"))
        if g["evidence_hit"] is False:
            bad.append((q["id"], "evidence of the gold answer not accepted"))
        if b["evidence_hit"] is True:
            bad.append((q["id"], "evidence of the wrong answer accepted"))
    print(json.dumps({"questions": len(qs), "sessions": len(rows), "problems": bad[:20],
                      "n_problems": len(bad)}, ensure_ascii=False, indent=1))
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
