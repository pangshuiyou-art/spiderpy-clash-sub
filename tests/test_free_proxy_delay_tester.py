#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tester 单元测试：节点命名、配置组装、内核报错定位

真实测活（需 mihomo 二进制）不在此覆盖，由 workflow 的端到端运行验证。
"""

import yaml

import delay_tester


def _record(ip: str, port: int, protocol: str = 'http', cc: str = 'US') -> dict:
    return {'ip': ip, 'port': port, 'protocol': protocol, 'country_code': cc,
            'sources': {'test'}}


# ---------- 节点命名 ----------

def test_make_unique_names_global_sequence():
    """节点序号全局递增，避免跨国家重名（Clash 对重名直接报错）"""
    records = [
        _record('1.1.1.1', 80, cc='US'),
        _record('2.2.2.2', 80, cc='US'),
        _record('3.3.3.3', 80, cc='DE'),
    ]
    nodes = delay_tester.make_unique_names(records)
    names = [n['name'] for n in nodes]
    assert len(names) == len(set(names))
    assert names == ['US-0001', 'US-0002', 'DE-0003']


def test_make_unique_names_with_prefix():
    """前缀用于区分住宅组与日常组"""
    records = [_record('1.1.1.1', 80, cc='JP')]
    nodes = delay_tester.make_unique_names(records, prefix='[R]')
    assert nodes[0]['name'] == '[R]JP-0001'


def test_make_unique_names_defaults_missing_country():
    """缺国家码时用 XX 占位"""
    records = [{'ip': '1.1.1.1', 'port': 80, 'protocol': 'http', 'sources': {'t'}}]
    nodes = delay_tester.make_unique_names(records)
    assert nodes[0]['name'] == 'XX-0001'


def test_make_unique_names_writes_back_to_records():
    """生成的节点名回写到记录，供后续产物生成使用"""
    records = [_record('1.1.1.1', 80)]
    delay_tester.make_unique_names(records, prefix='[D]')
    assert records[0]['node_name'] == '[D]US-0001'


# ---------- 配置组装 ----------

def test_to_clash_proxy_minimal_fields():
    """只输出内核认识的四个字段"""
    node = delay_tester._to_clash_proxy(_record('1.2.3.4', 8080, 'socks5'), 'X-0001')
    assert set(node.keys()) == {'name', 'type', 'server', 'port'}
    assert node['type'] == 'socks5'
    assert node['port'] == 8080


def test_build_config_shape(tmp_path):
    """配置含 proxies/策略组/rules，且 rules 不引入 GEOIP 依赖"""
    nodes = [delay_tester._to_clash_proxy(_record('1.2.3.4', 8080), 'X-0001')]
    path = delay_tester._build_config(tmp_path, nodes, '127.0.0.1:19090')
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert data['external-controller'] == '127.0.0.1:19090'
    assert data['rules'] == ['MATCH,ALL']
    assert data['mode'] == 'rule'


# ---------- 内核报错定位 ----------

def test_drop_invalid_node_by_server_port():
    """按 server:port 定位问题节点"""
    candidates = [
        {'name': 'A', 'server': '1.1.1.1', 'port': 80},
        {'name': 'B', 'server': '2.2.2.2', 'port': 90},
    ]
    error = 'level=error msg="proxy 1: 2.2.2.2:90 invalid"'
    dropped = delay_tester._drop_invalid_node(candidates, error)
    assert dropped['name'] == 'B'
    assert len(candidates) == 1


def test_drop_invalid_node_by_index_fallback():
    """无 server:port 时按 proxy 序号定位"""
    candidates = [
        {'name': 'A', 'server': '1.1.1.1', 'port': 80},
        {'name': 'B', 'server': '2.2.2.2', 'port': 90},
    ]
    error = 'proxy 0: unsupported type'
    dropped = delay_tester._drop_invalid_node(candidates, error)
    assert dropped['name'] == 'A'


def test_drop_invalid_node_returns_none_when_unparseable():
    """无法定位时返回 None（由调用方中止并报错）"""
    candidates = [{'name': 'A', 'server': '1.1.1.1', 'port': 80}]
    assert delay_tester._drop_invalid_node(candidates, 'some unrelated error') is None


# ---------- 端口占用预检 ----------

def test_pick_free_port_returns_usable_port():
    """挑出的端口可被立即绑定"""
    import socket

    port = delay_tester._pick_free_port()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(('127.0.0.1', port))
    finally:
        sock.close()


# ---------- 空输入 ----------

def test_test_records_multi_round_empty():
    """空输入直接返回，不启动内核"""
    kept, stats = delay_tester.test_records_multi_round([], '/nonexistent/mihomo', rounds=2)
    assert kept == []
    assert stats['alive'] == 0
