#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理池记录的源侧质量筛选与排序。

这些规则来自旧免费代理产线的实测阈值，聚合流水线继续复用。
"""

# 日常组源侧 uptime 百分比下限
DAILY_MIN_UPTIME = 50.0
# 日常组源侧延迟上限
DAILY_MAX_LATENCY_MS = 3000
# 住宅组数量稀少，只排除明显高延迟条目
RESIDENTIAL_MAX_LATENCY_MS = 5000


def apply_source_side_filter(records: list[dict], is_residential: bool) -> list[dict]:
    """只过滤带源侧质量元数据的记录；无元数据记录交给 TCP 与 mihomo 裁决。"""
    kept: list[dict] = []
    for record in records:
        latency = record.get('latency_ms')
        uptime = record.get('uptime')
        streak = record.get('streak')
        has_meta = latency is not None or uptime is not None or streak is not None
        if not has_meta:
            kept.append(record)
            continue
        if is_residential:
            if latency is not None and latency > RESIDENTIAL_MAX_LATENCY_MS:
                continue
            kept.append(record)
            continue

        # uptime 与 streak 满足其一即可；不同源对这两个字段的定义并不一致。
        if uptime is not None and uptime < DAILY_MIN_UPTIME and not (streak and streak >= 1):
            continue
        if latency is not None and latency > DAILY_MAX_LATENCY_MS:
            continue
        kept.append(record)
    return kept


def sort_by_quality(records: list[dict]) -> list[dict]:
    """按连续存活、uptime、延迟排序；无元数据记录排到最后。"""
    def key(record: dict):
        return (
            -(int(record.get('streak') or 0)),
            -(float(record.get('uptime') or 0)),
            float(record.get('latency_ms') if record.get('latency_ms') is not None else 10 ** 9),
        )

    return sorted(records, key=key)
