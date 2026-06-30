import time
from collections.abc import Callable


def full_args(**overrides) -> dict[str, object]:
    """init / update / delete が触る全 args 属性をデフォルト値で埋めた dict を返す。

    overrides で必要な属性のみ上書きする。 「テスト対象が将来別属性を参照するリグレッション」
    が入ったときに AttributeError で偽通過するのを防ぐため、 args 全属性を 1 箇所で定義する。
    s3_endpoint は RFC 6761 で予約された .invalid TLD を使い、 万一リグレッションで早期 return
    が抜けて S3 接続経路に進んでも DNS 解決段階で失敗させる。
    """
    defaults: dict[str, object] = {
        "db": "/tmp/dummy.db",
        "s3_endpoint": "s3.invalid",
        "s3_access_key_id": None,
        "s3_secret_access_key": None,
        "s3_use_ssl": False,
        "s3_region": "ap-northeast-1",
        "s3_bucket": "kohaku",
        "s3_prefix": "log",
        "retention_period": 7,
        "initial_maximum_load": 100,
        "update_maximum_load": 100,
    }
    defaults.update(overrides)
    return defaults


class WaitTimeoutError(Exception):
    pass


def wait_until(
    condition: Callable[[], bool],
    timeout_sec: float = 120,
    interval_sec: float = 1,
) -> None:
    """条件が真になるまで待機し、タイムアウトしたら WaitTimeoutError を送出する。"""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            if condition():
                return
        except Exception:
            pass
        time.sleep(interval_sec)
    raise WaitTimeoutError(f"condition was not met within {timeout_sec} seconds")
