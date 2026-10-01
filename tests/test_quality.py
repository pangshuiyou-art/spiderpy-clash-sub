"""免费代理池源侧质量筛选测试。"""

from free_proxy_merger.quality import apply_source_side_filter, sort_by_quality


def test_filter_keeps_records_without_source_metadata():
    records = [{"ip": "1.1.1.1"}]

    assert apply_source_side_filter(records, is_residential=True) == records
    assert apply_source_side_filter(records, is_residential=False) == records


def test_filter_applies_daily_latency_and_uptime_limits():
    good_uptime = {"latency_ms": 2000, "uptime": 80.0}
    streak_recovery = {"latency_ms": 2500, "uptime": 20.0, "streak": 2}
    slow = {"latency_ms": 3001, "uptime": 95.0}
    unstable = {"latency_ms": 2000, "uptime": 49.0}

    kept = apply_source_side_filter(
        [good_uptime, streak_recovery, slow, unstable], is_residential=False
    )

    assert kept == [good_uptime, streak_recovery]


def test_filter_only_applies_latency_limit_to_residential_records():
    usable = {"latency_ms": 5000, "uptime": 5.0}
    slow = {"latency_ms": 5001, "uptime": 5.0}

    kept = apply_source_side_filter([usable, slow], is_residential=True)

    assert kept == [usable]


def test_sort_by_quality_orders_streak_uptime_then_latency():
    records = [
        {"name": "latency", "latency_ms": 100, "uptime": 40.0},
        {"name": "streak", "latency_ms": 900, "uptime": 40.0, "streak": 1},
        {"name": "uptime", "latency_ms": 200, "uptime": 80.0},
        {"name": "unknown"},
    ]

    assert [item["name"] for item in sort_by_quality(records)] == [
        "streak",
        "uptime",
        "latency",
        "unknown",
    ]
