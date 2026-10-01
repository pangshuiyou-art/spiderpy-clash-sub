#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一命名与国家区域分组

节点名沿用项目既有约定「[用途标记]国家-全局序号」，下游（any-auto-register 等）
按 `[R]` / `[D]` 正则分流，故保持前缀不变：
  住宅组  [R]US-0001
  日常组  [D]US-0001

序号全局递增（而非按国家分组），因为 Clash 对重名节点直接报错。
"""

from collections import Counter

UNKNOWN_CODE = 'XX'

# 常见国家/地区代码 → 中文名（策略组可读性用；未收录的回退为代码本身）
COUNTRY_NAMES = {
    'AD': '安道尔', 'AE': '阿联酋', 'AR': '阿根廷', 'AT': '奥地利', 'AU': '澳大利亚',
    'BD': '孟加拉', 'BE': '比利时', 'BG': '保加利亚', 'BR': '巴西', 'CA': '加拿大',
    'CH': '瑞士', 'CL': '智利', 'CN': '中国', 'CO': '哥伦比亚', 'CZ': '捷克',
    'DE': '德国', 'DK': '丹麦', 'EE': '爱沙尼亚', 'EG': '埃及', 'ES': '西班牙',
    'FI': '芬兰', 'FR': '法国', 'GB': '英国', 'GR': '希腊', 'HK': '香港',
    'HR': '克罗地亚', 'HU': '匈牙利', 'ID': '印尼', 'IE': '爱尔兰', 'IL': '以色列',
    'IN': '印度', 'IR': '伊朗', 'IT': '意大利', 'JP': '日本', 'KR': '韩国',
    'KZ': '哈萨克斯坦', 'LT': '立陶宛', 'LU': '卢森堡', 'LV': '拉脱维亚', 'MD': '摩尔多瓦',
    'MX': '墨西哥', 'MY': '马来西亚', 'NL': '荷兰', 'NO': '挪威', 'NZ': '新西兰',
    'PH': '菲律宾', 'PK': '巴基斯坦', 'PL': '波兰', 'PT': '葡萄牙', 'RO': '罗马尼亚',
    'RS': '塞尔维亚', 'RU': '俄罗斯', 'SA': '沙特', 'SE': '瑞典', 'SG': '新加坡',
    'SK': '斯洛伐克', 'TH': '泰国', 'TR': '土耳其', 'TW': '台湾', 'UA': '乌克兰',
    'US': '美国', 'VN': '越南', 'ZA': '南非',
}


def country_label(code: str) -> str:
    """国家代码 → 策略组用标签（未知代码回退为代码本身）"""
    code = str(code or '').strip().upper() or UNKNOWN_CODE
    return COUNTRY_NAMES.get(code, code)


def assign_node_names(records: list[dict], tag: str) -> list[dict]:
    """写入 country_code 与全局唯一 node_name；返回同一列表"""
    for index, record in enumerate(records, start=1):
        code = str(record.get('country_code') or UNKNOWN_CODE).strip().upper() or UNKNOWN_CODE
        record['country_code'] = code
        record['node_name'] = f'[{tag}]{code}-{index:04d}'
    return records


def group_by_country(records: list[dict]) -> list[tuple[str, list[str]]]:
    """按国家聚合节点名，返回 [(国家代码, [节点名])]，按节点数降序、代码升序"""
    buckets: dict[str, list[str]] = {}
    for record in records:
        code = str(record.get('country_code') or UNKNOWN_CODE).strip().upper() or UNKNOWN_CODE
        buckets.setdefault(code, []).append(str(record.get('node_name') or ''))
    ordered = sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))
    return [(code, names) for code, names in ordered if names]


def summarize_countries(records: list[dict], label: str) -> str:
    """国家分布摘要文本，用于日志"""
    if not records:
        return f'[*] {label}: 0 条'
    counter = Counter(str(r.get('country_code') or UNKNOWN_CODE) for r in records)
    top = ' '.join(f'{code}:{count}' for code, count in counter.most_common(8))
    return f'[*] {label}: {len(records)} 条 | 国家分布 {top}'
