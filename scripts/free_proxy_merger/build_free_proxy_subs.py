#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""免费代理合并 → 住宅组 / 日常组 Clash 订阅（入口脚本）

流水线：
  拉取源 → 解析归一化 → 去重 + 元数据传播 → TCP 预筛
  → ip-api 补元数据 → 住宅分类（CIDR 段库 + hosting + ASN 白名单）
  → mihomo 多轮测活（住宅 3 轮 / 日常 2 轮）→ 产物生成 + 内核静态校验

用法：
    python scripts/free_proxy_merger/build_free_proxy_subs.py
    python scripts/free_proxy_merger/build_free_proxy_subs.py --mihomo ./mihomo   # 启用真实测活
    python scripts/free_proxy_merger/build_free_proxy_subs.py --skip-tcp          # 调试用，跳过 TCP 预筛
"""

import argparse
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

import builder
import classifier
import delay_tester
import processor
import socks4_tester
import source_loader

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_SOURCES = PROJECT_ROOT / 'config' / 'free_proxy_sources.yaml'
DEFAULT_WHITELIST = PROJECT_ROOT / 'config' / 'residential_asns.yaml'
DEFAULT_OUT_DIR = PROJECT_ROOT / 'data' / 'free_proxy'

# 粗筛门槛（针对有元数据的源；无元数据源靠 TCP + mihomo 兜底）
DAILY_MIN_UPTIME = 50.0        # 源侧 uptime 百分比下限
DAILY_MAX_LATENCY_MS = 3000    # 源侧延迟上限
RESIDENTIAL_MAX_LATENCY_MS = 5000


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='免费代理合并 → 分组 Clash 订阅')
    parser.add_argument('--sources', default=str(DEFAULT_SOURCES), help='源配置路径')
    parser.add_argument('--whitelist', default=str(DEFAULT_WHITELIST), help='住宅 ASN 白名单路径')
    parser.add_argument('--out-dir', default=str(DEFAULT_OUT_DIR), help='产物输出目录')
    parser.add_argument('--mihomo', default='', help='mihomo 二进制路径；提供则启用真实测活')
    parser.add_argument('--residential-rounds', type=int, default=3, help='住宅组测活轮数')
    parser.add_argument('--daily-rounds', type=int, default=2, help='日常组测活轮数')
    parser.add_argument('--skip-tcp', action='store_true', help='跳过 TCP 预筛（调试用）')
    parser.add_argument('--skip-ipapi', action='store_true', help='跳过 ip-api 元数据查询（调试用）')
    parser.add_argument('--max-residential-test', type=int, default=0,
                        help='住宅组送入测活的最大条数（0 表示不限）')
    parser.add_argument('--max-daily-test', type=int, default=0,
                        help='日常组送入测活的最大条数（0 表示不限）')
    return parser.parse_args(argv)


def fetch_all_sources(sources: list[dict], client: httpx.Client) -> tuple[list[dict], dict]:
    """逐源拉取并解析；单源失败不影响整体"""
    records: list[dict] = []
    per_source: dict[str, int] = {}
    for source in sources:
        name = str(source.get('name', 'unknown'))
        try:
            text = source_loader.fetch_text(str(source['url']), client)
            parsed = source_loader.parse_source(text, source)
            per_source[name] = len(parsed)
            records.extend(parsed)
            print(f'[*] {name}: {len(parsed)} 条')
        except Exception as exc:
            per_source[name] = -1
            print(f'[!] {name} 拉取或解析失败: {exc}', file=sys.stderr)
    return records, per_source


def fetch_auxiliary(auxiliary: list[dict], client: httpx.Client) -> list:
    """拉取辅助数据（当前仅 CIDR 黑名单）"""
    networks: list = []
    for item in auxiliary:
        if item.get('type') != 'cidr_blacklist':
            continue
        try:
            text = source_loader.fetch_text(str(item['url']), client)
            networks.extend(source_loader.parse_cidr_blacklist(text))
            print(f"[*] 辅助数据 {item.get('name')}: {len(networks)} 个网段")
        except Exception as exc:
            print(f"[!] 辅助数据 {item.get('name')} 拉取失败: {exc}", file=sys.stderr)
    return networks


def apply_source_side_filter(records: list[dict], is_residential: bool) -> list[dict]:
    """源侧粗筛：只对有元数据的记录生效，无元数据记录保留交由 TCP + mihomo 裁决

    用户明确要求「只要稳定、快速、长期能用的」，故日常组门槛从紧。
    住宅组数量稀少，不设 uptime 门槛。
    """
    kept: list[dict] = []
    for record in records:
        latency = record.get('latency_ms')
        uptime = record.get('uptime')
        streak = record.get('streak')
        has_meta = latency is not None or uptime is not None or streak is not None
        if not has_meta:
            kept.append(record)
            continue
        if is_residential:
            if latency is not None and latency > RESIDENTIAL_MAX_LATENCY_MS:
                continue
            kept.append(record)
            continue
        # 日常组：uptime 与 streak 满足其一即可（两字段各源定义不同，不能简单混用）
        if uptime is not None and uptime < DAILY_MIN_UPTIME and not (streak and streak >= 1):
            continue
        if latency is not None and latency > DAILY_MAX_LATENCY_MS:
            continue
        kept.append(record)
    return kept


def sort_by_quality(records: list[dict]) -> list[dict]:
    """按质量排序：连续存活天数 > uptime > 延迟 > 无元数据"""
    def key(record: dict):
        return (
            -(int(record.get('streak') or 0)),
            -(float(record.get('uptime') or 0)),
            float(record.get('latency_ms') if record.get('latency_ms') is not None else 10 ** 9),
        )

    return sorted(records, key=key)


def load_residential_seed(rows: list[dict]) -> None:
    """从各类源数据里采集住宅种子信息（供人工审查，不参与自动判定）"""
    for record in rows:
        if str(record.get('ip_type') or '').lower() == 'residential':
            record['seed_residential'] = True


def summarize(records: list[dict], label: str) -> None:
    """打印一组节点的国家分布摘要"""
    if not records:
        print(f'[*] {label}: 0 条')
        return
    from collections import Counter

    countries = Counter(str(r.get('country_code') or 'XX').upper() for r in records)
    top = ' '.join(f'{code}:{count}' for code, count in countries.most_common(8))
    print(f'[*] {label}: {len(records)} 条 | 国家分布 {top}')


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    started = time.time()

    sources, auxiliary = source_loader.load_source_config(Path(args.sources))
    if not sources:
        print('[!] 源配置为空', file=sys.stderr)
        return 1
    print(f'[*] 启用源 {len(sources)} 个，辅助数据 {len(auxiliary)} 个')

    with httpx.Client() as client:
        records, per_source = fetch_all_sources(sources, client)
        cidr_networks = fetch_auxiliary(auxiliary, client)

    if not records:
        print('[!] 所有源均无数据', file=sys.stderr)
        return 1
    print(f'[*] 原始记录合计: {len(records)}')

    # 去重 + 元数据传播
    records, dedup_stats = processor.dedup_records(records)
    print(f"[*] 去重: {dedup_stats['input']} -> {dedup_stats['output']}")
    records = processor.propagate_metadata(records)

    # 剔除端口扫描噪音（同 IP 上千个端口的记录，实测全部不可用）
    records, noise_stats = processor.drop_port_scanners(records)
    if noise_stats['dropped']:
        top = '，'.join(f'{ip}({count}端口)' for ip, count in noise_stats['noisy_ips'])
        print(f"[*] 剔除扫描噪音: {noise_stats['input']} -> {noise_stats['output']} "
              f"（{noise_stats['noisy_ip_count']} 个异常 IP，如 {top}）")

    # 源侧粗筛（先按住宅/日常两套门槛各筛一次，再各自走后续流程）
    residential_side = apply_source_side_filter(records, is_residential=True)
    daily_side = apply_source_side_filter(records, is_residential=False)
    print(f'[*] 源侧粗筛: 住宅侧 {len(residential_side)} 条, 日常侧 {len(daily_side)} 条')

    # TCP 预筛（两组合并做一次，避免重复探测）
    union: dict[tuple[str, int], dict] = {}
    for record in residential_side + daily_side:
        union[(record['ip'], record['port'])] = record
    candidates = list(union.values())
    if args.skip_tcp:
        alive = candidates
        print(f'[*] 跳过 TCP 预筛（调试），候选 {len(candidates)} 条')
    else:
        print(f'[*] TCP 预筛开始（{len(candidates)} 条，并发 {processor.TCP_CONNECT_WORKERS}）...')
        alive, tcp_stats = processor.tcp_prefilter(candidates)
        print(f"[*] TCP 预筛完成: {tcp_stats['alive']}/{tcp_stats['checked']} 可达 "
              f"（{tcp_stats['alive'] / max(1, tcp_stats['checked']) * 100:.1f}%）")

    if not alive:
        print('[!] TCP 预筛后无可达地址，终止', file=sys.stderr)
        return 1

    # ip-api 补元数据（只对存活且缺 ASN 的记录查）
    ip_metadata: dict[str, dict] = {}
    need_query = sorted({r['ip'] for r in alive if not r.get('asn')})
    if need_query and not args.skip_ipapi:
        print(f'[*] ip-api 查询 {len(need_query)} 个 IP（限速 15 批/分钟）...')
        with httpx.Client(trust_env=False) as client:
            ip_metadata = processor.query_ip_metadata(need_query, client)
        print(f'[*] ip-api 命中 {len(ip_metadata)}/{len(need_query)}')
    elif need_query:
        print(f'[*] 跳过 ip-api 查询（调试），{len(need_query)} 个 IP 无 ASN')

    # 住宅分类
    whitelist = classifier.load_asn_whitelist(Path(args.whitelist))
    cidr_match = processor.build_cidr_matcher(cidr_networks) if cidr_networks else None
    alive, class_stats = classifier.classify_records(alive, cidr_match, ip_metadata, whitelist)
    print(f"[*] 分类: 住宅高置信 {class_stats['residential_high']}, "
          f"住宅中置信 {class_stats['residential_medium']}, "
          f"机房 {class_stats['by_cidr_blocked'] + class_stats['by_hosting'] + class_stats['by_source_label']}, "
          f"未知 {class_stats['unknown']}")

    groups = classifier.split_by_kind(alive)
    residential_all = sort_by_quality(groups['residential'] + groups['residential_socks4'])
    daily_all = sort_by_quality(groups['daily'])
    summarize(residential_all, '住宅候选')
    summarize(daily_all, '日常候选')

    # 导出候选清单（人工审查住宅判定与白名单扩容用；不含测活结果，供交叉核对）
    out_dir = Path(args.out_dir)
    builder.dump_candidates_csv(out_dir / 'candidates_residential.csv', residential_all)
    builder.dump_candidates_csv(out_dir / 'candidates_daily.csv', daily_all)

    # 抽取要送入测活的子集（数量上限用于控制时长）
    residential_test = residential_all
    daily_test = daily_all
    if args.max_residential_test:
        residential_test = residential_test[:args.max_residential_test]
    if args.max_daily_test:
        daily_test = daily_test[:args.max_daily_test]

    # 测活
    residential_alive: list[dict] = []
    daily_alive: list[dict] = []
    residential_socks4_alive: list[dict] = []
    if args.mihomo:
        # mihomo 不支持 socks4，故把 socks4 记录拆出，走独立协议层测活
        residential_http = [r for r in residential_test if r['protocol'] != 'socks4']
        residential_socks4 = [r for r in residential_test if r['protocol'] == 'socks4']
        daily_http = [r for r in daily_test if r['protocol'] != 'socks4']

        print(f'[*] 住宅组测活开始（{len(residential_http)} 条 http/socks5，'
              f'{args.residential_rounds} 轮）...')
        residential_alive, res_stats = delay_tester.test_records_multi_round(
            residential_http, args.mihomo, rounds=args.residential_rounds, label='住宅组')
        print(f"[*] 住宅组测活结果: {res_stats['alive']} 条存活"
              f"（各轮 {res_stats['per_round']}）")

        if residential_socks4:
            print(f'[*] 住宅 socks4 测活开始（{len(residential_socks4)} 条，'
                  f'{args.residential_rounds} 轮，纯协议层）...')
            residential_socks4_alive, s4_stats = socks4_tester.test_records_multi_round(
                residential_socks4, rounds=args.residential_rounds, label='住宅socks4组')
            print(f"[*] 住宅 socks4 结果: {s4_stats['alive']} 条存活"
                  f"（各轮 {s4_stats['per_round']}）")

        print(f'[*] 日常组测活开始（{len(daily_http)} 条 http/socks5，'
              f'{args.daily_rounds} 轮）...')
        daily_alive, daily_stats = delay_tester.test_records_multi_round(
            daily_http, args.mihomo, rounds=args.daily_rounds, label='日常组')
        print(f"[*] 日常组测活结果: {daily_stats['alive']} 条存活"
              f"（各轮 {daily_stats['per_round']}）")
    else:
        print('[!] 未提供 --mihomo，跳过真实测活；产物将为空以确保不推送未验证节点', file=sys.stderr)

    # 唯一名（住宅与日常分开命名，避免跨组重名）
    for index, record in enumerate(residential_alive, start=1):
        code = str(record.get('country_code') or 'XX').upper()
        record['node_name'] = f'[R]{code}-{index:04d}'
    for index, record in enumerate(daily_alive, start=1):
        code = str(record.get('country_code') or 'XX').upper()
        record['node_name'] = f'[D]{code}-{index:04d}'
    for index, record in enumerate(residential_socks4_alive, start=1):
        record['node_name'] = f"[R4]{str(record.get('country_code') or 'XX').upper()}-{index:04d}"

    for label, subset in (('住宅组', residential_alive), ('日常组', daily_alive)):
        problem = builder.check_unique_names(subset)
        if problem:
            print(f'[!] {label} 节点名校验失败: {problem}', file=sys.stderr)
            return 1

    # 写产物
    out_dir = Path(args.out_dir)
    residential_path = out_dir / 'residential.yaml'
    daily_path = out_dir / 'daily.yaml'
    residential_socks4_path = out_dir / 'residential-socks4.yaml'

    res_header = [
        f'节点数: {len(residential_alive)}',
        f'生成时间: {stamp}',
        f'测活: mihomo 连续 {args.residential_rounds} 轮通过',
        '数据来源: config/free_proxy_sources.yaml 中配置的免费代理源',
    ]
    daily_header = [
        f'节点数: {len(daily_alive)}',
        f'生成时间: {stamp}',
        f'测活: mihomo 连续 {args.daily_rounds} 轮通过',
        '数据来源: config/free_proxy_sources.yaml 中配置的免费代理源',
    ]

    if residential_alive:
        builder.write_subscription(residential_path, residential_alive, '住宅', '住宅节点', res_header)
    if daily_alive:
        builder.write_subscription(daily_path, daily_alive, '日常', '日常节点', daily_header)

    # 住宅 socks4 单独输出（mihomo 不支持该类型，供 Python/curl 等场景使用）
    if residential_socks4_alive:
        lines = []
        for record in residential_socks4_alive:
            comment = ' '.join(x for x in (record.get('country_code'), record.get('isp')) if x)
            suffix = f'  # {comment}' if comment else ''
            lines.append(f"{record['ip']}:{record['port']}{suffix}")
        residential_socks4_path.parent.mkdir(parents=True, exist_ok=True)
        residential_socks4_path.write_text(
            f'# 住宅 socks4 代理（mihomo/Clash 不支持此类型，供 Python/curl 场景使用）\n'
            f'# 节点数: {len(residential_socks4_alive)}\n'
            f'# 生成时间: {stamp}\n'
            f'# 测活: SOCKS4 CONNECT + TLS 握手，连续 {args.residential_rounds} 轮通过\n'
            + '\n'.join(lines) + '\n',
            encoding='utf-8')
        print(f'[*] 住宅 socks4: {len(residential_socks4_alive)} 条 → {residential_socks4_path.name}')

    # 产物校验（结构 + 内核）
    problems: list[str] = []
    for path in (residential_path, daily_path):
        if not path.exists():
            continue
        problem = builder.check_subscription_structure(path)
        if problem:
            problems.append(f'{path.name}: {problem}')
            continue
        if args.mihomo:
            error = delay_tester.validate_config(path, args.mihomo)
            if error:
                problems.append(f'{path.name}: mihomo 校验失败: {error.strip()[-300:]}')
    if problems:
        print('[!] 产物校验失败:', file=sys.stderr)
        for item in problems:
            print(f'    {item}', file=sys.stderr)
        return 1
    print('[*] 产物校验通过')

    # 白名单自学习（写回文件，由 workflow 提交）
    learn = classifier.learn_whitelist(alive, whitelist, stamp)
    classifier.save_asn_whitelist(Path(args.whitelist), whitelist)
    print(f"[*] 白名单: {learn['whitelist_size']} 个 ASN（本轮新增 {len(learn['added_asns'])}）, "
          f"待审 {learn['pending_size']} 个")

    elapsed = time.time() - started
    print(f'[*] 全部完成，用时 {elapsed / 60:.1f} 分钟')
    print(f'[*] 产物: {residential_path} ({len(residential_alive)} 节点)')
    print(f'[*] 产物: {daily_path} ({len(daily_alive)} 节点)')
    return 0


if __name__ == '__main__':
    sys.exit(main())
