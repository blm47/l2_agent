from scripts.smoke_preview import summarize


def samples(growth: float = 0) -> list[dict[str, float]]:
    return [
        {
            "seconds": float(second),
            "rss_mb": 150.0,
            "private_mb": 100.0 + (growth if second > 500 else 0),
            "fps": 30.0,
        }
        for second in range(0, 601, 5)
    ]


def test_stable_ten_minute_preview_passes():
    report = summarize(samples(2), 600.1, 18000, 0.8)
    assert report["passed"]
    assert report["ten_minute_run"]
    assert report["private_growth_mb_after_warmup"] == 2


def test_stalled_preview_fails_even_with_many_frames():
    assert not summarize(samples(), 600.1, 10000, 5)["passed"]


def test_memory_growth_fails():
    assert not summarize(samples(50), 600.1, 18000, 0.5)["passed"]


def test_short_run_is_not_ten_minute_acceptance():
    report = summarize([], 30, 100, 0.5)
    assert not report["ten_minute_run"]
    assert report["private_growth_mb_after_warmup"] is None


def test_no_frames_fails():
    assert not summarize(samples(), 600, 0, 600)["passed"]


def test_interrupted_run_does_not_pass():
    report = summarize(samples(), 600, 18000, 0.5, "Прогон прерван")
    assert not report["passed"]
    assert report["error"] == "Прогон прерван"
