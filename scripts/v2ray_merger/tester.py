#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基于 mihomo 内核的真实协议级测活

在 GitHub Actions 上：下载 mihomo 二进制 → 写入临时 Clash 配置 → 启动内核
→ 通过其本地 REST API 对每个节点触发真实延迟测试，只保留 protocol 有效节点。

可靠性设计（吸取 any-auto-register 代理中心 mihomo 管理实战教训）：
- external-controller 用随机空闲端口，避免与本机其它 mihomo/Clash 实例端口冲突；
- 访问 127.0.0.1 controller 必须直连（禁环境代理），否则经代理访问本地会超时/404；
- 启动前端口预检：被残留 mihomo 占用则强制回收；被非 mihomo 占用则直接报错；
- 启动后归属校验：controller 应答者必须包含本次新进程 PID，防旧实例顶包"假活"；
- 停止按"句柄 → pid 文件 → 端口监听反查"三层清理，杜绝僵尸进程残留。

依赖：mihomo 二进制（由调用方下载/传入路径），本模块不负责下载。
"""

import platform
import shutil
import socket
import subprocess
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import httpx

TEST_URL = 'https://www.gstatic.com/generate_204'
DELAY_TIMEOUT_MS = 5000
_READY_POLL_SEC = 0.5
_READY_MAX_WAIT_SEC = 90
_TEST_CONCURRENCY = 8
_PORT_PROBE_BASE = 20000
_PORT_PROBE_SPAN = 2048

_IS_WINDOWS = platform.system().lower().startswith('win')


def _pick_free_port() -> int:
    """随机探测一个空闲本地端口，避免与既有 mihomo/Clash 冲突"""
    for candidate in range(_PORT_PROBE_BASE, _PORT_PROBE_BASE + _PORT_PROBE_SPAN):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('127.0.0.1', candidate))
            return candidate
        except OSError:
            continue
        finally:
            sock.close()
    raise RuntimeError('无法找到空闲本地端口')


def _tcp_listener_pids(port: int) -> set[int]:
    """返回监听指定端口的 PID 集合；非 Windows 或查询失败返回空集合（宽松处理）"""
    if not _IS_WINDOWS:
        return set()
    try:
        output = subprocess.run(
            ['netstat', '-ano', '-p', 'tcp'], capture_output=True, text=True, timeout=8,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    result: set[int] = set()
    for line in (output.stdout or '').splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != 'TCP' or parts[3].upper() != 'LISTENING':
            continue
        try:
            port_text = parts[1].rsplit(':', 1)[-1]
            pid_text = parts[-1]
        except IndexError:
            continue
        if port_text.isdigit() and pid_text.isdigit() and int(port_text) == port:
            result.add(int(pid_text))
    return result


def _pid_is_mihomo(pid: int) -> bool:
    """验证 PID 对应进程仍为 mihomo，避免误杀无关进程"""
    try:
        if _IS_WINDOWS:
            output = subprocess.run(
                ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                capture_output=True, text=True, timeout=5,
            )
            return 'mihomo' in (output.stdout or '').lower()
        exe = Path(f'/proc/{pid}/exe')
        return exe.exists() and 'mihomo' in exe.resolve().name.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def _terminate_pid(pid: int, timeout: float = 5.0) -> bool:
    """按 PID 强制终止进程（Windows taskkill /F；类 Unix 信号兜底）"""
    if pid <= 0:
        return False
    try:
        if _IS_WINDOWS:
            subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                           capture_output=True, timeout=timeout)
        else:
            import signal
            try:
                import os
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            time.sleep(0.2)
            try:
                import os
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _reclaim_port_pid(target_pid: int, controller_port: int) -> None:
    """回收残留：终止仍监听 controller 端口的 mihomo 进程（防僵尸占端口）"""
    stale_pids = {pid for pid in _tcp_listener_pids(controller_port)
                  if pid != target_pid and _pid_is_mihomo(pid)}
    for pid in stale_pids:
        _terminate_pid(pid)


def _ensure_port_free(controller_port: int, deadline_sec: float = 5.0) -> Optional[str]:
    """启动前端口预检：mihomo 残留占用则回收；非 mihomo 占用则报错"""
    deadline = time.time() + deadline_sec
    reclaimed_once = False
    while time.time() < deadline:
        holders = _tcp_listener_pids(controller_port)
        if not holders:
            return None
        external = {pid for pid in holders if not _pid_is_mihomo(pid)}
        if external:
            return f'controller 端口 {controller_port} 被非 mihomo 进程占用: PID {sorted(external)}'
        if reclaimed_once:
            raise RuntimeError(f'controller 端口 {controller_port} 被 mihomo 残留进程占用，回收失败')
        for pid in holders:
            _terminate_pid(pid)
        reclaimed_once = True
        time.sleep(0.5)
    return f'controller 端口 {controller_port} 仍被 mihomo 残留占用，回收超时'


def _listener_includes(controller_port: int, new_pid: int) -> bool:
    """校验 controller 端口监听者是否包含新进程 PID（防旧实例顶包假活）"""
    listeners = _tcp_listener_pids(controller_port)
    if not listeners:
        return True  # 查询受限时宽松放行，仅做力所能及的校验
    return new_pid in listeners


def _direct_client(base_url: str) -> httpx.Client:
    """controller 在本机，必须直连：禁用环境代理，否则经代理访问本地超时/404"""
    return httpx.Client(base_url=base_url, trust_env=False)


def _build_temp_config(working_dir: Path, nodes: list[dict], controller_addr: str) -> Path:
    """写入 mihomo 最小可用配置（全部节点 + 一条兜底策略组）"""
    config = {
        'mode': 'rule',
        'log-level': 'silent',
        'external-controller': controller_addr,
        'proxies': nodes,
        'proxy-groups': [{'name': 'ALL', 'type': 'select', 'proxies': [n['name'] for n in nodes]}],
        'rules': ['MATCH,ALL'],
    }
    import yaml
    config_path = working_dir / 'config.yaml'
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
    return config_path


def _wait_controller_ready(client: httpx.Client, controller_base: str) -> bool:
    """等待 mihomo controller 就绪"""
    deadline = time.time() + _READY_MAX_WAIT_SEC
    while time.time() < deadline:
        try:
            response = client.get(f'{controller_base}/version', timeout=2)
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(_READY_POLL_SEC)
    return False


def _test_one(client: httpx.Client, controller_base: str, name: str) -> Optional[int]:
    """触发单个节点延迟测试，返回延迟毫秒；失败返回 None"""
    encoded = urllib.parse.quote(name, safe='')
    url = f'{controller_base}/proxies/{encoded}/delay'
    try:
        response = client.get(
            url,
            params={'url': TEST_URL, 'timeout': DELAY_TIMEOUT_MS},
            timeout=DELAY_TIMEOUT_MS / 1000 + 5,
        )
        if response.status_code != 200:
            return None
        return int(response.json()['delay'])
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def test_nodes(nodes: list[dict], mihomo_bin: str, working_dir: Path) -> dict[str, int]:
    """对节点列表做真实测活，返回 {节点名: 延迟ms}（仅含通过的节点）

    流程：随机端口 → 端口预检 → 写配置 → 启动内核 → 等就绪 + 归属校验
    → 并发测活 → 三层清理。
    """
    controller_port = _pick_free_port()
    controller_addr = f'127.0.0.1:{controller_port}'
    controller_base = f'http://{controller_addr}'

    port_error = _ensure_port_free(controller_port)
    if port_error:
        raise RuntimeError(port_error)

    _build_temp_config(working_dir, nodes, controller_addr)

    process = subprocess.Popen(
        [mihomo_bin, '-d', str(working_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with _direct_client(controller_base) as client:
            if not _wait_controller_ready(client, controller_base):
                raise RuntimeError('mihomo controller 未在限定时间内就绪')
            if not _listener_includes(controller_port, process.pid):
                raise RuntimeError('controller 端口被旧 mihomo 实例顶包应答（假活）')

            alive: dict[str, int] = {}
            with ThreadPoolExecutor(max_workers=_TEST_CONCURRENCY) as executor:
                futures = {executor.submit(_test_one, client, controller_base, node['name']): node['name']
                           for node in nodes}
                for future in as_completed(futures):
                    name = futures[future]
                    delay = future.result()
                    if delay is not None:
                        alive[name] = delay
            return alive
    finally:
        # 三层清理：进程句柄 → 端口监听反查 → 配置文件目录
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=2)
                except Exception:
                    pass
        _reclaim_port_pid(process.pid, controller_port)
        shutil.rmtree(working_dir, ignore_errors=True)


def filter_alive(nodes: list[dict], mihomo_bin: str, working_dir: Path,
                 max_delay_ms: Optional[int]) -> list[dict]:
    """测活并按最大延迟过滤，返回存活节点（附带 delay 字段）"""
    alive_map = test_nodes(nodes, mihomo_bin, working_dir)
    kept: list[dict] = []
    for node in nodes:
        delay = alive_map.get(node['name'])
        if delay is None:
            continue
        if max_delay_ms is not None and delay > max_delay_ms:
            continue
        node['delay'] = delay
        kept.append(node)
    return kept