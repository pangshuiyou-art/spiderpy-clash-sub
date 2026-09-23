#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tester 单元测试：内核启动/API 调用全部 Mock，不真实运行 mihomo"""

import tempfile
from pathlib import Path
from unittest import mock

import pytest

import tester


def _sample_nodes():
    return [
        {'name': 'US-001', 'type': 'vless', 'server': '1.2.3.4', 'port': 443},
        {'name': 'JP-001', 'type': 'ss', 'server': '5.6.7.8', 'port': 8388},
    ]


# ---------- 配置写入 ----------

def test_build_temp_config_writes_yaml(tmp_path):
    """临时配置含全部节点 + 策略组"""
    config_path = tester._build_temp_config(tmp_path, _sample_nodes())
    assert config_path.exists()
    import yaml
    data = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    assert len(data['proxies']) == 2
    assert data['external-controller'] == tester.CONTROLLER_ADDR
    assert data['proxy-groups'][0]['name'] == 'ALL'


# ---------- controller 就绪探测 ----------

def test_wait_controller_ready_success():
    client = mock.Mock()
    client.get.return_value = mock.Mock(status_code=200)
    assert tester._wait_controller_ready(client) is True


def test_wait_controller_ready_timeout():
    client = mock.Mock()
    client.get.side_effect = tester.httpx.ConnectError('denied')
    # 缩短等待时间，让测试快速结束
    with mock.patch.object(tester, '_READY_MAX_WAIT_SEC', 0.1), \
         mock.patch.object(tester, '_READY_POLL_SEC', 0.05):
        assert tester._wait_controller_ready(client) is False


# ---------- 单节点延迟测试 ----------

def test_test_one_success():
    client = mock.Mock()
    client.get.return_value = mock.Mock(status_code=200, json=lambda: {'delay': 123})
    assert tester._test_one(client, 'US-001') == 123


def test_test_one_failed_status():
    client = mock.Mock()
    client.get.return_value = mock.Mock(status_code=404, json=lambda: {})
    assert tester._test_one(client, 'US-001') is None


def test_test_one_http_error():
    client = mock.Mock()
    client.get.side_effect = tester.httpx.ConnectError('boom')
    assert tester._test_one(client, 'US-001') is None


def test_test_one_bad_json():
    client = mock.Mock()
    client.get.return_value = mock.Mock(status_code=200, json=lambda: {'wrong': 'key'})
    assert tester._test_one(client, 'US-001') is None


# ---------- 编排（全 Mock 内核） ----------

@mock.patch('tester.subprocess.Popen')
def test_test_nodes_launches_kernel_and_queries(mock_popen):
    """启动内核 → 等就绪 → 对每个节点调 delay API → 返回存活表"""
    fake_proc = mock.Mock()
    mock_popen.return_value = fake_proc

    import httpx
    client_instance = mock.Mock()
    client_instance.get.side_effect = lambda url, **kw: mock.Mock(
        status_code=200, json=lambda: {'delay': 88})

    with mock.patch('tester.httpx.Client') as mock_client_cls, \
         mock.patch.object(tester, '_wait_controller_ready', return_value=True):
        mock_client_cls.return_value.__enter__.return_value = client_instance
        working_dir = Path(tempfile.mkdtemp(prefix='tester_ut_'))
        result = tester.test_nodes(_sample_nodes(), '/fake/mihomo', working_dir)

    assert mock_popen.call_count == 1
    assert mock_popen.call_args[0][0][0] == '/fake/mihomo'
    # 两个节点都应存活且带上延迟
    assert result == {'US-001': 88, 'JP-001': 88}
    fake_proc.terminate.assert_called_once()


@mock.patch('tester.subprocess.Popen')
def test_test_nodes_controller_not_ready(mock_popen):
    """controller 未就绪给错，抛出 RuntimeError"""
    fake_proc = mock.Mock()
    mock_popen.return_value = fake_proc

    with mock.patch('tester.httpx.Client'), \
         mock.patch.object(tester, '_wait_controller_ready', return_value=False):
        with pytest.raises(RuntimeError):
            working_dir = Path(tempfile.mkdtemp(prefix='tester_ut_'))
            tester.test_nodes(_sample_nodes(), '/fake/mihomo', working_dir)
    fake_proc.terminate.assert_called_once()


def test_filter_alive_keeps_only_delay_pass():
    nodes = _sample_nodes()
    alive_map = {'US-001': 100, 'JP-001': 9999}
    with mock.patch.object(tester, 'test_nodes', return_value=alive_map):
        kept = tester.filter_alive(nodes, '/fake/mihomo', Path('mem:/tmp'), max_delay_ms=1000)
    assert len(kept) == 1
    assert kept[0]['name'] == 'US-001'
    assert kept[0]['delay'] == 100


def test_filter_alive_no_max_delay_keeps_all_alive():
    nodes = _sample_nodes()
    alive_map = {'US-001': 100, 'JP-001': 500}
    with mock.patch.object(tester, 'test_nodes', return_value=alive_map):
        kept = tester.filter_alive(nodes, '/fake/mihomo', Path('mem:/tmp'), max_delay_ms=None)
    assert len(kept) == 2