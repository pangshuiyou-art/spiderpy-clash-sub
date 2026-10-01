#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""代理记录处理：去重、元数据合并、TCP 预筛、住宅识别

关键设计（来自本项目的实测结论）：
- 住宅判定用「三库组合」：X4BNet 段库排除 → ip-api hosting 字段 → ASN 白名单认证
  单用任何一库都有明显缺陷：X4BNet 会漏报（实测漏掉 Secure Internet LLC），
  ip-api hosting 召回过宽（把数据中心外的小 ISP 也判成住宅），
  ASN 白名单召回太窄（只认已知种子）。三者组合后精度与召回都可接受。
- TCP 预筛并发 200、超时 1.5s 是实测最优（并发 300+ 会因本机端口耗尽导致漏检）
"""

import ipaddress
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed
# TCP 预筛参数（实测最优值）
TCP_CONNECT_TIMEOUT_SEC = 1.5
TCP_CONNECT_WORKERS = 200

# 单 IP 端口数上限：超过此值的 IP 视为"端口扫描噪音"
# 实测依据：hookzof 里 9 个荷兰数据中心 IP 各带 1500-6000 个端口，
# 合计占该源 24000+ 行（74%），若不剔除会白耗大量 TCP 预筛与测活时间。
MAX_PORTS_PER_IP = 20

# ip-api 批量查询限制
IPAPI_BATCH_URL = 'http://ip-api.com/batch'
IPAPI_BATCH_SIZE = 100


def drop_port_scanners(records: list[dict], max_ports: int = MAX_PORTS_PER_IP) -> tuple[list[dict], dict]:
    """剔除"端口扫描噪音"：同一 IP 出现过多端口时，只保留最可信的若干条

    正常代理不会在一个 IP 上开放上千个端口；这类记录来自批量端口扫描，
    端口几乎全部不可用。保留少量条目以维持对该 IP 的探测机会。

    返回（清洗后记录, 统计信息）
    """
    from collections import defaultdict

    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[record['ip']].append(record)

    kept: list[dict] = []
    dropped_total = 0
    noisy_ips: list[tuple[str, int]] = []
    for ip, items in grouped.items():
        if len(items) <= max_ports:
            kept.extend(items)
            continue
        noisy_ips.append((ip, len(items)))
        # 优先保留有元数据、协议更可能可用的条目
        ordered = sorted(items, key=lambda r: (
            -_metadata_score(r),
            r['protocol'] != 'socks5',
            r['port'],
        ))
        kept.extend(ordered[:max_ports])
        dropped_total += len(items) - max_ports

    stats = {
        'input': len(records),
        'output': len(kept),
        'dropped': dropped_total,
        'noisy_ip_count': len(noisy_ips),
        'noisy_ips': sorted(noisy_ips, key=lambda kv: -kv[1])[:5],
    }
    return kept, stats


def dedup_records(records: list[dict]) -> tuple[list[dict], dict]:
    """按 (ip, port) 去重，同一条目合并各源元数据

    去重键只取 ip:port（不含协议）：同一地址的 http 与 socks5 是不同入口，
    但多数源对同一地址只给一个协议；用 ip:port 聚合可让元数据充分共享。
    保留元数据最全的那条（字段多的胜出），来源集合合并。

    返回（去重后记录, 统计信息）
    """
    merged: dict[tuple[str, int], dict] = {}
    for record in records:
        key = (record['ip'], record['port'])
        existing = merged.get(key)
        if existing is None:
            merged[key] = record
            continue
        # 来源集合合并
        existing['sources'] = set(existing.get('sources') or set()) | set(record.get('sources') or set())
        # 逐字段补齐（已有值不被覆盖，缺失值从新记录继承）
        for field, value in record.items():
            if field == 'sources':
                continue
            if existing.get(field) in (None, '', 0) and value not in (None, ''):
                existing[field] = value
        # 冲突时保留质量更高的元数据（有 uptime 优先、有 asn 优先）
        for field in ('uptime', 'latency_ms', 'asn', 'isp', 'anonymity'):
            if existing.get(field) in (None, '') and record.get(field) not in (None, ''):
                existing[field] = record[field]

    stats = {
        'input': len(records),
        'output': len(merged),
        'removed': len(records) - len(merged),
    }
    return list(merged.values()), stats


def propagate_metadata(records: list[dict]) -> list[dict]:
    """元数据传播：把有元数据源的字段，补给同 IP 的无元数据条目

    实测依据：ProxyScrape 的 socks5 有 90.6% 被 hookzof 覆盖，
    两源采样高度重叠。按 IP 传播可省掉大量重复的 ASN 查询。
    """
    # 建立 IP → 最佳元数据索引（只取有 asn 或 isp 的记录作为提供方）
    providers: dict[str, dict] = {}
    for record in records:
        ip = record['ip']
        if not (record.get('asn') or record.get('isp')):
            continue
        current = providers.get(ip)
        if current is None or _metadata_score(record) > _metadata_score(current):
            providers[ip] = record

    filled = 0
    for record in records:
        provider = providers.get(record['ip'])
        if provider is None or provider is record:
            continue
        for field in ('asn', 'isp', 'country_code', 'country', 'city', 'ip_type'):
            if record.get(field) in (None, '') and provider.get(field) not in (None, ''):
                record[field] = provider[field]
                filled += 1
        if record.get('uptime') in (None, 0) and provider.get('uptime') not in (None, 0):
            record['uptime'] = provider['uptime']
            filled += 1
    return records


def _metadata_score(record: dict) -> int:
    """元数据完整度打分，用于在多条同 IP 记录中挑提供方"""
    score = 0
    for field, weight in (('asn', 3), ('isp', 3), ('uptime', 2), ('latency_ms', 2),
                          ('country_code', 1), ('anonymity', 1), ('ip_type', 2)):
        if record.get(field) not in (None, '', 0):
            score += weight
    return score


def _is_public_ip(ip_text: str) -> bool:
    """是否公网地址（私网/保留段直接剔除，避免无意义外呼）"""
    private_nets = (
        '0.0.0.0/8', '10.0.0.0/8', '100.64.0.0/10', '127.0.0.0/8',
        '169.254.0.0/16', '172.16.0.0/12', '192.0.0.0/24', '192.0.2.0/24',
        '192.168.0.0/16', '198.18.0.0/15', '198.51.100.0/24', '203.0.113.0/24',
        '224.0.0.0/4', '240.0.0.0/4',
    )
    try:
        ip_obj = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    return not any(ip_obj in ipaddress.ip_network(net) for net in private_nets)


def tcp_prefilter(records: list[dict]) -> tuple[list[dict], dict]:
    """TCP 连通性预筛：端口不通的地址直接淘汰

    这是整套流水线最重要的省时间手段——实测 36669 条原始地址里只有约 6% 端口可达，
    先做一次廉价 TCP 探测，可以让昂贵的 mihomo 测活量下降 94%。

    返回（存活记录, 统计信息）
    """
    candidates = [r for r in records if _is_public_ip(r['ip'])]
    skipped_private = len(records) - len(candidates)

    def probe(record: dict) -> bool:
        try:
            with socket.create_connection((record['ip'], record['port']), timeout=TCP_CONNECT_TIMEOUT_SEC):
                return True
        except OSError:
            return False

    alive: list[dict] = []
    with ThreadPoolExecutor(max_workers=TCP_CONNECT_WORKERS) as executor:
        futures = {executor.submit(probe, record): record for record in candidates}
        for future in as_completed(futures):
            record = futures[future]
            try:
                if future.result():
                    alive.append(record)
            except Exception:
                continue

    stats = {
        'input': len(records),
        'checked': len(candidates),
        'alive': len(alive),
        'dead': len(candidates) - len(alive),
        'skipped_private': skipped_private,
    }
    return alive, stats


def build_cidr_matcher(networks: list) -> callable:
    """构建 CIDR 匹配函数

    实现要点：黑名单有 4 万+ 条网段，逐条 `in` 比较会拖慢整条流水线。
    这里按前缀长度分桶，并把每个网段的网络地址存成整数集合；
    查找时只需对每个出现过的前缀长度算一次网络地址再查集合，
    复杂度降到「不同前缀长度个数」量级。
    """
    by_prefix: dict[int, set[int]] = {}
    for network in networks:
        version = network.version
        key = (version, network.prefixlen)
        by_prefix.setdefault(key, set()).add(int(network.network_address))

    def match(ip_text: str) -> bool:
        try:
            ip_obj = ipaddress.ip_address(ip_text)
        except ValueError:
            return False
        version = ip_obj.version
        max_prefix = ip_obj.max_prefixlen
        for (net_version, prefixlen), network_addrs in by_prefix.items():
            if net_version != version:
                continue
            # 把待查 IP 按该前缀长度对齐后查集合
            masked = int(ip_obj) & (~((1 << (max_prefix - prefixlen)) - 1) & ((1 << max_prefix) - 1))
            if masked in network_addrs:
                return True
        return False

    return match


def query_ip_metadata(ips: list[str], client) -> dict[str, dict]:
    """批量查询 IP 元数据（ip-api），返回 {ip: {asn, isp, hosting, country_code, mobile}}

    限速：免费版 15 次/分钟，故每批之间 sleep。批量 100 IP/次。
    """
    import time

    result: dict[str, dict] = {}
    unique_ips = sorted({ip for ip in ips})
    for index in range(0, len(unique_ips), IPAPI_BATCH_SIZE):
        chunk = unique_ips[index:index + IPAPI_BATCH_SIZE]
        payload = [{'query': ip, 'fields': 'status,query,as,asname,isp,hosting,mobile,countryCode'}
                   for ip in chunk]
        try:
            response = client.post(IPAPI_BATCH_URL, json=payload, timeout=30)
            response.raise_for_status()
            for row in response.json():
                if not isinstance(row, dict) or row.get('status') != 'success':
                    continue
                ip_key = str(row.get('query', ''))
                result[ip_key] = {
                    'asn': str(row.get('as') or ''),
                    'isp': str(row.get('isp') or ''),
                    'hosting': bool(row.get('hosting')),
                    'mobile': bool(row.get('mobile')),
                    'country_code': str(row.get('countryCode') or ''),
                }
        except Exception as exc:  # 单批失败不影响整体，交由上游按"未知"处理
            print(f'[!] ip-api 批查询失败（{chunk[0]}...）: {exc}')
        # 末批之后不必等待
        if index + IPAPI_BATCH_SIZE < len(unique_ips):
            time.sleep(4.5)
    return result
