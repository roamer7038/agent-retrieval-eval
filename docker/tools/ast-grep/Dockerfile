# ast-grep, release binary. No index: every search parses the files.
FROM are-l0-base:pe1
USER root
ARG AG_VERSION=0.45.3
ARG AG_SHA256=f8ac830881339d1edee6b2652f54798c0f4da5a827f2db38a08ee31117783ce8
RUN curl -fsSL -o /tmp/ag.zip https://github.com/ast-grep/ast-grep/releases/download/${AG_VERSION}/app-x86_64-unknown-linux-gnu.zip \
 && echo "$AG_SHA256  /tmp/ag.zip" | sha256sum -c \
 && unzip -q /tmp/ag.zip ast-grep -d /usr/local/bin && rm /tmp/ag.zip
COPY run.sh /opt/l0/run.sh
LABEL l0.version="ast-grep 0.45.3"
USER agent
