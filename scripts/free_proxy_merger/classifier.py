#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""节点分类：住宅识别与用途分组

住宅识别采用「三库组合」，每一层的职责不同（均可配置开关）：

  第一层  CIDR 段库排除（X4BNet，4.4 万条数据中心段）
           命中即判定为机房，排除。零成本、零查询。
           局限：实测会漏报（例如 Secure Internet LLC 的 45.74.31.0/24 未被收录），
                 所以"未命中"不能直接当住宅。

  第二层  ip-api hosting 字段
           hosting=True  → 机房，排除
           hosting=False → 疑似住宅，进入第三层
           实测召回率 99%（以已确认住宅 ASN 为基准）。

  第三层  ASN 白名单认证
           命中白名单 → 高置信度住宅，标记 confidence=high
           未命中但 hosting=False → 低置信度住宅，标记 confidence=medium
           低置信度条目的 ASN 会写入待审清单，供人工确认后扩容白名单。

ASN 白名单自身也会自学习：每轮把高置信度住宅的 ASN 并入白名单文件。
"""

from pathlib import Path
from typing import Optional

import yaml

# 权威性较高的判定来源：Thordata 的 ip_type 字段由源方维护，直接采信
TRUSTED_SOURCE_IP_TYPE = {'residential', 'datacenter'}


def load_asn_whitelist(path: Path) -> dict:
    """读取住宅 ASN 白名单（不存在时返回空骨架）"""
    if not path.exists():
        return {'version': 1, 'updated': '', 'asns': {}, 'pending_review': {}}
    with open(path, encoding='utf-8') as handle:
        data = yaml.safe_load(handle) or {}
    data.setdefault('asns', {})
    data.setdefault('pending_review', {})
    return data


def save_asn_whitelist(path: Path, data: dict) -> None:
    """写回白名单（保持键序稳定，便于 git diff 阅读）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=True, default_flow_style=False)


def _normalize_asn(raw: object) -> str:
    """把源的 ASN 写法统一成 'AS22773' 形式；无法识别返回空串"""
    text = str(raw or '').strip()
    if not text:
        return ''
    # 优先取 "AS<digits>" 形式（ip-api 返回 "AS22773 Cox Communications Inc."）
    import re

    match = re.search(r'\bAS(\d{1,10})\b', text, re.IGNORECASE)
    if match is None:
        # 退回纯数字形式（部分源只给数字）
        stripped = text.strip()
        match_digits = re.fullmatch(r'(\d{1,10})', stripped)
        if match_digits is None:
            return ''
        return f'AS{match_digits.group(1)}'
    return f'AS{match.group(1)}'


def classify_records(
    records: list[dict],
    cidr_match: Optional[callable],
    ip_metadata: dict[str, dict],
    whitelist: dict,
    whitelist_enabled: bool = True,
) -> tuple[list[dict], dict]:
    """对记录打住宅/机房标签

    返回（打标后的记录, 统计信息）
    """
    whitelist_asns = set((whitelist.get('asns') or {}).keys())
    stats = {
        'total': len(records),
        'by_source_label': 0,     # 源自身标记（Thordata）
        'by_cidr_blocked': 0,     # 段库判定为机房
        'by_hosting': 0,          # ip-api 判定为机房
        'residential_high': 0,    # 白名单认证
        'residential_medium': 0,  # hosting=False 但不在白名单
        'unknown': 0,
    }

    for record in records:
        ip = record['ip']
        asn = _normalize_asn(record.get('asn'))
        if asn:
            record['asn'] = asn

        # 第一层：源自身带可信标记（Thordata 的 ip_type）
        source_type = str(record.get('ip_type') or '').strip().lower()
        if source_type in TRUSTED_SOURCE_IP_TYPE:
            record['ip_kind'] = 'residential' if source_type == 'residential' else 'datacenter'
            record['ip_confidence'] = 'high'
            stats['by_source_label'] += 1
            continue

        # 第二层：CIDR 段库排除
        if cidr_match is not None and cidr_match(ip):
            record['ip_kind'] = 'datacenter'
            record['ip_confidence'] = 'high'
            stats['by_cidr_blocked'] += 1
            continue

        # 第三层：ip-api hosting 字段
        info = ip_metadata.get(ip) or {}
        if info.get('asn') and not asn:
            record['asn'] = _normalize_asn(info['asn'])
        if info.get('isp') and not record.get('isp'):
            record['isp'] = info['isp']
        if info.get('country_code') and not record.get('country_code'):
            record['country_code'] = info['country_code']

        hosting = info.get('hosting')
        if hosting is True:
            record['ip_kind'] = 'datacenter'
            record['ip_confidence'] = 'high'
            stats['by_hosting'] += 1
            continue
        if hosting is False:
            # 第四层：ASN 白名单认证
            if whitelist_enabled and str(record.get('asn') or '') in whitelist_asns:
                record['ip_kind'] = 'residential'
                record['ip_confidence'] = 'high'
                stats['residential_high'] += 1
            else:
                record['ip_kind'] = 'residential'
                record['ip_confidence'] = 'medium'
                stats['residential_medium'] += 1
            continue

        # 无任何判定依据：只有 ASN 白名单能给出结论
        if whitelist_enabled and str(record.get('asn') or '') in whitelist_asns:
            record['ip_kind'] = 'residential'
            record['ip_confidence'] = 'high'
            stats['residential_high'] += 1
        else:
            record['ip_kind'] = 'unknown'
            record['ip_confidence'] = 'low'
            stats['unknown'] += 1

    return records, stats


def learn_whitelist(records: list[dict], whitelist: dict, stamp: str) -> dict:
    """自学习：把高置信度住宅的 ASN 并入白名单，中置信度的写入待审清单"""
    asns = whitelist.setdefault('asns', {})
    pending = whitelist.setdefault('pending_review', {})

    added: list[str] = []
    for record in records:
        if record.get('ip_kind') != 'residential':
            continue
        asn = str(record.get('asn') or '').strip()
        if not asn:
            continue
        org = str(record.get('isp') or '').strip()
        if record.get('ip_confidence') == 'high':
            if asn not in asns:
                asns[asn] = {'org': org, 'added': stamp, 'source': 'auto'}
                added.append(asn)
        elif record.get('ip_confidence') == 'medium' and asn not in asns:
            entry = pending.setdefault(asn, {'org': org, 'hits': 0, 'first_seen': stamp})
            entry['hits'] = int(entry.get('hits') or 0) + 1
            entry['last_seen'] = stamp

    whitelist['updated'] = stamp
    return {'added_asns': added, 'whitelist_size': len(asns), 'pending_size': len(pending)}


def split_by_kind(records: list[dict]) -> dict[str, list[dict]]:
    """按 ip_kind 拆分，并剔除 Clash 不支持的 socks4（单独成组，供非 Clash 场景）"""
    groups: dict[str, list[dict]] = {
        'residential': [],
        'daily': [],
        'residential_socks4': [],
    }
    for record in records:
        if record.get('ip_kind') == 'residential':
            if record['protocol'] == 'socks4':
                groups['residential_socks4'].append(record)
            else:
                groups['residential'].append(record)
        else:
            if record['protocol'] == 'socks4':
                continue  # 机房 socks4 没有保留价值
            groups['daily'].append(record)
    return groups
