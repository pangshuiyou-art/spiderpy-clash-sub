#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""socks4 代理测活（纯 Python 实现）

为何需要独立实现：mihomo 内核不支持 socks4（实测 `proxy 0: unsupport proxy type: socks4`），
而住宅 IP 里 socks4 占比很高（本项目实测约占住宅候选 28%），
直接丢弃会浪费大批有价值的节点，故这里用协议层实现补齐。

SOCKS4 协议（RFC 1928 的前身）极简：
  请求: VN(4) CD(1=CONNECT) DSTPORT(2) DSTIP(4) USERID(变长, \0 结尾)
  应答: VN(0) CD(90=成功) DSTPORT(2) DSTIP(4)

测活方式与 mihomo 路径保持一致：向目标 HTTPS 站点发起 CONNECT，
只有真正能建连并完成 TLS 握手的代理才算存活（历史教训：明文 HTTP 全过、
HTTPS 隧道全挂的代理等于没用）。
"""

import socket
import ssl
import struct
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

# 与 mihomo 路径保持同一测活目标
TEST_HOST = 'www.gstatic.com'
TEST_PORT = 443
SOCKS4_CONNECT_TIMEOUT_SEC = 6.0
SOCKS4_WORKERS = 64


def _read_exact(sock: socket.socket, count: int) -> bytes:
    """按长度读满，连接中断抛异常"""
    buffer = b''
    while len(buffer) < count:
        chunk = sock.recv(count - len(buffer))
        if not chunk:
            raise ConnectionError('连接被对端关闭')
        buffer += chunk
    return buffer


def socks4_connect(proxy_host: str, proxy_port: int, dest_host: str,
                   dest_port: int, timeout: float = SOCKS4_CONNECT_TIMEOUT_SEC) -> socket.socket:
    """通过 socks4 代理与目标建立 TCP 连接，返回已连接的 socket

    注意：SOCKS4 只支持 IPv4 地址，域名需由本地解析后再发 IP。
    """
    dest_ip = socket.gethostbyname(dest_host)
    packet = struct.pack('!BBH', 0x04, 0x01, dest_port)
    packet += socket.inet_aton(dest_ip)
    packet += b'\x00'  # 空 USERID

    sock = socket.create_connection((proxy_host, proxy_port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(packet)
        reply = _read_exact(sock, 8)
        if reply[0] != 0x00:
            raise ConnectionError(f'SOCKS4 应答版本异常: {reply[0]}')
        if reply[1] != 0x5A:
            raise ConnectionError(f'SOCKS4 连接被拒: CD=0x{reply[1]:02X}')
        return sock
    except Exception:
        sock.close()
        raise


def probe_one(record: dict, timeout: float = SOCKS4_CONNECT_TIMEOUT_SEC) -> Optional[float]:
    """测活单条 socks4 记录，返回握手耗时（毫秒）；失败返回 None"""
    import time

    started = time.time()
    sock = None
    try:
        sock = socks4_connect(record['ip'], int(record['port']), TEST_HOST, TEST_PORT, timeout)
        # 完成 TLS 握手才算真的可用（只有 TCP 通不代表隧道可用）
        context = ssl.create_default_context()
        with context.wrap_socket(sock, server_hostname=TEST_HOST) as tls_sock:
            tls_sock.settimeout(timeout)
            tls_sock.do_handshake()
        return (time.time() - started) * 1000
    except Exception:
        return None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def test_records_multi_round(records: list[dict], rounds: int = 2,
                             label: str = 'socks4组') -> tuple[list[dict], dict]:
    """多轮测活：连续通过 rounds 轮才保留，语义与 mihomo 路径一致"""
    if not records:
        return [], {'rounds': rounds, 'input': 0, 'alive': 0, 'per_round': []}

    survivors = list(records)
    per_round: list[int] = []
    last_delay: dict[tuple[str, int], float] = {}

    for round_index in range(1, rounds + 1):
        alive: dict[tuple[str, int], float] = {}
        with ThreadPoolExecutor(max_workers=SOCKS4_WORKERS) as executor:
            futures = {executor.submit(probe_one, record): record for record in survivors}
            for future in as_completed(futures):
                record = futures[future]
                try:
                    delay = future.result()
                except Exception:
                    delay = None
                if delay is not None:
                    alive[(record['ip'], record['port'])] = delay
        per_round.append(len(alive))
        last_delay = alive
        print(f'[*] {label} 第 {round_index}/{rounds} 轮: {len(alive)}/{len(survivors)} 存活')
        survivors = [r for r in survivors if (r['ip'], r['port']) in alive]
        if not survivors:
            break

    kept: list[dict] = []
    for record in survivors:
        record['delay_ms'] = int(last_delay.get((record['ip'], record['port']), 0))
        record['rounds_passed'] = rounds
        kept.append(record)

    return kept, {'rounds': rounds, 'input': len(records),
                  'alive': len(kept), 'per_round': per_round}
