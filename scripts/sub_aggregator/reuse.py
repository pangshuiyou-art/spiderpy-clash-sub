#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""复用既有两条线的已验证模块

free_proxy_merger 与 v2ray_merger 都是「以脚本目录为导入根」写的（彼此用裸模块名互相导入），
因此这里做一次 sys.path 注入，是全包唯一允许做导入路径处理的地方。
两个目录内的模块名互不冲突，可安全共存于 sys.path。
"""

import sys
from pathlib import Path

# 本仓库根目录 = scripts/sub_aggregator 的上两级
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FREE_PROXY_DIR = PROJECT_ROOT / 'scripts' / 'free_proxy_merger'
V2RAY_DIR = PROJECT_ROOT / 'scripts' / 'v2ray_merger'


def _register(directory: Path) -> None:
    """把脚本目录登记进 sys.path（幂等）"""
    text = str(directory)
    if text not in sys.path:
        sys.path.insert(0, text)


_register(V2RAY_DIR)
_register(FREE_PROXY_DIR)

# 免费代理线（ip:port 型）
import builder as free_builder            # noqa: E402
import build_free_proxy_subs as free_build  # noqa: E402
import classifier                          # noqa: E402
import delay_tester                        # noqa: E402
import processor                           # noqa: E402
import socks4_tester                       # noqa: E402
import source_loader                       # noqa: E402

# 订阅链接线（vmess/vless/ss/trojan 型）
import merge_v2ray_subs                    # noqa: E402
import tester as tester_proxy              # noqa: E402

__all__ = [
    'PROJECT_ROOT',
    'FREE_PROXY_DIR',
    'V2RAY_DIR',
    'free_builder',
    'free_build',
    'classifier',
    'delay_tester',
    'processor',
    'socks4_tester',
    'source_loader',
    'merge_v2ray_subs',
    'tester_proxy',
]
