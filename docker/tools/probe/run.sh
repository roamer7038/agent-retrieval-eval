#!/bin/bash
# probe has no index; query searches the tree with BM25 (the default ranker)
# and prints, per result, the file, the lines of the block and the first line
# of the block holding the query (probe's JSON output, reduced with jq).
set -euo pipefail
search() {
  probe search "$1" "/work/$2" --max-results 10 --format json 2>/dev/null | sed -n '/^{/,$p' |
    jq -r --arg q "$1" '.results[] | "\(.file):\(.lines | map(tostring) | join("-")) \(.code | split("\n") | map(select(ascii_downcase | contains($q | ascii_downcase))) | .[0] // "")"' || true
}
case "$1" in
  prepare|index|update) exit 0 ;;
  query)
    for r in $REPOS; do
      search "$Q_SYMBOL" "$r"
      search "$Q_TEXT" "$r"
    done ;;
  *) exit 2 ;;
esac
