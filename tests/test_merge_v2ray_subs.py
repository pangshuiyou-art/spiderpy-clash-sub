#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""merge_v2ray_subs 集成测试：去重 / 归属标注 / 名称唯一 / 输出（Mock 网络）"""

from unittest import mock

import httpx
import pytest

import merge_v2ray_subs as merger


def _make_node(server='1.2.3.4', port=443, node_type='vless', raw='vless://u@1.2.3.4:443'):
    return {'name': 'x', 'type': node_type, 'server': server, 'port': port,
            'raw_link': raw}


# ---------- 去重 ----------

def test_dedup_by_type_server_port():
    nodes = [
        _make_node(server='1.2.3.4', port=443),
        _make_node(server='1.2.3.4', port=443),           # 完全相同 → 去重
        _make_node(server='1.2.3.4', port=443, node_type='vmess'),  # 类型不同 → 保留
        _make_node(server='1.2.3.4', port=8443),          # 端口不同 → 保留
        _make_node(server='5.5.5.5', port=443),           # server 不同 → 保留
    ]
    unique = merger.dedup(nodes)
    assert len(unique) == 4


def test_dedup_empty():
    assert merger.dedup([]) == []


# ---------- 名称唯一化 ----------

def test_make_unique_names_counter_increases():
    nodes = [
        {'country_code': 'us', 'server': '1.1.1.1'},
        {'country_code': 'US', 'server': '2.2.2.2'},
        {'country_code': 'JP', 'server': '3.3.3.3'},
        {'country_code': '', 'server': '4.4.4.4'},
    ]
    result = merger.make_unique_names(nodes)
    assert result[0]['name'] == 'US-001'
    assert result[1]['name'] == 'US-002'   # 大小写归一，同国递增
    assert result[2]['name'] == 'JP-001'
    assert result[3]['name'] == 'XX-001'   # 缺省国家 → XX
    names = [node['name'] for node in result]
    assert len(names) == len(set(names))   # 全局唯一


# ---------- 源配置加载 ----------

def test_load_sources(tmp_path):
    cfg = tmp_path / 'sources.yaml'
    cfg.write_text('sources:\n  - name: a\n    url: https://x/1\n  - name: b\n    url: https://y/2\n', encoding='utf-8')
    sources = merger.load_sources(cfg)
    assert len(sources) == 2
    assert sources[0]['url'] == 'https://x/1'


def test_load_sources_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        merger.load_sources(tmp_path / 'not-exist.yaml')


def test_load_sources_malformed(tmp_path):
    cfg = tmp_path / 'sources.yaml'
    cfg.write_text('unknown_key: true\n', encoding='utf-8')
    with pytest.raises(ValueError):
        merger.load_sources(cfg)


# ---------- 解析管线（Mock 网络） ----------

def test_parse_source_text_plain_links():
    text = 'vless://u@1.2.3.4:443?security=none#n1\nss://Y2hhY2hhMjA6cA==@2.2.2.2:8388#n2\n'
    nodes = merger.parse_source_text(text)
    assert len(nodes) == 2
    assert nodes[0]['raw_link'].startswith('vless://')
    assert nodes[1]['type'] == 'ss'


def test_parse_source_text_clash_yaml():
    text = 'proxies:\n  - name: n\n    type: vless\n    server: 8.8.8.8\n    port: 443\n'
    nodes = merger.parse_source_text(text)
    assert len(nodes) == 1
    assert nodes[0]['server'] == '8.8.8.8'


def test_parse_source_text_ignores_invalid_lines():
    text = 'vless://u@1.2.3.4:443#ok\nnot-a-valid-link\nbeta://x\n'
    nodes = merger.parse_source_text(text)
    assert len(nodes) == 1


# ---------- fetch / annotate（Mock） ----------

def test_fetch_source_http_error():
    """拉取失败由调用方捕获，此处返回即抛 httpx.HTTPError"""
    with mock.patch.object(httpx.Client, 'get',
                           side_effect=httpx.ConnectError('网络中断')):
        with pytest.raises(httpx.HTTPError):
            with httpx.Client() as client:
                merger.fetch_source('https://example.com/x', client)


def test_annotate_regions_uses_lookup_mock():
    """归属标注通过 geo_lookup 返回结果写入节点"""
    nodes = [_make_node(server='9.9.9.9')]
    with mock.patch('geo_lookup.lookup_regions',
                    return_value={'9.9.9.9': {'country': '英国', 'countryCode': 'GB', 'city': 'London'}}):
        with httpx.Client() as client:
            result = merger.annotate_regions(nodes, client)
    assert result[0]['country_code'] == 'GB'
    assert result[0]['country'] == '英国'


def test_annotate_regions_missing_region():
    """查不到的 host 标为空码，不中断"""
    nodes = [_make_node(server='6.6.6.6')]
    with mock.patch('geo_lookup.lookup_regions', return_value={}):
        with httpx.Client() as client:
            result = merger.annotate_regions(nodes, client)
    assert result[0]['country_code'] == ''


# ---------- 输出构建 ----------

def test_build_clash_yaml_contains_names():
    nodes = [
        {'name': 'US-001', 'type': 'vless', 'server': '1.2.3.4', 'port': 443,
         'uuid': 'u', 'country': '美国', 'country_code': 'US'},
    ]
    text = merger.build_clash_yaml(nodes)
    assert 'US-001' in text
    assert 'type: vless' in text
    assert 'proxy-groups:' in text


def test_build_v2ray_text_uses_raw_links():
    nodes = [_make_node(raw='vless://u@1.2.3.4:443#n1'),
             _make_node(server='2.2.2.2', raw='trojan://t@2.2.2.2:443#n2')]
    text = merger.build_v2ray_text(nodes)
    assert 'vless://u@1.2.3.4:443#n1' in text
    assert 'trojan://t@2.2.2.2:443#n2' in text


def test_build_v2ray_text_skips_no_raw():
    nodes = [_make_node(raw=''), _make_node(raw='ss://x')]
    text = merger.build_v2ray_text(nodes)
    assert text.strip() == 'ss://x'