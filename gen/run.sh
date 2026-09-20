#!/bin/sh
# The whole procedure for one split, in order. The random choices depend only
# on the split name, SEED (gen/common.py) and the pinned corpora, so the test
# split is made by the same steps: gen/run.sh test
#
#   ARE_DATA=... gen/run.sh [dev|test]
set -eu
: "${ARE_DATA:?set ARE_DATA}"
GEN=$(cd "$(dirname "$0")" && pwd)
split=${1:-dev}
[ -s "$ARE_DATA/gold-work/out/c3-c.jsonl" ] || "$GEN/analyze.sh" all
cd "$GEN"
python3 cand_code.py --split "$split"
python3 cand_git.py --split "$split"
python3 cand_docs.py --split "$split"
python3 cand_s8.py --split "$split"
# S9: two judges decide which gold files handle the term; the rest becomes
# optional (the gold cannot be written before this, so it runs here)
python3 judge.py s9check --split "$split"
python3 judge.py s9apply --split "$split"
python3 phrase.py --split "$split"
python3 build.py --split "$split"
