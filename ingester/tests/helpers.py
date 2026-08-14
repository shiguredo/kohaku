from __future__ import annotations

import dataclasses
import time
from collections.abc import Callable

import run


def full_args(**overrides: object) -> run.Args:
    """init / update / delete が触る全 args 属性をデフォルト値で埋めた Args を返す。

    overrides で必要な属性のみ上書きする。 属性の定義は run.Args に委譲し、
    テスト用に本番デフォルトと異なる値が必要な属性 (db / s3_endpoint の
    .invalid TLD、 s3_use_ssl の False、 initial_maximum_load 等) のみを
    ここで上書きする。 s3_endpoint は RFC 6761 で予約された .invalid TLD を使い、
    万一リグレッションで早期 return が抜けて S3 接続経路に進んでも DNS 解決段階で
    失敗させる。

    run.Args に新フィールドが追加された場合はここにも追加すること (追加し忘れると
    Args の本番デフォルトが黙って適用されるため、 test_full_args_covers_all_args_fields
    でキー集合の一致を検証する)。
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
    unknown = sorted(set(overrides) - set(defaults))
    if unknown:
        raise TypeError(f"Unknown override keys: {unknown}")
    defaults.update(overrides)
    return dataclasses.replace(run.Args(), **defaults)


class WaitTimeoutError(Exception):
    pass


def wait_until(
    condition: Callable[[], bool],
    timeout_sec: float = 120,
    interval_sec: float = 1,
) -> None:
    """条件が真になるまで待機し、タイムアウトしたら WaitTimeoutError を送出する。

    condition の呼び出し中に例外が出てもポーリングは継続する (接続待ち等では初期の
    connection refused 等が正常なため)。 タイムアウト時は最後に観測した例外を
    WaitTimeoutError の __cause__ に付けてメッセージにも含め、 「なぜ条件が満たされなかったか」
    を診断できるようにする。
    """
    deadline = time.time() + timeout_sec
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            if condition():
                return
        except Exception as error:
            last_error = error
        time.sleep(interval_sec)
    if last_error is not None:
        raise WaitTimeoutError(
            f"condition was not met within {timeout_sec} seconds; "
            f"last error: {type(last_error).__name__}: {last_error}"
        ) from last_error
    raise WaitTimeoutError(f"condition was not met within {timeout_sec} seconds")
