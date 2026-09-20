# cscope from Ubuntu 24.04 (frozen apt snapshot). C only.
FROM are-l0-base:pe1
USER root
RUN apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" cscope=15.9-2 \
 && rm -rf /var/lib/apt/lists/*
COPY run.sh /opt/l0/run.sh
LABEL l0.version="cscope 15.9 (Ubuntu 15.9-2)"
USER agent
