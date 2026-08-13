.PHONY: init up up-external-s3 down down-external-s3 build

COMPOSE_RUSTFS := compose.yml
COMPOSE_EXTERNAL_S3 := compose.external-s3.yml

# プラグインを準備して RustFS の保存ディレクトリを作成する
init: build
	mkdir -p rustfs/data  rustfs/logs

# 標準構成でコンテナを起動する (fluent-bit.yml が無い場合のみ生成する)
up:
	@if [ ! -f fluent-bit.yml ]; then $(MAKE) fluent-bit-yml-for-rustfs; fi
	USER_ID=`id -u` GROUP_ID=`id -g` docker compose -f $(COMPOSE_RUSTFS) up -d --build

# 外部 S3 構成でコンテナを起動する (fluent-bit.yml が無い場合のみ生成する)
up-external-s3:
	@if [ ! -f fluent-bit.yml ]; then $(MAKE) fluent-bit-yml; fi
	docker compose -f $(COMPOSE_EXTERNAL_S3) up -d --build

# 標準構成のコンテナを停止する
down:
	USER_ID=`id -u` GROUP_ID=`id -g` docker compose -f $(COMPOSE_RUSTFS) down --rmi local

# 外部 S3 構成のコンテナを停止する
down-external-s3:
	docker compose -f $(COMPOSE_EXTERNAL_S3) down --rmi local

# 生成ファイル、Docker ボリューム、RustFS の保存データを削除する
clean:
	rm -rf ./plugins ./fluent-bit.yml
	rm -rf init/dist
	-docker volume rm -f kohaku-volume kohaku-fluent-bit-state
	rm -rf ./rustfs/data ./rustfs/logs
	-docker network rm -f kohaku-network

