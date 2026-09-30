#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""link_parser 单元测试：各协议解析 + 异常 + 边界"""

import pytest

import link_parser


# ---------- 正常流程 ----------

def test_parse_vmess_ws():
    """vmess:// base64(JSON) 解析为 Clash 节点"""
    import base64
    import json
    info = {'v': '2', 'ps': 'JP-节点', 'add': '1.2.3.4', 'port': '443',
            'id': 'uuid-1234', 'aid': '0', 'net': 'ws', 'type': 'none',
            'host': 'cdn.example.com', 'path': '/ws', 'tls': 'tls', 'sni': 'cdn.example.com'}
    payload = base64.b64encode(json.dumps(info).encode('utf-8')).decode('ascii')
    node = link_parser.parse_link(f'vmess://{payload}')
    assert node is not None
    assert node['type'] == 'vmess'
    assert node['server'] == '1.2.3.4'
    assert node['port'] == 443
    assert node['uuid'] == 'uuid-1234'
    assert node['network'] == 'ws'
    assert node['tls'] is True
    assert node['ws-opts']['path'] == '/ws'


def test_parse_vless_reality():
    """vless:// reality 参数解析"""
    node = link_parser.parse_link(
        'vless://uuid@5.6.7.8:443?security=reality&sni=example.com&pbk=pubkey&sid=abcd&flow=xtls-rprx-vision&type=tcp&fp=chrome#US')
    assert node is not None
    assert node['type'] == 'vless'
    assert node['server'] == '5.6.7.8'
    assert node['tls'] is True
    assert node['reality-opts']['public-key'] == 'pubkey'
    assert node['flow'] == 'xtls-rprx-vision'
    assert node['client-fingerprint'] == 'chrome'


def test_parse_ss_sip002():
    """ss:// SIP002 格式（userinfo 为 base64）"""
    import base64
    userinfo = base64.b64encode(b'aes-256-gcm:secret123').decode('ascii')
    node = link_parser.parse_link(f'ss://{userinfo}@1.2.3.4:8388#SG')
    assert node is not None
    assert node['type'] == 'ss'
    assert node['server'] == '1.2.3.4'
    assert node['port'] == 8388
    assert node['cipher'] == 'aes-256-gcm'
    assert node['password'] == 'secret123'


def test_parse_ss_legacy():
    """ss:// legacy 格式（整段 base64，无 @ 后的 host:port）"""
    import base64
    raw = 'aes-256-gcm:secret@9.9.9.9:7777'
    payload = base64.b64encode(raw.encode('utf-8')).decode('ascii')
    node = link_parser.parse_link(f'ss://{payload}#comment')
    assert node is not None
    assert node['server'] == '9.9.9.9'
    assert node['port'] == 7777
    assert node['cipher'] == 'aes-256-gcm'


def test_parse_ss_invalid_cipher_rejected():
    """乱码 cipher（源站常见）必须剔除，否则内核启动即失败"""
    import base64
    userinfo = base64.b64encode('MZi>۞ɜ:pass'.encode('utf-8')).decode('ascii')
    assert link_parser.parse_link(f'ss://{userinfo}@1.2.3.4:2083#bad') is None


def test_parse_ss_2022_cipher_requires_base64_key():
    """2022 系列 cipher 的密钥须为定长 base64：明文密码剔除、合规密钥保留"""
    import base64
    plain = base64.b64encode(b'2022-blake3-aes-256-gcm:plain-password').decode('ascii')
    assert link_parser.parse_link(f'ss://{plain}@1.2.3.4:443#k') is None

    key = base64.b64encode(b'0' * 32).decode('ascii')
    valid = base64.b64encode(f'2022-blake3-aes-256-gcm:{key}'.encode('utf-8')).decode('ascii')
    node = link_parser.parse_link(f'ss://{valid}@1.2.3.4:443#k2')
    assert node is not None
    assert node['cipher'] == '2022-blake3-aes-256-gcm'


def test_parse_vless_reality_short_id_validation():
    """REALITY short-id 非法节点剔除；合法值（含前导零）保留"""
    base = 'vless://uuid-1@1.2.3.4:443?security=reality&pbk=KEY&sni=a.com&sid='
    assert link_parser.parse_link(base + 'abc#bad') is None          # 奇数长度
    assert link_parser.parse_link(base + 'xy#bad2') is None          # 非十六进制
    assert link_parser.parse_link(base + '0123456789abcdef0123#x') is None  # 超长（20 位）

    node = link_parser.parse_link(base + '08#ok')
    assert node is not None
    assert node['reality-opts']['short-id'] == '08'

    empty_sid = link_parser.parse_link(base + '#ok2')
    assert empty_sid is not None
    assert empty_sid['reality-opts']['short-id'] == ''


