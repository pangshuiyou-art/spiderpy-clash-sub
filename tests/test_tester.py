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
    config_path = tester._build_temp_config(tmp_path, _sample_nodes(), '127.0.0.1:19090')
    assert config_path.exists()
    import yaml
    data = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    assert len(data['proxies']) == 2
    assert data['external-controller'] == '127.0.0.1:19090'
    assert data['proxy-groups'][0]['name'] == 'ALL'


# ---------- 配置预校验与坏节点剔除 ----------

def test_drop_invalid_node_matches_server_port():
    """内核报错含 server:port 时，定位并剔除对应节点"""
    candidates = _sample_nodes()
    error_output = 'msg="proxy 160: ss 5.6.7.8:8388 cipher: MZi initialize error"'
    dropped = tester._drop_invalid_node(candidates, error_output)
    assert dropped is not None and dropped['name'] == 'JP-001'
    assert [node['name'] for node in candidates] == ['US-001']


def test_drop_invalid_node_unmatched_returns_none():
    """报错里定位不到节点时返回 None（交上层报错，避免误删）"""
    candidates = _sample_nodes()
    assert tester._drop_invalid_node(candidates, 'configuration file test failed') is None
    assert len(candidates) == 2


def test_drop_invalid_node_falls_back_to_index():
    """报错不含 server:port 时按 0 基编号剔除（实测 mihomo 报错编号语义）"""
    candidates = _sample_nodes()
    dropped = tester._drop_invalid_node(candidates, 'msg="proxy 1: invalid REALITY short ID"')
    assert dropped is not None and dropped['name'] == 'JP-001'
    assert [node['name'] for node in candidates] == ['US-001']


def test_drop_invalid_node_index_out_of_range_returns_none():
    """编号越界时不误删节点，返回 None 交由上层报错"""
    candidates = _sample_nodes()
    assert tester._drop_invalid_node(candidates, 'msg="proxy 99: invalid REALITY short ID"') is None
    assert len(candidates) == 2


def test_prepare_valid_config_drops_bad_node_then_passes(tmp_path):
    """首次预校验失败 → 剔除问题节点 → 再次预校验通过"""
    candidates = _sample_nodes()
    error_output = 'msg="proxy 1: ss 5.6.7.8:8388 cipher: MZi initialize error"'
    with mock.patch.object(tester, '_run_config_test', side_effect=[error_output, None]):
        result = tester._prepare_valid_config(
            '/fake/mihomo', tmp_path, candidates, '127.0.0.1:20000')
    assert result is None
    assert [node['name'] for node in candidates] == ['US-001']


def test_prepare_valid_config_undroppable_returns_error(tmp_path):
    """预校验失败且无法定位节点时返回错误文本（上层据此报错，不再空跑）"""
    with mock.patch.object(tester, '_run_config_test',
                           return_value='configuration file test failed'):
        result = tester._prepare_valid_config(
            '/fake/mihomo', tmp_path, _sample_nodes(), '127.0.0.1:20000')
    assert result is not None and 'test failed' in result


# ---------- 延迟分布统计 ----------

def test_format_delay_distribution_buckets():
    """各延迟落入正确分桶，未测出数量按总数差额计算"""
    alive = {'a': 120, 'b': 700, 'c': 1200, 'd': 1800, 'e': 2500, 'f': 4200}
    text = tester._format_delay_distribution(alive, total=10)
    assert '<500ms: 1' in text
    assert '500-1000ms: 1' in text
    assert '1000-1500ms: 1' in text
    assert '1500-2000ms: 1' in text
    assert '2000-3000ms: 1' in text
    assert '3000-5000ms: 1' in text
    assert '未测出(超时/失败): 4' in text


def test_format_delay_distribution_all_failed():
    """全部节点未测出时各桶归零"""
    text = tester._format_delay_distribution({}, total=5)
    assert '<500ms: 0' in text
    assert '未测出(超时/失败): 5' in text


# ---------- 端口预检与归属校验 ----------

