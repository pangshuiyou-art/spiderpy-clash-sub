#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""订阅源格式嗅探与链接提取

职责：对来源文本判定其编排格式（base64 整文件 / base64 每行 /
明文链接 / Clash YAML / v2rayN JSON），并把内容统一提取为链接行列表。
识别失败时逐级降级，不会因单源异常中断整体。
"""

import base64
import json
import re
from typing import Iterable, Optional

import yaml


# 常见的 v2ray 系协议前缀，用于判定解码结果是否是链接文本
PROXY_SCHEME_PATTERN = re.compile(
    r'^\s*(?:vmess|vless|trojan|ss|hysteria2?|socks5?|http|https|shadowtls)://',
    re.IGNORECASE,
)

# base64 字符集（去除空白后可判定）
_BASE64_CHARS = set('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=')


def _is_base64_text(text: str) -> bool:
    """文本是否为合法 base64 编码（长度≥8 且 bytes 比例达标）"""
    compact = re.sub(r'\s+', '', text)
    if len(compact) < 8:
        return False
    ratio = sum(1 for ch in compact if ch in _BASE64_CHARS) / len(compact)
    return ratio >= 0.95 and len(compact) % 4 != 1


def _looks_like_links(text: str) -> bool:
    """解码后的文本是否像代理链接列表（含协议前缀行）"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    hit = sum(1 for ln in lines if PROXY_SCHEME_PATTERN.match(ln))
    # 至少 1 行命中协议前缀，且命中行占比过半（容忍注释行 #profile-*）
    return hit >= 1 and hit / len(lines) >= 0.5


def decode_base64_whole(text: str) -> Optional[str]:
    """整文按 base64 解码，成功返回明文，失败返回 None"""
    compact = re.sub(r'\s+', '', text)
    if not _is_base64_text(compact):
        return None
    try:
        raw = base64.b64decode(compact)
        decoded = raw.decode('utf-8', 'ignore')
        return decoded if _looks_like_links(decoded) else None
    except (ValueError, TypeError):
        return None


def decode_base64_per_line(text: str) -> Optional[list[str]]:
    """逐行 base64 解码（每行独立加密），全部成功才视为命中"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    decoded_lines: list[str] = []
    for ln in lines:
        if not _is_base64_text(ln):
            return None
        try:
            decoded_lines.append(base64.b64decode(ln).decode('utf-8', 'ignore'))
        except (ValueError, TypeError):
            return None
    return decoded_lines if _looks_like_links('\n'.join(decoded_lines)) else None


def looks_like_clash_yaml(text: str) -> bool:
    """判定是否为 Clash YAML（存在 proxies 段）"""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return False
    return isinstance(data, dict) and 'proxies' in data


def looks_like_v2rayn_json(text: str) -> bool:
    """判定是否为 v2rayN/Netch 风格 JSON 节点数组"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict)


def extract_link_lines(text: str) -> Iterable[str]:
    """把订阅文本按行整理为链接行（丢弃空行与注释行）"""
    for ln in text.splitlines():
        line = ln.strip()
        if not line or line.startswith('#'):
            continue
        yield line


def detect_and_prepare(text: str) -> tuple[str, list[str]]:
    """嗅探格式并返回 (格式名, 链接行列表)

    依次尝试：base64 整文 → base64 每行 → Clash YAML → v2rayN JSON → 明文链接。
    YAML / JSON 只会被识别，链接提取交给各自的解析器，此处返回空列表。
    """
    whole = decode_base64_whole(text)
    if whole is not None:
        return 'base64_whole', list(extract_link_lines(whole))

    per_line = decode_base64_per_line(text)
    if per_line is not None:
        return 'base64_per_line', list(extract_link_lines('\n'.join(per_line)))

    if looks_like_clash_yaml(text):
        return 'clash_yaml', []

    if looks_like_v2rayn_json(text):
        return 'v2rayn_json', []

    return 'plain_links', list(extract_link_lines(text))