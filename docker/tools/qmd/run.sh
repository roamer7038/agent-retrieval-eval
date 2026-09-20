#!/bin/bash
# qmd: one collection per repository (qmd's default mask **/*.md) in the index
# /index/cache/qmd/index.sqlite, embedded with Qwen3-Embedding-0.6B on the GPU.
# update: `qmd update` re-scans the collections (content hash per document)
# and `qmd embed` embeds only the chunks that have no vector yet.
# query: `qmd query --no-rerank` (BM25 + vector with RRF; the query-expansion
# model runs unless BM25 alone gives a strong signal) for $Q_TEXT.
# c3 (Linux) documents are reStructuredText, not Markdown: not applicable.
set -euo pipefail
[ "$CORPUS" = c3 ] && exit 3
# Models are read from the image: the cache's models directory is a link to it.
mkdir -p "$XDG_CACHE_HOME/qmd" "$QMD_CONFIG_DIR"
[ -e "$XDG_CACHE_HOME/qmd/models" ] || ln -s /opt/qmd-models "$XDG_CACHE_HOME/qmd/models"
case "$1" in
  prepare) exit 0 ;;
  index)
    for r in $REPOS; do
      qmd collection add "/work/$r" --name "$r"
    done
    qmd embed --timeout 0 ;;
  update)
    qmd update
    qmd embed --timeout 0 ;;
  query)
    qmd query --no-rerank -n 10 "$Q_TEXT" ;;
  *) exit 2 ;;
esac
