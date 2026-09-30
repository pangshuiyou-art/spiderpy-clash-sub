#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""format_detector 单元测试：五类格式识别 + 降级 + 边界"""

import base64
import json

import pytest

import format_detector as detector


# ---------- 正常流程：五类格式均能识别 ----------

def test_detect_base64_whole():
    """base64 整文件解码为链接列表"""
    raw = 'ss://Y2hhY2hhMjA6cGFzcw==@1.2.3.4:8388\n# 注释行\ntrojan://token@9.9.9.9:443'
    b64 = base64.b64encode(raw.encode('utf-8')).decode('ascii')
    fmt, links = detector.detect_and_prepare(b64)
    assert fmt == 'base64_whole'
    assert len(links) == 2
    assert links[0].startswith('ss://')
    assert links[1].startswith('trojan://')


def test_detect_base64_per_line():
    """每行独立 base64 的订阅（整体无法作为一个大 base64 解码）也能识别"""
    lines = ['hello-meta-line', 'ss://Y2hhY2hhMjA6cGFzcw==@1.2.3.4:8388']
    text = '\n'.join(base64.b64encode(ln.encode('utf-8')).decode('ascii') for ln in lines)
    fmt, links = detector.detect_and_prepare(text)
    assert fmt == 'base64_per_line'
    # 元数据行可由下游 link_parser 丢弃，此处保留原噪声行以保真
    assert 'ss://Y2hhY2hhMjA6cGFzcw==@1.2.3.4:8388' in links


def test_detect_plain_links():
    """明文链接列表直接识别"""
    text = 'vmess://abc\nss://def@1.1.1.1:80'
    fmt, links = detector.detect_and_prepare(text)
    assert fmt == 'plain_links'
    assert len(links) == 2


def test_detect_clash_yaml():
    """Clash YAML 识别（含 proxies 段），不提取链接（交给解析器）"""
    text = 'proxies:\n  - name: "a"\n    type: vless\nrules:\n  - MATCH,a'
    fmt, links = detector.detect_and_prepare(text)
    assert fmt == 'clash_yaml'
    assert links == []


def test_detect_v2rayn_json():
    """v2rayN JSON 数组识别"""
    text = json.dumps([{'ps': 'n1', 'add': '1.2.3.4', 'port': 443, 'id': 'uuid'}])
    fmt, links = detector.detect_and_prepare(text)
    assert fmt == 'v2rayn_json'
    assert links == []


# ---------- 异常处理 ----------

def test_garbage_text_falls_back_to_plain():
    """无法解码的垃圾文本降级为明文链接（不抛异常）"""
    fmt, links = detector.detect_and_prepare('\u4e2d\u6587\u4e71\u7801!!!')
    assert fmt == 'plain_links'
    # 乱码行非链接，但函数本身不抛错
    assert isinstance(links, list)


def test_empty_text():
    """空文本不抛异常"""
    fmt, links = detector.detect_and_prepare('')
    assert fmt == 'plain_links'
    assert links == []


def test_invalid_base64_chars():
    """含非法字符但比例高时按 base64 尝试失败 → 降级明文"""
    text = 'a' * 20 + '###' + '###'
    fmt, links = detector.detect_and_prepare(text)
    assert fmt == 'plain_links'


def test_comment_only_text():
    """只有注释行，无链接"""
    fmt, links = detector.detect_and_prepare('#profile-title: hello\n#support-url: https://x')
    assert fmt == 'plain_links'
    assert links == []


# ---------- 边界值 ----------

def test_short_base64_input():
    """长度不足 base64 判定下限（<8）时不误判"""
    fmt, _ = detector.detect_and_prepare('ss://ab')
    assert fmt == 'plain_links'


def test_base64_decodes_but_not_links():
    """base64 解码成功但内容不是链接 → 不当作 base64_whole"""
    b64 = base64.b64encode('纯文本内容'.encode('utf-8')).decode('ascii')
    fmt, _ = detector.detect_and_prepare(b64)
    assert fmt == 'plain_links'


def test_yaml_empty_proxies_list():
    """Clash 配置中 proxies 为空列表仍应识别为 clash_yaml"""
    text = 'proxies: []\nmode: rule'
    fmt, _ = detector.detect_and_prepare(text)
    assert fmt == 'clash_yaml'


@pytest.mark.parametrize('scheme', ['vmess', 'vless', 'trojan', 'ss', 'hysteria2', 'socks5'])
def test_each_proxy_scheme_matched(scheme):
    """各协议前缀均被链接判定识别"""
    item = f'{scheme}://example.org:443#name'
    fmt, links = detector.detect_and_prepare(item)
    assert fmt == 'plain_links'
    assert links == [item]