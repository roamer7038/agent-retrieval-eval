#!/bin/bash
# codebase-memory-mcp: one project per repository, SQLite under
# ~/.cache/codebase-memory-mcp (HOME=/index/home). index_repository runs in the
# default mode "full" (graph, BM25 and the bundled semantic embeddings) and
# does not write the .codebase-memory/ artifact (--persistence defaults to
# false). update runs index_repository again over the existing project (the
# CLI has no separate incremental command). The project name is derived from
# the path: work-<repo>.
set -euo pipefail
cbm() { codebase-memory-mcp cli "$@"; }
case "$1" in
  prepare) exit 0 ;;
  index|update)
    for r in $REPOS; do
      cbm --progress index_repository --repo-path "/work/$r"
    done ;;
  query)
    for r in $REPOS; do
      echo "## $r"
      cbm search_graph --project "work-$r" --query "$Q_SYMBOL" --limit 10 || true
    done ;;
  *) exit 2 ;;
esac
