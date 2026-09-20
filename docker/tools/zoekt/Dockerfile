# zoekt (no releases): built at a fixed commit. zoekt-index indexes a directory
# (the working tree, not a git commit) and takes symbols from universal-ctags
# of the ctags image for ranking and sym: queries.
FROM golang:1.25-bookworm@sha256:3b4a11519ad929d1e1d261a12cff056f0c85b735253d7d861346b9c6f8b36437 AS build
ARG ZOEKT_COMMIT=153817f643cde8b229ee388c1dddbcf07f4798af
ENV GOTOOLCHAIN=local CGO_ENABLED=0 GOBIN=/opt/zoekt/bin
RUN go install github.com/sourcegraph/zoekt/cmd/zoekt-index@${ZOEKT_COMMIT} \
 && go install github.com/sourcegraph/zoekt/cmd/zoekt@${ZOEKT_COMMIT}

FROM are-l0-ctags:pe1
COPY --from=build /opt/zoekt/bin /opt/zoekt/bin
ENV PATH=/opt/zoekt/bin:$PATH CTAGS_COMMAND=/opt/ctags/bin/ctags
COPY run.sh /opt/l0/run.sh
LABEL l0.version="zoekt 153817f6 (2026-09-11, go1.25.14; universal-ctags 6.2.1)"
