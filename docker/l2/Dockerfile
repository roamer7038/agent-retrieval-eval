# The image of an L2 session: a tool's L0 image (PE1, unchanged) with Claude
# Code and the standard commands added, so that the standard part of every
# condition is the same image layer and only the tool differs.
#   BASE=are-l0-base:pe1              A0・A1（標準のみ）
#   BASE=are-l0-<tool>:pe1            A2・A3・A5・A6・A7
#   BASE=are-l2-semble-qmd-base:pe4   A4（semble と qmd）
ARG BASE=are-l0-base:pe1
FROM are-agent:dev AS cc
FROM ${BASE}
USER root
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" ripgrep fd-find tree less \
 && ln -sf /usr/bin/fdfind /usr/local/bin/fd \
 && rm -rf /var/lib/apt/lists/*
COPY --from=cc /usr/local/bin/claude /usr/local/bin/claude
RUN mkdir -p /idx /corpus /work /workspace && chown agent:agent /workspace
ENTRYPOINT []
USER agent
WORKDIR /workspace
