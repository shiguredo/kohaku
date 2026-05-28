FROM ubuntu:24.04

ENV TZ=UTC

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates tzdata \
    && ln -fs /usr/share/zoneinfo/UTC /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/lib/kohaku/duckdb

RUN useradd --no-create-home --gid 0 kohaku

RUN curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

RUN mkdir -p /ingester \
    && chown -R kohaku:root /var/lib/kohaku \
    && chmod -R g+rw /var/lib/kohaku \
    && chown -R kohaku /ingester

ENV HOME=/var/lib/kohaku

WORKDIR /ingester

COPY --chown=kohaku . /ingester

USER kohaku

# lock ファイルに基づいて依存を事前インストールする
RUN uv sync --frozen

ENTRYPOINT ["bash", "/ingester/run.sh"]
