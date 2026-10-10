#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""节点台账：聚合流水线的跨轮记忆层

解决的问题：流水线原本每轮无状态、产物整体覆盖，上一轮验证过的好节点只要
当轮源头没再列出、或测活瞬时抖动一次，就永久消失。台账把每个验证通过过的
节点连同履历（通过轮数、连败数、末次验证时间、元数据、稳定名）持久化到
仓库文件，下一轮把它拉回考场，与新节点同标准重考。

设计要点：
- 键 = (protocol, server, port)，与 pipeline._cross_dedup 的去重口径一致；
- 只记 record_kind='proxypool' 的记录（ip:port 型）。链接型节点（vmess 等）
  携带凭据且其订阅源本身重列稳定，本期不入台账，避免扩大敏感面；
- 质量标准零改动：台账只决定「谁有资格再被测」，不决定「谁直接上榜」；
- 淘汰线：连败达到 RETENTION_MAX_FAIL_STREAK，或距末次验证成功超过
  RETENTION_MAX_AGE_DAYS 天；条目总数超 MAX_ENTRIES 按末次验证时间裁剪。
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

LEDGER_FILENAME = 'known_nodes.jsonl'

# 保留组准入与淘汰线（docs/方案_聚合订阅节点保留机制_20261011.md §4.3）
RETENTION_MIN_VERIFIED = 3      # 历史通过轮数达到该值才进保留组
RETENTION_MAX_FAIL_STREAK = 3   # 连败达到该值即淘汰（1~2 连败为保留观察期）
RETENTION_MAX_AGE_DAYS = 7      # 距末次验证成功超过该天数即淘汰

# 回炉重测预算：按 tier 封顶（TCP 预筛前置，进 mihomo 前生效）
RETEST_CAPS = {'residential': 300, 'daily': 800, 'residential_socks4': 300}

# 台账条目上限：超出按 last_verified 降序裁剪，防止仓库文件无限膨胀
MAX_ENTRIES = 5000

# 节点名形如 [R]US-0001 / [D]JP-0002 / [R4]US-0003
_NAME_PATTERN = re.compile(r'^\[([A-Z0-9]+)\][A-Z]{2}-(\d+)$')


def ledger_path(out_dir: Path) -> Path:
    """台账文件与产物同目录（CNB 影子线传 --out-dir 即自动落影子目录）"""
    return Path(out_dir) / LEDGER_FILENAME


def make_key(record: dict) -> tuple[str, str, int]:
    """统一记录 → 台账键（协议小写，server 取 ip 或 server）"""
    protocol = str(record.get('protocol') or '').lower()
    server = str(record.get('ip') or record.get('server') or '')
    try:
        port = int(record.get('port') or 0)
    except (TypeError, ValueError):
        port = 0
    return (protocol, server, port)


def load(path: Path) -> dict[tuple[str, str, int], dict]:
    """读台账；单行损坏跳过不影响整体，文件缺失视为空台账"""
    entries: dict[tuple[str, str, int], dict] = {}
    path = Path(path)
    if not path.exists():
        return entries
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = item.get('key')
        if isinstance(key, list) and len(key) == 3:
            try:
                entries[(str(key[0]), str(key[1]), int(key[2] or 0))] = item
            except (TypeError, ValueError):
                continue
    return entries


