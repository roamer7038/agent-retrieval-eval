# universal-ctags, built from the release tarball.
FROM are-l0-base:pe1 AS build
USER root
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" gcc make pkg-config libjansson-dev libyaml-dev libxml2-dev
ARG CTAGS_VERSION=6.2.1
ARG CTAGS_SHA256=2c63efe9e0e083dc50e6fdd8c5414781cc8873d8c8940cf553c01870ed962f8c
RUN curl -fsSL -o /tmp/ctags.tgz https://github.com/universal-ctags/ctags/releases/download/v${CTAGS_VERSION}/universal-ctags-${CTAGS_VERSION}.tar.gz \
 && echo "$CTAGS_SHA256  /tmp/ctags.tgz" | sha256sum -c \
 && tar xzf /tmp/ctags.tgz -C /tmp && cd /tmp/universal-ctags-${CTAGS_VERSION} \
 && ./configure --prefix=/opt/ctags && make -j8 && make install

FROM are-l0-base:pe1
USER root
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" libjansson4 libyaml-0-2 libxml2 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/ctags /opt/ctags
ENV PATH=/opt/ctags/bin:$PATH
COPY run.sh /opt/l0/run.sh
LABEL l0.version="universal-ctags 6.2.1"
USER agent
