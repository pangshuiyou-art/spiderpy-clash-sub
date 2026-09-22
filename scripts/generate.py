"""将 spiderpy 代理池 /all 接口转换为 Clash 订阅 YAML。

每次运行拉取 http://demo.spiderpy.cn/all/ 的 JSON 代理列表，
逐条转换为 Clash `proxies` 定义，并生成含选择组与兜底规则的
完整配置，输出到仓库根目录 clash.yaml。

用法::

    python scripts/generate.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import yaml

SOURCE_URL = 'http://demo.spiderpy.cn/all/'
TIMEOUT = 10.0
OUTPUT_PATH = Path(__file__).resolve().parent.parent / 'clash.yaml'
GROUP_NAME = 'PROXY'


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
    for index, address in enumerate(dict.fromkeys(proxy_list)):
        if ':' not in address:
            continue
        host, port = address.rsplit(':', 1)
        if not port.isdigit():
            continue
        name = f'proxy-{index:04d}'
        names.append(name)
        proxies.append(
            {
                'name': name,
                'type': 'http',
                'server': host,
                'port': int(port),
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