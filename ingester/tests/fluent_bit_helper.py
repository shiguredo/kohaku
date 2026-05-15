from pathlib import Path

from jinja2 import Template

FLUENT_BIT_S3_ENDPOINT = "http://rustfs:9000"
FLUENT_BIT_SORA_LOG_PATH = "/log"
FLUENT_BIT_CONFIG_TEMPLATE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "fluent-bit"
    / "default-fluent-bit.yml.j2"
)


def create_fluent_bit_config(
    config_path,
    template_path=FLUENT_BIT_CONFIG_TEMPLATE_PATH,
    s3_endpoint=FLUENT_BIT_S3_ENDPOINT,
    s3_bucket="kohaku",
    s3_prefix="log",
    sora_log_path=FLUENT_BIT_SORA_LOG_PATH,
):
    """
    fluent-bit 設定テンプレートを描画して設定ファイルを書き出す。
    :param config_path: 出力先設定ファイルの Path
    :param template_path: fluent-bit 設定テンプレートの Path
    :param s3_endpoint: fluent-bit が接続する S3 エンドポイント
    :param s3_bucket: 出力先バケット名
    :param s3_prefix: 出力オブジェクトのプレフィックス
    :param sora_log_path: fluent-bit が参照するログディレクトリパス
    :return: なし
    """
    template_text = Path(template_path).read_text(encoding="utf-8")
    config_text = Template(template_text).render(
        s3_endpoint=s3_endpoint,
        s3_bucket=s3_bucket,
        s3_prefix=s3_prefix,
        sora_log_path=sora_log_path,
    )
    config_path.write_text(
        config_text,
        encoding="utf-8",
    )
