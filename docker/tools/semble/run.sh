#!/bin/bash
# semble: no separate index command; a search builds the index of each path
# and saves it in the cache (SEMBLE_CACHE_LOCATION, one directory per path).
# The next search reuses it when no file changed, and otherwise rebuilds it
# reusing the chunks and vectors of unchanged files. index and update run one
# search per repository; query searches the indexed repositories together.
# Content: semble's default (code only). A repository without code files is
# skipped (semble refuses it); a corpus without any is not applicable.
set -euo pipefail
export SEMBLE_CACHE_LOCATION=/index/semble
LIST=/index/semble-repos
case "$1" in
  prepare) exit 0 ;;
  index|update)
    : > "$LIST.new"
    for r in $REPOS; do
      if semble search "areL0Index" "/work/$r" -k 1 --format text >/dev/null 2>/tmp/err; then
        echo "$r" >> "$LIST.new"
      elif grep -q "No supported files found" /tmp/err; then
        echo "semble: no code files in $r, skipped" >&2
      else
        cat /tmp/err >&2; exit 1
      fi
    done
    mv "$LIST.new" "$LIST"
    [ -s "$LIST" ] || exit 3 ;;
  query)
    paths=(); while read -r r; do paths+=("/work/$r"); done < "$LIST"
    semble search "$Q_SYMBOL" "${paths[@]}" -k 10 --format text ;;
  *) exit 2 ;;
esac
