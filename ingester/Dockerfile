FROM ubuntu:24.04

ENV TZ=UTC

ARG USER_ID

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates python3 tzdata \
    && ln -fs /usr/share/zoneinfo/UTC /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /var/lib/kohaku/duckdb

RUN useradd --uid ${USER_ID} --no-create-home kohaku

RUN curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh

RUN mkdir -p /ingester \
    && chown -R ${USER_ID} /var/lib/kohaku /ingester

ENV HOME=/var/lib/kohaku

WORKDIR /ingester

COPY --chown=${USER_ID} . /ingester

USER ${USER_ID}

# lock ファイルに基づいて依存を事前インストールする
RUN uv sync --frozen

ENTRYPOINT ["bash", "/ingester/run.sh"]
