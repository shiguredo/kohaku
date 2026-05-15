import time
from collections.abc import Callable


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
