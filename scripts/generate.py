"""将 spiderpy 代理池 /all 接口转换为 Clash 订阅 YAML。

每次运行拉取 http://demo.spiderpy.cn/all/ 的 JSON 代理列表，
逐条转换为 Clash `proxies` 定义，并生成含选择组与兜底规则的
完整配置，输出到仓库根目录 clash.yaml。

节点名称带国旗与归属国家（通过免费 ip-api.com 按 IP 查询）；
查询失败或非 IP 时降级为 `HTTP-<host>:<port>`，保证端点可见。

用法::

    python scripts/generate.py
"""

from __future__ import annotations

import ipaddress
import json
import re
import sys
import time
from pathlib import Path

import httpx
import yaml

SOURCE_URL = 'http://demo.spiderpy.cn/all/'
TIMEOUT = 10.0
GEO_TIMEOUT = 3.0
GEO_INTERVAL = 1.2  # ip-api 免费版限 45 次/分钟，逐节点加间隔防超限
OUTPUT_PATH = Path(__file__).resolve().parent.parent / 'clash.yaml'
GROUP_NAME = 'PROXY'
_FLAG_BASE = 127397  # 区域指示符 emoji 偏移量：'🇺' = 127397 + ord('U')
_COUNTRY_CACHE: dict[str, str] = {}


def flag_emoji(country_code: str) -> str:
    """国家码（如 US）转国旗 emoji（🇺🇸）。"""
    return ''.join(chr(_FLAG_BASE + ord(char)) for char in country_code.upper())


def resolve_country(host: str, client: httpx.Client) -> str:
    """按 IP 查询归属国家，返回 `国家名 国旗` 组合；失败返回空串。"""
    if host in _COUNTRY_CACHE:
        return _COUNTRY_CACHE[host]
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        return ''
    if parsed.is_private or parsed.is_loopback:
        return ''
    try:
        response = client.get(f'http://ip-api.com/json/{host}', timeout=GEO_TIMEOUT)
        time.sleep(GEO_INTERVAL)
        payload = response.json()
        if payload.get('status') != 'success':
            return ''
        country = str(payload.get('country', ''))
        code = str(payload.get('countryCode', ''))
        result = f'{country} {flag_emoji(code)}' if country else ''
    except (httpx.HTTPError, json.JSONDecodeError):
        result = ''
    _COUNTRY_CACHE[host] = result
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
    with httpx.Client() as client:
        for address in dict.fromkeys(proxy_list):
            if ':' not in address:
                continue
            host, port_str = address.rsplit(':', 1)
            if not port_str.isdigit():
                continue
            port = int(port_str)
            seen[host] = seen.get(host, 0) + 1
            country = resolve_country(host, client)
            name = node_name(host, port, seen[host], country)
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