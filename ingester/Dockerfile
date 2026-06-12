FROM ubuntu:24.04

ENV TZ=UTC

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates tzdata \
    && ln -fs /usr/share/zoneinfo/UTC /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --no-create-home --gid 0 kohaku

# uv binary をバージョン pin で取得し、 sha256 を検証してから /usr/local/bin に配置する。
# TARGETARCH は BuildKit が自動で amd64 / arm64 を設定する。
ARG UV_VERSION=0.11.21
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) UV_TARGET=x86_64-unknown-linux-gnu ;; \
        arm64) UV_TARGET=aarch64-unknown-linux-gnu ;; \
        *) echo "Unsupported TARGETARCH: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    UV_TARBALL="uv-${UV_TARGET}.tar.gz"; \
    UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_TARBALL}"; \
    cd /tmp; \
    curl -LsSf -o "${UV_TARBALL}" "${UV_URL}"; \
    curl -LsSf -o "${UV_TARBALL}.sha256" "${UV_URL}.sha256"; \
    sha256sum -c "${UV_TARBALL}.sha256"; \
    tar -xzf "${UV_TARBALL}"; \
    mv "uv-${UV_TARGET}/uv" /usr/local/bin/uv; \
    mv "uv-${UV_TARGET}/uvx" /usr/local/bin/uvx; \
    rm -rf "uv-${UV_TARGET}" "${UV_TARBALL}" "${UV_TARBALL}.sha256"

RUN mkdir -p /var/lib/kohaku/duckdb /ingester \
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