# DuckDB データソースプラグインを取得し、init 配下のビルド成果物を配置する
build: download
	make -C init
	mkdir -p plugins
	cp init/dist/* plugins/motherduck-duckdb-datasource/

GRAFANA_DUCKDB_DATASOURCE_VERSION ?= 0.4.0

# Grafana 用 DuckDB データソースプラグインを取得する
download:
	rm -rf plugins/motherduck-duckdb-datasource
	rm -f motherduck-duckdb-datasource-${GRAFANA_DUCKDB_DATASOURCE_VERSION}.zip
	curl -LO https://github.com/motherduckdb/grafana-duckdb-datasource/releases/download/v${GRAFANA_DUCKDB_DATASOURCE_VERSION}/motherduck-duckdb-datasource-${GRAFANA_DUCKDB_DATASOURCE_VERSION}.zip
	unzip motherduck-duckdb-datasource-${GRAFANA_DUCKDB_DATASOURCE_VERSION}.zip -d plugins/
	rm motherduck-duckdb-datasource-${GRAFANA_DUCKDB_DATASOURCE_VERSION}.zip


# Fluent Bit、Grafana、Kohaku 保存領域のホスト側設定をまとめて反映する
setup: setup-fluent-bit setup-grafana setup-kohaku

# S3_ENDPOINT は .env で未指定なら Amazon S3 のエンドポイントを使う
S3_ENDPOINT ?= s3.amazonaws.com

define ENV_FLUENT_BIT
env:
  S3_ENDPOINT: ${S3_ENDPOINT}
  S3_BUCKET: ${S3_BUCKET}
  S3_PREFIX: ${S3_PREFIX}
  S3_REGION: ${S3_REGION}
  SORA_LOG_PATH: ${LOG_PATH}

endef

ifeq ($(DOCKER),true)
  LOG_PATH:=/log
else
  LOG_PATH=$(SORA_LOG_PATH)
endif

ifeq ($(S3_USE_SSL),true)
  S3_ENDPOINT_PROTO:=https
else
  S3_ENDPOINT_PROTO:=http
endif

define ENV_FLUENT_BIT_FOR_RUSTFS
env:
  S3_ENDPOINT: ${S3_ENDPOINT_PROTO}://${S3_ENDPOINT}
  S3_BUCKET: ${S3_BUCKET}
  S3_PREFIX: ${S3_PREFIX}
  SORA_LOG_PATH: ${LOG_PATH}

endef

define ENV_FLUENT_BIT_SYSTEMD
AWS_ACCESS_KEY_ID=${AWS_ACCESS_KEY_ID}
AWS_SECRET_ACCESS_KEY=${AWS_SECRET_ACCESS_KEY}

endef

define SYSTEMD_FLUENT_BIT
[Service]
EnvironmentFile=/etc/fluent-bit/kohaku.env
ExecStart=
ExecStart=/opt/fluent-bit/bin/fluent-bit -c /etc/fluent-bit/fluent-bit.yml

endef

export ENV_FLUENT_BIT
export ENV_FLUENT_BIT_FOR_RUSTFS
export ENV_FLUENT_BIT_SYSTEMD
export SYSTEMD_FLUENT_BIT

# Fluent Bit のホスト側設定と systemd drop-in を反映する
setup-fluent-bit: fluent-bit-yml
	mkdir -p /etc/fluent-bit
	cp ./fluent-bit.yml /etc/fluent-bit/
	echo "$$ENV_FLUENT_BIT_SYSTEMD" | tee /etc/fluent-bit/kohaku.env 1>/dev/null
	chmod 600 /etc/fluent-bit/kohaku.env
	# Fluent Bit の tail input が inode / offset を保存する DB ファイルを永続化する
	mkdir -p /var/lib/fluent-bit
	mkdir -p /etc/systemd/system/fluent-bit.service.d
	echo "$$SYSTEMD_FLUENT_BIT" | tee /etc/systemd/system/fluent-bit.service.d/override.conf 1>/dev/null
	systemctl daemon-reload

# Fluent Bit の標準設定ファイルを生成する
fluent-bit-yml:
	echo "$$ENV_FLUENT_BIT" | tee fluent-bit.yml 1>/dev/null
	cat ./fluent-bit/fluent-bit.yml.s3 | tee -a fluent-bit.yml 1>/dev/null

# RustFS 向け Fluent Bit のホスト側設定と systemd drop-in を反映する
setup-fluent-bit-for-rustfs: fluent-bit-yml-for-rustfs
	mkdir -p /etc/fluent-bit
	cp ./fluent-bit.yml /etc/fluent-bit/
	echo "$$ENV_FLUENT_BIT_SYSTEMD" | tee /etc/fluent-bit/kohaku.env 1>/dev/null
	chmod 600 /etc/fluent-bit/kohaku.env
	# Fluent Bit の tail input が inode / offset を保存する DB ファイルを永続化する
	mkdir -p /var/lib/fluent-bit
	mkdir -p /etc/systemd/system/fluent-bit.service.d
	echo "$$SYSTEMD_FLUENT_BIT" | tee /etc/systemd/system/fluent-bit.service.d/override.conf 1>/dev/null
	systemctl daemon-reload

# RustFS 向けの Fluent Bit 設定ファイルを生成する
fluent-bit-yml-for-rustfs:
	echo "$$ENV_FLUENT_BIT_FOR_RUSTFS" | tee fluent-bit.yml 1>/dev/null
	cat ./fluent-bit/fluent-bit.yml.rustfs | tee -a fluent-bit.yml 1>/dev/null

# init と clean だけは .env 不要にし、setup-* ターゲットは従来どおり .env を必須にする
ifeq ($(filter init clean,$(MAKECMDGOALS)),)
include .env
endif

# Grafana の起動設定とプロビジョニング設定を反映する
setup-grafana:
	grep GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS /etc/default/grafana-server >/dev/null 2>&1 || echo 'GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=motherduck-duckdb-datasource' | tee -a /etc/default/grafana-server
	grep GF_PATHS_DATA /etc/default/grafana-server >/dev/null 2>&1 || echo 'GF_PATHS_DATA=/var/lib/grafana' | tee -a /etc/default/grafana-server
	grep GF_PLUGINS_FORWARD_HOST_ENV_VARS /etc/default/grafana-server >/dev/null 2>&1 || echo 'GF_PLUGINS_FORWARD_HOST_ENV_VARS=motherduck-duckdb-datasource' | tee -a /etc/default/grafana-server
	grep GF_SERVER_HTTP_PORT /etc/default/grafana-server >/dev/null 2>&1 || echo 'GF_SERVER_HTTP_PORT=$(GRAFANA_HTTP_PORT)' | tee -a /etc/default/grafana-server
	sed "s@path:.*@path: ${DUCKDB_DB_PATH}.readonly@g" grafana/datasources/duckdb.yml > duckdb.yml
	cp duckdb.yml /etc/grafana/provisioning/datasources/duckdb.yml
	cp grafana/dashboards/kohaku.yml /etc/grafana/provisioning/dashboards/kohaku.yml
	mkdir -p /var/lib/grafana/dashboards/kohaku
	cp -r grafana/dashboards/kohaku/. /var/lib/grafana/dashboards/kohaku/
	sudo chown -R grafana:grafana /var/lib/grafana/dashboards/kohaku/

# Kohaku の保存領域と権限を準備する
# grafana ユーザーも kohaku グループに属している前提にする。
# /var/lib/kohaku 以下のファイルを Grafana から参照できるようにする
setup-kohaku:
	mkdir -p /var/lib/kohaku/duckdb
	chown -R kohaku:kohaku /var/lib/kohaku
	find /var/lib/kohaku/ -type d -exec chmod 755 {} +
	find /var/lib/kohaku/ -type f -exec chmod 660 {} +
