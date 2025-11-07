FROM ubuntu:24.04

ENV GF_LOG_MODE=console
ENV GF_PATHS_DATA=/var/lib/grafana
ENV GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=motherduck-duckdb-datasource
ENV GF_HOMEPATH=/usr/share/grafana
ENV GF_CONFIG=/etc/grafana/grafana.ini
ENV GF_PATHS_PLUGINS=/var/lib/grafana/plugins
ENV GF_PATHS_PROVISIONING=/etc/grafana/provisioning

ENV GF_SECURITY_ADMIN_USER
ENV GF_SECURITY_ADMIN_PASSWORD

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y apt-transport-https software-properties-common wget

RUN mkdir -p /etc/apt/keyrings/

RUN wget -q -O - https://apt.grafana.com/gpg.key | gpg --dearmor | tee /etc/apt/keyrings/grafana.gpg > /dev/null

RUN echo "deb [signed-by=/etc/apt/keyrings/grafana.gpg] https://apt.grafana.com stable main" | tee -a /etc/apt/sources.list.d/grafana.list

RUN apt-get update && apt-get -y install grafana

USER grafana

ENTRYPOINT ["grafana-server"]

CMD ["--homepath=${GF_HOMEPATH}", "--config=${GF_CONFIG}", "cfg:default.log.mode=${GF_LOG_MODE}", "cfg:default.paths.data=${GF_PATHS_DATA}", "cfg:default.paths.plugins=${GF_PATHS_PLUGINS}", "cfg:default.paths.provisioning=${GF_PATHS_PROVISIONING}"]
