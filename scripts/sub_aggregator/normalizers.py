#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""源文本 → 统一内部记录

两类源各复用一个既有解析器，不重写解析逻辑：
  proxypool → free_proxy_merger.source_loader.parse_source
  link      → v2ray_merger.merge_v2ray_subs.parse_source_text

统一记录的关键字段：
  record_kind  'proxypool' | 'link'（决定后续如何转 Clash proxy）
  protocol     Clash 协议名（http / socks5 / vmess / vless / ss / trojan / hysteria2）
  ip / port    测活与去重用的地址（link 记录由 server 复制而来）
  server       原始 server（link 记录用，可能是域名）
"""

from .reuse import merge_v2ray_subs, source_loader


def normalize_proxypool(text: str, source: dict) -> list[dict]:
    """代理池文本 → 统一记录（ip:port 型）"""
    name = str(source.get('name', 'unknown'))
    records = source_loader.parse_source(text, source)
    for record in records:
        record['record_kind'] = 'proxypool'
        record['sources'] = set(record.get('sources') or {name})
    return records


def normalize_link(text: str, source: dict) -> list[dict]:
    """订阅链接文本 → 统一记录（vmess/vless/ss/trojan 型）"""
    name = str(source.get('name', 'unknown'))
    nodes = merge_v2ray_subs.parse_source_text(text)
    records: list[dict] = []
    for node in nodes:
        server = str(node.get('server') or '').strip()
        port = node.get('port')
        try:
            port = int(port)
        except (TypeError, ValueError):
            continue
        if not server or not (0 < port < 65536) or not node.get('type'):
            continue
        record = dict(node)
        record['record_kind'] = 'link'
        record['protocol'] = str(node['type']).lower()
        # 复用 merge_v2ray_subs.dedup 的 (type, server, port) 键，故同步写入 type
        record['type'] = record['protocol']
        # ip 字段复用 server，让多轮测活与命名逻辑无需分支
        record['ip'] = server
        record['port'] = port
        record['sources'] = {name}
        records.append(record)
    return records


NORMALIZERS = {
    'proxypool': normalize_proxypool,
    'link': normalize_link,
}
