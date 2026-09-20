# codebase-memory-mcp: the static ("-portable") Linux release binary. Only the
# binary is used; its `install` subcommand (agent registration) is not run.
FROM are-l0-base:pe1
USER root
ARG CBM_VERSION=0.11.0
ARG CBM_SHA256=1f9e8293eb2bc5c05cfa27a7e8fc033da6d729ffad525ccfcdaa3fd606306683
RUN curl -fsSL -o /tmp/cbm.tgz https://github.com/DeusData/codebase-memory-mcp/releases/download/v${CBM_VERSION}/codebase-memory-mcp-linux-amd64-portable.tar.gz \
 && echo "$CBM_SHA256  /tmp/cbm.tgz" | sha256sum -c \
 && mkdir -p /opt/cbm && tar xzf /tmp/cbm.tgz -C /opt/cbm && rm /tmp/cbm.tgz \
 && ln -s /opt/cbm/codebase-memory-mcp /usr/local/bin/codebase-memory-mcp
COPY run.sh /opt/l0/run.sh
LABEL l0.version="codebase-memory-mcp 0.11.0 (linux-amd64-portable)"
USER agent
