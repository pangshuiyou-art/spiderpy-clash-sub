#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""免费代理源加载与解析

负责：读取源配置 → 拉取 → 按格式解析 → 归一化为统一记录结构。

统一记录结构（内部模型）：
    {
        'ip': str, 'port': int, 'protocol': str,      # 必填
        'country_code': str, 'country': str, 'city': str,
        'asn': str, 'isp': str,
        'latency_ms': int, 'uptime': float, 'anonymity': str,
        'streak': int, 'score': float, 'ip_type': str,
        'sources': set[str],   # 该记录来自哪些源（用于元数据溯源）
    }
"""

import csv
import io
import json
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

FETCH_TIMEOUT = 60.0
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

# 源里可能出现的协议别名，统一到 Clash 认识的名字
PROTOCOL_ALIASES = {
    'http': 'http',
    'https': 'http',      # Clash 没有 https 代理类型，映射为 http（源把"能代理 https 的 http 代理"标为 https）
    'socks5': 'socks5',
    'socks4': 'socks4',
}


def load_source_config(path: Path) -> tuple[list[dict], list[dict]]:
    """读取源配置，返回（代理源列表, 辅助数据列表）"""
    if not path.exists():
        raise FileNotFoundError(f'源配置不存在: {path}')
    with open(path, encoding='utf-8') as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f'源配置格式有误: {path}')
    sources = [item for item in (data.get('sources') or [])
               if isinstance(item, dict) and item.get('url') and item.get('enabled', True)]
    auxiliary = [item for item in (data.get('auxiliary') or [])
                 if isinstance(item, dict) and item.get('url') and item.get('enabled', True)]
    return sources, auxiliary


def fetch_text(url: str, client: httpx.Client) -> str:
    """拉取源文本"""
    response = client.get(url, headers=REQUEST_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    return response.text


def _dig(data: Any, path: str) -> Any:
    """按点号路径取值，任一层缺失返回 None"""
    current = data
    for key in path.split('.'):
        if isinstance(current, dict):
            current = current.get(key)
        elif isinstance(current, list) and key.isdigit():
            index = int(key)
            current = current[index] if 0 <= index < len(current) else None
        else:
            return None
        if current is None:
            return None
    return current


def _to_int(value: Any) -> Optional[int]:
    """安全转整数"""
    if value is None or value == '':
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _to_float(value: Any) -> Optional[float]:
    """安全转浮点"""
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_protocol(raw: Any) -> Optional[str]:
    """协议名归一化，未知协议返回 None（调用方剔除）"""
    if raw is None:
        return None
    return PROTOCOL_ALIASES.get(str(raw).strip().lower())


def _clean_ip(raw: Any) -> Optional[str]:
    """清洗 IP 文本，非法或为空返回 None"""
    if raw is None:
        return None
    text = str(raw).strip().strip('"').strip("'")
    if not text or len(text) > 45:
        return None
    return text


def _build_record(fields: dict, mapping: dict, source_name: str) -> Optional[dict]:
    """按字段映射把一条原始数据转为统一记录；关键字段缺失返回 None"""
    ip = _clean_ip(_dig(fields, mapping['ip']))
    port = _to_int(_dig(fields, mapping['port']))
    if mapping.get('protocol'):
        protocol = _normalize_protocol(_dig(fields, mapping['protocol']))
    else:
        protocol = _normalize_protocol(mapping.get('fixed_protocol'))
    if ip is None or port is None or not (0 < port < 65536) or protocol is None:
        return None

    record: dict = {
        'ip': ip,
        'port': port,
        'protocol': protocol,
        'sources': {source_name},
    }

    def take(field_key: str, target: str, converter=None) -> None:
        """按映射取值并写入目标字段（该字段未在映射中则跳过）"""
        path = mapping.get(field_key)
        if not path:
            return
        value = _dig(fields, path)
        if value is None or value == '':
            return
        if converter is not None:
            value = converter(value)
            if value is None:
                return
        record[target] = value

    take('country_code', 'country_code', lambda v: str(v).strip().upper())
    take('country', 'country', lambda v: str(v).strip())
    take('city', 'city', lambda v: str(v).strip())
    take('anonymity', 'anonymity', lambda v: str(v).strip().lower())
    take('ip_type', 'ip_type', lambda v: str(v).strip().lower())
    take('uptime', 'uptime', _to_float)
    take('latency_ms', 'latency_ms', _to_int)
    take('latency_s', 'latency_ms', lambda v: int(_to_float(v) * 1000) if _to_float(v) is not None else None)
    take('streak', 'streak', _to_int)
    take('score', 'score', _to_float)
    take('isp', 'isp', lambda v: str(v).strip())
    take('asn', 'asn', lambda v: f"AS{str(v).strip()}" if str(v).strip().isdigit() else str(v).strip())
    return record


def parse_csv_source(text: str, mapping: dict, source_name: str) -> list[dict]:
    """解析 CSV 源"""
    rows = csv.DictReader(io.StringIO(text))
    records: list[dict] = []
    for row in rows:
        record = _build_record(row, mapping, source_name)
        if record is not None:
            records.append(record)
    return records


def _iter_json_items(data: Any) -> list[Any]:
    """从 JSON 里找出记录数组：支持顶层数组、以及 common wrapper keys"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ('proxies', 'data', 'items', 'results', 'list'):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def parse_json_source(text: str, mapping: dict, source_name: str) -> list[dict]:
    """解析 JSON 源"""
    data = json.loads(text)
    records: list[dict] = []
    for item in _iter_json_items(data):
        if not isinstance(item, dict):
            continue
        record = _build_record(item, mapping, source_name)
        if record is not None:
            records.append(record)
    return records


def parse_plain_source(text: str, mapping: dict, source_name: str) -> list[dict]:
    """解析纯文本源：每行 ip:port 或 scheme://ip:port

    行内可能带 `,base64,` 等尾巴，取第一个合法 ip:port 片段。
    """
    fixed_protocol = mapping.get('fixed_protocol')
    records: list[dict] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        protocol = fixed_protocol
        if '://' in line:
            scheme, _, remainder = line.partition('://')
            protocol = _normalize_protocol(scheme)
            line = remainder
        # 兼容 "ip:port,country,city" 之类的尾巴
        line = line.split(',')[0].strip()
        if ':' not in line:
            continue
        host, _, port_text = line.rpartition(':')
        port = _to_int(port_text)
        ip = _clean_ip(host)
        normalized = _normalize_protocol(protocol)
        if ip is None or port is None or not (0 < port < 65536) or normalized is None:
            continue
        records.append({
            'ip': ip,
            'port': port,
            'protocol': normalized,
            'sources': {source_name},
        })
    return records


def parse_source(text: str, source: dict) -> list[dict]:
    """按源配置的 format 分派解析器"""
    fmt = str(source.get('format', 'plain')).strip().lower()
    name = str(source.get('name', 'unknown'))
    mapping = dict(source.get('fields') or {})
    mapping['fixed_protocol'] = source.get('protocol')

    if fmt == 'csv':
        return parse_csv_source(text, mapping, name)
    if fmt == 'json':
        return parse_json_source(text, mapping, name)
    return parse_plain_source(text, mapping, name)


def parse_cidr_blacklist(text: str) -> list:
    """解析 CIDR 黑名单文本，返回 ip_network 列表（非法行跳过）"""
    import ipaddress

    networks = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        try:
            networks.append(ipaddress.ip_network(line, strict=False))
        except ValueError:
            continue
    return networks
