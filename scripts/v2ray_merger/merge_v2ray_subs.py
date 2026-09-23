#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多订阅源合并脚本（Clash / v2rayN 双格式输出）

管线：
  读取订阅源配置 → 逐源拉取 → 自动识别格式并解析为节点
  → 去重 → 归属地查询打国家标签 → （可选）mihomo 真实测活过滤
  → 名称唯一化 → 输出 clash_merged.yaml 与 v2ray_merged.txt

用法：
  python merge_v2ray_subs.py                       # 仅合并，不测活
  python merge_v2ray_subs.py --mihomo ./mihomo    # 合并 + mihomo 真实测活(默认丢弃超高延迟)
"""

import argparse
import base64
import signal
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
import yaml

import config_parser
import format_detector
import geo_lookup
import link_parser

# 项目根 = scripts/v2ray_merger 的上三级
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCES = PROJECT_ROOT / 'config' / 'sources.yaml'
DEFAULT_OUT_CLASH = PROJECT_ROOT / 'data' / 'clash' / 'clash_merged.yaml'
DEFAULT_OUT_V2RAY = PROJECT_ROOT / 'data' / 'clash' / 'v2ray_merged.txt'

FETCH_TIMEOUT = 30.0
REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                  'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
}
DEFAULT_MAX_DELAY_MS = 1500


def load_sources(path: Path) -> list[dict]:
    """读取订阅源配置，返回 [{'name','url'}]"""
    if not path.exists():
        raise FileNotFoundError(f'订阅源配置不存在: {path}')
    with open(path, encoding='utf-8') as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict) or not isinstance(data.get('sources'), list):
        raise ValueError(f'订阅源配置格式有误（缺少 sources 列表）: {path}')
    sources = []
    for item in data['sources']:
        if isinstance(item, dict) and item.get('url'):
            sources.append({'name': str(item.get('name', 'unknown')), 'url': str(item['url'])})
    return sources


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


def build_clash_yaml(nodes: list[dict]) -> str:
    """生成 Clash 配置文本（proxies + 汇总策略组 + 规则）"""
    lines = [
        '# 由 merge_v2ray_subs.py 自动生成',
        '# 数据来源: config/sources.yaml 中配置的免费订阅源（仅作研究用途）',
        f'# 生成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}',
        f'# 节点总数: {len(nodes)}',
        '',
        'mixed-port: 7890',
        'allow-lan: false',
        'mode: rule',
        'log-level: info',
        '',
        'proxies:',
    ]
    for node in nodes:
        proxy_item = {
            'name': node['name'],
            'type': node.get('type', ''),
            'server': node['server'],
            'port': node['port'],
        }
        for key in ('uuid', 'alter_id', 'cipher', 'password', 'network', 'tls',
                    'servername', 'sni', 'flow', 'skip-cert-verify', 'ws-opts',
                    'grpc-opts', 'h2-opts', 'reality-opts', 'plugin'):
            if node.get(key):
                proxy_item[key] = node[key]
        delay = node.get('delay')
        comment = f'  # {node["country"]} {node["server"]}:{node["port"]}'
        if delay is not None:
            comment += f' | delay={delay}ms'
        lines.append('  - ' + yaml.safe_dump(proxy_item, allow_unicode=True,
                                             sort_keys=False).strip().replace('\n', '\n    ') + comment)
    lines.extend([
        '',
        'proxy-groups:',
        '  - name: "汇总"',
        '    type: url-test',
        '    url: "https://www.gstatic.com/generate_204"',
        '    interval: 300',
        '    tolerance: 150',
        '    proxies:',
    ])
    for node in nodes:
        lines.append(f'      - "{node["name"]}"')
    lines.extend(['',
                  'rules:',
                  '  - MATCH,汇总', ''])
    return '\n'.join(lines)


def build_v2ray_text(nodes: list[dict]) -> str:
    """生成 v2rayN 可直接导入的订阅文本（每行一个原始链接）"""
    return '\n'.join(str(node.get('raw_link', '')) for node in nodes if node.get('raw_link')) + '\n'


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='多订阅源合并（Clash / v2rayN）')
    parser.add_argument('--sources', default=str(DEFAULT_SOURCES), help='订阅源配置路径')
    parser.add_argument('--out-clash', default=str(DEFAULT_OUT_CLASH), help='Clash 输出路径')
    parser.add_argument('--out-v2ray', default=str(DEFAULT_OUT_V2RAY), help='v2rayN 输出路径')
    parser.add_argument('--mihomo', default='', help='mihomo 二进制路径；提供则启用真实测活')
    parser.add_argument('--max-delay', type=int, default=DEFAULT_MAX_DELAY_MS,
                        help='测活存活最大延迟毫秒（默认1500），仅测活时生效')
    parser.add_argument('--temp-dir', default='', help='mihomo 临时工作目录（默认系统临时目录）')
    parser.add_argument('--no-geo', action='store_true', help='跳过归属地查询（调试用）')
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])

    sources = load_sources(Path(args.sources))
    if not sources:
        print('[!] 订阅源配置为空，未执行任何工作', file=sys.stderr)
        return 1

    all_nodes: list[dict] = []
    with httpx.Client() as client:
        for source in sources:
            try:
                text = fetch_source(source['url'], client)
            except httpx.HTTPError as exc:
                print(f'[!] 拉取失败 [{source["name"]}]: {exc}', file=sys.stderr)
                continue
            try:
                nodes = parse_source_text(text)
            except (ValueError, yaml.YAMLError) as exc:
                print(f'[!] 解析失败 [{source["name"]}]: {exc}', file=sys.stderr)
                continue
            print(f'[*] {source["name"]}: 解析到 {len(nodes)} 个节点')
            all_nodes.extend(nodes)

    if not all_nodes:
        print('[!] 所有源均解析失败，无输出', file=sys.stderr)
        return 1

    total = len(all_nodes)
    all_nodes = dedup(all_nodes)
    print(f'[*] 合并去重: {total} -> {len(all_nodes)}')

    if not args.no_geo:
        try:
            with httpx.Client() as client:
                all_nodes = annotate_regions(all_nodes, client)
            known = sum(1 for node in all_nodes if node.get('country_code'))
            print(f'[*] 归属地查询完成: {known}/{len(all_nodes)} 命中')
        except httpx.HTTPError as exc:
            print(f'[!] 归属地查询整体失败，降级为未知: {exc}', file=sys.stderr)

    # 测活（可选）
    if args.mihomo:
        if not args.temp_dir:
            import tempfile
            temp_dir_base = Path(tempfile.mkdtemp(prefix='mihomo_'))
        else:
            temp_dir_base = Path(args.temp_dir)
        print(f'[*] 开始 mihomo 真实测活 (n={len(all_nodes)}) ...')
        try:
            import tester
            kept = tester.filter_alive(all_nodes, args.mihomo, temp_dir_base, args.max_delay)
            print(f'[*] 测活完成: {len(kept)}/{len(all_nodes)} 存活（最大延迟 {args.max_delay}ms）')
            all_nodes = kept
        except Exception as exc:
            print(f'[!] mihomo 测活失败，保留未测活节点: {exc}', file=sys.stderr)

    all_nodes = make_unique_names(all_nodes)

    out_clash = Path(args.out_clash)
    out_v2ray = Path(args.out_v2ray)
    out_clash.parent.mkdir(parents=True, exist_ok=True)
    out_v2ray.parent.mkdir(parents=True, exist_ok=True)
    out_clash.write_text(build_clash_yaml(all_nodes), encoding='utf-8')
    out_v2ray.write_text(build_v2ray_text(all_nodes), encoding='utf-8')
    print(f'[OK] Clash: {out_clash} ({len(all_nodes)} 节点)')
    print(f'[OK] v2rayN: {out_v2ray}')

    if not all_nodes:
        print('[!] 最终无存活节点，请检查上游订阅源', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    sys.exit(main())