@mock.patch.object(tester, '_pid_is_mihomo', return_value=True)
def test_ensure_port_free_reclaims_mihomo_stale(mock_is_mihomo):
    """端口被 mihomo 残留占用时强制回收后返回 None（回收一次后端口释放）"""
    listener_results = iter([{1234}, set()])
    with mock.patch.object(tester, '_tcp_listener_pids', side_effect=lambda *a: next(listener_results)):
        with mock.patch.object(tester, '_terminate_pid', return_value=True) as mock_kill:
            result = tester._ensure_port_free(19090)
    assert result is None
    mock_kill.assert_called_with(1234)


@mock.patch.object(tester, '_pid_is_mihomo', return_value=False)
def test_ensure_port_free_external_holder_errors(mock_is_mihomo):
    """端口被非 mihomo 进程占用时报错且不回收"""
    with mock.patch.object(tester, '_tcp_listener_pids', return_value={9999}):
        result = tester._ensure_port_free(19090)
    assert result is not None
    assert '非 mihomo' in result


def test_listener_includes_new_pid():
    with mock.patch.object(tester, '_tcp_listener_pids', return_value={100, 456}):
        assert tester._listener_includes(19090, 456) is True
        assert tester._listener_includes(19090, 789) is False


def test_listener_includes_empty_is_lenient():
    """查询受限（返回空集）时宽松放行"""
    with mock.patch.object(tester, '_tcp_listener_pids', return_value=set()):
        assert tester._listener_includes(19090, 456) is True


def test_direct_client_disables_trust_env():
    """controller 访问必须直连（trust_env=False）"""
    base = 'http://127.0.0.1:19090'
    with mock.patch.object(tester.httpx, 'Client') as mock_cls:
        tester._direct_client(base)
    _, kwargs = mock_cls.call_args
    assert kwargs['base_url'] == base
    assert kwargs['trust_env'] is False


# ---------- controller 就绪探测 ----------

def test_wait_controller_ready_success():
    client = mock.Mock()
    client.get.return_value = mock.Mock(status_code=200)
    assert tester._wait_controller_ready(client, 'http://127.0.0.1:19090') is True


def test_wait_controller_ready_timeout():
    client = mock.Mock()
    client.get.side_effect = tester.httpx.ConnectError('denied')
    # 缩短等待时间，让测试快速结束
    with mock.patch.object(tester, '_READY_MAX_WAIT_SEC', 0.1), \
         mock.patch.object(tester, '_READY_POLL_SEC', 0.05):
        assert tester._wait_controller_ready(client, 'http://127.0.0.1:19090') is False


# ---------- 单节点延迟测试 ----------

def test_test_one_success():
    client = mock.Mock()
    client.get.return_value = mock.Mock(status_code=200, json=lambda: {'delay': 123})
    assert tester._test_one(client, 'http://127.0.0.1:19090', 'US-001') == 123


def test_test_one_failed_status():
    client = mock.Mock()
    client.get.return_value = mock.Mock(status_code=404, json=lambda: {})
    assert tester._test_one(client, 'http://127.0.0.1:19090', 'US-001') is None


def test_test_one_http_error():
    client = mock.Mock()
    client.get.side_effect = tester.httpx.ConnectError('boom')
    assert tester._test_one(client, 'http://127.0.0.1:19090', 'US-001') is None


def test_test_one_bad_json():
    client = mock.Mock()
    client.get.return_value = mock.Mock(status_code=200, json=lambda: {'wrong': 'key'})
    assert tester._test_one(client, 'http://127.0.0.1:19090', 'US-001') is None


# ---------- 编排（全 Mock 内核） ----------

def _patch_env(return_alive: bool = True) -> dict:
    """组合 mock 并返回 {fake_proc, fake_subprocess, client} 引用供断言。"""
    fake_proc = mock.MagicMock()
    fake_proc.pid = 456
    fake_proc.poll.return_value = None
    client_instance = mock.MagicMock()
    if return_alive:
        client_instance.get.side_effect = lambda url, **kw: mock.Mock(
            status_code=200, json=lambda: {'delay': 88})
    client_instance.__enter__.return_value = client_instance
    client_instance.__exit__.return_value = False
    fake_subprocess = mock.MagicMock()
    fake_subprocess.Popen = mock.MagicMock(return_value=fake_proc)
    return {
        'patches': {
            'subprocess': mock.patch.object(tester, 'subprocess', fake_subprocess),
            'port_free': mock.patch.object(tester, '_ensure_port_free',
                                           return_value=None),
            'config_ok': mock.patch.object(tester, '_prepare_valid_config',
                                           return_value=None),
            'ready': mock.patch.object(tester, '_wait_controller_ready',
                                       return_value=return_alive),
            'owner': mock.patch.object(tester, '_listener_includes',
                                       return_value=True),
            'listeners': mock.patch.object(tester, '_tcp_listener_pids',
                                           return_value=set()),
            'port_pick': mock.patch.object(tester, '_pick_free_port',
                                           return_value=20000),
            'http': mock.patch.object(tester, 'httpx', mock.MagicMock(
                Client=mock.MagicMock(return_value=client_instance))),
        },
        'fake_proc': fake_proc,
        'fake_subprocess': fake_subprocess,
        'client': client_instance,
    }


