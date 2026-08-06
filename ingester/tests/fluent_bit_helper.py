from pathlib import Path

from jinja2 import Template

# fluent-bit からアクセスする S3 エンドポイント (同一Docker network 上の rustfs ホスト名を解決)
FLUENT_BIT_S3_ENDPOINT = "http://rustfs:9000"
# fluent-bit コンテナ内で監視対象とするログディレクトリパス
FLUENT_BIT_SORA_LOG_PATH = "/log"
# fluent-bit 設定生成に用いる Jinja2 テンプレートの配置パス
FLUENT_BIT_CONFIG_TEMPLATE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "fluent-bit"
    / "default-fluent-bit.yml.j2"
)


def create_fluent_bit_config(
    config_path: Path,
    template_path: Path = FLUENT_BIT_CONFIG_TEMPLATE_PATH,
    s3_endpoint: str = FLUENT_BIT_S3_ENDPOINT,
    s3_bucket: str = "kohaku",
    s3_prefix: str = "log",
    sora_log_path: str = FLUENT_BIT_SORA_LOG_PATH,
    hostname: str = "fluent-bit-test",
) -> None:
    """fluent-bit 設定テンプレートを描画して設定ファイルを書き出す。"""
    template_text = Path(template_path).read_text(encoding="utf-8")
    config_text = Template(template_text).render(
        s3_endpoint=s3_endpoint,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
        sora_log_path=sora_log_path,
        hostname=hostname,
    )
    config_path.write_text(
        config_text,
        encoding="utf-8",
    )
