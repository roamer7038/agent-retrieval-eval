#!/bin/bash
# zoekt: one set of shards per repository in /index/shards. update runs
# zoekt-index again: zoekt-index has no file-level incremental mode and
# rewrites the repository's shards.
set -euo pipefail
case "$1" in
  prepare) exit 0 ;;
  index|update)
    mkdir -p /index/shards
    for r in $REPOS; do
      zoekt-index -index /index/shards -require_ctags "/work/$r"
    done ;;
  query)
    zoekt -index_dir /index/shards "sym:$Q_SYMBOL" | head -20 || true
    zoekt -index_dir /index/shards "$Q_SYMBOL" | head -20 || true
    zoekt -index_dir /index/shards "$Q_TEXT" | head -20 || true ;;
  *) exit 2 ;;
esac
