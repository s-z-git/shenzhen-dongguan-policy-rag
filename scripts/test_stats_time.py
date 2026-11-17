"""stats.py 时间窗纯函数测试：验证 date-only --until 覆盖当天 23:59:59。"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from stats import _parse_time, in_time_window  # noqa: E402


def main() -> int:
    since = _parse_time("2026-11-08")
    until = _parse_time("2026-11-08", end_of_day=True)
    assert until.hour == 23 and until.minute == 59 and until.second == 59
    assert until.microsecond == 999999

    assert in_time_window({"ts": "2026-11-08T00:00:00"}, since, until)
    assert in_time_window({"ts": "2026-11-08T12:00:00"}, since, until)
    assert in_time_window({"ts": "2026-11-08T23:59:59"}, since, until)
    assert not in_time_window({"ts": "2026-11-09T00:00:00"}, since, until)

    # 带时区输入能解析即可；内部 tzinfo 形态不作为契约。
    assert _parse_time("2026-11-08T12:00:00+08:00") is not None

    print("stats time tests: 7/7 PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
