# Clang toolchain for the C gold (S1, S3, S7 on the Linux corpus): builds the
# kernel out of tree with LLVM=1 and walks the ASTs of the compiled
# translation units with libclang. The corpus is mounted read-only.
FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends \
      clang-19 lld-19 llvm-19 python3-clang-19 libclang-19-dev \
      make bc flex bison libelf-dev libssl-dev python3 cpio kmod xz-utils ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV PATH=/usr/lib/llvm-19/bin:$PATH
