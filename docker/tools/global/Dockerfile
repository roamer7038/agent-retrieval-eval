# GNU global, built from the release tarball with universal-ctags (from the
# ctags image) as the plug-in parser for the languages without a built-in one.
FROM are-l0-ctags:pe1 AS build
USER root
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" gcc make libncurses-dev libltdl-dev
ARG GLOBAL_VERSION=6.6.15
ARG GLOBAL_SHA256=cf0937cb3ed521b2ab1acfa7aff45103040b860bb642c4c2f094ac3a3fe86024
RUN curl -fsSL -o /tmp/global.tgz https://ftp.gnu.org/gnu/global/global-${GLOBAL_VERSION}.tar.gz \
 && echo "$GLOBAL_SHA256  /tmp/global.tgz" | sha256sum -c \
 && tar xzf /tmp/global.tgz -C /tmp && cd /tmp/global-${GLOBAL_VERSION} \
 && ./configure --prefix=/opt/global --with-universal-ctags=/opt/ctags/bin/ctags && make -j8 && make install

FROM are-l0-ctags:pe1
USER root
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" libncurses6 libltdl7 \
 && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/global /opt/global
ENV PATH=/opt/global/bin:$PATH GTAGSCONF=/opt/global/share/gtags/gtags.conf
COPY run.sh /opt/l0/run.sh
LABEL l0.version="GNU global 6.6.15 (plug-in parser universal-ctags 6.2.1)"
USER agent
