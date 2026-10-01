#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clash 产物组装

产物形态与项目已验证可订阅的形态逐字段对齐（历史教训：自创形态会导致客户端订阅失败）：
- rules 只留 MATCH，不写 GEOIP（客户端缺 GeoIP 库时整份配置校验失败）；
- 策略组用 url-test + select，组名不带 emoji；
- ip:port 型节点只保留 name/type/server/port；
- 链接型节点按 merge_v2ray_subs 的字段映射输出（保留凭据，剔除内部字段）。
"""

import time
from pathlib import Path
from typing import Any

import yaml

from .naming import country_label, group_by_country
from .reuse import merge_v2ray_subs

TEST_URL = 'https://www.gstatic.com/generate_204'


def _dump(data: Any) -> str:
    """Clash 兼容 YAML（交给 PyYAML 默认策略处理引号）"""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def to_clash_proxy(record: dict) -> dict:
    """统一记录 → Clash proxy 表述"""
    if record.get('record_kind') == 'link':
        # 交给既有字段映射处理（vmess 的 alterId、vless 的 reality-opts 等都在其中）
        node = dict(record)
        node['name'] = record['node_name']
        node['type'] = record.get('protocol') or record.get('type')
        node['server'] = record.get('server') or record['ip']
        node['port'] = int(record['port'])
        return merge_v2ray_subs._to_clash_proxy(node)
    return {
        'name': record['node_name'],
        'type': record['protocol'],
        'server': record['ip'],
        'port': int(record['port']),
    }


def build_payload(records: list[dict], group_name: str,
                  with_country_groups: bool = False) -> dict:
    """组装单份 Clash 配置（可选按国家区域增加子策略组）"""
    proxies = [to_clash_proxy(record) for record in records]
    all_names = [item['name'] for item in proxies]

    groups: list[dict] = [
        {
            'name': group_name,
            'type': 'url-test',
            'url': TEST_URL,
            'interval': 300,
            'tolerance': 200,
            'proxies': all_names,
        },
        {
            'name': f'{group_name}-手动',
            'type': 'select',
            'proxies': all_names,
        },
    ]

    if with_country_groups:
        for code, names in group_by_country(records):
            groups.append({
                'name': f'{group_name}-{country_label(code)}',
                'type': 'url-test',
                'url': TEST_URL,
                'interval': 300,
                'tolerance': 200,
                'proxies': names,
            })

    return {
        'mixed-port': 7890,
        'allow-lan': False,
        'mode': 'rule',
        'log-level': 'warning',
        'proxies': proxies,
        'proxy-groups': groups,
        # 只保留 MATCH：不写 GEOIP 规则，避免客户端缺少 GeoIP 数据库时整份配置校验失败
        'rules': [f'MATCH,{group_name}'],
    }


def write_subscription(path: Path, records: list[dict], group_name: str,
                       title: str, header_lines: list[str],
                       with_country_groups: bool = False) -> int:
    """写出订阅文件，返回节点数"""
    payload = build_payload(records, group_name, with_country_groups)
    header = (f'# {title}\n'
              f'# 生成时间: {time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}\n'
              + ''.join(f'# {line}\n' for line in header_lines))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + _dump(payload), encoding='utf-8')
    return len(records)


def write_socks4_list(path: Path, records: list[dict], rounds: int) -> int:
    """写出住宅 socks4 纯文本清单，返回节点数

    格式沿用既有线（`ip:port  # 国家 ISP`）：mihomo/Clash 不支持 socks4，
    这份清单供 Python/curl 场景直接使用，不是 Clash 配置。
    """
    lines = [
        '# 住宅 socks4 代理（mihomo/Clash 不支持此类型，供 Python/curl 场景使用）',
        f'# 节点数: {len(records)}',
        f'# 生成时间: {time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())}',
        f'# 测活: SOCKS4 CONNECT + TLS 握手，连续 {rounds} 轮通过',
    ]
    for record in records:
        comment = ' '.join(str(x) for x in (record.get('country_code'), record.get('isp')) if x)
        suffix = f'  # {comment}' if comment else ''
        lines.append(f"{record['ip']}:{record['port']}{suffix}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return len(records)


def check_socks4_list(path: Path) -> str | None:
    """socks4 清单自检：至少一条合法 `ip:port` 端点"""
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except OSError as exc:
        return f'读取失败: {exc}'
    endpoints = [line for line in lines if line and not line.startswith('#')]
    if not endpoints:
        return '清单为空'
    for line in endpoints:
        address = line.split('#', 1)[0].strip()
        host, _, port = address.rpartition(':')
        if not host or not port.isdigit():
            return f'端点格式非法: {line}'
    return None


def check_structure(path: Path) -> str | None:
    """结构自检：YAML 可解析、节点非空且字段完整、组引用完整、支持多协议"""
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        return f'YAML 解析失败: {exc}'
    if not isinstance(data, dict):
        return '配置顶层不是映射'

    proxies = data.get('proxies')
    if not isinstance(proxies, list) or not proxies:
        return 'proxies 为空'

    names: set[str] = set()
    for item in proxies:
        if not isinstance(item, dict):
            return 'proxies 含非映射项'
        name = str(item.get('name') or '')
        if not name:
            return '存在无名节点'
        if name in names:
            return f'节点名重复: {name}'
        names.add(name)
        if not item.get('type') or not item.get('server') or not item.get('port'):
            return f'节点 {name} 缺 type/server/port'

    groups = data.get('proxy-groups')
    if not isinstance(groups, list) or not groups:
        return 'proxy-groups 为空'
    group_names: set[str] = set()
    for group in groups:
        gname = str(group.get('name') or '')
        if not gname:
            return '存在无名策略组'
        if gname in group_names:
            return f'策略组重名: {gname}'
        group_names.add(gname)
        members = group.get('proxies')
        if not members:
            return f'策略组 {gname} 无成员'
        for member in members:
            if member not in names:
                return f'策略组 {gname} 引用了不存在的节点: {member}'

    rules = data.get('rules')
    if not isinstance(rules, list) or not rules:
        return 'rules 为空'
    return None
