# A4 is one condition with two tools (semble for code, qmd for Markdown):
# semble's installation is copied into qmd's image, both unchanged from PE1.
FROM are-l0-semble:pe1 AS semble
FROM are-l0-qmd:pe1
USER root
COPY --from=semble /opt/uv-tools /opt/uv-tools
COPY --from=semble /opt/semble /opt/semble
COPY --from=semble /opt/semble-model /opt/semble-model
ENV PATH=/opt/semble/bin:$PATH \
    SEMBLE_MODEL_NAME=/opt/semble-model/potion-code-16M-v2 \
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
# qmd's reranker, the third model of its configuration (index.yml). PE1 put
# only the embedding and the query-expansion models in the image, because L1
# queries with --no-rerank; `qmd query` and qmd's MCP server rerank by default
# and, without the model and without the network, wait for ever.
ARG RERANK_REVISION=a02f48bb4f057028298c21fa033da2b30d7742d5
ARG RERANK_SHA256=22c9979ce4fbcdc5acdc310c6641c32797eff1aa980b8f7a2db8a8ea23429a48
RUN curl -fsSL -o /opt/qmd-models/hf_ggml-org_qwen3-reranker-0.6b-q8_0.gguf \
      https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF/resolve/${RERANK_REVISION}/qwen3-reranker-0.6b-q8_0.gguf \
 && echo "$RERANK_SHA256  /opt/qmd-models/hf_ggml-org_qwen3-reranker-0.6b-q8_0.gguf" | sha256sum -c \
 && chmod -R a+rX /opt/qmd-models
LABEL l2.version="semble 0.6.0 + qmd 2.8.3 (rerank Qwen3-Reranker-0.6B-Q8_0@a02f48b)"
USER agent
