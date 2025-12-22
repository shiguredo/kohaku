.PHONY: init up down build

init: build
	mkdir -p rustfs/data  rustfs/logs plugins
	sudo chown -R 10001:10001 rustfs/data rustfs/logs

up:
	docker compose up -d

down:
	docker compose down --rmi local
	sudo rm -rf ./ingester/.venv

clean:
	rm -rf ./plugins ./fluent-bit.yml
	rm -rf init/dist
	docker volume rm kohaku-volume
	sudo rm -rf ./rustfs/data ./rustfs/logs
	-docker network rm -f kohaku-network

# 独自でビルドが必要になったとき用
build: download
	make -C init
	cp init/dist/* plugins/motherduck-duckdb-datasource/

GRAFANA_DUCKDB_DATASOURCE_VERSION ?= 0.4.0

download:
	curl -LO https://github.com/motherduckdb/grafana-duckdb-datasource/releases/download/v${GRAFANA_DUCKDB_DATASOURCE_VERSION}/motherduck-duckdb-datasource-${GRAFANA_DUCKDB_DATASOURCE_VERSION}.zip
	unzip motherduck-duckdb-datasource-${GRAFANA_DUCKDB_DATASOURCE_VERSION}.zip -d plugins/
	rm motherduck-duckdb-datasource-${GRAFANA_DUCKDB_DATASOURCE_VERSION}.zip


setup: setup-fluent-bit setup-grafana setup-kohaku

define ENV_FLUENT_BIT
env:
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

define ENV_FLUENT_BIT_FOR_RUSTFS
env:
  S3_ENDPOINT: ${S3_ENDPOINT_SCHEME}://${S3_ENDPOINT}
  S3_BUCKET: ${S3_BUCKET}
  S3_PREFIX: ${S3_PREFIX}
  SORA_LOG_PATH: ${LOG_PATH}

endef

define SYSTEMD_FLUENT_BIT
[Service]
EnvironmentFile=-/opt/kohaku/.env
ExecStart=
ExecStart=/opt/fluent-bit/bin/fluent-bit -c /etc/fluent-bit/fluent-bit.yml

endef

export ENV_FLUENT_BIT
export ENV_FLUENT_BIT_FOR_RUSTFS
export SYSTEMD_FLUENT_BIT

setup-fluent-bit: fluent-bit-yml
	cp ./fluent-bit.yml /etc/fluent-bit/
	mkdir -p /etc/systemd/system/fluent-bit.service.d
	echo "$$SYSTEMD_FLUENT_BIT" | tee /etc/systemd/system/fluent-bit.service.d/override.conf 1>/dev/null
	systemctl daemon-reload

fluent-bit-yml:
	echo "$$ENV_FLUENT_BIT" | tee fluent-bit.yml 1>/dev/null
	cat ./fluent-bit/fluent-bit.yml.s3 | tee -a fluent-bit.yml 1>/dev/null

setup-fluent-bit-for-rustfs: fluent-bit-yml-for-rustfs
	cp ./fluent-bit.yml /etc/fluent-bit/
	mkdir -p /etc/systemd/system/fluent-bit.service.d
	echo "$$SYSTEMD_FLUENT_BIT" | tee /etc/systemd/system/fluent-bit.service.d/override.conf 1>/dev/null
	systemctl daemon-reload

fluent-bit-yml-for-rustfs:
	echo "$$ENV_FLUENT_BIT_FOR_RUSTFS" | tee fluent-bit.yml 1>/dev/null
	cat ./fluent-bit/fluent-bit.yml.rustfs | tee -a fluent-bit.yml 1>/dev/null

include .env

setup-grafana:
	grep GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS /etc/default/grafana-server >/dev/null 2>&1; [ "0" -ne "$$?" ] && echo 'GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=motherduck-duckdb-datasource' | tee -a /etc/default/grafana-server
	grep GF_PATHS_DATA /etc/default/grafana-server >/dev/null 2>&1; [ "0" -ne "$$?" ] && echo 'GF_PATHS_DATA=/var/lib/grafana' | tee -a /etc/default/grafana-server
	sed "s@path:.*@path: ${DUCKDB_DB_PATH}.readonly@g" grafana/datasources/duckdb.yml > duckdb.yml
	cp duckdb.yml /etc/grafana/provisioning/datasources/duckdb.yml
	cp grafana/dashboards/kohaku.yml /etc/grafana/provisioning/dashboards/kohaku.yml
	mkdir -p /var/lib/grafana/dashboards
	cp -r grafana/dashboards/kohaku /var/lib/grafana/dashboards/kohaku
	sudo chown -R grafana:grafana /var/lib/grafana/dashboards/kohaku/

setup-kohaku:
	mkdir -p /var/lib/kohaku/duckdb
	chown -R kohaku:kohaku /var/lib/kohaku
	find /var/lib/kohaku/ -type d -exec chmod 755 {} +
	find /var/lib/kohaku/ -type f -exec chmod 666 {} +
