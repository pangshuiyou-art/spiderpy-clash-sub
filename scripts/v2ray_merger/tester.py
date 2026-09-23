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
- 启动前用 `mihomo -t` 预校验配置，逐个剔除内核不接受的节点（源站曾出现乱码 cipher），
  避免"一颗坏节点让内核启动即退出、整轮测活被跳过"；
- 内核运行输出落盘（mihomo_runtime.log），启动失败时把日志尾部带入异常信息便于定位；
- 停止按"句柄 → pid 文件 → 端口监听反查"三层清理，杜绝僵尸进程残留。

依赖：mihomo 二进制（由调用方下载/传入路径），本模块不负责下载。
"""

import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import httpx

import yaml_dumper

TEST_URL = 'https://www.gstatic.com/generate_204'
DELAY_TIMEOUT_MS = 5000
_READY_POLL_SEC = 0.5
_READY_MAX_WAIT_SEC = 90
_TEST_CONCURRENCY = 8
_PORT_PROBE_BASE = 20000
_PORT_PROBE_SPAN = 2048
_CONFIG_TEST_TIMEOUT_SEC = 120
_MAX_CONFIG_REPAIRS = 10

# 延迟分桶（左闭右开），用于日志输出分布、辅助人工确定存活阈值
_DELAY_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ('<500ms', 0, 500),
    ('500-1000ms', 500, 1000),
    ('1000-1500ms', 1000, 1500),
    ('1500-2000ms', 1500, 2000),
    ('2000-3000ms', 2000, 3000),
    ('3000-5000ms', 3000, 5000),
)

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
    config_path = working_dir / 'config.yaml'
    config_path.write_text(yaml_dumper.dump_yaml(config), encoding='utf-8')
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


def _run_config_test(mihomo_bin: str, working_dir: Path) -> Optional[str]:
    """用 mihomo -t 预校验配置：通过返回 None，失败返回内核错误输出"""
    try:
        result = subprocess.run(
            [mihomo_bin, '-t', '-d', str(working_dir)],
            capture_output=True,
            text=True,
            timeout=_CONFIG_TEST_TIMEOUT_SEC,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f'配置校验执行失败: {exc}'
    if result.returncode == 0:
        return None
    return (result.stdout or '') + (result.stderr or '')


def _read_log_tail(log_path: Path, max_chars: int = 400) -> str:
    """读取内核运行日志尾部，用于诊断启动失败原因"""
    try:
        text = log_path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '(无法读取内核日志)'
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return ' | '.join(lines[-5:])[:max_chars] or '(内核无输出)'


def _drop_invalid_node(candidates: list[dict], error_output: str) -> Optional[dict]:
    """依据内核报错剔除不被接受的节点，返回被剔除的节点

    优先用报错里的 server:port 精确定位；报错不含地址时（如
    `proxy 758: invalid REALITY short ID`）退回用编号定位，
    该编号实测为 proxies 列表的 0 基索引。
    """
    server_port = re.search(r'proxy \d+: \S+ ([\w.\-]+:\d+)', error_output)
    if server_port is not None:
        target = server_port.group(1)
        for index, node in enumerate(candidates):
            if f"{node.get('server')}:{node.get('port')}" == target:
                return candidates.pop(index)
        return None

    index_match = re.search(r'proxy (\d+):', error_output)
    if index_match is None:
        return None
    index = int(index_match.group(1))
    if 0 <= index < len(candidates):
        return candidates.pop(index)
    return None


def _prepare_valid_config(mihomo_bin: str, working_dir: Path,
                          candidates: list[dict], controller_addr: str) -> Optional[str]:
    """循环预校验配置并剔除内核不接受的节点，返回最终错误信息（通过为 None）

    源站节点质量不可控（曾出现乱码 cipher 导致内核启动即退出），故在内核启动前
    先做一次 -t 校验，逐个剔除问题节点，避免"一颗坏节点废掉整轮测活"。
    """
    for _ in range(_MAX_CONFIG_REPAIRS + 1):
        _build_temp_config(working_dir, candidates, controller_addr)
        error_output = _run_config_test(mihomo_bin, working_dir)
        if error_output is None:
            return None
        dropped = _drop_invalid_node(candidates, error_output)
        if dropped is None:
            return error_output.strip()[-400:]
        print(f"[!] 剔除内核不接受的节点: {dropped.get('name')} "
              f"({dropped.get('server')}:{dropped.get('port')})")
    return f'连续剔除 {_MAX_CONFIG_REPAIRS} 个节点后配置仍不合法'


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

    流程：随机端口 → 端口预检 → 配置预校验（剔除内核不接受的节点）
    → 启动内核 → 等就绪 + 归属校验 → 并发测活 → 三层清理。

    被剔除的节点没有延迟值，调用方按"无 delay"自然过滤，不进入产物。
    """
    controller_port = _pick_free_port()
    controller_addr = f'127.0.0.1:{controller_port}'
    controller_base = f'http://{controller_addr}'

    port_error = _ensure_port_free(controller_port)
    if port_error:
        raise RuntimeError(port_error)

    log_path = working_dir / 'mihomo_runtime.log'
    log_handle = open(log_path, 'w', encoding='utf-8', errors='replace')
    process: Optional[subprocess.Popen] = None
    try:
        candidates = list(nodes)
        config_error = _prepare_valid_config(mihomo_bin, working_dir, candidates, controller_addr)
        if config_error is not None:
            raise RuntimeError(f'mihomo 配置校验未通过: {config_error}')

        process = subprocess.Popen(
            [mihomo_bin, '-d', str(working_dir)],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
        )
        with _direct_client(controller_base) as client:
            if not _wait_controller_ready(client, controller_base):
                log_handle.flush()
                raise RuntimeError(
                    f'mihomo controller 未在限定时间内就绪'
                    f'（内核输出: {_read_log_tail(log_path)}）'
                )
            if not _listener_includes(controller_port, process.pid):
                raise RuntimeError('controller 端口被旧 mihomo 实例顶包应答（假活）')

            alive: dict[str, int] = {}
            with ThreadPoolExecutor(max_workers=_TEST_CONCURRENCY) as executor:
                futures = {executor.submit(_test_one, client, controller_base, node['name']): node['name']
                           for node in candidates}
                for future in as_completed(futures):
                    name = futures[future]
                    delay = future.result()
                    if delay is not None:
                        alive[name] = delay
            return alive
    finally:
        # 三层清理：进程句柄 → 端口监听反查 → 配置文件目录
        log_handle.close()
        if process is not None:
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


def _format_delay_distribution(alive_map: dict[str, int], total: int) -> str:
    """生成延迟分桶统计文本，便于人工判断阈值该取多少"""
    counts = [0] * len(_DELAY_BUCKETS)
    for delay in alive_map.values():
        for index, (_, low, high) in enumerate(_DELAY_BUCKETS):
            if low <= delay < high:
                counts[index] += 1
                break
    parts = [f'{name}: {count}' for (name, _, _), count in zip(_DELAY_BUCKETS, counts)]
    parts.append(f'未测出(超时/失败): {total - len(alive_map)}')
    return ' | '.join(parts)


def filter_alive(nodes: list[dict], mihomo_bin: str, working_dir: Path,
                 max_delay_ms: Optional[int]) -> list[dict]:
    """测活并按最大延迟过滤，返回存活节点（附带 delay 字段）"""
    alive_map = test_nodes(nodes, mihomo_bin, working_dir)
    print(f'[*] 延迟分布: {_format_delay_distribution(alive_map, len(nodes))}')
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