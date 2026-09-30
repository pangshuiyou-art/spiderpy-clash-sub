#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""yaml_dumper 单元测试：数字样字符串必须加引号，避免其它 YAML 解析器误判类型"""

import yaml

import yaml_dumper


def _dump_value(value: str) -> str:
    """把单个字符串值序列化后取出字面量文本"""
    text = yaml_dumper.dump_yaml({'key': value}).strip()
    return text.split(':', 1)[1].strip()


def test_numeric_like_strings_are_quoted():
    """形似数字的字符串（尤其带前导零）必须加引号，否则 Go 系解析器会读成数字"""
    for value in ['08', '0808', '0', '123', '1.5', '1e5', '0x1f', '0o7', '1:30']:
        assert _dump_value(value) == f"'{value}'", value


def test_plain_strings_stay_unquoted():
    """非数字样字符串保持原样，避免产物塞满无意义引号"""
    for value in ['1.2.3.4', '53499a662395', 'abc', '/ws-path', 'XX-406']:
        assert _dump_value(value) == value, value


def test_empty_string_quoted():
    """空字符串序列化为 ''，保证解析回来仍是字符串"""
    assert _dump_value('') == "''"


def test_round_trip_preserves_numeric_like_string():
    """序列化后再解析，类型与取值保持原样（防类型歧义）"""
    data = {'short-id': '08', 'server': '1.2.3.4'}
    loaded = yaml.safe_load(yaml_dumper.dump_yaml(data))
    assert isinstance(loaded['short-id'], str)
    assert loaded['short-id'] == '08'
    assert loaded['server'] == '1.2.3.4'