#!/bin/bash
# Build the session images of the L2 conditions (A0-A7). The L0 images of PE1
# (are-l0-*:pe1) and are-agent:dev must exist.
set -euo pipefail
cd "$(dirname "$0")"
build() { docker build -t "are-l2-$1:pe4" --build-arg "BASE=$2" -f Dockerfile .; }
docker build -t are-l2-semble-qmd-base:pe4 -f semble-qmd.Dockerfile .
build base                are-l0-base:pe1
build ctags               are-l0-ctags:pe1
build zoekt               are-l0-zoekt:pe1
build semble-qmd          are-l2-semble-qmd-base:pe4
build codebase-memory-mcp are-l0-codebase-memory-mcp:pe1
build serena              are-l0-serena:pe1
build wikictl             are-l0-wikictl:pe1
