# ingester

## 実行環境の構築

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
. $HOME/.local/bin/env
uv sync
```

## 実行例

### テーブル作成および初期データの挿入

```bash
uv run python src/run.py --db ./duck.db \
                      --storage minio \
                      --s3_endpoint minio:90000 \
                      --s3_access_key_id AWS_ACCESS_KEY_ID \
                      --s3_secret_access_key AWS_SECRET_ACCESS_KEY \
                      --s3_bucket kohaku \
                      --s3_prefix log  \
                      --initial_maximum_load 100 \
                      init
```

### データの挿入

```bash
uv run python src/run.py --db ./duck.db \
                      --storage minio \
                      --s3_endpoint minio:9000 \
                      --s3_access_key_id AWS_ACCESS_KEY_ID \
                      --s3_secret_access_key AWS_SECRET_ACCESS_KEY \
                      --s3_bucket kohaku \
                      --s3_prefix log  \
                      --initial_maximum_load 100 \
                      update
```

### 保存期間を超えたデータの削除

```bash
uv run python src/run.py --db ./duck.db \
                      --retention_period 7 \
                      delete
```
