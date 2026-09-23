#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""节点归属地批量查询（ip-api.com）

关键纪律（来自项目既有踩坑学习）：
- host:port 整串查询必失败，必须先把 host 拆出来
- 批量接口一次最多 100 个 IP
- 私网/保留地址直接跳过查询
- 任一节点查询失败仅降级为"未知"，不中断整体
"""

import ipaddress
import socket
from typing import Optional

import httpx

_BATCH_URL = 'http://ip-api.com/batch'
_PRIVATE_NETS = (
    '10.0.0.0/8', '127.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16',
    '169.254.0.0/16', '224.0.0.0/4', '240.0.0.0/4', '0.0.0.0/8',
)
_BATCH_LIMIT = 100
QUERY_TIMEOUT = 20.0

# 源标志来源节点的链路标识（无归属时显示为未知地区）
UNKNOWN_REGION = '未知'


def resolve_host(host: str) -> Optional[str]:
    """把域名解析为 IP 字符串；本身是 IP 或解析失败返回 None"""
    try:
        ipaddress.ip_address(host)
        return None  # 已是 IP，无需解析
    except ValueError:
        pass
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def is_private(ip_text: str) -> bool:
    """IP 是否属于私网/保留地址段"""
    try:
        ip_obj = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    return any(ip_obj in ipaddress.ip_network(net) for net in _PRIVATE_NETS)


def _query_batch(ips: list[str], client: httpx.Client) -> dict[str, dict]:
    """查询一批 IP 的归属，返回 {ip: {country, countryCode, city, query}}"""
    result: dict[str, dict] = {}
    response = client.post(
        _BATCH_URL,
        json=[{'query': ip, 'fields': 'status,country,countryCode,city,query'}
              for ip in ips],
        timeout=QUERY_TIMEOUT,
    )
    response.raise_for_status()
    for row in response.json():
        if not isinstance(row, dict):
            continue
        if row.get('status') != 'success':
            continue
        ip_key = str(row.get('query', ''))
        result[ip_key] = {
            'country': str(row.get('country', '')),
            'countryCode': str(row.get('countryCode', '')),
            'city': str(row.get('city', '')),
            'query': ip_key,
        }
    return result


def lookup_regions(hosts: list[str], client: httpx.Client) -> dict[str, dict]:
    """批量查询归属；内部按《先拆 host → 私网跳过 → ≤100 分批》执行

    返回 {host: {country, countryCode, city}}，查不到的 host 不出现于结果。
    """
    # 1. 拆 host：纯 IP 直接用；域名解析成功才纳入；私网跳过，避免无意义外呼
    queryable_ips: list[tuple[str, str]] = []
    for host in hosts:
        if is_private(host):
            continue  # 私网直接忽略，避免无意义外呼
        try:
            ipaddress.ip_address(host)
            queryable_ips.append((host, host))
            continue
        except ValueError:
            pass
        ip_text = resolve_host(host)
        if ip_text:
            queryable_ips.append((host, ip_text))

    # 2. 按 ≤100 分批，逐批并发查询
    region_map: dict[str, dict] = {}
    for index in range(0, len(queryable_ips), _BATCH_LIMIT):
        chunk = queryable_ips[index:index + _BATCH_LIMIT]
        ips = [ip for _, ip in chunk]
        try:
            batch_result = _query_batch(ips, client)
        except httpx.HTTPError:
            batch_result = {}
        for host, ip_text in chunk:
            info = batch_result.get(ip_text)
            if info:
                region_map[host] = info
    return region_map