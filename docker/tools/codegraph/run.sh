#!/bin/bash
# codegraph: one .codegraph/codegraph.db (SQLite) inside each repository
# (/work, the overlay). index runs `codegraph init --yes` (creates .codegraph/
# and builds the full graph); update runs `codegraph sync`, which re-parses
# only the files changed since the last index. codegraph indexes code files
# only; a repository without code gets an empty graph.
set -euo pipefail
case "$1" in
  prepare) exit 0 ;;
  index)
    for r in $REPOS; do
      codegraph init --yes "/work/$r"
    done ;;
  update)
    for r in $REPOS; do
      codegraph sync "/work/$r"
    done ;;
  query)
    for r in $REPOS; do
      echo "## $r"
      codegraph query "$Q_SYMBOL" -p "/work/$r" --limit 10 --json || true
    done ;;
  *) exit 2 ;;
esac
