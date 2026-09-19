# Serena (oraios/serena) with its language servers for Go (gopls), TypeScript
# (typescript-language-server + typescript) and C (clangd), all pinned and
# installed at build time so that nothing is downloaded at run time.

# --- Go toolchain, gopls and the module caches of the corpora ---------------
FROM are-l0-base:pe1 AS go
USER root
ARG GO_VERSION=1.27.1
ARG GO_SHA256=63d339f0da5ab53635a56f2490a7984dfe12dfcff22ad749f63edaf590168445
RUN curl -fsSL -o /tmp/go.tgz https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz \
 && echo "$GO_SHA256  /tmp/go.tgz" | sha256sum -c \
 && tar xzf /tmp/go.tgz -C /opt && rm /tmp/go.tgz
ENV PATH=/opt/go/bin:$PATH GOPATH=/tmp/gopath GOMODCACHE=/opt/gomodcache
ARG GOPLS_VERSION=v0.23.0
RUN GOBIN=/opt/go-tools/bin go install golang.org/x/tools/gopls@${GOPLS_VERSION}
# Module caches for the Go code of the corpora, taken from the go.mod/go.sum
# (and go.work/go.work.sum) of the pinned commits (corpora/corpora.yaml).
# go.sum and the checksum database verify every module.
COPY gomod.sh /tmp/gomod.sh
ARG WIKICTL_COMMIT=66846e2b597d898c44c18ea14f96307c03e81be0
RUN sh /tmp/gomod.sh https://github.com/roamer7038/wikictl.git ${WIKICTL_COMMIT}
ARG GRAFANA_COMMIT=39703a2c01e37d6ef9f91147d1db29dd16c82611
ARG WITH_GRAFANA_GOMOD=1
RUN if [ "$WITH_GRAFANA_GOMOD" = 1 ]; then sh /tmp/gomod.sh https://github.com/grafana/grafana.git ${GRAFANA_COMMIT}; fi \
 && go clean -cache && chmod -R a+rX /opt/gomodcache

# --- Serena and the other language servers ---------------------------------
FROM are-l0-base:pe1
USER root
# Kernel build (c3 prepare: compile_commands.json from a real build).
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" \
      gcc make libc6-dev flex bison bc libelf-dev libssl-dev cpio kmod perl \
 && rm -rf /var/lib/apt/lists/*

# uv, used only to install Serena's locked dependencies.
ARG UV_VERSION=0.12.17
ARG UV_SHA256=fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63
RUN curl -fsSL -o /tmp/uv.tgz https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz \
 && echo "$UV_SHA256  /tmp/uv.tgz" | sha256sum -c \
 && tar xzf /tmp/uv.tgz -C /usr/local/bin --strip-components=1 && rm /tmp/uv.tgz

# Serena 1.7.0 from PyPI: the dependencies from the uv.lock in its sdist
# (with hashes), then the wheel itself without dependencies.
ARG SERENA_VERSION=1.7.0
ARG SERENA_SDIST_SHA256=1ef15db14ee5426f3e3dc48fdd7bb7e864d1f35359115eaa3bdd7248b1533ce6
ARG SERENA_WHEEL_SHA256=6dbf1459670d96fb0595f84932adef34260a6fe14ba5135b901fdb3c8c76e891
RUN cd /tmp \
 && curl -fsSL -o serena.tgz https://files.pythonhosted.org/packages/source/s/serena-agent/serena_agent-${SERENA_VERSION}.tar.gz \
 && echo "$SERENA_SDIST_SHA256  serena.tgz" | sha256sum -c \
 && curl -fsSL -o serena_agent-${SERENA_VERSION}-py3-none-any.whl \
      https://files.pythonhosted.org/packages/py3/s/serena-agent/serena_agent-${SERENA_VERSION}-py3-none-any.whl \
 && echo "$SERENA_WHEEL_SHA256  serena_agent-${SERENA_VERSION}-py3-none-any.whl" | sha256sum -c \
 && tar xzf serena.tgz && cd serena_agent-${SERENA_VERSION} \
 && UV_NO_CACHE=1 uv export --frozen --no-dev --no-emit-project --format requirements-txt -o /tmp/req.txt \
 && UV_NO_CACHE=1 uv venv --python /usr/bin/python3 /opt/serena \
 && UV_NO_CACHE=1 VIRTUAL_ENV=/opt/serena uv pip install --require-hashes -r /tmp/req.txt \
 && UV_NO_CACHE=1 VIRTUAL_ENV=/opt/serena uv pip install --no-deps /tmp/serena_agent-${SERENA_VERSION}-py3-none-any.whl \
 && rm -rf /tmp/serena* /tmp/req.txt

# Node.js (the version in grafana's .nvmrc) and the TypeScript language server
# at the versions Serena 1.7.0 pins (DEFAULT_TYPESCRIPT_*); neither package has
# dependencies of its own.
ARG NODE_VERSION=24.11.0
ARG NODE_SHA256=46da9a098973ab7ba4fca76945581ecb2eaf468de347173897044382f10e0a0a
RUN curl -fsSL -o /tmp/node.txz https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
 && echo "$NODE_SHA256  /tmp/node.txz" | sha256sum -c \
 && mkdir /opt/node && tar xJf /tmp/node.txz -C /opt/node --strip-components=1 && rm /tmp/node.txz
ENV PATH=/opt/node/bin:$PATH
RUN mkdir /opt/ts-lsp && cd /opt/ts-lsp \
 && npm install --no-fund --no-audit --no-update-notifier --save-exact typescript@5.9.3 typescript-language-server@5.1.3 \
 && npm cache clean --force

# clangd 19.1.2, the release and checksum Serena 1.7.0 would download itself.
ARG CLANGD_VERSION=19.1.2
ARG CLANGD_SHA256=7c09614eff857d590e4502ef516f035ff94cfb8b795de14ece5afbc53a206caf
RUN curl -fsSL -o /tmp/clangd.zip https://github.com/clangd/clangd/releases/download/${CLANGD_VERSION}/clangd-linux-${CLANGD_VERSION}.zip \
 && echo "$CLANGD_SHA256  /tmp/clangd.zip" | sha256sum -c \
 && unzip -q /tmp/clangd.zip -d /opt && mv /opt/clangd_${CLANGD_VERSION} /opt/clangd && rm /tmp/clangd.zip

COPY --from=go /opt/go /opt/go
COPY --from=go /opt/go-tools /opt/go-tools
COPY --from=go /opt/gomodcache /opt/gomodcache

ENV PATH=/opt/serena/bin:/opt/go/bin:/opt/go-tools/bin:/opt/clangd/bin:$PATH \
    GOMODCACHE=/opt/gomodcache GOPROXY=off GOSUMDB=off GOTOOLCHAIN=local \
    SERENA_USAGE_REPORTING=false NPM_CONFIG_UPDATE_NOTIFIER=false \
    PYTHONUNBUFFERED=1
COPY serena_config.yml query.py /opt/l0/
COPY run.sh /opt/l0/run.sh
RUN chmod 755 /opt/l0/run.sh
LABEL l0.version="serena-agent 1.7.0 (uv 0.12.17, Python 3.12); gopls v0.23.0 (Go 1.27.1); typescript-language-server 5.1.3 + typescript 5.9.3 (Node 24.11.0); clangd 19.1.2"
USER agent
