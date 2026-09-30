#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""classifier 单元测试：住宅三库判定、白名单自学习、分组"""

import ipaddress

import classifier
import processor


def _record(ip: str, protocol: str = 'http', **extra) -> dict:
    record = {'ip': ip, 'port': 8080, 'protocol': protocol, 'sources': {'test'}}
    record.update(extra)
    return record


def _matcher(*cidrs: str):
    return processor.build_cidr_matcher([ipaddress.ip_network(c) for c in cidrs])


# ---------- ASN 归一化 ----------

def test_normalize_asn_parses_ipapi_format():
    """ip-api 的 'AS22773 Cox Communications Inc.' 取出正确 ASN"""
    assert classifier._normalize_asn('AS22773 Cox Communications Inc.') == 'AS22773'


def test_normalize_asn_ignores_digits_in_org():
    """组织名里的数字不被误当作 ASN（回归：'3xK Tech GmbH' 曾误判为 AS2003733）"""
    assert classifier._normalize_asn('3xK Tech GmbH') == ''
    assert classifier._normalize_asn('Heart Internet limited') == ''


def test_normalize_asn_accepts_bare_number():
    """纯数字形式的 ASN 补上 AS 前缀"""
    assert classifier._normalize_asn('2497') == 'AS2497'


# ---------- 三库判定 ----------

def test_classify_source_label_wins():
    """源自身 ip_type 标记优先采信"""
    records = [_record('1.2.3.4', ip_type='residential')]
    records, stats = classifier.classify_records(records, None, {}, {})
    assert records[0]['ip_kind'] == 'residential'
    assert records[0]['ip_confidence'] == 'high'
    assert stats['by_source_label'] == 1


def test_classify_cidr_blacklist_marks_datacenter():
    """CIDR 段库命中即判定为机房"""
    records = [_record('45.74.31.30')]
    # 注意：CIDR 判定在 hosting 之前，故此例不给 ip-api 数据也应判为机房
    records, stats = classifier.classify_records(records, _matcher('45.74.31.0/24'), {}, {})
    assert records[0]['ip_kind'] == 'datacenter'
    assert stats['by_cidr_blocked'] == 1


def test_classify_hosting_true_marks_datacenter():
    """ip-api hosting=True 判定为机房"""
    records = [_record('9.9.9.9')]
    records, stats = classifier.classify_records(
        records, _matcher('10.0.0.0/8'), {'9.9.9.9': {'hosting': True, 'asn': 'AS1', 'isp': 'Cloud'}}, {})
    assert records[0]['ip_kind'] == 'datacenter'
    assert stats['by_hosting'] == 1


def test_classify_hosting_false_with_whitelist_is_high():
    """hosting=False 且 ASN 在白名单 → 高置信住宅"""
    records = [_record('1.2.3.4')]
    metadata = {'1.2.3.4': {'hosting': False, 'asn': 'AS22773 Cox Communications', 'isp': 'Cox'}}
    whitelist = {'asns': {'AS22773': {'org': 'Cox'}}}
    records, stats = classifier.classify_records(
        records, _matcher('10.0.0.0/8'), metadata, whitelist)
    assert records[0]['ip_kind'] == 'residential'
    assert records[0]['ip_confidence'] == 'high'
    assert stats['residential_high'] == 1


def test_classify_hosting_false_without_whitelist_is_medium():
    """hosting=False 但 ASN 不在白名单 → 中置信住宅（供人工审查）"""
    records = [_record('1.2.3.4')]
    metadata = {'1.2.3.4': {'hosting': False, 'asn': 'AS99999 Some ISP', 'isp': 'Some ISP'}}
    records, stats = classifier.classify_records(
        records, None, metadata, {'asns': {}})
    assert records[0]['ip_kind'] == 'residential'
    assert records[0]['ip_confidence'] == 'medium'
    assert stats['residential_medium'] == 1


