#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 Thordata 免费代理列表按用途筛成两组 Clash 订阅

- 住宅组（[R]）：ip_type=residential，供对注册 IP 有要求的场景使用
- 日常组（[D]）：高匿 + 源侧延迟达标，供日常使用

节点名统一带用途标记，便于下游（如 any-auto-register 的节点过滤正则）按用途分流；
socks4 一律剔除——Clash/mihomo 内核不支持该类型（实测报 unsupport proxy type）。

用法：
    python scripts/thordata_build.py                                  # 从配置里的 URL 抓取
    python scripts/thordata_build.py --csv data/thordata_proxies.csv  # 改用本地 CSV（离线调试）
"""

import argparse
import csv
import io
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
import yaml

# 源站类型 → Clash 类型映射：
#   源把「能代理 https 请求的 http 代理」标为 https，而 Clash 只有 http 类型承载它
#   （https 不是合法 Clash 类型，内核会报 unsupport proxy type）
#   socks4 不在映射中——Clash/mihomo 不支持该类
SOURCE_TYPE_TO_CLASH = {
    'http': 'http',
    'https': 'http',
    'socks5': 'socks5',
}

# 策略组自动测速用的探测地址
TEST_URL = 'http://www.gstatic.com/generate_204'


def dump_yaml(data: Any) -> str:
    """输出 Clash 兼容的 YAML 文本（交给 PyYAML 默认行为：需要引号的字符串会自动加引号）"""
    return yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)


def fetch_source_text(source: str, timeout: float) -> str:
    """读取源数据：http(s) 走网络，其它按本地文件路径读"""
    if source.startswith(('http://', 'https://')):
        response = httpx.get(source, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        return response.text
    return Path(source).read_text(encoding='utf-8')


def load_group_config(config_path: Path) -> dict:
    """读取分组配置（源地址 + 各组筛选条件）"""
    data = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not data.get('groups'):
        raise ValueError(f'分组配置不合法: {config_path}')
    return data


def _to_int(value: object, default: int = 0) -> int:
    """安全转整数"""
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def load_rows(source_text: str) -> tuple[list[dict], int]:
    """解析源 CSV，返回（Clash 可映射的行, 被剔除的行数）"""
    rows = list(csv.DictReader(io.StringIO(source_text)))
    usable: list[dict] = []
    dropped = 0
    for row in rows:
        node_type = (row.get('type') or '').strip().lower()
        port = _to_int(row.get('port'))
        if node_type not in SOURCE_TYPE_TO_CLASH or not (row.get('ip') or '').strip() or port <= 0:
            dropped += 1
            continue
        usable.append(row)
    return usable, dropped


def matches(row: dict, require: dict) -> bool:
    """判断一行是否命中分组条件（各字段为允许值列表，AND 关系）"""
    for field, expected in require.items():
        if field == 'min_streak':
            if _to_int(row.get('streak')) < _to_int(expected):
                return False
            continue
        value = (row.get(field) or '').strip().lower()
        allowed = [str(item).strip().lower() for item in expected]
        if value not in allowed:
            return False
    return True


def to_clash_proxy(row: dict, tag: str) -> dict:
    """源 CSV 行 → Clash proxy（名称带用途标记，便于下游正则筛选）

    字段只保留 name/type/server/port，与已验证可订阅的产物形态一致，降低客户端兼容风险。
    """
    clash_type = SOURCE_TYPE_TO_CLASH[row['type'].strip().lower()]
    return {
        'name': f"[{tag}]{row['country_code']}-{clash_type}-{row['ip']}:{row['port']}",
        'type': clash_type,
        'server': row['ip'].strip(),
        'port': _to_int(row['port']),
    }


def build_payload(proxies: list[dict], group_name: str) -> dict:
    """组装带策略组与最小分流规则的 Clash 配置"""
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
        # 只保留 MATCH：不写 GEOIP 规则，避免客户端缺少 GeoIP 数据库时整份配置校验失败
        'rules': [f'MATCH,{group_name}'],
    }


def write_group(spec: dict, matched: list[dict], stamp: str) -> int:
    """写出单个分组的订阅文件，返回节点数"""
    proxies = [to_clash_proxy(row, spec['tag']) for row in matched]
    header = (
        f"# {spec.get('title', spec.get('group_name', '订阅'))}"
        f"（源: Thordata/awesome-free-proxy-list）\n"
        f'# 生成时间: {stamp}\n'
        f"# 节点数: {len(proxies)}\n"
        f"# 筛选条件: {spec.get('require') or {}}\n"
    )
    out_path = Path(spec['output'])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(header + dump_yaml(build_payload(proxies, spec['group_name'])), encoding='utf-8')
    countries = Counter(row['country_code'] for row in matched)
    print(f"  {spec['output']}: {len(proxies)} 节点 | 国家 {dict(countries.most_common(6))}")
    return len(proxies)


def main() -> None:
    parser = argparse.ArgumentParser(description='Thordata 免费代理列表 → 分组 Clash 订阅')
    parser.add_argument('--config', default='config/thordata_groups.yaml', help='分组配置路径')
    parser.add_argument('--csv', default=None, help='改用本地 CSV（离线调试），默认读配置里的 URL')
    parser.add_argument('--timeout', type=float, default=30.0, help='抓取超时秒数')
    args = parser.parse_args()

    config = load_group_config(Path(args.config))
    source = args.csv or config['source_url']
    rows, dropped = load_rows(fetch_source_text(source, args.timeout))
    print(f'[*] 源: {source}')
    print(f'[*] 源数据可用 {len(rows)} 条（剔除 {dropped} 条：socks4、缺 IP/端口等）')

    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    print(f'[*] 生成产物（时间戳 {stamp}）：')
    total = 0
    for key, spec in config['groups'].items():
        matched = [row for row in rows if matches(row, spec.get('require') or {})]
        total += write_group(spec, matched, stamp)

    if total == 0:
        raise SystemExit('两组均为 0 节点，源数据可能异常，终止')


if __name__ == '__main__':
    main()