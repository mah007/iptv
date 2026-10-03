"""ffmpeg `-progress` parsing (apps/media/progress.py)."""

from __future__ import annotations

import pytest

from apps.media.progress import ProgressParser, ProgressUpdate, Throttle

CAPTURED = """\
frame=0
fps=0.00
stream_0_0_q=0.0
bitrate=N/A
total_size=48
out_time_us=-9223372036854775807
out_time_ms=-9223372036854775807
out_time=-577014:32:22.775808
dup_frames=0
drop_frames=0
speed=N/A
progress=continue
frame=120
fps=59.94
stream_0_0_q=28.0
bitrate=1503.2kbits/s
total_size=752640
out_time_us=4004000
out_time_ms=4004000
out_time=00:00:04.004000
dup_frames=0
drop_frames=0
speed=1.99x
progress=continue
frame=180
fps=60.00
bitrate=1490.0kbits/s
total_size=1117440
out_time_us=6000000
out_time_ms=6000000
out_time=00:00:06.000000
speed=2x
progress=end
"""


def test_parses_a_captured_stream() -> None:
    parser = ProgressParser(duration_ms=10_000)
    updates = parser.feed_lines(CAPTURED.splitlines())
    assert updates == [
        ProgressUpdate(
            out_time_ms=None,
            frame=0,
            fps=0.0,
            speed=None,
            bitrate_kbps=None,
            total_size=48,
            done=False,
            percent=None,
            eta_s=None,
        ),
        ProgressUpdate(
            out_time_ms=4004,
            frame=120,
            fps=59.94,
            speed=1.99,
            bitrate_kbps=1503.2,
            total_size=752_640,
            done=False,
            percent=40.04,
            eta_s=4,  # 5.996 s of media left at 1.99x
        ),
        ProgressUpdate(
            out_time_ms=6000,
            frame=180,
            fps=60.0,
            speed=2.0,
            bitrate_kbps=1490.0,
            total_size=1_117_440,
            done=True,
            percent=100.0,
            eta_s=0,
        ),
    ]
    assert parser.last == updates[-1]


def test_without_a_duration_there_is_no_percentage() -> None:
    update = ProgressParser().feed_lines(["out_time_ms=1500000", "speed=1x", "progress=continue"])
    assert update == [
        ProgressUpdate(
            out_time_ms=1500,
            frame=None,
            fps=None,
            speed=1.0,
            bitrate_kbps=None,
            total_size=None,
            done=False,
            percent=None,
            eta_s=None,
        )
    ]


@pytest.mark.parametrize(
    ("lines", "out_time_ms"),
    [
        (["out_time=01:02:03.500000"], 3_723_500),
        (["out_time=N/A"], None),
        (["out_time=12:34"], None),
        (["out_time=aa:bb:cc"], None),
        (["out_time_us=2500000", "out_time=00:00:09.000000"], 2500),
    ],
)
def test_out_time_sources(lines: list[str], out_time_ms: int | None) -> None:
    (update,) = ProgressParser().feed_lines([*lines, "progress=continue"])
    assert update.out_time_ms == out_time_ms


def test_percent_is_capped_and_garbage_is_ignored() -> None:
    parser = ProgressParser(duration_ms=1000)
    assert parser.feed("not a key value line") is None
    assert parser.feed("fps=nan") is None
    (update,) = parser.feed_lines(["out_time_ms=5000000", "progress=continue"])
    assert update.percent == 100.0
    assert update.fps is None
    assert update.eta_s is None  # no speed yet


def test_throttle() -> None:
    now = [100.0]
    throttle = Throttle(5.0, clock=lambda: now[0])
    assert throttle.ready()
    now[0] = 103.0
    assert not throttle.ready()
    assert throttle.ready(force=True)
    now[0] = 107.9
    assert not throttle.ready()
    now[0] = 108.0
    assert throttle.ready()
