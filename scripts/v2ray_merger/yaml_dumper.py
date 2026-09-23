#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clash YAML 安全序列化：消除 PyYAML 与 Go yaml.v3 的"数字样字符串"解析分歧

背景（实测复现）：short-id 为 "08" 时，PyYAML 认为它是普通字符串（前导零不构成
十进制整数）因而不加引号；而 mihomo 使用的 Go yaml.v3 会把它解析成数字，
于是字段类型不符，内核直接报 `invalid REALITY short ID` 并拒绝启动。

对策：对本模块输出 YAML 时，把所有"可能被其它解析器读成数字"的字符串强制加引号。
仅影响文本表现，不改变任何字段取值。
"""

import re
from typing import Any

import yaml

# 覆盖 YAML 1.1/1.2 各类数字写法（含前导零十进制、0o/0b/0x、小数、科学计数、六十进制）
_NUMERIC_LIKE = re.compile(
    r'''^[+-]?(?:
        [0-9][0-9_]*(?:\.[0-9_]*)?(?:[eE][+-]?[0-9]+)?
      | \.[0-9][0-9_]*(?:[eE][+-]?[0-9]+)?
      | 0[oO][0-7_]+
      | 0[bB][01_]+
      | 0[xX][0-9a-fA-F_]+
      | [0-9][0-9_]*:[0-5]?[0-9](?::[0-5]?[0-9])*
    )$''',
    re.X,
)


class _ClashSafeDumper(yaml.SafeDumper):
    """数字样字符串强制加引号的 SafeDumper"""

    def represent_str(self, data: str) -> Any:
        """字符串默认风格下，若形似数字则改用单引号包裹"""
        style = "'" if _NUMERIC_LIKE.match(data) else None
        return self.represent_scalar('tag:yaml.org,2002:str', data, style=style)


_ClashSafeDumper.add_representer(str, _ClashSafeDumper.represent_str)


def dump_yaml(data: Any, sort_keys: bool = True) -> str:
    """按 Clash 安全风格序列化 YAML 文本"""
    return yaml.dump(data, Dumper=_ClashSafeDumper, allow_unicode=True,
                     sort_keys=sort_keys, default_flow_style=False)