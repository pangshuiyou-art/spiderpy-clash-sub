"""将 spiderpy 代理池 /all 接口转换为 Clash 订阅 YAML。

每次运行拉取 http://demo.spiderpy.cn/all/ 的 JSON 代理列表，
逐条转换为 Clash `proxies` 定义，并生成含选择组与兜底规则的
完整配置，输出到仓库根目录 clash.yaml。

节点名称带国旗与归属国家。国家查询使用 ip-api /batch 批量接口
（一次请求最多 100 个 IP，避免逐节点串行导致运行缓慢）；
查询失败或非 IP 时降级为 `HTTP-<host>:<port>`，保证端点可见。

用法::

    python scripts/generate.py
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
from pathlib import Path

import httpx
import yaml

SOURCE_URL = 'http://demo.spiderpy.cn/all/'
TIMEOUT = 10.0
GEO_TIMEOUT = 8.0
BATCH_SIZE = 100  # ip-api /batch 单次最多 100 个 IP
OUTPUT_PATH = Path(__file__).resolve().parent.parent / 'clash.yaml'
GROUP_NAME = 'PROXY'
_FLAG_BASE = 127397  # 区域指示符 emoji 偏移量：'🇺' = 127397 + ord('U')

# 私有/保留地址段，跳过查询
_PRIVATE_NETS = (
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('169.254.0.0/16'),
)


def flag_emoji(country_code: str) -> str:
    """国家码（如 US）转国旗 emoji（🇺🇸）。"""
    return ''.join(chr(_FLAG_BASE + ord(char)) for char in country_code.upper())


def _is_public_host(host: str) -> bool:
    """判断 host 是否为可查询归属的公网 IPv4。"""
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError as exc:
        # 无法解析的 host 当作非公网 IP 处理
        print(f'host 非法，跳过归属查询: {host} ({exc})', file=sys.stderr)
        return False
    if parsed.version != 4:
        return False
    return not any(parsed in net for net in _PRIVATE_NETS)


def resolve_countries_batch(hosts: list[str], client: httpx.Client) -> dict[str, str]:
    """批量查询归属国家，返回 {host: '国家名 国旗'}。

    使用 ip-api /batch，一次最多 BATCH_SIZE 个 IP；
    避免逐节点串行 sleep，大幅缩短运行时间。
    """
    result: dict[str, str] = {}
    unique_hosts = list(dict.fromkeys(hosts))
    for start in range(0, len(unique_hosts), BATCH_SIZE):
        chunk = unique_hosts[start : start + BATCH_SIZE]
        try:
            response = client.post(
                'http://ip-api.com/batch',
                json=chunk,
                timeout=GEO_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            print(f'批量归属查询失败: {exc}', file=sys.stderr)
            continue
        if not isinstance(payload, list):
            continue
        for item in payload:
            if not isinstance(item, dict) or item.get('status') != 'success':
                continue
            host = str(item.get('query', ''))
            country = str(item.get('country', ''))
            code = str(item.get('countryCode', ''))
            if host and country:
                result[host] = f'{country} {flag_emoji(code)}'
    return result


def node_name(host: str, port: int, index: int, country: str) -> str:
    """生成可读节点名：有国家用 `国旗 国家-序号`，否则 `HTTP-端点`。"""
    if country:
        return f'{country}-{index:02d}'
    return f'HTTP-{host}:{port}'


def fetch_proxy_list() -> list[str]:
    """拉取并解析 /all 响应，返回 host:port 字符串列表。

    兼容三种常见响应结构：
    - `[{"proxy": "ip:port"}, ...]`
    - `{"data": [...], "count": n}`
    - `["ip:port", ...]`
    """
    try:
        response = httpx.get(SOURCE_URL, timeout=TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        print(f'拉取代理失败: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc

    if isinstance(payload, dict):
        data = payload.get('data') or payload.get('proxies') or []
    else:
        data = payload

    proxy_list: list[str] = []
    for item in data:
        if isinstance(item, str):
            proxy_list.append(item)
        elif isinstance(item, dict):
            # 兼容 ProxyPool 及常见字段命名差异
            proxy = item.get('proxy') or item.get('http') or item.get('host') or item.get('server')
            if proxy:
                proxy_list.append(str(proxy))
    return proxy_list


def build_config(proxy_list: list[str]) -> dict:
    """依据去重后的代理列表构建完整 Clash 配置。"""
    names: list[str] = []
    proxies: list[dict] = []
    seen: dict[str, int] = {}
    country_map: dict[str, str] = {}
    with httpx.Client() as client:
        # 先收集所有公网 host，再一次性批量查询归属
        public_hosts = [host for host in dict.fromkeys(proxy_list) if _is_public_host(host)]
        if public_hosts:
            country_map = resolve_countries_batch(public_hosts, client)

        for address in dict.fromkeys(proxy_list):
            if ':' not in address:
                continue
            host, port_str = address.rsplit(':', 1)
            if not port_str.isdigit():
                continue
            port = int(port_str)
            seen[host] = seen.get(host, 0) + 1
            name = node_name(host, port, seen[host], country_map.get(host, ''))
            names.append(name)
            proxies.append(
                {
                    'name': name,
                    'type': 'http',
                    'server': host,
                    'port': port,
                }
            )

    return {
        'port': 7890,
        'socks-port': 7891,
        'allow-lan': False,
        'mode': 'rule',
        'log-level': 'warning',
        'proxies': proxies,
        'proxy-groups': [
            {
                'name': GROUP_NAME,
                'type': 'select',
                'proxies': names + ['DIRECT'],
            }
        ],
        'rules': [f'MATCH,{GROUP_NAME}'],
    }


def main() -> None:
    """入口：拉取、转换并写出 clash.yaml。"""
    proxy_list = fetch_proxy_list()
    if not proxy_list:
        print('未获取到任何代理，终止处理', file=sys.stderr)
        raise SystemExit(1)
    config = build_config(proxy_list)
    with OUTPUT_PATH.open('w', encoding='utf-8') as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    print(f'已生成 {OUTPUT_PATH}，共 {len(config["proxies"])} 个节点')


if __name__ == '__main__':
    main()