#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""config_parser 单元测试：Clash YAML / v2rayN JSON 解析"""

import json

import pytest

import config_parser


# ---------- Clash YAML ----------

def test_from_clash_yaml_normal():
    """Clash YAML proxies 段解析为节点"""
    text = '''
proxies:
  - name: "节点A"
    type: vless
    server: 1.2.3.4
    port: 443
    uuid: abc
    tls: true
    servername: ex.com
  - name: "节点B"
    type: ss
    server: 2.2.2.2
    port: 8388
    cipher: aes-128-gcm
    password: pwd
'''
    nodes = config_parser.from_clash_yaml(text)
    assert len(nodes) == 2
    assert nodes[0]['type'] == 'vless'
    assert nodes[0]['server'] == '1.2.3.4'
    assert nodes[1]['type'] == 'ss'
    assert nodes[1]['cipher'] == 'aes-128-gcm'


def test_from_clash_yaml_ws_opts_kept():
    """ws-opts 等传输层字段被保留"""
    text = '''
proxies:
  - name: n
    type: vless
    server: h
    port: 443
    network: ws
    ws-opts:
      path: /x
      headers: {Host: h.ex}
'''
    nodes = config_parser.from_clash_yaml(text)
    assert nodes[0]['network'] == 'ws'
    assert nodes[0]['ws-opts']['path'] == '/x'


def test_from_clash_yaml_empty_proxies():
    """proxies 为空列表 → 空节点列表"""
    nodes = config_parser.from_clash_yaml('proxies: []\nmode: rule')
    assert nodes == []


def test_from_clash_yaml_skip_invalid_items():
    """缺 server 或 port 的条目被跳过"""
    text = '''
proxies:
  - name: ok
    type: vless
    server: 1.2.3.4
    port: 443
  - name: bad-missing-port
    type: vless
    server: 5.5.5.5
'''
    nodes = config_parser.from_clash_yaml(text)
    assert len(nodes) == 1
    assert nodes[0]['name'] == 'ok'


def test_from_clash_yaml_malformed_raises():
    """非法 YAML 抛 ValueError"""
    with pytest.raises(ValueError):
        config_parser.from_clash_yaml('proxies: [unclosed')


def test_from_clash_yaml_wrong_root_raises():
    """根节点不是 dict 抛 ValueError"""
    with pytest.raises(ValueError):
        config_parser.from_clash_yaml('- just\n- a\n- list')


# ---------- v2rayN JSON ----------

def test_from_v2rayn_json_normal():
    """v2rayN JSON 数组解析"""
    data = [
        {'ps': 'JP 节点', 'add': '1.1.1.1', 'port': '443', 'id': 'uuid1', 'net': 'ws', 'path': '/p', 'host': 'h.id', 'tls': 'tls'},
        {'ps': 'SS 节点', 'add': '2.2.2.2', 'port': '8388', 'method': 'aes-256-gcm', 'password': 'p', 'type': 'ss'},
    ]
    nodes = config_parser.from_v2rayn_json(json.dumps(data))
    assert len(nodes) == 2
    assert nodes[0]['type'] == 'vmess'
    assert nodes[0]['server'] == '1.1.1.1'
    assert nodes[0]['ws-opts']['path'] == '/p'
    assert nodes[0]['tls'] is True


def test_from_v2rayn_json_ss_type():
    """明确 type=ss 的 JSON 节点映射为 ss"""
    data = [{'ps': 'n', 'add': '3.3.3.3', 'port': '80', 'type': 'ss', 'password': 'p', 'cipher': 'chacha20-ietf-poly1305'}]
    nodes = config_parser.from_v2rayn_json(json.dumps(data))
    assert nodes[0]['type'] == 'ss'
    assert nodes[0]['password'] == 'p'


def test_from_v2rayn_json_empty():
    """空数组 → 空列表"""
    assert config_parser.from_v2rayn_json('[]') == []


def test_from_v2rayn_json_skip_missing_server():
    """缺 add 的条目被跳过"""
    data = [{'ps': 'bad', 'port': 443}, {'ps': 'ok', 'add': '4.4.4.4', 'port': 443}]
    nodes = config_parser.from_v2rayn_json(json.dumps(data))
    assert len(nodes) == 1
    assert nodes[0]['name'] == 'ok'


def test_from_v2rayn_json_malformed_raises():
    with pytest.raises(ValueError):
        config_parser.from_v2rayn_json('not json {')