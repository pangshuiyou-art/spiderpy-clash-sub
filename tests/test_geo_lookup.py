#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""geo_lookup 单元测试：私网拦截 / 域名解析 / 批量分批（用 Mock 消除外网）"""

from unittest import mock

import httpx

import geo_lookup


# ---------- 私网 / 地址判定 ----------

def test_is_private_ipv4():
    assert geo_lookup.is_private('192.168.1.1') is True
    assert geo_lookup.is_private('10.0.0.5') is True
    assert geo_lookup.is_private('127.0.0.1') is True
    assert geo_lookup.is_private('172.16.0.1') is True


def test_is_private_public_ipv4():
    assert geo_lookup.is_private('8.8.8.8') is False
    assert geo_lookup.is_private('104.16.132.229') is False


def test_is_private_invalid():
    assert geo_lookup.is_private('not-an-ip') is True


# ---------- resolve_host ----------

def test_resolve_host_ip_returns_none():
    """本身是 IP 无需解析，返回 None"""
    assert geo_lookup.resolve_host('8.8.8.8') is None


@mock.patch('geo_lookup.socket.gethostbyname', return_value='1.2.3.4')
def test_resolve_host_domain(mock_resolve):
    assert geo_lookup.resolve_host('example.com') == '1.2.3.4'


@mock.patch('geo_lookup.socket.gethostbyname', side_effect=OSError('无法解析'))
def test_resolve_host_failure(mock_resolve):
    assert geo_lookup.resolve_host('no-such-host.invalid') is None


# ---------- 批量查询（Mock） ----------

def _fake_batch_response(*args, **kwargs):
    return mock.Mock(
        raise_for_status=lambda: None,
        json=lambda: [
            {'status': 'success', 'country': '美国', 'countryCode': 'US', 'city': 'Los Angeles', 'query': '1.2.3.4'},
            {'status': 'success', 'country': '日本', 'countryCode': 'JP', 'city': 'Tokyo', 'query': '5.6.7.8'},
        ],
    )


def test_lookup_regions_batch_mock():
    """Mock 批量接口：返回 host→归属映射"""
    with mock.patch.object(httpx.Client, 'post', side_effect=_fake_batch_response) as fake_post:
        result = geo_lookup.lookup_regions(['1.2.3.4', '5.6.7.8'], httpx.Client())
    assert result['1.2.3.4']['countryCode'] == 'US'
    assert result['5.6.7.8']['city'] == 'Tokyo'
    # 批量接口只调用一次
    assert fake_post.call_count == 1


def test_lookup_regions_skips_private():
    """私网 host 不发起请求"""
    with mock.patch.object(httpx.Client, 'post') as fake_post:
        geo_lookup.lookup_regions(['192.168.0.1', '10.0.0.2'], httpx.Client())
    fake_post.assert_not_called()


def test_lookup_regions_batch_split():
    """超过 100 个 IP 自动分批"""
    hosts = [f'1.{i // 250}.{i % 250}.1' for i in range(201)]
    with mock.patch.object(httpx.Client, 'post',
                          return_value=mock.Mock(raise_for_status=lambda: None, json=lambda: [])) as fake_post:
        geo_lookup.lookup_regions(hosts, httpx.Client())
    assert fake_post.call_count == 3  # 201 个 → 100+100+1 = 3 批


def test_lookup_regions_http_error_degrades():
    """批量查询 HTTP 错误时整体降级为空映射，不抛异常"""
    with mock.patch.object(httpx.Client, 'post', side_effect=httpx.ConnectError('连接失败')):
        result = geo_lookup.lookup_regions(['8.8.8.8'], httpx.Client())
    assert result == {}