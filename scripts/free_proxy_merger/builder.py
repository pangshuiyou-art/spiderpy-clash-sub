#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clash 订阅产物生成与严格自校验

产物形态与项目已验证可订阅的产物逐字段对齐（历史教训：自创形态导致客户端订阅失败）：
- rules 只留 MATCH，不写 GEOIP（客户端缺 GeoIP 库时整份配置校验失败）
- 节点字段只保留 name/type/server/port
- 策略组用 url-test + select，组名不带 emoji
- 自定义 dumper 只作用于节点行，顶层键保持不加引号
"""

from pathlib import Path
from typing import Any, Optional

import yaml

TEST_URL = 'https://www.gstatic.com/generate_204'


def _dump(data: Any) -> str:
    """输出 Clash 兼容 YAML（交给 PyYAML 默认策略处理引号）"""
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def build_subscription(records: list[dict], group_name: str,
                       header_lines: list[str]) -> dict:
    """组装单份 Clash 配置"""
    proxies = [
        {
            'name': record['node_name'],
            'type': record['protocol'],
            'server': record['ip'],
            'port': int(record['port']),
        }
        for record in records
    ]
    names = [item['name'] for item in proxies]
    return {
        'mixed-port': 7890,
        'allow-lan': False,
        'mode': 'rule',
        'log-level': 'warning',
        'proxies': proxies,
        'proxy-groups': [
            {
                'name': group_name,
                'type': 'url-test',
                'url': TEST_URL,
                'interval': 300,
                'tolerance': 200,
                'proxies': names,
            },
            {
                'name': f'{group_name}-手动',
                'type': 'select',
                'proxies': names,
            },
        ],
        'rules': [f'MATCH,{group_name}'],
    }


def write_subscription(path: Path, records: list[dict], group_name: str,
                       title: str, header_lines: list[str]) -> int:
    """写出订阅文件，返回节点数"""
    payload = build_subscription(records, group_name, header_lines)
    header = f'# {title}\n' + ''.join(f'# {line}\n' for line in header_lines)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + _dump(payload), encoding='utf-8')
    return len(records)


def check_unique_names(records: list[dict]) -> Optional[str]:
    """节点名唯一性校验（Clash 对重名直接报错）"""
    names = [str(record.get('node_name') or '') for record in records]
    if len(names) != len(set(names)):
        seen: set[str] = set()
        for name in names:
            if name in seen:
                return f'节点名重复: {name}'
            seen.add(name)
    return None


def check_subscription_structure(path: Path) -> Optional[str]:
    """结构自检：节点非空、类型合法、组引用完整"""
    try:
        data = yaml.safe_load(path.read_text(encoding='utf-8'))
    except yaml.YAMLError as exc:
        return f'YAML 解析失败: {exc}'
    if not isinstance(data, dict):
        return '配置顶层不是映射'

    proxies = data.get('proxies')
    if not isinstance(proxies, list) or not proxies:
        return 'proxies 为空'

    allowed_types = {'http', 'socks5'}
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
        if item.get('type') not in allowed_types:
            return f'节点 {name} 类型非法: {item.get("type")}'
        if not item.get('server') or not item.get('port'):
            return f'节点 {name} 缺 server/port'

    groups = data.get('proxy-groups')
    if not isinstance(groups, list) or not groups:
        return 'proxy-groups 为空'
    for group in groups:
        for member in group.get('proxies') or []:
            if member not in names:
                return f'策略组 {group.get("name")} 引用了不存在的节点: {member}'

    rules = data.get('rules')
    if not isinstance(rules, list) or not rules:
        return 'rules 为空'
    return None


def write_history(path: Path, entries: dict, stamp: str,
                  max_entries: int = 60000) -> None:
    """写出测活历史（跨轮次复用），按条目数上限裁剪最旧记录"""
    import json

    payload = {'updated': stamp, 'entries': entries}
    # 条目过多时按 last_seen 升序裁剪
    if len(entries) > max_entries:
        ordered = sorted(entries.items(), key=lambda kv: str(kv[1].get('last_seen') or ''))
        payload['entries'] = dict(ordered[-max_entries:])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                    encoding='utf-8')


def load_history(path: Path) -> dict:
    """读取测活历史（不存在或损坏时返回空）"""
    import json

    if not path.exists():
        return {'updated': '', 'entries': {}}
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'updated': '', 'entries': {}}
    if not isinstance(data, dict) or not isinstance(data.get('entries'), dict):
        return {'updated': '', 'entries': {}}
    return data


def dump_candidates_csv(path: Path, records: list[dict]) -> int:
    """导出候选清单为 CSV，便于人工审查住宅判定质量与白名单扩容"""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ['ip', 'port', 'protocol', 'ip_kind', 'ip_confidence', 'country_code',
               'asn', 'isp', 'delay_ms', 'rounds_passed', 'sources']
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for record in records:
            writer.writerow([
                record.get('ip', ''),
                record.get('port', ''),
                record.get('protocol', ''),
                record.get('ip_kind', ''),
                record.get('ip_confidence', ''),
                record.get('country_code', ''),
                record.get('asn', ''),
                record.get('isp', ''),
                record.get('delay_ms', ''),
                record.get('rounds_passed', ''),
                ','.join(sorted(record.get('sources') or [])),
            ])
    return len(records)
