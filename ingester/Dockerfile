FROM ubuntu:24.04

ENV TZ=UTC

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends curl ca-certificates tzdata \
    && ln -fs /usr/share/zoneinfo/UTC /etc/localtime \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --no-create-home --gid 0 kohaku

# uv バイナリをバージョン固定で取得し、SHA-256 を検証してから /usr/local/bin に配置する
# SHA-256 は同じリリースから取得するため、転送・保存時の不整合検出を主目的にする。
# TARGETARCH は BuildKit が自動で amd64 / arm64 を設定する。
ARG UV_VERSION=0.11.21
ARG TARGETARCH
RUN set -eux; \
    case "${TARGETARCH}" in \
        amd64) UV_TARGET=x86_64-unknown-linux-gnu ;; \
        arm64) UV_TARGET=aarch64-unknown-linux-gnu ;; \
        *) echo "Unsupported TARGETARCH: '${TARGETARCH}'" >&2; exit 1 ;; \
    esac; \
    UV_TARBALL="uv-${UV_TARGET}.tar.gz"; \
    UV_URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${UV_TARBALL}"; \
    cd /tmp; \
    curl -LsSf -o "${UV_TARBALL}" "${UV_URL}"; \
    curl -LsSf -o "${UV_TARBALL}.sha256" "${UV_URL}.sha256"; \
    sha256sum -c "${UV_TARBALL}.sha256"; \
    tar -xzf "${UV_TARBALL}"; \
    [ -d "uv-${UV_TARGET}" ] || { \
        echo "Unexpected tarball structure: uv-${UV_TARGET}/ not found in ${UV_TARBALL}" >&2; \
        exit 1; \
    }; \
    mv "uv-${UV_TARGET}/uv" /usr/local/bin/uv; \
    rm -rf "uv-${UV_TARGET}" "${UV_TARBALL}" "${UV_TARBALL}.sha256"

# DuckDB ファイルと読み取り専用コピーの配置先になる /var/lib/kohaku と /ingester を kohaku:root で
# 用意する。 USER kohaku で uv sync が .venv を作れるように、/ingester ディレクトリ
# 自体の所有者を事前に kohaku:root にしておく必要がある。 COPY --chown はコピーする
# 中身の所有者変更を行うため、コピー後の再帰 chown は不要。
RUN mkdir -p /var/lib/kohaku/duckdb /ingester \
    && chown -R kohaku:root /var/lib/kohaku \
    && chmod -R g+rw /var/lib/kohaku \
    && chown kohaku:root /ingester

ENV HOME=/var/lib/kohaku

WORKDIR /ingester

# 依存の解決は pyproject.toml と uv.lock だけを先に COPY してレイヤキャッシュを効かせる。
# src 変更で依存インストールが再走しないようにする。
COPY --chown=kohaku:root pyproject.toml uv.lock /ingester/
USER kohaku
RUN uv sync --frozen --no-install-project

# 残り (src / DUCKDB_COLUMNS / run.sh / tests 等) を COPY する。 依存に変化がなければ
# 以降のレイヤだけが差し替わる。
USER root
COPY --chown=kohaku:root . /ingester
USER kohaku

ENTRYPOINT ["/ingester/run.sh"]
