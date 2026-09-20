# Base of the L0 tool images: Ubuntu 24.04 with the apt archive frozen at a
# snapshot date, the measuring wrapper, and the host user's uid 1000 renamed
# to agent. Each tool's image (docker/tools/<tool>/Dockerfile) starts from it.
FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3
ARG APT_SNAPSHOT=20260919T000000Z
ENV DEBIAN_FRONTEND=noninteractive LANG=C.UTF-8 APT_SNAPSHOT=${APT_SNAPSHOT}
RUN apt-get update -q && apt-get install -qy --no-install-recommends ca-certificates \
 && apt-get update -q --snapshot "$APT_SNAPSHOT" \
 && apt-get install -qy --no-install-recommends --snapshot "$APT_SNAPSHOT" \
      curl git time procps python3 xz-utils unzip file jq \
 && rm -rf /var/lib/apt/lists/*
RUN usermod -l agent -d /home/agent -m ubuntu && groupmod -n agent ubuntu
COPY measure.py /opt/l0/measure.py
# Tools that phone home or check for updates are told not to.
ENV DO_NOT_TRACK=1 TELEMETRY_DISABLED=1 CODEGRAPH_TELEMETRY=0 NO_UPDATE_NOTIFIER=1 \
    HF_HUB_DISABLE_TELEMETRY=1 GOTOOLCHAIN=local
USER agent
WORKDIR /work
ENTRYPOINT ["python3", "/opt/l0/measure.py"]
