#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""基于 mihomo 内核的真实协议级测活

在 GitHub Actions 上：下载 mihomo 二进制 → 写入临时 Clash 配置 → 启动内核
→ 通过其本地 REST API 对每个节点触发真实延迟测试，只保留 protocol 有效节点。

依赖：mihomo 二进制（由 workflow 下载，路径通过构造参数传入）。
本地开发不联网测试时，调用方不用本模块即可。
"""

import json
import shutil
import subprocess
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

import httpx

TEST_URL = 'https://www.gstatic.com/generate_204'
DELAY_TIMEOUT_MS = 5000
CONTROLLER_ADDR = '127.0.0.1:19090'
CONTROLLER_BASE = f'http://{CONTROLLER_ADDR}'
_READY_POLL_SEC = 0.5
_READY_MAX_WAIT_SEC = 30
_TEST_CONCURRENCY = 8


def _build_temp_config(working_dir: Path, nodes: list[dict]) -> Path:
    """写入 mihomo 最小可用配置（全部节点 + 一条兜底策略组）"""
    config = {
        'mixed-port': 7890,
        'log-level': 'silent',
        'external-controller': CONTROLLER_ADDR,
        'proxies': nodes,
        'proxy-groups': [{'name': 'ALL', 'type': 'select', 'proxies': [n['name'] for n in nodes]}],
        'rules': ['MATCH,ALL'],
    }
    config_path = working_dir / 'config.yaml'
    import yaml
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
    return config_path


def _wait_controller_ready(client: httpx.Client) -> bool:
    """等待 mihomo controller 就绪"""
    deadline = time.time() + _READY_MAX_WAIT_SEC
    while time.time() < deadline:
        try:
            response = client.get(f'{CONTROLLER_BASE}/version', timeout=2)
            if response.status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(_READY_POLL_SEC)
    return False


def _test_one(client: httpx.Client, name: str) -> Optional[int]:
    """触发单个节点延迟测试，返回延迟毫秒；失败返回 None"""
    encoded = urllib.parse.quote(name, safe='')
    url = f'{CONTROLLER_BASE}/proxies/{encoded}/delay'
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
    """对节点列表做真实测活，返回 {节点名: 延迟ms}（仅含通过的节点）"""
    _build_temp_config(working_dir, nodes)

    process = subprocess.Popen(
        [mihomo_bin, '-d', str(working_dir)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        with httpx.Client(base_url=CONTROLLER_BASE) as client:
            if not _wait_controller_ready(client):
                raise RuntimeError('mihomo controller 未在限定时间内就绪')

            alive: dict[str, int] = {}
            with ThreadPoolExecutor(max_workers=_TEST_CONCURRENCY) as executor:
                futures = {executor.submit(_test_one, client, node['name']): node['name']
                           for node in nodes}
                for future in as_completed(futures):
                    name = futures[future]
                    delay = future.result()
                    if delay is not None:
                        alive[name] = delay
            return alive
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        # 清理生成的内核配置文件，避免残留
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