def test_classify_without_any_signal_is_unknown():
    """无任何判定依据时标记为 unknown"""
    records = [_record('1.2.3.4')]
    records, stats = classifier.classify_records(records, None, {}, {'asns': {}})
    assert records[0]['ip_kind'] == 'unknown'
    assert stats['unknown'] == 1


def test_classify_whitelist_works_without_hosting():
    """没有 ip-api 数据时，白名单仍能独立判定住宅"""
    records = [_record('1.2.3.4', asn='AS22773')]
    records, stats = classifier.classify_records(
        records, None, {}, {'asns': {'AS22773': {'org': 'Cox'}}})
    assert records[0]['ip_kind'] == 'residential'
    assert records[0]['ip_confidence'] == 'high'


# ---------- 白名单自学习 ----------

def test_learn_whitelist_adds_high_confidence():
    """高置信住宅的 ASN 并入白名单"""
    records = [_record('1.2.3.4', ip_kind='residential', ip_confidence='high',
                       asn='AS12345', isp='New ISP')]
    whitelist = {'asns': {}, 'pending_review': {}}
    result = classifier.learn_whitelist(records, whitelist, '2026-09-30')
    assert 'AS12345' in whitelist['asns']
    assert result['added_asns'] == ['AS12345']


def test_learn_whitelist_pending_for_medium():
    """中置信住宅的 ASN 进入待审清单，不直接入白名单"""
    records = [_record('1.2.3.4', ip_kind='residential', ip_confidence='medium',
                       asn='AS54321', isp='Maybe ISP')]
    whitelist = {'asns': {}, 'pending_review': {}}
    classifier.learn_whitelist(records, whitelist, '2026-09-30')
    assert 'AS54321' not in whitelist['asns']
    assert whitelist['pending_review']['AS54321']['hits'] == 1


def test_learn_whitelist_counts_pending_hits():
    """同一待审 ASN 多次出现累加 hits"""
    records = [
        _record('1.2.3.4', ip_kind='residential', ip_confidence='medium', asn='AS54321'),
        _record('1.2.3.5', ip_kind='residential', ip_confidence='medium', asn='AS54321'),
    ]
    whitelist = {'asns': {}, 'pending_review': {}}
    classifier.learn_whitelist(records, whitelist, '2026-09-30')
    assert whitelist['pending_review']['AS54321']['hits'] == 2


# ---------- 分组 ----------

def test_split_by_kind_routes_socks4_separately():
    """住宅 socks4 单独成组（Clash 不支持该类型）"""
    records = [
        _record('1.1.1.1', 'socks4', ip_kind='residential'),
        _record('2.2.2.2', 'socks5', ip_kind='residential'),
        _record('3.3.3.3', 'http', ip_kind='datacenter'),
        _record('4.4.4.4', 'socks4', ip_kind='datacenter'),
    ]
    groups = classifier.split_by_kind(records)
    assert len(groups['residential']) == 1
    assert len(groups['residential_socks4']) == 1
    assert len(groups['daily']) == 1


# ---------- 白名单读写 ----------

def test_whitelist_round_trip(tmp_path):
    """白名单写入后可原样读回"""
    path = tmp_path / 'asns.yaml'
    original = {
        'version': 1,
        'updated': '2026-09-30',
        'asns': {'AS22773': {'org': 'Cox', 'added': '2026-09-30', 'source': 'seed'}},
        'pending_review': {'AS99999': {'org': 'X', 'hits': 3}},
    }
    classifier.save_asn_whitelist(path, original)
    loaded = classifier.load_asn_whitelist(path)
    assert loaded['asns']['AS22773']['org'] == 'Cox'
    assert loaded['pending_review']['AS99999']['hits'] == 3


def test_whitelist_missing_file_returns_skeleton(tmp_path):
    """白名单文件不存在时返回可用骨架"""
    loaded = classifier.load_asn_whitelist(tmp_path / 'nope.yaml')
    assert loaded['asns'] == {}
    assert loaded['pending_review'] == {}
