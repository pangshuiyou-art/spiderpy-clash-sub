#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订阅聚合层：把多条既有订阅线的源合并为「日常组 / 住宅组」两份产物

设计原则（用户决策）：
- 只产出两份产物：住宅组 + 日常组；日常组按国家区域分组，主要供本机使用；
- 日常组不收录已被判为住宅的条目（避免两组重复）；
- 复用 free_proxy_merger / v2ray_merger 两条已验证线，不重写探测与分析逻辑；
- 住宅判定在既有三库组合之上做「放宽」处理（可配置）；
- 不改动 spiderpy 线（scripts/generate.py）。
"""

__all__ = ['build_aggregated_subs']
