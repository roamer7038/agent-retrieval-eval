# qmd (tobi/qmd, npm @tobilu/qmd) on Node.js, with package-lock.json
# (resolved with --before 2026-09-20). GPU: node-llama-cpp's prebuilt CUDA
# binary needs libcudart.so.13 and libcublas.so.13, taken from NVIDIA's pip
# wheels; libcuda.so.1 comes from the host through the NVIDIA runtime.
# Models (embedding: Qwen3-Embedding-0.6B; query expansion for
# `qmd query`: qmd-query-expansion-1.7B) are downloaded at fixed revisions.
FROM are-l0-base:pe1 AS build
USER root
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" g++ make
ARG NODE_VERSION=24.21.0
ARG NODE_SHA256=fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6
RUN curl -fsSL -o /tmp/node.txz https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
 && echo "$NODE_SHA256  /tmp/node.txz" | sha256sum -c \
 && mkdir -p /opt/node && tar xJf /tmp/node.txz -C /opt/node --strip-components=1
ENV PATH=/opt/node/bin:$PATH NODE_LLAMA_CPP_SKIP_DOWNLOAD=true
COPY package.json package-lock.json /opt/qmd/
RUN cd /opt/qmd && npm ci --no-audit --no-fund
ARG UV_VERSION=0.12.17
ARG UV_SHA256=fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63
RUN curl -fsSL -o /tmp/uv.tgz https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz \
 && echo "$UV_SHA256  /tmp/uv.tgz" | sha256sum -c \
 && tar xzf /tmp/uv.tgz -C /usr/local/bin --strip-components=1 \
 && uv pip install --no-cache --python /usr/bin/python3 --target /opt/cuda-libs --no-deps \
      nvidia-cuda-runtime==13.4.92 nvidia-cublas==13.8.0.4 nvidia-cuda-nvrtc==13.4.92
ARG EMBED_REVISION=370f27d7550e0def9b39c1f16d3fbaa13aa67728
ARG EMBED_SHA256=06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439
ARG EXPAND_REVISION=7816de0b72572c6c860ca1eddf97ba9e7fb8cc65
ARG EXPAND_SHA256=000dfb1c06efa6a049e9f64ba921c3740e2454f62abab6fa10e77bd30bb2bcc0
# File names as node-llama-cpp's resolveModelFile stores hf: URIs.
RUN mkdir -p /opt/qmd-models && cd /opt/qmd-models \
 && curl -fsSL -o hf_Qwen_Qwen3-Embedding-0.6B-Q8_0.gguf \
      https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF/resolve/${EMBED_REVISION}/Qwen3-Embedding-0.6B-Q8_0.gguf \
 && echo "$EMBED_SHA256  hf_Qwen_Qwen3-Embedding-0.6B-Q8_0.gguf" | sha256sum -c \
 && curl -fsSL -o hf_tobil_qmd-query-expansion-1.7B-q4_k_m.gguf \
      https://huggingface.co/tobil/qmd-query-expansion-1.7B-gguf/resolve/${EXPAND_REVISION}/qmd-query-expansion-1.7B-q4_k_m.gguf \
 && echo "$EXPAND_SHA256  hf_tobil_qmd-query-expansion-1.7B-q4_k_m.gguf" | sha256sum -c

FROM are-l0-base:pe1
USER root
COPY --from=build /opt/node /opt/node
COPY --from=build /opt/qmd /opt/qmd
COPY --from=build /opt/cuda-libs/nvidia/cu13/lib /opt/cuda-libs/lib
COPY --from=build /opt/qmd-models /opt/qmd-models
ENV PATH=/opt/qmd/node_modules/.bin:/opt/node/bin:$PATH \
    LD_LIBRARY_PATH=/opt/cuda-libs/lib \
    QMD_EMBED_MODEL=hf:Qwen/Qwen3-Embedding-0.6B-GGUF/Qwen3-Embedding-0.6B-Q8_0.gguf \
    XDG_CACHE_HOME=/index/cache QMD_CONFIG_DIR=/index/config \
    NODE_LLAMA_CPP_SKIP_DOWNLOAD=true
COPY run.sh /opt/l0/run.sh
LABEL l0.version="qmd 2.8.3 (node 24.21.0, node-llama-cpp 3.20.0, CUDA runtime 13.4.92 / cuBLAS 13.8.0.4, Qwen3-Embedding-0.6B-Q8_0@370f27d, qmd-query-expansion-1.7B-q4_k_m@7816de0)"
USER agent