def test_test_nodes_launches_kernel_and_queries():
    """启动内核 → 等就绪 → 归属校验 → 对每个节点调 delay API → 返回存活表"""
    env = _patch_env(return_alive=True)
    for patch_obj in env['patches'].values():
        patch_obj.start()
    try:
        fake_subprocess = env['fake_subprocess']
        fake_proc = env['fake_proc']
        working_dir = Path(tempfile.mkdtemp(prefix='tester_ut_'))
        result = tester.test_nodes(_sample_nodes(), '/fake/mihomo', working_dir)

        assert fake_subprocess.Popen.call_count == 1
        assert fake_subprocess.Popen.call_args[0][0][0] == '/fake/mihomo'
        # 两个节点都应存活且带上延迟
        assert result == {'US-001': 88, 'JP-001': 88}
        fake_proc.terminate.assert_called_once()
    finally:
        for patch_obj in env['patches'].values():
            patch_obj.stop()


def test_test_nodes_controller_not_ready():
    """controller 未就绪给错，抛出 RuntimeError"""
    env = _patch_env(return_alive=False)
    for patch_obj in env['patches'].values():
        patch_obj.start()
    try:
        fake_proc = env['fake_proc']
        with pytest.raises(RuntimeError):
            working_dir = Path(tempfile.mkdtemp(prefix='tester_ut_'))
            tester.test_nodes(_sample_nodes(), '/fake/mihomo', working_dir)
    finally:
        for patch_obj in env['patches'].values():
            patch_obj.stop()
    fake_proc.terminate.assert_called_once()


def test_test_nodes_port_precheck_error():
    """端口被非 mihomo 占用时，测活直接报错且不起进程"""
    with mock.patch.object(tester, '_ensure_port_free',
                          return_value='controller 端口被非 mihomo 进程占用'), \
         mock.patch.object(tester, '_pick_free_port', return_value=20000), \
         mock.patch.object(tester, 'subprocess') as fake_subprocess:
        with pytest.raises(RuntimeError):
            working_dir = Path(tempfile.mkdtemp(prefix='tester_ut_'))
            tester.test_nodes(_sample_nodes(), '/fake/mihomo', working_dir)
    fake_subprocess.Popen.assert_not_called()


def test_test_nodes_old_instance_takeover_detected():
    """controller 应答者不是新进程（旧实例顶包）时抛错，防任务活"""
    with mock.patch.multiple(
        tester,
        subprocess=mock.MagicMock(Popen=mock.MagicMock(
            return_value=mock.MagicMock(pid=456, poll=mock.MagicMock(return_value=None)))),
        _ensure_port_free=mock.MagicMock(return_value=None),
        _prepare_valid_config=mock.MagicMock(return_value=None),
        _wait_controller_ready=mock.MagicMock(return_value=True),
        _listener_includes=mock.MagicMock(return_value=False),
        _tcp_listener_pids=mock.MagicMock(return_value=set()),
        _pick_free_port=mock.MagicMock(return_value=20000),
        httpx=mock.MagicMock(Client=mock.MagicMock(return_value=mock.MagicMock(
            __enter__=mock.MagicMock(return_value=mock.MagicMock()),
            __exit__=mock.MagicMock(return_value=False)))),
    ):
        with pytest.raises(RuntimeError):
            working_dir = Path(tempfile.mkdtemp(prefix='tester_ut_'))
            tester.test_nodes(_sample_nodes(), '/fake/mihomo', working_dir)


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