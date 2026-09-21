#!/bin/sh
# PE3c: the cells whose queries change when the question boilerplate is
# dropped from the identifiers.
#
#   ARE_DATA=<...> l1/pe3c.sh
#
# PE2d wrote the "leave the tests out" rule into the body of the question and
# listed the file-name patterns in backticks. L1's identifier rule picked all
# twenty of them up, so every S3 and S7 question with identifiers carried 21
# symbols and the one that was asked about came last (pe3b-l1.md, section 6).
# l1/tasks.py now drops an identifier that appears in 10 or more questions as
# boilerplate of the template; exactly the 20 words qualify.
#
# 35 questions change (S3 24, S7 11), in c1, c2 and c3; no other scenario and
# no question of c4 changes, so those keep their PE3b measurement. To keep the
# record of a scenario whole, every S3 and S7 question of c1, c2 and c3 is
# measured again here, not only the 35 that change.
#
# The same cells, indexes, limits and conditions as PE3b: one slot, one
# measurement at a time; 8 vCPU, 16GiB (semble c3 32GiB, graphify c3 32GiB),
# no network. Slot a throughout, slot c for qmd alone (the GPU); the script is
# sequential, so the two never overlap.
#
# The primary condition is the Japanese question (identifier tools are asked
# once, in the form id). The second condition, the English translation, is
# measured for the same text tools as PE3b, leaving out the c3 cells whose
# single search takes minutes (probe, semble, codebase-memory-mcp).
set -eu
cd "$(dirname "$0")/.."
ARE_L1_RESULTS="${ARE_L1_RESULTS:-$PWD/results/pe3c-l1.jsonl}"
export ARE_L1_RESULTS
R="l1/run.py run --scenarios S3,S7"

# --- primary: the Japanese question (or the identifiers) -------------------
$R --slot a --corpora c1 --langs ja --tools grep,ctags,global,ast-grep,probe,zoekt,wikictl,semble,ck,codebase-memory-mcp,codegraph,graphify,gitnexus,serena
$R --slot c --corpora c1 --langs ja --tools qmd
$R --slot a --corpora c2 --langs ja --tools grep,ctags,global,ast-grep,probe,zoekt,semble,codebase-memory-mcp,codegraph,graphify,gitnexus,serena
$R --slot c --corpora c2 --langs ja --tools qmd
$R --slot a --corpora c3 --langs ja --tools grep,ctags,global,cscope,zoekt,codegraph,ast-grep,semble,codebase-memory-mcp,graphify,probe

# --- second condition: the English translation -----------------------------
$R --slot a --corpora c1 --langs en --tools grep,probe,zoekt,wikictl,semble,ck,codebase-memory-mcp,gitnexus
$R --slot c --corpora c1 --langs en --tools qmd
$R --slot a --corpora c2 --langs en --tools grep,probe,zoekt,semble,codebase-memory-mcp,gitnexus
$R --slot c --corpora c2 --langs en --tools qmd
$R --slot a --corpora c3 --langs en --tools grep,zoekt
