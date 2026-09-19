# probe, release binary (musl). No index: each search reads the files, ranks
# with BM25 and cuts out the enclosing code block with tree-sitter.
FROM are-l0-base:pe1
USER root
ARG PROBE_VERSION=v0.6.0-rc339
ARG PROBE_SHA256=16c265e3984f1da477ead7f2161e9e1fa2a0288fd5975138918668d4b496fbdf
RUN curl -fsSL -o /tmp/probe.tgz https://github.com/probelabs/probe/releases/download/${PROBE_VERSION}/probe-${PROBE_VERSION}-x86_64-unknown-linux-musl.tar.gz \
 && echo "$PROBE_SHA256  /tmp/probe.tgz" | sha256sum -c \
 && mkdir /tmp/probe && tar xzf /tmp/probe.tgz -C /tmp/probe \
 && install -m 755 "$(find /tmp/probe -type f -name probe | head -1)" /usr/local/bin/probe && rm -rf /tmp/probe*
COPY run.sh /opt/l0/run.sh
LABEL l0.version="probe v0.6.0-rc339"
USER agent
