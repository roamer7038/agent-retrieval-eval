# codegraph: the self-contained Linux release bundle (its own node binary and
# the native kernel). `codegraph install` (agent wiring) is not run.
FROM are-l0-base:pe1
USER root
ARG CODEGRAPH_VERSION=1.6.0
ARG CODEGRAPH_SHA256=de3391f79ed42622d937e6cd5b7642a7ea8bb7d1473607e80b879ba73ef216b0
RUN curl -fsSL -o /tmp/cg.tgz https://github.com/colbymchenry/codegraph/releases/download/v${CODEGRAPH_VERSION}/codegraph-linux-x64.tar.gz \
 && echo "$CODEGRAPH_SHA256  /tmp/cg.tgz" | sha256sum -c \
 && tar xzf /tmp/cg.tgz -C /opt && rm /tmp/cg.tgz \
 && ln -s /opt/codegraph-linux-x64/bin/codegraph /usr/local/bin/codegraph
# No telemetry, no update check, no background daemon. CODEGRAPH_NO_WATCH is
# left unset on purpose: with it, `init --yes` installs git post-commit/merge/
# checkout hooks as the fallback; the CLI does not start a watcher anyway.
ENV CODEGRAPH_TELEMETRY=0 DO_NOT_TRACK=1 CODEGRAPH_NO_UPDATE_CHECK=1 \
    CODEGRAPH_NO_DAEMON=1
COPY run.sh /opt/l0/run.sh
LABEL l0.version="codegraph 1.6.0 (codegraph-linux-x64 release bundle)"
USER agent
