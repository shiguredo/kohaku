FROM ubuntu:22.04 AS builder

ARG GRAFANA_DUCKDB_DATASOURCE_VERSION=0.4.0
ARG GO_VERSION=1.24.4

ARG TARGETOS
ARG TARGETARCH
ARG TARGETPLATFORM

RUN apt-get -y update && apt-get -y install git curl g++

# go tarball を dl.google.com から取得し、同時に配布されている .sha256 (プレーン
# テキストの 64 桁hex) と突き合わせて完全性を確認する。go.dev/dl/ の .sha256 は
# ドキュメントページへの HTML リダイレクトを返すため、tarball と .sha256 の両方が
# 素の内容で配信される dl.google.com/go/ を用いる。同一サーバから取得するため
# 悪意ある差し替えを完全には防げないが、転送破損や中間の書き換えは検出できる
# (ingester/Dockerfile の uv verify-before-extract と同じ設計方針)。
RUN curl -fLO https://dl.google.com/go/go${GO_VERSION}.${TARGETOS}-${TARGETARCH}.tar.gz \
 && curl -fLO https://dl.google.com/go/go${GO_VERSION}.${TARGETOS}-${TARGETARCH}.tar.gz.sha256 \
 && echo "$(cat go${GO_VERSION}.${TARGETOS}-${TARGETARCH}.tar.gz.sha256)  go${GO_VERSION}.${TARGETOS}-${TARGETARCH}.tar.gz" | sha256sum -c - \
 && tar -C /usr/local -xzf go${GO_VERSION}.${TARGETOS}-${TARGETARCH}.tar.gz \
 && rm go${GO_VERSION}.${TARGETOS}-${TARGETARCH}.tar.gz*

ENV PATH=$PATH:/usr/local/go/bin:/root/go/bin

ARG MAGE_VERSION=v1.17.2
RUN /usr/local/go/bin/go install github.com/magefile/mage@${MAGE_VERSION}

WORKDIR /plugin

RUN git clone -b v${GRAFANA_DUCKDB_DATASOURCE_VERSION} https://github.com/motherduckdb/grafana-duckdb-datasource.git

WORKDIR /plugin/grafana-duckdb-datasource

COPY ./dispose.patch .

RUN patch -p 1 < dispose.patch

RUN <<EOF
    set -eux
    case "${TARGETPLATFORM}" in
        "linux/amd64" ) BUILD_TARGET=Linux ;;
        "linux/arm64" ) BUILD_TARGET=LinuxARM64 ;;
        * ) echo "Unsupported platform: ${TARGETPLATFORM}" >&2; exit 1 ;;
    esac
    go get github.com/grafana/sqlds/v4
    mage -v build:${BUILD_TARGET} build:GenerateManifestFile
EOF

FROM scratch

COPY --from=builder /plugin/grafana-duckdb-datasource/dist /dist
