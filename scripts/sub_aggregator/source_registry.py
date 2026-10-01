#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一源清单的读取与分类

config/aggregator_sources.yaml 是聚合层唯一的源配置入口：
  新增一个源只需在该文件追加一项，不需要改代码（用户要求「预留接口」）。

源的两种 kind：
  proxypool  ip:port 型代理池（format: csv / json / plain），可参与住宅判定；
  link       订阅链接型（base64 / 明文链接 / Clash YAML / v2rayN JSON 自动识别），进日常组。
"""

from pathlib import Path
from typing import Any

import yaml

VALID_KINDS = ('proxypool', 'link')

# 住宅判定放宽策略的默认值（配置文件未写时生效）
DEFAULT_RESIDENTIAL_POLICY = {
    # 移动网络按住宅处理：运营商 IP 常被 ip-api 标成 hosting，是住宅召回的主要漏点
    'mobile_as_residential': True,
    # ip-api 无结论（未覆盖/查询失败）但有 ASN 证据的条目，收回住宅（低置信）
    'unknown_with_asn_as_residential': True,
    # 上述放宽不适用于本轮 ip-api 明确判为机房的 ASN
    'exclude_datacenter_asns': True,
}


def _enabled_items(raw: Any) -> list[dict]:
    """过滤出「有 url 且启用」的配置项"""
    items = raw if isinstance(raw, list) else []
    return [item for item in items
            if isinstance(item, dict) and item.get('url') and item.get('enabled', True)]


def load_registry(path: Path) -> tuple[list[dict], list[dict], dict]:
    """读取源清单，返回（源列表, 辅助数据列表, 住宅策略）"""
    if not path.exists():
        raise FileNotFoundError(f'聚合源配置不存在: {path}')
    with open(path, encoding='utf-8') as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f'聚合源配置格式有误: {path}')

    sources = _enabled_items(data.get('sources'))
    auxiliary = _enabled_items(data.get('auxiliary'))

    policy = dict(DEFAULT_RESIDENTIAL_POLICY)
    raw_policy = data.get('residential_policy')
    if isinstance(raw_policy, dict):
        policy.update({key: bool(value) for key, value in raw_policy.items()
                       if key in DEFAULT_RESIDENTIAL_POLICY})
    return sources, auxiliary, policy


def split_by_kind(sources: list[dict]) -> dict[str, list[dict]]:
    """按 kind 拆分源；未知 kind 直接报错，避免静默漏源"""
    grouped: dict[str, list[dict]] = {kind: [] for kind in VALID_KINDS}
    for source in sources:
        kind = str(source.get('kind') or 'proxypool').strip().lower()
        if kind not in VALID_KINDS:
            raise ValueError(f"源 {source.get('name')} 的 kind 非法: {kind}（可选 {VALID_KINDS}）")
        grouped[kind].append(source)
    return grouped
