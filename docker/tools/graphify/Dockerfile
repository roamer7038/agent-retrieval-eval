# graphify (PyPI graphifyy) in a venv, code-only: the tree-sitter AST pass,
# no LLM backend and no extras. requirements.txt pins every package.
FROM are-l0-base:pe1
USER root
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" python3-venv \
 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /opt/graphify/requirements.txt
RUN python3 -m venv /opt/graphify/venv \
 && /opt/graphify/venv/bin/pip install -q --no-cache-dir --only-binary :all: -r /opt/graphify/requirements.txt \
 && ln -s /opt/graphify/venv/bin/graphify /usr/local/bin/graphify
ENV GRAPHIFY_QUERY_LOG_DISABLE=1
COPY run.sh /opt/l0/run.sh
LABEL l0.version="graphifyy 0.9.64"
USER agent
