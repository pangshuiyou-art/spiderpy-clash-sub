#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""住宅判定放宽（用户要求：住宅组可以放宽一些）

既有三库组合（段库排除 → ip-api hosting → ASN 白名单）偏保守，实测漏点集中在两处：
  1. 移动运营商 IP 常被 ip-api 标成 hosting=True，被当作机房丢掉；
  2. ip-api 未覆盖 / 查询失败的条目直接落进 unknown，被整批丢弃。

放宽策略对这两处收口，且都要求「有 ASN 证据」：
  mobile_as_residential            移动网络 → 住宅（中置信）
  unknown_with_asn_as_residential  unknown 且有 ASN → 住宅（低置信）
  exclude_datacenter_asns          本轮被 ip-api 判为机房的 ASN 不参与上述放宽

放宽只提升召回，不覆盖源方明确标注（Thordata ip_type）与最终 mihomo 多轮测活两道闸门。
"""

from .reuse import classifier, processor


def collect_datacenter_asns(ip_metadata: dict[str, dict]) -> set[str]:
    """本轮 ip-api 判为机房的 ASN 集合（用于阻止放宽把机房 ASN 收进住宅）

    必须剔除 mobile=True 的条目：ip-api 对移动运营商地址同时给出 hosting=True 与
    mobile=True，若不剔除，移动 ASN 会自己把自己挡在放宽之外，mobile_as_residential 形同虚设。
    """
    asns: set[str] = set()
    for info in (ip_metadata or {}).values():
        if info.get('mobile'):
            continue
        if info.get('hosting') and info.get('asn'):
            asn = classifier._normalize_asn(info['asn'])
            if asn:
                asns.add(asn)
    return asns


def relax_residential(records: list[dict], ip_metadata: dict[str, dict],
                      policy: dict) -> dict:
    """对已分类记录做住宅放宽，原地更新 ip_kind/ip_confidence，返回统计"""
    mobile_enabled = bool(policy.get('mobile_as_residential', True))
    unknown_enabled = bool(policy.get('unknown_with_asn_as_residential', True))
    guard_enabled = bool(policy.get('exclude_datacenter_asns', True))
    datacenter_asns = collect_datacenter_asns(ip_metadata) if guard_enabled else set()

    stats = {'by_mobile': 0, 'by_unknown_asn': 0, 'blocked_datacenter_asn': 0}
    for record in records:
        if record.get('ip_kind') == 'residential':
            continue
        # 源方明确标注过的条目（Thordata）视为可信，不参与放宽
        source_type = str(record.get('ip_type') or '').strip().lower()
        if source_type in classifier.TRUSTED_SOURCE_IP_TYPE:
            continue

        info = ip_metadata.get(record['ip']) or {}
        asn = classifier._normalize_asn(record.get('asn') or info.get('asn'))
        if asn and asn in datacenter_asns:
            stats['blocked_datacenter_asn'] += 1
            continue

        if mobile_enabled and info.get('mobile'):
            record['ip_kind'] = 'residential'
            record['ip_confidence'] = 'medium'
            record['relaxed'] = 'mobile'
            stats['by_mobile'] += 1
            continue

        if unknown_enabled and record.get('ip_kind') == 'unknown' and asn:
            record['ip_kind'] = 'residential'
            record['ip_confidence'] = 'low'
            record['relaxed'] = 'unknown_asn'
            stats['by_unknown_asn'] += 1

    return stats


def split_records(records: list[dict]) -> dict[str, list[dict]]:
    """按用途拆分：住宅组 / 日常组 / 住宅 socks4

    用户决策（选项 B）：日常组不收录已被判为住宅的条目，避免两份产物重复。
    socks4 不剔除：mihomo/Clash 虽不支持（实测 unsupport proxy type），但住宅 IP 里
    socks4 占比很高，Python/curl 场景仍要用，故单独成组走独立协议层测活。
    机房 socks4 没有保留价值，直接丢掉。
    """
    groups: dict[str, list[dict]] = {
        'residential': [],
        'daily': [],
        'residential_socks4': [],
    }
    for record in records:
        is_residential = record.get('ip_kind') == 'residential'
        if record.get('protocol') == 'socks4':
            if is_residential:
                groups['residential_socks4'].append(record)
            continue
        if is_residential:
            groups['residential'].append(record)
        else:
            groups['daily'].append(record)
    return groups


def build_cidr_matcher(cidr_networks: list) -> object | None:
    """CIDR 匹配函数（无辅助数据时返回 None）"""
    if not cidr_networks:
        return None
    return processor.build_cidr_matcher(cidr_networks)
