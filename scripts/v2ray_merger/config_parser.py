#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clash YAML 与 v2rayN JSON 配置解析

把 Clash 配置（proxies 段）与 v2rayN/Netch 风格 JSON 节点数组
统一为内部节点模型（与 link_parser 输出同构），供下游共用。
"""

import json
from typing import Optional

import yaml


def _maybe_int(value: object) -> int:
    """安全转 int，失败返回 0"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def from_clash_yaml(config_text: str) -> list[dict]:
    """解析 Clash YAML 的 proxies 段为节点模型列表"""
    try:
        data = yaml.safe_load(config_text)
    except yaml.YAMLError as exc:
        raise ValueError(f'Clash YAML 解析失败: {exc}') from exc
    if not isinstance(data, dict):
        raise ValueError('Clash 配置根节点不是字典')
    proxies = data.get('proxies', [])
    if not isinstance(proxies, list):
        return []
    nodes: list[dict] = []
    for item in proxies:
        if not isinstance(item, dict):
            continue
        node = {
            'name': str(item.get('name', '')),
            'type': str(item.get('type', '')).lower(),
            'server': str(item.get('server', '')),
            'port': _maybe_int(item.get('port')),
        }
        for key in ('cipher', 'password', 'uuid', 'alter_id', 'network', 'tls',
                    'servername', 'sni', 'flow', 'skip-cert-verify'):
            if key in item:
                node[key] = item[key]
        # 保留 ws/grpc/h2/reality 传输层
        for key in ('ws-opts', 'grpc-opts', 'h2-opts', 'reality-opts', 'plugin'):
            if key in item:
                node[key] = item[key]
        if node['server'] and node['port']:
            nodes.append(node)
    return nodes


def _map_json_vecore_node(item: dict) -> Optional[dict]:
    """单个 v2rayN/Netch JSON 节点 → 内部模型"""
    vecore_type = str(item.get('type', 'vmess')).lower()
    node_type = 'vmess' if vecore_type == 'vmess' else vecore_type
    server = str(item.get('add', '') or item.get('server', ''))
    port = _maybe_int(item.get('port'))
    if not server or not port:
        return None
    node = {
        'name': str(item.get('ps', '') or item.get('remark', server)),
        'type': node_type,
        'server': server,
        'port': port,
    }
    map_keys = {
        'uuid': 'uuid',
        'id': 'uuid',
        'aid': 'alter_id',
        'scy': 'cipher',
        'cipher': 'cipher',
        'method': 'cipher',
        'password': 'password',
        'network': 'network',
        'net': 'network',
        'tls': 'tls',
        'path': 'path',
        'host': 'host',
        'sni': 'sni',
    }
    for src, dst in map_keys.items():
        if src in item and item[src]:
            node[dst] = item[src]
    if node.get('network') == 'ws':
        node['ws-opts'] = {
            'path': str(item.get('path', '/')),
            'headers': {'Host': str(item.get('host', ''))},
        }
    if node.get('network') == 'grpc':
        node['grpc-opts'] = {'grpc-service-name': str(item.get('path', ''))}
    if item.get('tls'):
        node['tls'] = True
        node['servername'] = str(item.get('sni') or item.get('host') or server)
    if vecore_type in ('ss', 'trojan'):
        node['type'] = vecore_type
    return node


def from_v2rayn_json(config_text: str) -> list[dict]:
    """解析 v2rayN/Netch 风格 JSON 节点数组"""
    try:
        data = json.loads(config_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f'v2rayN JSON 解析失败: {exc}') from exc
    if not isinstance(data, list):
        raise ValueError('v2rayN JSON 根节点不是数组')
    nodes: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        node = _map_json_vecore_node(item)
        if node is not None:
            nodes.append(node)
    return nodes