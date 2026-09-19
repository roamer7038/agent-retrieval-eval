# semble (MinishLab/semble) from PyPI with uv, on the system Python 3.12.
# Dependencies are resolved as of --exclude-newer. The embedding model
# (potion-code-16M-v2, semble's default) is downloaded at a fixed revision
# and named by SEMBLE_MODEL_NAME.
FROM are-l0-base:pe1
USER root
ARG UV_VERSION=0.12.17
ARG UV_SHA256=fa82fd8dde8e8eefdecada6aa0889666556cfceb690d06e0c3bca49eb3070a63
RUN curl -fsSL -o /tmp/uv.tgz https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/uv-x86_64-unknown-linux-gnu.tar.gz \
 && echo "$UV_SHA256  /tmp/uv.tgz" | sha256sum -c \
 && tar xzf /tmp/uv.tgz -C /usr/local/bin --strip-components=1 && rm /tmp/uv.tgz
ARG SEMBLE_VERSION=0.6.0
ENV UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/opt/semble/bin UV_PYTHON=/usr/bin/python3 \
    UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
RUN uv tool install --exclude-newer 2026-09-20T00:00:00Z "semble==${SEMBLE_VERSION}" \
 && uv pip list --python /opt/uv-tools/semble/bin/python
ARG MODEL_REVISION=e9d2a44ca6a05ac6685f3b23709ea57eb7352d5b
ARG MODEL_SHA256=75cf7a6c2171b230ad19b1e7d8e0b1aee86da5a02af8e7cacedd9921d227623c
RUN /opt/uv-tools/semble/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('minishlab/potion-code-16M-v2', revision='${MODEL_REVISION}', local_dir='/opt/semble-model/potion-code-16M-v2')" \
 && echo "$MODEL_SHA256  /opt/semble-model/potion-code-16M-v2/model.safetensors" | sha256sum -c \
 && rm -rf /opt/semble-model/potion-code-16M-v2/.cache && chmod -R a+rX /opt/semble-model
ENV PATH=/opt/semble/bin:$PATH SEMBLE_MODEL_NAME=/opt/semble-model/potion-code-16M-v2 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
COPY run.sh /opt/l0/run.sh
LABEL l0.version="semble 0.6.0 (uv 0.12.17, deps as of 2026-09-20, model minishlab/potion-code-16M-v2@e9d2a44)"
USER agent
