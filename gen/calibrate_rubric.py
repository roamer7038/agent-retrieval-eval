#!/usr/bin/env python3
"""Set the threshold of the rubric check (gen/phrase.py stage 1b) from the S4
bases an audit has already looked at.

    gen/calibrate_rubric.py [--phrased <S4.jsonl>] [--out results/pe2d-rubric-calib.jsonl]

The set is fixed: the 41 phrased S4 candidates of PE2c, kept as they were in
gold-work/phrased/dev-pe2c/S4.jsonl, of which 33 carry key points and a
verdict. It has to be a set an audit has been through, and the phrased file of
the split is written over every time the generators run, so the calibration
does not read that one.

The audit's error_kind says what was wrong, so each base falls in one of three
groups:

  rubric_bad  the audit found the key points or the section at fault
              (points, point_restates_question, point_off_target,
               question-points, no_reason_in_section, section_has_no_reason,
               ref_range_excludes_reason, question_wraps_fact)
              -> the check should reject it
  ok          the audit passed it -> the check should keep it
  other_bad   the audit threw it away for the paraphrase or for not being
              unique -> rejecting it costs nothing, so it is reported apart

The judge (CHECK_MODEL) is asked once per base and the labels are cached, so
the rules below are compared on the same answers. Every base, its labels and
the verdict of each rule are written to the output file as the record of the
calibration.
"""
import argparse
import collections
import json
import os
import sys

import llm
import phrase
from common import ROOT, read_jsonl, write_jsonl

RUBRIC_BAD = {"points", "point_restates_question", "point_off_target", "question-points",
              "no_reason_in_section", "section_has_no_reason", "ref_range_excludes_reason",
              "question_wraps_fact"}


def group(a):
    if a["verdict"] in ("ok", "ok-strict", "regenerate"):
        return "ok"
    return "rubric_bad" if a.get("error_kind") in RUBRIC_BAD else "other_bad"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrased", default=os.path.join(phrase.PHRASED, "dev-pe2c", "S4.jsonl"))
    ap.add_argument("--out", default=os.path.join(ROOT, "results", "pe2d-rubric-calib.jsonl"))
    a = ap.parse_args()
    audit = {}
    for x in read_jsonl(os.path.join(ROOT, "results", "pe2-audit.jsonl")):
        if x["scenario"] == "S4":
            audit[x["base_id"]] = x
    rows = []
    for c in read_jsonl(a.phrased):
        au = audit.get(c["base_id"])
        if not au or not (c.get("gold") or {}).get("points"):
            continue
        phrase.stage_rubric(c)   # labels only; the rule is applied below
        r = c["rubric_rounds"][-1]
        rows.append({"base_id": c["base_id"], "corpus": c["corpus"], "group": group(au),
                     "verdict": au["verdict"], "error_kind": au.get("error_kind"),
                     "points": r["points"], "labels": (r["result"] or {}).get("labels"),
                     "section_states_reason": (r["result"] or {}).get("section_states_reason"),
                     "judge_reason": (r["result"] or {}).get("reason")})
        print(rows[-1]["group"], rows[-1]["base_id"], rows[-1]["labels"],
              rows[-1]["section_states_reason"], file=sys.stderr)
    # the rules to compare: which labels a point may carry, how large a share
    # of the points may fail, and whether section_states_reason alone rejects
    rules = []
    for keep in ({"reason"}, {"reason", "caveat"}, {"reason", "consequence", "caveat"}):
        for mx in (0.0, 0.34, 0.5):
            for need in (True, False):
                rules.append((keep, mx, need))
    out = []
    for keep, mx, need in rules:
        tab = collections.Counter()
        for r in rows:
            labels = r["labels"] or []
            if len(labels) != len(r["points"]):
                rej = True
            else:
                bad = sum(1 for l in labels if l not in keep)
                rej = bad / len(labels) > mx or (need and r["section_states_reason"] is False)
            tab[(r["group"], "reject" if rej else "keep")] += 1
        n_bad = sum(v for (g, _), v in tab.items() if g == "rubric_bad")
        n_ok = sum(v for (g, _), v in tab.items() if g == "ok")
        n_other = sum(v for (g, _), v in tab.items() if g == "other_bad")
        out.append({"rule": {"keep": sorted(keep), "bad_share_max": mx, "need_section_reason": need},
                    "rubric_bad_rejected": f"{tab[('rubric_bad', 'reject')]}/{n_bad}",
                    "ok_wrongly_rejected": f"{tab[('ok', 'reject')]}/{n_ok}",
                    "other_bad_rejected": f"{tab[('other_bad', 'reject')]}/{n_other}"})
    write_jsonl(a.out, [{"kind": "case", **r} for r in rows] + [{"kind": "rule", **o} for o in out])
    print(json.dumps({"n": len(rows), "groups": collections.Counter(r["group"] for r in rows),
                      "rules": out, "llm": llm.stats}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