def test_parse_trojan():
    """trojan:// 解析"""
    node = link_parser.parse_link('trojan://mytoken@8.8.8.8:443?sni=t.example.com#HK')
    assert node is not None
    assert node['type'] == 'trojan'
    assert node['port'] == 443
    assert node['password'] == 'mytoken'
    assert node['sni'] == 't.example.com'


def test_parse_hysteria2():
    """hysteria2:// 解析"""
    node = link_parser.parse_link('hysteria2://pass@7.7.7.7:443?insecure=1&sni=h.example.com#DE')
    assert node is not None
    assert node['type'] == 'hysteria2'
    assert node['server'] == '7.7.7.7'
    assert node['password'] == 'pass'
    assert node['skip-cert-verify'] is True


def test_parse_socks():
    """socks:// 解析为 socks5"""
    node = link_parser.parse_link('socks://3.3.3.3:1080#RU')
    assert node is not None
    assert node['type'] == 'socks5'
    assert node['port'] == 1080


# ---------- 异常与边界 ----------

def test_unrecognized_scheme_returns_none():
    """不认识的协议返回 None（不抛异常）"""
    assert link_parser.parse_link('beta://something') is None


def test_empty_line_returns_none():
    assert link_parser.parse_link('') is None
    assert link_parser.parse_link('   ') is None


def test_malformed_vmess_returns_none():
    """vmess 内层不是合法 base64/JSON → 返回 None"""
    node = link_parser.parse_link('vmess://not-a-base64|segment')
    assert node is None


def test_vless_missing_port_defaults_zero():
    """缺少端口的 vless 链接不抛异常，端口为 0（由上游过滤）"""
    node = link_parser.parse_link('vless://uuid@1.2.3.4?security=none')
    assert node is not None
    assert node['port'] == 0


def test_trojan_query_only_sni():
    """trojan 无 query 时使用 host 作为 sni"""
    node = link_parser.parse_link('trojan://tok@6.6.6.6:443')
    assert node['sni'] == '6.6.6.6'


# ---------- 修复后新增：ss2022 密钥校验 / path 净化 / 空 Host ----------

def test_ss_2022_invalid_password_rejected():
    """ss 2022 系列要求密码是合法定长 base64，非法明文应被拒绝"""
    # chacha20-poly1305 需 32 字节 base64；明文 'secret' 解码后长度不符
    node = link_parser.parse_link('ss://MjAyMi1ibGFrZTMtY2hhY2hhMjAtcG9seTEzMDU6c2VjcmV0@1.2.3.4:8388')
    assert node is None


def test_ss_2022_valid_base64_key_accepted():
    """2022 cipher 配合法 base64 密钥可接受（32 字节 = 44 字符 base64）"""
    import base64
    key = base64.b64encode(b'x' * 32).decode('ascii')
    userinfo = base64.b64encode(b'2022-blake3-aes-256-gcm:' + key.encode()).decode('ascii')
    node = link_parser.parse_link(f'ss://{userinfo}@1.2.3.4:8388')
    assert node is not None
    assert node['type'] == 'ss'


def test_ss_traditional_cipher_any_password():
    """传统 cipher（非 2022）允许任意密码"""
    node = link_parser.parse_link('ss://YWVzLTI1Ni1nY206c2VjcmV0MTIz@1.2.3.4:8388')
    assert node is not None
    assert node['cipher'] == 'aes-256-gcm'


def test_vless_ws_path_query_residue_cleaned():
    """ws path 混入 ?ed=2560 等 query 残留时应被净化"""
    import base64
    node = link_parser.parse_link(
        'vless://uuid@1.2.3.4:443?security=none&type=ws&path=%2Fpyip%3Dabc%3Fed%3D2560&host=cdn.example.com')
    # path 值被 URL 编码为 /pyip=abc?ed=2560 → 净化后取 /pyip=abc
    assert node is not None
    assert node['network'] == 'ws'
    assert '?' not in node['ws-opts']['path']
    assert node['ws-opts']['path'].startswith('/pyip=abc')


def test_vless_ws_empty_host_omitted():
    """ws Host 为空时省略 headers，避免 Clash 校验失败"""
    node = link_parser.parse_link('vless://uuid@1.2.3.4:443?security=none&type=ws&path=%2Fws')
    assert node is not None
    assert node['ws-opts']['headers'] == {}