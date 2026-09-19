# GitNexus from npm, pinned by package-lock.json (gitnexus 1.6.12 and every
# dependency), on the Node 22 LTS release binary. The build stage has the C++
# toolchain for the native modules that build from source.
FROM are-l0-base:pe1 AS build
USER root
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" g++ make
ARG NODE_VERSION=22.23.2
ARG NODE_SHA256=d60acfe00a2932254bb0ad20e01b0d74397a0875595de719654b214f4b03f307
RUN curl -fsSL -o /tmp/node.txz https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
 && echo "$NODE_SHA256  /tmp/node.txz" | sha256sum -c \
 && mkdir -p /opt/node && tar xJf /tmp/node.txz -C /opt/node --strip-components=1
ENV PATH=/opt/node/bin:$PATH
COPY package.json package-lock.json /opt/gitnexus/
# SCARF_ANALYTICS=false: @scarf/scarf's install-time analytics.
RUN cd /opt/gitnexus && SCARF_ANALYTICS=false npm ci --no-audit --no-fund

FROM are-l0-base:pe1
USER root
# LadybugDB's fts and vector extensions, which GitNexus otherwise downloads at
# run time into ~/.lbdb; run.sh links $HOME/.lbdb to this copy.
ARG LBUG_EXT=https://extension.ladybugdb.com/v0.18.1/linux_amd64
RUN d=/opt/lbdb/.lbdb/extension/0.18.1/linux_amd64 && mkdir -p $d/fts $d/vector $d/common \
 && curl -fsSL -o $d/fts/libfts.lbug_extension $LBUG_EXT/fts/libfts.lbug_extension \
 && curl -fsSL -o $d/vector/libvector.lbug_extension $LBUG_EXT/vector/libvector.lbug_extension \
 && echo "cf687fda0f82bfbe116e775f2f17c39faf45be6300fd1937c2b1afd21142c121  $d/fts/libfts.lbug_extension" | sha256sum -c \
 && echo "af4f03f9dff6ff0e4686a583a672cdb3c212fbbb6187ebb5dc60723809721949  $d/vector/libvector.lbug_extension" | sha256sum -c
# The model of the opt-in local embeddings (analyze --embeddings, off by
# default), used only when run.sh is told to (L0_EMBEDDINGS=1).
ARG EMB_REV=d8c86521100d3556476a063fc2342036d45c106f
RUN d=/opt/hf/Snowflake/snowflake-arctic-embed-xs && u=https://huggingface.co/Snowflake/snowflake-arctic-embed-xs/resolve/$EMB_REV \
 && mkdir -p $d/onnx \
 && for f in config.json tokenizer.json tokenizer_config.json onnx/model.onnx; do curl -fsSL -o $d/$f $u/$f; done \
 && printf '%s  %s\n' \
      d7d071046ab952af96b7abad788db7ab3fc997b465e1b9914ff39707092254ec $d/config.json \
      91f1def9b9391fdabe028cd3f3fcc4efd34e5d1f08c3bf2de513ebb5911a1854 $d/tokenizer.json \
      9ca59277519f6e3692c8685e26b94d4afca2d5438deff66483db495e48735810 $d/tokenizer_config.json \
      cf2698d30ff05da02c70a088313bad56e5c2f401d734cb24a8390d446111936c $d/onnx/model.onnx | sha256sum -c
COPY --from=build /opt/node /opt/node
COPY --from=build /opt/gitnexus /opt/gitnexus
ENV PATH=/opt/gitnexus/node_modules/.bin:/opt/node/bin:$PATH \
    SCARF_ANALYTICS=false GITNEXUS_LBUG_EXTENSION_INSTALL=load-only HF_HOME=/opt/hf
COPY run.sh /opt/l0/run.sh
LABEL l0.version="gitnexus 1.6.12 (npm, package-lock.json), node 22.23.2, LadybugDB extensions 0.18.1, snowflake-arctic-embed-xs@d8c8652 (only with L0_EMBEDDINGS=1)"
USER agent
