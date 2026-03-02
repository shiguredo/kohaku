FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.local/bin:${PATH}"
WORKDIR /ingester

COPY . /ingester

# lock ファイルに基づいて依存を事前インストールする
RUN uv sync --frozen

ENTRYPOINT ["bash", "/ingester/run.sh"]
