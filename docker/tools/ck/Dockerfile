# ck (BeaconBay/ck), the release binary. The embedding model (fastembed's
# bge-small, ck's default) is fetched at build time by indexing a tiny tree.
FROM are-l0-base:pe1
USER root
ARG CK_VERSION=0.7.11
ARG CK_SHA256=1d88fb42ca4f3c0687b8c5cbfceeef9db032baf5897daff9d01483b36ace652a
RUN curl -fsSL -o /tmp/ck.tgz https://github.com/BeaconBay/ck/releases/download/${CK_VERSION}/ck-${CK_VERSION}-x86_64-unknown-linux-gnu.tar.gz \
 && echo "$CK_SHA256  /tmp/ck.tgz" | sha256sum -c \
 && mkdir -p /opt/ck/bin && tar xzf /tmp/ck.tgz -C /opt/ck \
 && mv /opt/ck/ck /opt/ck/bin/ck && rm /tmp/ck.tgz
ENV PATH=/opt/ck/bin:$PATH
# Models live in $XDG_CACHE_HOME/ck/models; run.sh points XDG_CACHE_HOME here.
ENV XDG_CACHE_HOME=/opt/ck-cache
# fastembed takes the model from the repository's main branch; the build fails
# if main has moved from the revision seen on 2026-09-20.
ARG BGE_REVISION=ea104dacec62c0de699686887e3f920caeb4f3e3
ARG BGE_ONNX_SHA256=828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35
RUN mkdir -p /tmp/seed && printf 'package main\n\nfunc main() {}\n' > /tmp/seed/main.go \
 && ck --index /tmp/seed && ck --sem main /tmp/seed \
 && rm -rf /tmp/seed \
 && M=/opt/ck-cache/ck/models/models--Xenova--bge-small-en-v1.5 \
 && test "$(cat $M/refs/main)" = "$BGE_REVISION" \
 && echo "$BGE_ONNX_SHA256  $M/snapshots/$BGE_REVISION/onnx/model.onnx" | sha256sum -c \
 && chmod -R a+rX /opt/ck-cache
COPY run.sh /opt/l0/run.sh
LABEL l0.version="ck 0.7.11 (fastembed 5.13.2, ort 2.0.0-rc.11, model Xenova/bge-small-en-v1.5@ea104da)"
USER agent
