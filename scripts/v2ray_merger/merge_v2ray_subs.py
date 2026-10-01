#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订阅链接解析库

管线：
  逐源拉取 → 自动识别格式并解析为节点 → 去重 → 归属地查询。

本模块由聚合流水线复用，不再直接产出旧 data/clash 订阅。
"""

import httpx

import config_parser
import format_detector
import geo_lookup
import link_parser

FETCH_TIMEOUT = 30.0
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}

def fetch_source(url: str, client: httpx.Client) -> str:
    """拉取单个订阅源文本"""
    response = client.get(url, headers=REQUEST_HEADERS, timeout=FETCH_TIMEOUT, follow_redirects=True)
    response.raise_for_status()
    return response.text


def parse_source_text(text: str) -> list[dict]:
    """识别格式并解析为节点列表"""
    fmt, link_lines = format_detector.detect_and_prepare(text)
    nodes: list[dict] = []
    if fmt == 'clash_yaml':
        nodes = config_parser.from_clash_yaml(text)
    elif fmt == 'v2rayn_json':
        nodes = config_parser.from_v2rayn_json(text)
    else:
        for line in link_lines:
            parsed = link_parser.parse_link(line)
            if parsed is not None:
                parsed['raw_link'] = line
                nodes.append(parsed)
    return nodes


def dedup(nodes: list[dict]) -> list[dict]:
    """按 (type, server, port) 去重，保留首次出现"""
    seen: set[tuple[str, str, int]] = set()
    unique: list[dict] = []
    for node in nodes:
        key = (str(node.get('type', '')), str(node.get('server', '')), int(node.get('port') or 0))
        if key in seen:
            continue
        seen.add(key)
        unique.append(node)
    return unique


def annotate_regions(nodes: list[dict], client: httpx.Client) -> list[dict]:
    """逐一查询归属地并打上 region/country 标签"""
    hosts = sorted({str(node.get('server', '')) for node in nodes if node.get('server')})
    region_map = geo_lookup.lookup_regions(hosts, client)
    for node in nodes:
        info = region_map.get(str(node.get('server', '')), {})
        if info:
            node['country'] = info.get('country', '')
            node['country_code'] = info.get('countryCode', '')
        else:
            node['country'] = ''
            node['country_code'] = ''
    return nodes


def make_unique_names(nodes: list[dict]) -> list[dict]:
    """名称唯一化：采用「国家代码-全局序号」，规避重名导致 Clash 校验失败"""
    code_map: dict[str, int] = {}
    for node in nodes:
        country_code = str(node.get('country_code', '')).upper() or 'XX'
        code_map[country_code] = code_map.get(country_code, 0) + 1
        node['name'] = f'{country_code}-{code_map[country_code]:03d}'
    return nodes


def _to_clash_proxy(node: dict) -> dict:
    """把内部节点模型转换为 mihomo/Clash 可解析的 proxy 表述

    关键点（来自 mihomo 实测报错）：
    - vmess 必须输出 alterId（0 也要输出），键名是 alterId 而非 alter_id；
    - 剔除内部字段（country/country_code/raw_link/delay/name 原样保留），
      防止把解析中间态直接喂给 mihomo 配置导致 Parse config error。
    """
    proxy_item = {
        'name': node['name'],
        'type': node.get('type', ''),
        'server': node['server'],
        'port': node['port'],
    }
    # vmess：alterId 必须存在（0 也要输出），键名用 mihomo 期望的 alterId
    if proxy_item['type'] == 'vmess':
        proxy_item['alterId'] = int(node.get('alter_id') or 0)
        proxy_item['cipher'] = node.get('cipher') or 'auto'
    for key in ('uuid', 'cipher', 'password', 'network', 'tls', 'servername',
                'sni', 'flow', 'skip-cert-verify', 'ws-opts', 'grpc-opts',
                'h2-opts', 'reality-opts', 'plugin'):
        if node.get(key):
            proxy_item[key] = node[key]
    return proxy_item
