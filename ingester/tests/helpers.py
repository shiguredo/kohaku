import time


class WaitTimeoutError(Exception):
    pass


def wait_until(condition, timeout_sec=120, interval_sec=1):
    """
    条件が真になるまで待機する。
    :param condition: 真偽値を返す関数
    :param timeout_sec: タイムアウト秒数
    :param interval_sec: 再試行間隔秒数
    :return: なし。条件が満たされない場合は WaitTimeoutError を送出する
    """
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            if condition():
                return
        except Exception:
            pass
        time.sleep(interval_sec)
    raise WaitTimeoutError(f"condition was not met within {timeout_sec} seconds")