def save(path: Path, entries: dict[tuple[str, str, int], dict]) -> None:
    """写台账（临时文件 + 原子替换，避免半写状态被下一轮读到）"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(entry, ensure_ascii=False, sort_keys=True)
             for entry in entries.values()]
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(''.join(line + '\n' for line in lines), encoding='utf-8')
    os.replace(tmp, path)


def _parse_time(value) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _age_days(entry: dict, now: datetime) -> float:
    verified = _parse_time(entry.get('last_verified'))
    if verified is None:
        return float('inf')
    return (now - verified).total_seconds() / 86400


def _is_active(entry: dict, now: datetime) -> bool:
    """有资格回炉重测：未踩淘汰线且末次验证成功未过期"""
    if int(entry.get('fail_streak') or 0) >= RETENTION_MAX_FAIL_STREAK:
        return False
    return _age_days(entry, now) <= RETENTION_MAX_AGE_DAYS


def record_pass(entries: dict, record: dict, tier: str, now: datetime) -> None:
    """测活通过：新建或强化台账条目（通过数 +1、连败清零、元数据刷新）"""
    key = make_key(record)
    now_iso = now.isoformat()
    entry = entries.get(key)
    if entry is None:
        entry = {
            'key': [key[0], key[1], key[2]],
            'protocol': key[0], 'ip': key[1], 'port': key[2],
            'tier': tier,
            'first_seen': now_iso, 'last_seen': now_iso, 'last_verified': now_iso,
            'verified_count': 0, 'pass_streak': 0, 'fail_streak': 0,
            'best_delay_ms': None, 'last_delay_ms': None,
            'country_code': '', 'asn': '', 'isp': '',
            'ip_kind': '', 'ip_confidence': '', 'relaxed': '',
            'sources': [], 'node_name': None,
        }
        entries[key] = entry
    entry['last_seen'] = now_iso
    entry['last_verified'] = now_iso
    entry['verified_count'] = int(entry.get('verified_count') or 0) + 1
    entry['pass_streak'] = int(entry.get('pass_streak') or 0) + 1
    entry['fail_streak'] = 0
    entry['tier'] = tier
    delay = record.get('delay_ms')
    if isinstance(delay, (int, float)):
        entry['last_delay_ms'] = int(delay)
        best = entry.get('best_delay_ms')
        if not isinstance(best, (int, float)) or delay < best:
            entry['best_delay_ms'] = int(delay)
    for field in ('country_code', 'asn', 'isp', 'ip_kind', 'ip_confidence', 'relaxed'):
        value = record.get(field)
        if value not in (None, ''):
            entry[field] = value
    sources = set(entry.get('sources') or []) | set(record.get('sources') or [])
    entry['sources'] = sorted(str(item) for item in sources)
    if record.get('node_name'):
        entry['node_name'] = str(record['node_name'])


def mark_fail(entries: dict, key: tuple[str, str, int], now: datetime) -> None:
    """回炉节点本轮测活失败：连败 +1、连续通过清零（不动 verified_count）"""
    entry = entries.get(key)
    if entry is None:
        return
    entry['fail_streak'] = int(entry.get('fail_streak') or 0) + 1
    entry['pass_streak'] = 0
    entry['last_seen'] = now.isoformat()


def build_retest_records(entries: dict, now: datetime) -> list[dict]:
    """在役台账节点 → 回炉候选记录（元数据回填，免重查 ip-api）

    每档按 last_verified 降序取前 RETEST_CAPS[tier] 条；连败达淘汰线或
    末次验证已过期的不回炉。
    """
    actives = [entry for entry in entries.values() if _is_active(entry, now)]
    actives.sort(key=lambda item: str(item.get('last_verified') or ''), reverse=True)
    counts = {tier: 0 for tier in RETEST_CAPS}
    records: list[dict] = []
    for entry in actives:
        tier = str(entry.get('tier') or '')
        if tier not in RETEST_CAPS or counts[tier] >= RETEST_CAPS[tier]:
            continue
        counts[tier] += 1
        records.append({
            'record_kind': 'proxypool',
            'protocol': entry.get('protocol') or entry['key'][0],
            'ip': entry.get('ip') or entry['key'][1],
            'port': entry.get('port') or entry['key'][2],
            'ledger_tier': tier,
            'country_code': entry.get('country_code') or '',
            'asn': entry.get('asn') or '',
            'isp': entry.get('isp') or '',
            'ip_kind': entry.get('ip_kind') or '',
            'ip_confidence': entry.get('ip_confidence') or '',
            'relaxed': entry.get('relaxed') or '',
            'sources': set(entry.get('sources') or []),
            'from_ledger': True,
        })
    return records


def is_retention_eligible(entry: dict, now: datetime) -> bool:
    """保留组准入：历史通过≥3 轮、处于 1~2 连败观察期、未过 7 天保质期"""
    if str(entry.get('tier') or '') not in RETEST_CAPS:
        return False
    if int(entry.get('verified_count') or 0) < RETENTION_MIN_VERIFIED:
        return False
    fail_streak = int(entry.get('fail_streak') or 0)
    if not 0 < fail_streak < RETENTION_MAX_FAIL_STREAK:
        return False
    return _age_days(entry, now) <= RETENTION_MAX_AGE_DAYS


def retention_records(entries: dict, tier: str, now: datetime,
                      exclude_names: set[str] | None = None) -> list[dict]:
    """取指定档位的保留组节点（输出用），按节点名排序保证可读且稳定"""
    exclude = exclude_names or set()
    picked = [
        entry for entry in entries.values()
        if entry.get('tier') == tier
        and is_retention_eligible(entry, now)
        and entry.get('node_name')
        and entry['node_name'] not in exclude
    ]
    picked.sort(key=lambda item: str(item.get('node_name')))
    records: list[dict] = []
    for entry in picked:
        records.append({
            'record_kind': 'proxypool',
            'protocol': entry.get('protocol') or entry['key'][0],
            'ip': entry.get('ip') or entry['key'][1],
            'port': entry.get('port') or entry['key'][2],
            'node_name': entry.get('node_name'),
            'country_code': entry.get('country_code') or '',
            'isp': entry.get('isp') or '',
            'last_verified_date': str(entry.get('last_verified') or '')[:10],
        })
    return records


def prune(entries: dict, now: datetime) -> dict:
    """淘汰线清理 + 总量裁剪，返回统计（直接修改传入的 entries）"""
    dropped_streak = dropped_age = 0
    for key in list(entries):
        entry = entries[key]
        if int(entry.get('fail_streak') or 0) >= RETENTION_MAX_FAIL_STREAK:
            dropped_streak += 1
            del entries[key]
        elif _age_days(entry, now) > RETENTION_MAX_AGE_DAYS:
            dropped_age += 1
            del entries[key]
    dropped_cap = 0
    if len(entries) > MAX_ENTRIES:
        ordered = sorted(entries.items(),
                         key=lambda item: str(item[1].get('last_verified') or ''),
                         reverse=True)
        for key, _entry in ordered[MAX_ENTRIES:]:
            del entries[key]
        dropped_cap = len(ordered) - MAX_ENTRIES
    return {'dropped_streak': dropped_streak, 'dropped_age': dropped_age,
            'dropped_cap': dropped_cap, 'remaining': len(entries)}


def assign_stable_names(records: list[dict], entries: dict, tag: str,
                        extra_names: set[str] | None = None) -> None:
    """稳定命名：台账在册节点沿用历史名，新节点取全局最大序号之后的新号

    序号只增不复用（淘汰节点的号随历史名一并退役）；台账里的名字与当前
    档位 tag 不一致时（节点分类变化）重新分配，因为下游按 [R]/[D] 前缀
    正则分流，tag 必须与当前组一致。
    """
    claimed: set[str] = set(extra_names or set())  # 本轮已被占用（含同文件其他组）
    used_for_seq = set(claimed)
    for entry in entries.values():
        if entry.get('node_name'):
            used_for_seq.add(str(entry['node_name']))
    next_seq = 1
    for name in used_for_seq:
        match = _NAME_PATTERN.match(name)
        if match:
            next_seq = max(next_seq, int(match.group(2)) + 1)
    prefix = f'[{tag}]'
    for record in records:
        code = str(record.get('country_code') or 'XX').strip().upper() or 'XX'
        record['country_code'] = code
        entry = entries.get(make_key(record))
        stored = str(entry.get('node_name') or '') if entry else ''
        if stored.startswith(prefix) and stored not in claimed:
            record['node_name'] = stored
        else:
            record['node_name'] = f'{prefix}{code}-{next_seq:04d}'
            next_seq += 1
        claimed.add(record['node_name'])
