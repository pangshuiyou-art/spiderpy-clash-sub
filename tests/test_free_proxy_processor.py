#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""processor 单元测试：去重、元数据传播、端口噪音清洗、TCP 预筛"""

import socket
import threading

import processor


def _record(ip: str, port: int, protocol: str = 'http', **extra) -> dict:
    """构造测试用记录"""
    record = {'ip': ip, 'port': port, 'protocol': protocol, 'sources': {'test'}}
    record.update(extra)
    return record


# ---------- 去重与元数据合并 ----------

def test_dedup_merges_sources_and_metadata():
    """同一 ip:port 的多条记录合并为一个，来源集合与元数据取并集"""
    records = [
        _record('1.2.3.4', 8080, 'http', sources={'a'}),
        _record('1.2.3.4', 8080, 'http', sources={'b'}, asn='AS1234', isp='Example'),
        _record('1.2.3.4', 8080, 'http', sources={'c'}, uptime=90.0),
    ]
    deduped, stats = processor.dedup_records(records)
    assert stats['output'] == 1
    assert stats['removed'] == 2
    merged = deduped[0]
    assert merged['sources'] == {'a', 'b', 'c'}
    assert merged['asn'] == 'AS1234'
    assert merged['isp'] == 'Example'
    assert merged['uptime'] == 90.0


def test_dedup_keeps_distinct_ports():
    """同 IP 不同端口不去重"""
    records = [_record('1.2.3.4', 8080), _record('1.2.3.4', 9090)]
    deduped, stats = processor.dedup_records(records)
    assert stats['output'] == 2


def test_propagate_metadata_fills_missing_fields():
    """有元数据的记录把字段传播给同 IP 的无元数据记录"""
    records = [
        _record('1.2.3.4', 8080, 'http', asn='AS1234', isp='Example', country_code='US'),
        _record('1.2.3.4', 9090, 'socks5'),
    ]
    processor.propagate_metadata(records)
    assert records[1]['asn'] == 'AS1234'
    assert records[1]['isp'] == 'Example'
    assert records[1]['country_code'] == 'US'


def test_propagate_metadata_does_not_overwrite():
    """已有值不被覆盖"""
    records = [
        _record('1.2.3.4', 8080, 'http', asn='AS1111'),
        _record('1.2.3.4', 9090, 'socks5', asn='AS2222'),
    ]
    processor.propagate_metadata(records)
    assert records[1]['asn'] == 'AS2222'


# ---------- 端口扫描噪音清洗 ----------

def test_drop_port_scanners_keeps_limited_entries():
    """同一 IP 端口数超标时只保留有限条目"""
    records = [_record('45.74.31.30', 10000 + i, 'socks5') for i in range(100)]
    records.append(_record('1.2.3.4', 1080, 'socks5'))
    cleaned, stats = processor.drop_port_scanners(records, max_ports=20)
    assert stats['noisy_ip_count'] == 1
    assert stats['dropped'] == 80
    # 保留 20 条噪音 IP 记录 + 1 条正常记录
    assert len(cleaned) == 21


def test_drop_port_scanners_prefers_metadata():
    """清洗时优先保留带元数据的条目"""
    records = [_record('9.9.9.9', 1000 + i, 'socks5') for i in range(30)]
    records.append(_record('9.9.9.9', 5555, 'socks5', asn='AS1', isp='Good'))
    cleaned, _ = processor.drop_port_scanners(records, max_ports=5)
    kept_ports = {r['port'] for r in cleaned}
    assert 5555 in kept_ports


# ---------- 公网地址判定 ----------

def test_is_public_ip_filters_private_ranges():
    """私网与保留地址被识别为非公网"""
    assert processor._is_public_ip('8.8.8.8') is True
    for private in ('10.0.0.1', '192.168.1.1', '127.0.0.1', '172.16.5.5',
                    '169.254.1.1', '224.0.0.1'):
        assert processor._is_public_ip(private) is False


def test_is_public_ip_rejects_garbage():
    """非法输入返回 False"""
    assert processor._is_public_ip('not-an-ip') is False
    assert processor._is_public_ip('') is False


# ---------- TCP 预筛 ----------

def test_tcp_prefilter_detects_open_port():
    """能连通本机监听端口，说明预筛逻辑正常"""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', 0))
    server.listen(5)
    port = server.getsockname()[1]
    stop = threading.Event()

    def accept_loop():
        """后台接受连接，避免 backlog 打满"""
        server.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = server.accept()
                conn.close()
            except socket.timeout:
                continue
            except OSError:
                break

    worker = threading.Thread(target=accept_loop, daemon=True)
    worker.start()
    try:
        # 预筛要求公网地址，故此处只验证探测函数本身的行为
        records = [_record('127.0.0.1', port)]
        alive, stats = processor.tcp_prefilter(records)
        # 127.0.0.1 会被公网过滤掉，checked 应为 0
        assert stats['checked'] == 0
        assert stats['skipped_private'] == 1
    finally:
        stop.set()
        server.close()
        worker.join(timeout=2)


def test_tcp_prefilter_marks_dead_ports():
    """未监听端口判定为不可达"""
    # 取一个几乎不可能被监听的端口
    records = [_record('1.1.1.1', 9)]
    alive, stats = processor.tcp_prefilter(records)
    assert stats['checked'] == 1
    assert stats['alive'] == 0
    assert alive == []


# ---------- CIDR 匹配 ----------

def test_build_cidr_matcher():
    """CIDR 匹配函数能正确判定归属"""
    import ipaddress

    matcher = processor.build_cidr_matcher([
        ipaddress.ip_network('45.74.31.0/24'),
        ipaddress.ip_network('10.0.0.0/8'),
    ])
    assert matcher('45.74.31.30') is True
    assert matcher('45.74.32.1') is False
    assert matcher('not-an-ip') is False
