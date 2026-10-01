#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""聚合流水线：多源 → 统一记录 → 分类 → 测活 → 三份产物

流程：
  1. 读源清单（config/aggregator_sources.yaml）
  2. 拉取：代理池源（proxypool）+ 链接源（link）+ 辅助数据（数据中心 CIDR 段库）
  3. 代理池线：去重 → 元数据传播 → 剔除端口扫描噪音 → TCP 预筛 → ip-api → 住宅分类（含放宽）
  4. 链接线：去重 → 归属地查询（进日常组）
  5. 拆分：住宅组 / 日常组 / 住宅 socks4（日常组不含住宅条目），跨线去重
  6. 测活：socks4 走纯协议层，其余交 mihomo 多轮（住宅 3 轮 / 日常 2 轮）
  7. 命名（[R]/[D]/[R4] + 国家 + 全局序号）→ 写产物 → 结构校验 + 内核静态校验

产物直接写 data/free_proxy/，与客户端既有订阅链接一致（整合后无需改动客户端）。

设计取舍：链接型节点只进日常组。订阅链接的 server 多为中转/CDN 入口，
不能用 IP 归属判定住宅，硬塞进住宅组只会污染注册场景。
"""

import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import normalizers, output_builder, residential
from .naming import assign_node_names, summarize_countries
from .reuse import (PROJECT_ROOT, classifier, delay_tester, free_builder,
                    merge_v2ray_subs, processor, quality, socks4_tester,
                    source_loader, tester_proxy)
from .source_registry import load_registry, split_by_kind

DEFAULT_CONFIG = PROJECT_ROOT / 'config' / 'aggregator_sources.yaml'
DEFAULT_WHITELIST = PROJECT_ROOT / 'config' / 'residential_asns.yaml'
# 整合后的唯一产出目录：新旧订阅源合并后直接覆盖免费代理线的产物路径，
# 客户端既有链接无需改动即可拿到聚合结果。
DEFAULT_OUT_DIR = PROJECT_ROOT / 'data' / 'free_proxy'

# 源侧粗筛门槛（沿用 free_proxy_merger 的实测结论）
DAILY_MIN_UPTIME = quality.DAILY_MIN_UPTIME
DAILY_MAX_LATENCY_MS = quality.DAILY_MAX_LATENCY_MS
RESIDENTIAL_MAX_LATENCY_MS = quality.RESIDENTIAL_MAX_LATENCY_MS


def _fetch_proxypool(sources: list[dict], client: httpx.Client) -> list[dict]:
    """拉取并解析代理池源；单源失败不影响整体"""
    records: list[dict] = []
    for source in sources:
        name = str(source.get('name', 'unknown'))
        try:
            text = source_loader.fetch_text(str(source['url']), client)
            parsed = normalizers.normalize_proxypool(text, source)
            records.extend(parsed)
            print(f'[*] [代理池] {name}: {len(parsed)} 条')
        except Exception as exc:
            print(f'[!] [代理池] {name} 拉取或解析失败: {exc}', file=sys.stderr)
    return records


def _fetch_links(sources: list[dict], client: httpx.Client) -> list[dict]:
    """拉取并解析链接源；单源失败不影响整体"""
    records: list[dict] = []
    for source in sources:
        name = str(source.get('name', 'unknown'))
        try:
            text = merge_v2ray_subs.fetch_source(str(source['url']), client)
            parsed = normalizers.normalize_link(text, source)
            records.extend(parsed)
            print(f'[*] [链接] {name}: {len(parsed)} 条')
        except Exception as exc:
            print(f'[!] [链接] {name} 拉取或解析失败: {exc}', file=sys.stderr)
    return records


def _fetch_cidr(auxiliary: list[dict], client: httpx.Client) -> list:
    """拉取辅助数据（数据中心 CIDR 段库）"""
    networks: list = []
    for item in auxiliary:
        if item.get('type') != 'cidr_blacklist':
            continue
        try:
            text = source_loader.fetch_text(str(item['url']), client)
            parsed = source_loader.parse_cidr_blacklist(text)
            networks.extend(parsed)
            print(f"[*] [辅助] {item.get('name')}: {len(parsed)} 个网段")
        except Exception as exc:
            print(f"[!] [辅助] {item.get('name')} 拉取失败: {exc}", file=sys.stderr)
    return networks


def _cross_dedup(records: list[dict]) -> list[dict]:
    """跨线去重：同一 (协议, 地址, 端口) 只保留一条（代理池优先于链接）"""
    seen: set[tuple[str, str, int]] = set()
    unique: list[dict] = []
    for record in records:
        key = (str(record.get('protocol') or ''),
               str(record.get('server') or record.get('ip') or ''),
               int(record.get('port') or 0))
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _build_test_proxies(records: list[dict]) -> list[dict]:
    """把统一记录转成测活用的临时节点配置（临时名 + 纯配置）

    返回值只含 mihomo 能接受的字段：历史教训是内部字段（raw_link/record_kind 等）
    一旦写进配置就会让 mihomo 报 Parse config error，故这里绝不附加任何内部键。
    """
    proxies: list[dict] = []
    for index, record in enumerate(records, start=1):
        proxies.append(output_builder.to_clash_proxy({**record, 'node_name': f'tmp-{index:04d}'}))
    return proxies


def _test_link_multi_round(records: list[dict], mihomo_bin: str,
                           rounds: int, label: str) -> list[dict]:
    """链接型节点多轮测活：连续通过 rounds 轮才保留

    复用 v2ray_merger/tester.test_nodes（单轮已验证原语），在此叠加多轮语义。
    tester.test_nodes 每轮结束会清掉工作目录，所以每轮都用全新的临时目录。
    """
    if not records:
        return []
    # 测活用临时名，避免与住宅组命名规则耦合；最终名在合并后统一分配
    proxies = _build_test_proxies(records)

    survivors = proxies
    delays: dict[str, int] = {}
    for round_index in range(1, rounds + 1):
        working_dir = Path(tempfile.mkdtemp(prefix='mihomo_link_'))
        try:
            delays = tester_proxy.test_nodes(survivors, mihomo_bin, working_dir)
        finally:
            # test_nodes 正常路径会自清目录，这里兜底异常路径
            if working_dir.exists():
                import shutil

                shutil.rmtree(working_dir, ignore_errors=True)
        print(f'[*] {label} 第 {round_index}/{rounds} 轮: {len(delays)}/{len(survivors)} 存活')
        survivors = [node for node in survivors if node['name'] in delays]
        if not survivors:
            break

    alive_names = {node['name'] for node in survivors}
    kept: list[dict] = []
    for index, record in enumerate(records, start=1):
        test_name = f'tmp-{index:04d}'
        if test_name not in alive_names:
            continue
        survived = dict(record)
        survived['delay_ms'] = delays.get(test_name)
        survived['rounds_passed'] = rounds
        kept.append(survived)
    return kept


def _run_pipeline(config_path: Path, whitelist_path: Path, out_dir: Path,
                  mihomo_bin: str, residential_rounds: int, daily_rounds: int,
                  skip_tcp: bool, skip_ipapi: bool,
                  max_residential_test: int, max_daily_test: int) -> int:
    stamp = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    started = time.time()

    sources, auxiliary, policy = load_registry(config_path)
    if not sources:
        print('[!] 源清单为空', file=sys.stderr)
        return 1
    grouped = split_by_kind(sources)
    print(f"[*] 启用源: 代理池 {len(grouped['proxypool'])} 个, "
          f"链接 {len(grouped['link'])} 个, 辅助数据 {len(auxiliary)} 个")
    print(f"[*] 住宅放宽策略: {policy}")

    with httpx.Client() as client:
        pool_records = _fetch_proxypool(grouped['proxypool'], client)
        link_records = _fetch_links(grouped['link'], client)
        cidr_networks = _fetch_cidr(auxiliary, client)

    if not pool_records and not link_records:
        print('[!] 所有源均无数据', file=sys.stderr)
        return 1
    print(f'[*] 原始记录: 代理池 {len(pool_records)} 条, 链接 {len(link_records)} 条')

    # ---------- 代理池线 ----------
    residential_candidates: list[dict] = []
    daily_candidates: list[dict] = []
    residential_socks4_candidates: list[dict] = []
    whitelist = classifier.load_asn_whitelist(whitelist_path)

    if pool_records:
        pool_records, dedup_stats = processor.dedup_records(pool_records)
        print(f"[*] 代理池去重: {dedup_stats['input']} -> {dedup_stats['output']}")
        pool_records = processor.propagate_metadata(pool_records)

        pool_records, noise_stats = processor.drop_port_scanners(pool_records)
        if noise_stats['dropped']:
            print(f"[*] 剔除扫描噪音: {noise_stats['input']} -> {noise_stats['output']} "
                  f"（{noise_stats['noisy_ip_count']} 个异常 IP）")

        res_side = quality.apply_source_side_filter(pool_records, is_residential=True)
        daily_side = quality.apply_source_side_filter(pool_records, is_residential=False)
        print(f'[*] 源侧粗筛: 住宅侧 {len(res_side)} 条, 日常侧 {len(daily_side)} 条')

        union: dict[tuple[str, int], dict] = {}
        for record in res_side + daily_side:
            union[(record['ip'], record['port'])] = record
        candidates = list(union.values())

        if skip_tcp:
            alive = candidates
            print(f'[*] 跳过 TCP 预筛（调试），候选 {len(candidates)} 条')
        else:
            print(f'[*] TCP 预筛开始（{len(candidates)} 条，并发 {processor.TCP_CONNECT_WORKERS}）...')
            alive, tcp_stats = processor.tcp_prefilter(candidates)
            print(f"[*] TCP 预筛完成: {tcp_stats['alive']}/{tcp_stats['checked']} 可达 "
                  f"（{tcp_stats['alive'] / max(1, tcp_stats['checked']) * 100:.1f}%）")

        if alive:
            ip_metadata: dict[str, dict] = {}
            need_query = sorted({r['ip'] for r in alive if not r.get('asn')})
            if need_query and not skip_ipapi:
                print(f'[*] ip-api 查询 {len(need_query)} 个 IP（限速 15 批/分钟）...')
                with httpx.Client(trust_env=False) as client:
                    ip_metadata = processor.query_ip_metadata(need_query, client)
                print(f'[*] ip-api 命中 {len(ip_metadata)}/{len(need_query)}')
            elif need_query:
                print(f'[*] 跳过 ip-api 查询（调试），{len(need_query)} 个 IP 无 ASN')

            cidr_match = residential.build_cidr_matcher(cidr_networks)
            alive, class_stats = classifier.classify_records(alive, cidr_match, ip_metadata, whitelist)
            print(f"[*] 分类: 住宅高置信 {class_stats['residential_high']}, "
                  f"住宅中置信 {class_stats['residential_medium']}, "
                  f"机房 {class_stats['by_cidr_blocked'] + class_stats['by_hosting'] + class_stats['by_source_label']}, "
                  f"未知 {class_stats['unknown']}")

            relax_stats = residential.relax_residential(alive, ip_metadata, policy)
            print(f"[*] 住宅放宽: 移动网络 +{relax_stats['by_mobile']}, "
                  f"未知有ASN +{relax_stats['by_unknown_asn']}, "
                  f"被机房ASN拦下 {relax_stats['blocked_datacenter_asn']}")

            split = residential.split_records(alive)
            residential_candidates = quality.sort_by_quality(split['residential'])
            daily_candidates = quality.sort_by_quality(split['daily'])
            residential_socks4_candidates = quality.sort_by_quality(split['residential_socks4'])

    # ---------- 链接线 ----------
    link_daily: list[dict] = []
    if link_records:
        link_records = merge_v2ray_subs.dedup(link_records)
        print(f'[*] 链接去重: {len(link_records)} 条')
        with httpx.Client(trust_env=False) as client:
            link_records = merge_v2ray_subs.annotate_regions(link_records, client)
        link_daily = quality.sort_by_quality(link_records)

    print(summarize_countries(residential_candidates, '住宅候选'))
    print(summarize_countries(daily_candidates + link_daily, '日常候选'))
    print(summarize_countries(residential_socks4_candidates, '住宅socks4候选'))

    out_dir.mkdir(parents=True, exist_ok=True)
    _dump_candidates(out_dir / 'candidates_residential.csv', residential_candidates)
    _dump_candidates(out_dir / 'candidates_daily.csv', daily_candidates + link_daily)
    _dump_candidates(out_dir / 'candidates_residential_socks4.csv',
                     residential_socks4_candidates)

    if max_residential_test:
        residential_candidates = residential_candidates[:max_residential_test]
        residential_socks4_candidates = residential_socks4_candidates[:max_residential_test]
    if max_daily_test:
        # 预算按「代理池 + 链接」合计封顶，避免两条线各自封顶使测活量翻倍
        daily_candidates = daily_candidates[:max_daily_test]
        link_daily = link_daily[:max(0, max_daily_test - len(daily_candidates))]

    # ---------- 测活 ----------
    residential_alive: list[dict] = []
    daily_alive: list[dict] = []
    residential_socks4_alive: list[dict] = []
    if not mihomo_bin:
        print('[!] 未提供 --mihomo，跳过真实测活；产物将为空以确保不推送未验证节点', file=sys.stderr)
    else:
        pool_res = [r for r in residential_candidates if r.get('record_kind') == 'proxypool']
        pool_daily = [r for r in daily_candidates if r.get('record_kind') == 'proxypool']

        if pool_res:
            print(f'[*] 住宅组（代理池）测活开始（{len(pool_res)} 条，{residential_rounds} 轮）...')
            residential_alive, res_stats = delay_tester.test_records_multi_round(
                pool_res, mihomo_bin, rounds=residential_rounds, label='住宅组')
            print(f"[*] 住宅组测活结果: {res_stats['alive']} 条存活（各轮 {res_stats['per_round']}）")

        if pool_daily:
            print(f'[*] 日常组（代理池）测活开始（{len(pool_daily)} 条，{daily_rounds} 轮）...')
            daily_alive, daily_stats = delay_tester.test_records_multi_round(
                pool_daily, mihomo_bin, rounds=daily_rounds, label='日常组')
            print(f"[*] 日常组测活结果: {daily_stats['alive']} 条存活（各轮 {daily_stats['per_round']}）")

        if link_daily:
            print(f'[*] 日常组（链接）测活开始（{len(link_daily)} 条，{daily_rounds} 轮）...')
            link_alive = _test_link_multi_round(link_daily, mihomo_bin, daily_rounds, '日常组-链接')
            print(f'[*] 日常组（链接）结果: {len(link_alive)} 条存活')
            daily_alive.extend(link_alive)

        if residential_socks4_candidates:
            print(f'[*] 住宅 socks4 测活开始（{len(residential_socks4_candidates)} 条，'
                  f'{residential_rounds} 轮，纯协议层）...')
            residential_socks4_alive, s4_stats = socks4_tester.test_records_multi_round(
                residential_socks4_candidates, rounds=residential_rounds, label='住宅socks4组')
            print(f"[*] 住宅 socks4 结果: {s4_stats['alive']} 条存活"
                  f"（各轮 {s4_stats['per_round']}）")

    # ---------- 命名与产物 ----------
    # 先去重再命名，保证序号连续且全局唯一（Clash 对重名直接报错）
    daily_alive = _cross_dedup(daily_alive)
    assign_node_names(residential_alive, 'R')
    assign_node_names(daily_alive, 'D')
    # socks4 用独立前缀，避免与住宅组重名
    for index, record in enumerate(residential_socks4_alive, start=1):
        code = str(record.get('country_code') or 'XX').upper()
        record['node_name'] = f'[R4]{code}-{index:04d}'

    for label, subset in (('住宅组', residential_alive), ('日常组', daily_alive),
                          ('住宅socks4组', residential_socks4_alive)):
        problem = free_builder.check_unique_names(
            [{'node_name': r.get('node_name')} for r in subset])
        if problem:
            print(f'[!] {label} 节点名校验失败: {problem}', file=sys.stderr)
            return 1

    resid_path = out_dir / 'residential.yaml'
    daily_path = out_dir / 'daily.yaml'
    socks4_path = out_dir / 'residential-socks4.yaml'

    if residential_alive:
        output_builder.write_subscription(
            resid_path, residential_alive, '住宅', '住宅节点（注册场景）',
            [f'节点数: {len(residential_alive)}',
             f'测活: mihomo 连续 {residential_rounds} 轮通过',
             '数据来源: config/aggregator_sources.yaml（代理池源，住宅判定已放宽）',
             *_confidence_breakdown(residential_alive)])
    if daily_alive:
        output_builder.write_subscription(
            daily_path, daily_alive, '日常', '日常节点（按国家区域分组）',
            [f'节点数: {len(daily_alive)}',
             f'测活: mihomo 连续 {daily_rounds} 轮通过',
             '数据来源: config/aggregator_sources.yaml（代理池源 + 订阅链接源）',
             '策略组: 日常 / 日常-手动 / 日常-<国家>'],
            with_country_groups=True)
    if residential_socks4_alive:
        output_builder.write_socks4_list(socks4_path, residential_socks4_alive,
                                         residential_rounds)

    # socks4 纯文本清单不是 Clash 配置，只做存在性/格式自检，不交给内核静态校验
    problems = _validate_outputs(mihomo_bin, (resid_path, daily_path))
    if socks4_path.exists():
        problem = output_builder.check_socks4_list(socks4_path)
        if problem:
            problems.append(f'{socks4_path.name}: {problem}')
    if problems:
        print('[!] 产物校验失败:', file=sys.stderr)
        for item in problems:
            print(f'    {item}', file=sys.stderr)
        return 1
    print('[*] 产物校验通过')

    # 白名单自学习（沿用既有线；写回后由 workflow 提交）
    learn = classifier.learn_whitelist(residential_alive + daily_alive, whitelist, stamp)
    classifier.save_asn_whitelist(whitelist_path, whitelist)
    print(f"[*] 白名单: {learn['whitelist_size']} 个 ASN（本轮新增 {len(learn['added_asns'])}）, "
          f"待审 {learn['pending_size']} 个")

    elapsed = time.time() - started
    print(f'[*] 全部完成，用时 {elapsed / 60:.1f} 分钟')
    print(f'[*] 产物: {resid_path} ({len(residential_alive)} 节点)')
    print(f'[*] 产物: {daily_path} ({len(daily_alive)} 节点)')
    if residential_socks4_alive:
        print(f'[*] 产物: {socks4_path} ({len(residential_socks4_alive)} 节点)')
    return 0


def _confidence_breakdown(records: list[dict]) -> list[str]:
    """住宅组置信度构成（便于按需回退：低置信条目可整批剔除）"""
    from collections import Counter

    counter = Counter(str(r.get('ip_confidence') or 'unknown') for r in records)
    text = ', '.join(f'{key} {value}' for key, value in counter.most_common())
    return [f'置信度构成: {text or "无"}'] if records else []


def _dump_candidates(path: Path, records: list[dict]) -> None:
    """导出候选清单（人工审查住宅判定质量用），链接型记录单独标注"""
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    columns = ['kind', 'ip', 'port', 'protocol', 'ip_kind', 'ip_confidence', 'relaxed',
               'country_code', 'asn', 'isp', 'delay_ms', 'sources']
    with open(path, 'w', encoding='utf-8', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        for record in records:
            writer.writerow([
                record.get('record_kind', ''),
                record.get('ip', ''),
                record.get('port', ''),
                record.get('protocol', ''),
                record.get('ip_kind', ''),
                record.get('ip_confidence', ''),
                record.get('relaxed', ''),
                record.get('country_code', ''),
                record.get('asn', ''),
                record.get('isp', ''),
                record.get('delay_ms', ''),
                ','.join(sorted(record.get('sources') or [])),
            ])


def _validate_outputs(mihomo_bin: str, paths: tuple[Path, ...]) -> list[str]:
    """产物结构校验 + （有内核时）mihomo 静态校验"""
    problems: list[str] = []
    for path in paths:
        if not path.exists():
            continue
        problem = output_builder.check_structure(path)
        if problem:
            problems.append(f'{path.name}: {problem}')
            continue
        if mihomo_bin:
            error = delay_tester.validate_config(path, mihomo_bin)
            if error:
                problems.append(f'{path.name}: mihomo 校验失败: {error.strip()[-300:]}')
    return problems


def run(config_path: Path = DEFAULT_CONFIG,
        whitelist_path: Path = DEFAULT_WHITELIST,
        out_dir: Path = DEFAULT_OUT_DIR,
        mihomo_bin: str = '',
        residential_rounds: int = 3,
        daily_rounds: int = 2,
        skip_tcp: bool = False,
        skip_ipapi: bool = False,
        max_residential_test: int = 0,
        max_daily_test: int = 0) -> int:
    """聚合入口（供 CLI 与测试调用）"""
    return _run_pipeline(config_path, whitelist_path, out_dir, mihomo_bin,
                         residential_rounds, daily_rounds, skip_tcp, skip_ipapi,
                         max_residential_test, max_daily_test)
