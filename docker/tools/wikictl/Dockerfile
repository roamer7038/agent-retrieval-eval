# wikictl v0.5.1, release binary. No index: the "index" is the bare mirror of
# the wiki repository that every command reads (git grep over the mirror).
FROM are-l0-base:pe1
USER root
ARG WIKICTL_VERSION=v0.5.1
ARG WIKICTL_SHA256=c65ebca6f21dd94c278eaf5a9a0f2a010c841034e20377e03fc62b0e0a212daf
RUN curl -fsSL -o /usr/local/bin/wikictl https://github.com/roamer7038/wikictl/releases/download/${WIKICTL_VERSION}/wikictl_linux_x86_64 \
 && echo "$WIKICTL_SHA256  /usr/local/bin/wikictl" | sha256sum -c && chmod 755 /usr/local/bin/wikictl
COPY run.sh /opt/l0/run.sh
LABEL l0.version="wikictl v0.5.1"
USER agent
