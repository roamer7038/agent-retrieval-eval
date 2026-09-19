# The image each agent session runs in: Claude Code at a fixed version and
# the standard commands. The tools of the other conditions are added later.
FROM ubuntu:24.04
ARG CLAUDE_VERSION=2.1.278
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8
RUN apt-get update -q && apt-get install -qy --no-install-recommends \
      ca-certificates curl git ripgrep fd-find tree jq python3 file less procps \
 && ln -s /usr/bin/fdfind /usr/local/bin/fd \
 && rm -rf /var/lib/apt/lists/*
# Ubuntu's image has a user with uid 1000; rename it so that files match the host user.
RUN usermod -l agent -d /home/agent -m ubuntu && groupmod -n agent ubuntu
USER agent
RUN curl -fsSL https://claude.ai/install.sh | bash -s "$CLAUDE_VERSION" \
 && /home/agent/.local/bin/claude --version
USER root
RUN install -m 755 "$(readlink -f /home/agent/.local/bin/claude)" /usr/local/bin/claude && rm -rf /home/agent/.local /home/agent/.claude*
USER agent
WORKDIR /workspace
