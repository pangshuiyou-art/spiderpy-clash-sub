#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订阅聚合入口：多源 → 只产出「住宅组 / 日常组」两份 Clash 订阅

用法：
    python scripts/sub_aggregator/build_aggregated_subs.py --mihomo ./mihomo
    python scripts/sub_aggregator/build_aggregated_subs.py --skip-tcp --skip-ipapi   # 离线调试

产物：
    data/subscriptions/residential.yaml   住宅组（注册场景）
    data/subscriptions/daily.yaml         日常组（按国家区域分组）
"""

import argparse
import sys
from pathlib import Path

# 允许以脚本方式直接运行（python scripts/sub_aggregator/build_aggregated_subs.py）
if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sub_aggregator import pipeline  # type: ignore
else:
    from . import pipeline


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='多源订阅聚合 → 住宅组 / 日常组')
    parser.add_argument('--config', default=str(pipeline.DEFAULT_CONFIG),
                        help='聚合源清单路径')
    parser.add_argument('--whitelist', default=str(pipeline.DEFAULT_WHITELIST),
                        help='住宅 ASN 白名单路径')
    parser.add_argument('--out-dir', default=str(pipeline.DEFAULT_OUT_DIR),
                        help='产物输出目录')
    parser.add_argument('--mihomo', default='', help='mihomo 二进制路径；提供则启用真实测活')
    parser.add_argument('--residential-rounds', type=int, default=3, help='住宅组测活轮数')
    parser.add_argument('--daily-rounds', type=int, default=2, help='日常组测活轮数')
    parser.add_argument('--skip-tcp', action='store_true', help='跳过 TCP 预筛（调试用）')
    parser.add_argument('--skip-ipapi', action='store_true', help='跳过 ip-api 元数据查询（调试用）')
    parser.add_argument('--max-residential-test', type=int, default=0,
                        help='住宅组送入测活的最大条数（0 表示不限）')
    parser.add_argument('--max-daily-test', type=int, default=0,
                        help='日常组送入测活的最大条数（0 表示不限）')
    parser.add_argument('--proxycheck-token', default='',
                        help='proxycheck.io 凭证；提供则对住宅候选做独立类型交叉复核')
    parser.add_argument('--proxycheck-budget', type=int, default=150,
                        help='单轮 second opinion 复核的住宅候选上限（免费配额 1000 次/天）')
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    return pipeline.run(
        config_path=Path(args.config),
        whitelist_path=Path(args.whitelist),
        out_dir=Path(args.out_dir),
        mihomo_bin=args.mihomo,
        residential_rounds=args.residential_rounds,
        daily_rounds=args.daily_rounds,
        skip_tcp=args.skip_tcp,
        skip_ipapi=args.skip_ipapi,
        max_residential_test=args.max_residential_test,
        max_daily_test=args.max_daily_test,
        proxycheck_token=args.proxycheck_token,
        proxycheck_budget=args.proxycheck_budget,
    )


if __name__ == '__main__':
    sys.exit(main())
