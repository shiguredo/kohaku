FROM ubuntu:22.04 AS builder

ARG GRAFANA_DUCKDB_DATASOURCE_VERSION=0.4.0
ARG GO_VERSION=1.24.4

ARG TARGETOS
ARG TARGETARCH
ARG TARGETPLATFORM

RUN apt -y update && apt -y install git curl g++

RUN curl -LO https://go.dev/dl/go${GO_VERSION}.${TARGETOS}-${TARGETARCH}.tar.gz && tar -C /usr/local -xzf go${GO_VERSION}.${TARGETOS}-${TARGETARCH}.tar.gz

ENV PATH=$PATH:/usr/local/go/bin:/root/go/bin

RUN /usr/local/go/bin/go install github.com/magefile/mage@latest

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
