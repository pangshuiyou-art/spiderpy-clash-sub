#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""多轮 mihomo 真实测活

与 v2ray_merger/tester.py 的关系：复用其全部可靠性设计，扩展为「多轮」语义。
可靠性要点（来自项目既有实战教训，勿删）：
- external-controller 用随机空闲端口，避免与本机其它 mihomo 实例冲突；
- 访问 127.0.0.1 controller 必须直连（trust_env=False），否则经环境代理会 502/超时；
- 启动前端口预检 + 启动后归属校验，防旧实例顶包"假活"；
- 启动前 mihomo -t 预校验并逐个剔除内核不接受的节点；
- finally 三层清理，杜绝僵尸进程。

多轮测活语义：
  第 1 轮对全部候选测试；后续轮只测上一轮通过的节点。
  只有连续通过 rounds 轮的节点才被保留——这是对「单轮测活误判」的修正，
  历史教训：89ip 那批代理明文 HTTP 全过、HTTPS 隧道全挂，单轮测活等于没测。
"""

import random
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import httpx
import yaml

# 测活目标必须走 HTTPS：明文 HTTP 测活通过的代理，可能在 CONNECT 隧道下全挂
TEST_URL = 'https://www.gstatic.com/generate_204'
DELAY_TIMEOUT_MS = 3000
_READY_POLL_SEC = 0.5
_READY_MAX_WAIT_SEC = 90
_TEST_CONCURRENCY = 32
_PORT_PROBE_BASE = 20000
_PORT_PROBE_SPAN = 2048
_CONFIG_TEST_TIMEOUT_SEC = 180
_MAX_CONFIG_REPAIRS = 15


def _pick_free_port() -> int:
    """随机探测一个空闲本地端口，避免与既有 mihomo/Clash 冲突"""
    candidates = list(range(_PORT_PROBE_BASE, _PORT_PROBE_BASE + _PORT_PROBE_SPAN))
    random.shuffle(candidates)
    for candidate in candidates:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(('127.0.0.1', candidate))
            return candidate
        except OSError:
            continue
        finally:
            sock.close()
    raise RuntimeError('无法找到空闲本地端口')


def _port_holders(port: int) -> set[int]:
    """返回监听指定端口的 PID 集合（Linux 下用 ss，失败返回空集）"""
    try:
        result = subprocess.run(['ss', '-lptn', f'sport = :{port}'],
                                capture_output=True, text=True, timeout=8)
    except (OSError, subprocess.SubprocessError):
        return set()
    pids: set[int] = set()
    for match in re.finditer(r'pid=(\d+)', result.stdout or ''):
        pids.add(int(match.group(1)))
    return pids


def _is_mihomo_process(pid: int) -> bool:
    """验证 PID 对应进程是否为 mihomo"""
    try:
        exe = Path(f'/proc/{pid}/exe')
        return exe.exists() and 'mihomo' in exe.resolve().name.lower()
    except OSError:
        return False


def _ensure_port_free(port: int, deadline_sec: float = 5.0) -> Optional[str]:
    """启动前端口预检：mihomo 残留则回收，被其他进程占用则报错"""
    deadline = time.time() + deadline_sec
    reclaimed = False
    while time.time() < deadline:
        holders = _port_holders(port)
        if not holders:
            return None
        external = {pid for pid in holders if not _is_mihomo_process(pid)}
        if external:
            return f'controller 端口 {port} 被非 mihomo 进程占用: PID {sorted(external)}'
        if reclaimed:
            return f'controller 端口 {port} 被 mihomo 残留进程占用，回收失败'
        for pid in holders:
            try:
                subprocess.run(['kill', '-9', str(pid)], capture_output=True, timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        reclaimed = True
        time.sleep(0.5)
    return f'controller 端口 {port} 回收超时'


def _to_clash_proxy(record: dict, name: str) -> dict:
    """内部记录 → Clash proxy 表述（只输出内核认识的字段）"""
    return {
        'name': name,
        'type': record['protocol'],
        'server': record['ip'],
        'port': int(record['port']),
    }


def make_unique_names(records: list[dict], prefix: str = '') -> list[dict]:
    """生成全局唯一节点名：<前缀><国家>-<全局序号>

    Clash 对重名节点直接报错，故序号必须全局递增而非按国家分组递增。
    """
    items: list[dict] = []
    for index, record in enumerate(records, start=1):
        code = str(record.get('country_code') or 'XX').upper()
        name = f'{prefix}{code}-{index:04d}'
        items.append(_to_clash_proxy(record, name))
        record['node_name'] = name
    return items


def _build_config(working_dir: Path, nodes: list[dict], controller_addr: str) -> Path:
    """写入 mihomo 最小可用配置"""
    config = {
        'mode': 'rule',
        'log-level': 'silent',
        'external-controller': controller_addr,
        'proxies': nodes,
        'proxy-groups': [{'name': 'ALL', 'type': 'select', 'proxies': [n['name'] for n in nodes]}],
        'rules': ['MATCH,ALL'],
    }
    config_path = working_dir / 'config.yaml'
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                           encoding='utf-8')
    return config_path


def _run_config_test(mihomo_bin: str, working_dir: Path) -> Optional[str]:
    """mihomo -t 预校验：通过返回 None，失败返回内核输出"""
    try:
        result = subprocess.run([mihomo_bin, '-t', '-d', str(working_dir)],
                                capture_output=True, text=True,
                                timeout=_CONFIG_TEST_TIMEOUT_SEC)
    except (OSError, subprocess.SubprocessError) as exc:
        return f'配置校验执行失败: {exc}'
    if result.returncode == 0:
        return None
    return (result.stdout or '') + (result.stderr or '')


def _drop_invalid_node(candidates: list[dict], error_output: str) -> Optional[dict]:
    """按内核报错定位并剔除不被接受的节点"""
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


def _prepare_config(mihomo_bin: str, working_dir: Path,
                    candidates: list[dict], controller_addr: str) -> Optional[str]:
    """循环校验配置并剔除问题节点"""
    for _ in range(_MAX_CONFIG_REPAIRS + 1):
        _build_config(working_dir, candidates, controller_addr)
        error_output = _run_config_test(mihomo_bin, working_dir)
        if error_output is None:
            return None
        dropped = _drop_invalid_node(candidates, error_output)
        if dropped is None:
            return error_output.strip()[-500:]
        print(f"[!] 剔除内核不接受的节点: {dropped.get('name')} "
              f"({dropped.get('server')}:{dropped.get('port')})")
    return f'连续剔除 {_MAX_CONFIG_REPAIRS} 个节点后配置仍不合法'


def _read_log_tail(log_path: Path, max_chars: int = 400) -> str:
    """读取内核日志尾部用于诊断"""
    try:
        text = log_path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return '(无法读取内核日志)'
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return ' | '.join(lines[-5:])[:max_chars] or '(内核无输出)'


def _wait_ready(client: httpx.Client, base: str) -> bool:
    """等待 controller 就绪"""
    deadline = time.time() + _READY_MAX_WAIT_SEC
    while time.time() < deadline:
        try:
            if client.get(f'{base}/version', timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(_READY_POLL_SEC)
    return False


def _test_one(client: httpx.Client, base: str, name: str) -> Optional[int]:
    """触发单节点延迟测试，返回毫秒延迟；失败返回 None"""
    try:
        response = client.get(
            f'{base}/proxies/{urllib.parse.quote(name, safe="")}/delay',
            params={'url': TEST_URL, 'timeout': DELAY_TIMEOUT_MS},
            timeout=DELAY_TIMEOUT_MS / 1000 + 5,
        )
        if response.status_code != 200:
            return None
        return int(response.json()['delay'])
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def _test_batch(nodes: list[dict], mihomo_bin: str) -> dict[str, int]:
    """启动一次内核，对一批节点做并发延迟测试

    返回 {节点名: 延迟ms}，仅含通过本轮的节点。
    """
    if not nodes:
        return {}
    port = _pick_free_port()
    controller_addr = f'127.0.0.1:{port}'
    base = f'http://{controller_addr}'
    port_error = _ensure_port_free(port)
    if port_error:
        raise RuntimeError(port_error)

    working_dir = Path(tempfile.mkdtemp(prefix='mihomo_free_proxy_'))
    log_path = working_dir / 'mihomo_runtime.log'
    log_handle = open(log_path, 'w', encoding='utf-8', errors='replace')
    process: Optional[subprocess.Popen] = None
    try:
        candidates = list(nodes)
        config_error = _prepare_config(mihomo_bin, working_dir, candidates, controller_addr)
        if config_error is not None:
            raise RuntimeError(f'mihomo 配置校验未通过: {config_error}')

        process = subprocess.Popen([mihomo_bin, '-d', str(working_dir)],
                                   stdout=log_handle, stderr=subprocess.STDOUT)
        with httpx.Client(base_url=base, trust_env=False) as client:
            if not _wait_ready(client, base):
                log_handle.flush()
                raise RuntimeError(f'mihomo controller 未就绪（内核输出: {_read_log_tail(log_path)}）')
            alive: dict[str, int] = {}
            with ThreadPoolExecutor(max_workers=_TEST_CONCURRENCY) as executor:
                futures = {executor.submit(_test_one, client, base, node['name']): node['name']
                           for node in candidates}
                for future in as_completed(futures):
                    delay = future.result()
                    if delay is not None:
                        alive[futures[future]] = delay
            return alive
    finally:
        log_handle.close()
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=2)
                except Exception:
                    pass
        shutil.rmtree(working_dir, ignore_errors=True)


def test_records_multi_round(
    records: list[dict],
    mihomo_bin: str,
    rounds: int = 2,
    label: str = '',
) -> tuple[list[dict], dict]:
    """多轮测活：连续通过 rounds 轮的节点才保留

    返回（存活记录, 统计信息）；存活记录带 delay_ms 与 rounds_passed 字段。
    """
    if not records:
        return [], {'rounds': rounds, 'input': 0, 'alive': 0, 'per_round': []}

    nodes = make_unique_names(records)
    name_to_record = {record['node_name']: record for record in records}
    survivors = nodes
    per_round: list[int] = []
    last_delay: dict[str, int] = {}

    for round_index in range(1, rounds + 1):
        alive = _test_batch(survivors, mihomo_bin)
        per_round.append(len(alive))
        last_delay = alive
        print(f'[*] {label} 第 {round_index}/{rounds} 轮: {len(alive)}/{len(survivors)} 存活')
        survivors = [node for node in survivors if node['name'] in alive]
        if not survivors:
            break

    kept: list[dict] = []
    for node in survivors:
        record = name_to_record.get(node['name'])
        if record is None:
            continue
        record['delay_ms'] = last_delay.get(node['name'])
        record['rounds_passed'] = rounds
        kept.append(record)

    stats = {
        'rounds': rounds,
        'input': len(records),
        'alive': len(kept),
        'per_round': per_round,
    }
    return kept, stats


def validate_config(path: Path, mihomo_bin: str) -> Optional[str]:
    """对产物做 mihomo -t 静态校验，返回错误文本（通过为 None）"""
    working_dir = Path(tempfile.mkdtemp(prefix='mihomo_validate_'))
    try:
        shutil.copy(path, working_dir / 'config.yaml')
        return _run_config_test(mihomo_bin, working_dir)
    finally:
        shutil.rmtree(working_dir, ignore_errors=True)
