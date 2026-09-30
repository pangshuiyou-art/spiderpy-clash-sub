#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_loader 单元测试：各源格式解析与归一化"""

import json

import pytest

import source_loader


# ---------- CSV 源 ----------

def test_parse_csv_source_maps_fields():
    """CSV 源按字段映射提取元数据，协议别名归一化"""
    text = (
        'ip,port,protocol,country_code,anonymity,uptime_percent,asn,isp,latency_ms\n'
        '1.2.3.4,8080,https,US,elite,99.5,AS1234,Example ISP,120\n'
    )
    source = {
        'name': 'demo',
        'format': 'csv',
        'fields': {
            'ip': 'ip', 'port': 'port', 'protocol': 'protocol',
            'country_code': 'country_code', 'anonymity': 'anonymity',
            'uptime': 'uptime_percent', 'asn': 'asn', 'isp': 'isp',
            'latency_ms': 'latency_ms',
        },
    }
    records = source_loader.parse_source(text, source)
    assert len(records) == 1
    record = records[0]
    # https 是"支持 https 的 http 代理"，Clash 无该类型，必须归一化为 http
    assert record['protocol'] == 'http'
    assert record['ip'] == '1.2.3.4'
    assert record['port'] == 8080
    assert record['country_code'] == 'US'
    assert record['uptime'] == 99.5
    assert record['latency_ms'] == 120
    assert record['sources'] == {'demo'}


def test_parse_csv_source_skips_invalid_rows():
    """缺 ip 或端口越界的行被剔除，不影响其它行"""
    text = (
        'ip,port,protocol\n'
        '1.2.3.4,8080,http\n'
        ',9090,http\n'
        '5.6.7.8,0,http\n'
        '9.9.9.9,70000,http\n'
    )
    source = {'name': 'demo', 'format': 'csv',
              'fields': {'ip': 'ip', 'port': 'port', 'protocol': 'protocol'}}
    records = source_loader.parse_source(text, source)
    assert len(records) == 1
    assert records[0]['ip'] == '1.2.3.4'


# ---------- JSON 源 ----------

def test_parse_json_source_wrapper_keys():
    """JSON 源的 proxies 包裹键能被正确识别"""
    payload = {
        'count': 1,
        'proxies': [
            {'ip': '1.1.1.1', 'port': 1080, 'protocol': 'SOCKS5', 'country_code': 'DE'},
        ],
    }
    source = {'name': 'demo', 'format': 'json',
              'fields': {'ip': 'ip', 'port': 'port', 'protocol': 'protocol',
                         'country_code': 'country_code'}}
    records = source_loader.parse_source(json.dumps(payload), source)
    assert len(records) == 1
    assert records[0]['protocol'] == 'socks5'
    assert records[0]['country_code'] == 'DE'


def test_parse_json_source_nested_fields():
    """嵌套字段路径（geolocation.country.iso_code）可取值"""
    payload = [
        {
            'host': '2.2.2.2',
            'port': 8888,
            'protocol': 'http',
            'geolocation': {'country': {'iso_code': 'jp'}, 'city': {'names': {'en': 'Tokyo'}}},
            'asn': {'autonomous_system_number': 2497, 'autonomous_system_organization': 'NTT'},
        },
    ]
    source = {'name': 'demo', 'format': 'json',
              'fields': {'ip': 'host', 'port': 'port', 'protocol': 'protocol',
                         'country_code': 'geolocation.country.iso_code',
                         'city': 'geolocation.city.names.en',
                         'asn': 'asn.autonomous_system_number',
                         'isp': 'asn.autonomous_system_organization'}}
    records = source_loader.parse_source(json.dumps(payload), source)
    assert len(records) == 1
    record = records[0]
    assert record['country_code'] == 'JP'
    assert record['city'] == 'Tokyo'
    # 纯数字 ASN 需补 AS 前缀
    assert record['asn'] == 'AS2497'
    assert record['isp'] == 'NTT'


def test_parse_json_latency_seconds_to_ms():
    """秒为单位的延迟自动转毫秒"""
    payload = [{'host': '3.3.3.3', 'port': 80, 'protocol': 'http', 'timeout': 1.5}]
    source = {'name': 'demo', 'format': 'json',
              'fields': {'ip': 'host', 'port': 'port', 'protocol': 'protocol',
                         'latency_s': 'timeout'}}
    records = source_loader.parse_source(json.dumps(payload), source)
    assert records[0]['latency_ms'] == 1500


# ---------- plain 源 ----------

def test_parse_plain_source_bare_ip_port():
    """无 scheme 的 ip:port 行按源固定协议处理"""
    text = '1.2.3.4:8080\n5.6.7.8:1080\n\n# 注释行被忽略\n'
    source = {'name': 'demo', 'format': 'plain', 'protocol': 'socks5'}
    records = source_loader.parse_source(text, source)
    assert len(records) == 2
    assert {r['protocol'] for r in records} == {'socks5'}


def test_parse_plain_source_scheme_prefix_wins():
    """带 scheme 前缀时以行内协议为准"""
    text = 'http://1.2.3.4:8080\nsocks5://5.6.7.8:1080\n'
    source = {'name': 'demo', 'format': 'plain', 'protocol': 'socks5'}
    records = source_loader.parse_source(text, source)
    assert {r['protocol'] for r in records} == {'http', 'socks5'}


def test_parse_plain_source_trailing_metadata():
    """行尾附加的国家/城市等信息不影响解析"""
    text = '1.2.3.4:8080,US,Alexandria\n'
    source = {'name': 'demo', 'format': 'plain', 'protocol': 'socks5'}
    records = source_loader.parse_source(text, source)
    assert len(records) == 1
    assert records[0]['ip'] == '1.2.3.4'
    assert records[0]['port'] == 8080


# ---------- CIDR 黑名单 ----------

def test_parse_cidr_blacklist_skips_invalid():
    """CIDR 解析跳过非法行"""
    networks = source_loader.parse_cidr_blacklist(
        '1.0.0.0/8\n\n# 注释\nnot-a-cidr\n2.0.0.0/16\n')
    assert len(networks) == 2
    assert str(networks[0]) == '1.0.0.0/8'


# ---------- 源配置 ----------

def test_load_source_config_filters_disabled(tmp_path):
    """enabled: false 的源被过滤掉"""
    config = tmp_path / 'sources.yaml'
    config.write_text(
        'sources:\n'
        '  - name: enabled_src\n    url: http://a\n    format: plain\n    protocol: http\n'
        '  - name: disabled_src\n    url: http://b\n    format: plain\n    protocol: http\n    enabled: false\n'
        'auxiliary:\n'
        '  - name: aux\n    url: http://c\n    type: cidr_blacklist\n',
        encoding='utf-8')
    sources, auxiliary = source_loader.load_source_config(config)
    assert [s['name'] for s in sources] == ['enabled_src']
    assert [a['name'] for a in auxiliary] == ['aux']
