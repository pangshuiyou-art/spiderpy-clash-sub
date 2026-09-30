#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""builder 单元测试：产物结构与严格自校验"""

import yaml

import builder


def _node(ip: str, port: int, protocol: str = 'http', name: str = '[D]US-0001') -> dict:
    return {'ip': ip, 'port': port, 'protocol': protocol, 'node_name': name,
            'country_code': 'US', 'sources': {'test'}}


# ---------- 产物生成 ----------

def test_write_subscription_structure(tmp_path):
    """产物含 proxies/策略组/rules，且 rules 只留 MATCH"""
    path = tmp_path / 'daily.yaml'
    count = builder.write_subscription(
        path, [_node('1.2.3.4', 8080)], '日常', '日常节点', ['生成时间: test'])
    assert count == 1

    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert data['proxies'][0]['type'] == 'http'
    assert data['proxies'][0]['server'] == '1.2.3.4'
    assert data['rules'] == ['MATCH,日常']
    group_names = [g['name'] for g in data['proxy-groups']]
    assert '日常' in group_names


def test_write_subscription_no_geoip_rule(tmp_path):
    """rules 不得包含 GEOIP（客户端缺 GeoIP 库会导致整份配置校验失败）"""
    path = tmp_path / 'daily.yaml'
    builder.write_subscription(path, [_node('1.2.3.4', 8080)], '日常', 't', [])
    text = path.read_text(encoding='utf-8')
    assert 'GEOIP' not in text


def test_write_subscription_node_fields_minimal(tmp_path):
    """节点只输出 name/type/server/port 四个字段"""
    path = tmp_path / 'daily.yaml'
    builder.write_subscription(path, [_node('1.2.3.4', 8080, 'socks5')], '日常', 't', [])
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert set(data['proxies'][0].keys()) == {'name', 'type', 'server', 'port'}


# ---------- 唯一名校验 ----------

def test_check_unique_names_detects_duplicate():
    """重复节点名会被检出"""
    records = [_node('1.1.1.1', 80, name='X-0001'), _node('2.2.2.2', 80, name='X-0001')]
    assert builder.check_unique_names(records) is not None


def test_check_unique_names_passes():
    """唯一节点名通过校验"""
    records = [_node('1.1.1.1', 80, name='X-0001'), _node('2.2.2.2', 80, name='X-0002')]
    assert builder.check_unique_names(records) is None


# ---------- 结构自检 ----------

def test_check_subscription_structure_ok(tmp_path):
    """合法产物通过结构自检"""
    path = tmp_path / 'daily.yaml'
    builder.write_subscription(path, [_node('1.2.3.4', 8080)], '日常', 't', [])
    assert builder.check_subscription_structure(path) is None


def test_check_subscription_structure_empty_proxies(tmp_path):
    """proxies 为空被检出"""
    path = tmp_path / 'empty.yaml'
    path.write_text(
        'proxies: []\n'
        'proxy-groups:\n  - name: G\n    type: select\n    proxies: []\n'
        'rules:\n  - MATCH,G\n',
        encoding='utf-8')
    assert 'proxies 为空' in builder.check_subscription_structure(path)


def test_check_subscription_structure_bad_type(tmp_path):
    """非法节点类型被检出"""
    path = tmp_path / 'bad.yaml'
    path.write_text(
        'proxies:\n'
        '  - name: A\n    type: vmess\n    server: 1.2.3.4\n    port: 443\n'
        'proxy-groups:\n  - name: G\n    type: select\n    proxies:\n      - A\n'
        'rules:\n  - MATCH,G\n',
        encoding='utf-8')
    assert '类型非法' in builder.check_subscription_structure(path)


def test_check_subscription_structure_dangling_group_reference(tmp_path):
    """策略组引用不存在的节点被检出"""
    path = tmp_path / 'dangling.yaml'
    path.write_text(
        'proxies:\n'
        '  - name: A\n    type: http\n    server: 1.2.3.4\n    port: 8080\n'
        'proxy-groups:\n  - name: G\n    type: select\n    proxies:\n      - A\n      - GHOST\n'
        'rules:\n  - MATCH,G\n',
        encoding='utf-8')
    assert '不存在的节点' in builder.check_subscription_structure(path)


def test_check_subscription_structure_duplicate_names(tmp_path):
    """产物内重名被检出"""
    path = tmp_path / 'dup.yaml'
    path.write_text(
        'proxies:\n'
        '  - name: A\n    type: http\n    server: 1.2.3.4\n    port: 8080\n'
        '  - name: A\n    type: http\n    server: 5.6.7.8\n    port: 8080\n'
        'proxy-groups:\n  - name: G\n    type: select\n    proxies:\n      - A\n'
        'rules:\n  - MATCH,G\n',
        encoding='utf-8')
    assert '节点名重复' in builder.check_subscription_structure(path)


# ---------- 候选导出 ----------

def test_dump_candidates_csv(tmp_path):
    """候选清单导出为 CSV，供人工审查"""
    path = tmp_path / 'candidates.csv'
    records = [
        _node('1.2.3.4', 8080),
        {**_node('5.6.7.8', 1080, 'socks5', '[R]US-0001'),
         'ip_kind': 'residential', 'ip_confidence': 'medium', 'asn': 'AS1', 'isp': 'X'},
    ]
    count = builder.dump_candidates_csv(path, records)
    assert count == 2
    text = path.read_text(encoding='utf-8')
    assert 'ip_confidence' in text.splitlines()[0]
    assert 'residential' in text
