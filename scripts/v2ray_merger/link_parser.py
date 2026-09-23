#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2ray 系协议链接解析

把 vmess/vless/ss/trojan/hysteria2/socks 等协议链接解析为统一的
Clash 节点模型 dict（可被 tester 与最终输出直接使用）。
解析失败返回 None，由调用方跳过，不中断整体。

注意：vmess:// 与 ss:// 的内层是 base64，若交给 urlparse 拆 host/path
会被 base64 中的 / + 等字符截断，因此这两类一律先做字符串级拆分。
"""

import base64
import json
import urllib.parse
from typing import Optional


def _safe_b64decode(text: str) -> bytes:
    """兼容标准 base64 与 URL-safe base64，自动补 padding"""
    candidate = text.replace('-', '+').replace('_', '/')
    padding = '=' * ((4 - len(candidate) % 4) % 4)
    return base64.b64decode(candidate + padding)


def _split_fragment(line: str) -> tuple[str, str]:
    """拆出 # 后的节点名（fragment），返回 (主串, fragment)"""
    if '#' in line:
        body, fragment = line.split('#', 1)
        return body, fragment
    return line, ''


def parse_vmess(line: str) -> Optional[dict]:
    """vmess://<base64(JSON)>[#名称]"""
    body, _ = _split_fragment(line[len('vmess://'):])
    try:
        info = json.loads(_safe_b64decode(body).decode('utf-8', 'ignore'))
    except (ValueError, json.JSONDecodeError):
        return None
    server = str(info.get('add', ''))
    try:
        port = int(info.get('port') or 0)
    except (TypeError, ValueError):
        port = 0
    if not server or not port:
        return None
    result: dict = {
        'name': str(info.get('ps') or server),
        'type': 'vmess',
        'server': server,
        'port': port,
        'uuid': str(info.get('id', '')),
        'alter_id': int(info.get('aid') or 0),
        'cipher': str(info.get('scy') or 'auto'),
    }
    network = str(info.get('net', '')).lower()
    if network == 'ws':
        result['network'] = 'ws'
        result['ws-opts'] = {
            'path': str(info.get('path', '/')),
            'headers': {'Host': str(info.get('host', ''))},
        }
    elif network == 'grpc':
        result['network'] = 'grpc'
        result['grpc-opts'] = {'grpc-service-name': str(info.get('path', ''))}
    elif network == 'h2':
        result['network'] = 'h2'
        result['h2-opts'] = {'path': str(info.get('path', '/'))}
    tls_mode = str(info.get('tls', '')).lower()
    if tls_mode == 'tls':
        result['tls'] = True
        result['servername'] = str(info.get('sni') or info.get('host') or server)
    elif tls_mode == 'reality':
        result['tls'] = True
        result['reality-opts'] = {'public-key': str(info.get('pbk', ''))}
        result['client-fingerprint'] = str(info.get('fp', 'chrome'))
    return result


def parse_vless(line: str) -> Optional[dict]:
    """vless://uuid@host:port?query[#name]（含 reality/ws 等）"""
    body, fragment = _split_fragment(line[len('vless://'):])
    if '@' not in body:
        return None
    user, target = body.split('@', 1)
    hostport, _, query = target.partition('?')
    if ':' in hostport:
        host, port_text = hostport.rsplit(':', 1)
        try:
            port = int(port_text)
        except ValueError:
            port = 0
    else:
        host, port = hostport, 0
    if not host:
        return None
    params = dict(urllib.parse.parse_qsl(query))
    result: dict = {
        'name': fragment or host,
        'type': 'vless',
        'server': host,
        'port': port,
        'uuid': urllib.parse.unquote(user),
    }
    security = params.get('security', 'none')
    if security == 'reality':
        result['tls'] = True
        result['servername'] = params.get('sni') or host
        result['flow'] = params.get('flow', '')
        result['reality-opts'] = {
            'public-key': params.get('pbk', ''),
            'short-id': params.get('sid', ''),
        }
        result['client-fingerprint'] = params.get('fp', 'chrome')
    elif security == 'tls':
        result['tls'] = True
        result['servername'] = params.get('sni') or host
    network = params.get('type', 'tcp').lower()
    if network == 'ws':
        result['network'] = 'ws'
        result['ws-opts'] = {
            'path': params.get('path', '/'),
            'headers': {'Host': params.get('host', '')},
        }
    elif network == 'grpc':
        result['network'] = 'grpc'
        result['grpc-opts'] = {'grpc-service-name': params.get('serviceName', '')}
    elif network in ('http', 'h2'):
        result['network'] = 'h2'
        result['h2-opts'] = {'path': params.get('path', '/'), 'host': [params.get('host', '')]}
    return result


def _decode_userinfo(userinfo: str) -> tuple[str, str]:
    """ss:// 的 userinfo：可能是 base64(method:pass) 或明文 method:pass"""
    text = userinfo
    try:
        decoded = _safe_b64decode(userinfo).decode('utf-8', 'ignore')
        if ':' in decoded:
            text = decoded
    except (ValueError, TypeError):
        pass
    if ':' in text:
        method, password = text.split(':', 1)
        return method, password
    return text, ''


def parse_ss(line: str) -> Optional[dict]:
    """ss:// 兼容 SIP002(userinfo base64@host) 与 legacy(整体base64) 两种写法"""
    body, fragment = _split_fragment(line[len('ss://'):])
    # 剥离 query（可能带 plugin 参数），query 出现在 @ 之后的 hostport 段
    if '@' in body:
        userinfo, target = body.split('@', 1)
        hostport, _, query_text = target.partition('?')
        if ':' not in hostport:
            return None
        host, port_text = hostport.rsplit(':', 1)
        method, password = _decode_userinfo(userinfo)
    else:
        # legacy：整段是 base64(method:pass@host:port)
        try:
            text = _safe_b64decode(body.split('?', 1)[0]).decode('utf-8', 'ignore')
        except (ValueError, TypeError):
            return None
        if '@' not in text or ':' not in text.split('@', 1)[0]:
            return None
        userinfo, hostport = text.split('@', 1)
        host, port_text = hostport.rsplit(':', 1)
        method, password = _decode_userinfo(userinfo)
    try:
        port = int(port_text)
    except (ValueError, IndexError):
        return None
    if not host or not port:
        return None
    result: dict = {
        'name': fragment or host,
        'type': 'ss',
        'server': host,
        'port': port,
        'cipher': method,
        'password': password,
    }
    params = dict(urllib.parse.parse_qsl(query_text)) if '@' in body else {}
    if params.get('plugin'):
        result['plugin'] = params['plugin']
    return result


def parse_trojan(line: str) -> Optional[dict]:
    """trojan://password@host:port?query[#name]"""
    body, fragment = _split_fragment(line[len('trojan://'):])
    if '@' not in body:
        return None
    password, target = body.split('@', 1)
    hostport, _, query = target.partition('?')
    if ':' not in hostport:
        return None
    host, port_text = hostport.rsplit(':', 1)
    try:
        port = int(port_text)
    except ValueError:
        return None
    params = dict(urllib.parse.parse_qsl(query))
    result: dict = {
        'name': fragment or host,
        'type': 'trojan',
        'server': host,
        'port': port,
        'password': urllib.parse.unquote(password),
        'sni': params.get('sni') or params.get('peer') or host,
    }
    if params.get('type') == 'ws':
        result['network'] = 'ws'
        result['ws-opts'] = {'path': params.get('path', '/'),
                             'headers': {'Host': params.get('host', '')}}
    return result


def parse_hysteria2(line: str) -> Optional[dict]:
    """hysteria2://password@host:port?query[#name]"""
    body, fragment = _split_fragment(line[len('hysteria2://'):])
    if '@' not in body:
        return None
    password, target = body.split('@', 1)
    hostport, _, query = target.partition('?')
    if ':' not in hostport:
        return None
    host, port_text = hostport.rsplit(':', 1)
    try:
        port = int(port_text)
    except ValueError:
        return None
    params = dict(urllib.parse.parse_qsl(query))
    return {
        'name': fragment or host,
        'type': 'hysteria2',
        'server': host,
        'port': port,
        'password': urllib.parse.unquote(password),
        'sni': params.get('sni') or host,
        'skip-cert-verify': params.get('insecure') == '1',
    }


def parse_socks(line: str) -> Optional[dict]:
    """socks:// 或 socks5://[user:pass@]host:port[#name]"""
    body_pos = line.find('://') + 3
    body, fragment = _split_fragment(line[body_pos:])
    target = body.split('@', 1)[-1]
    if ':' not in target:
        return None
    host, port_text = target.rsplit(':', 1)
    try:
        port = int(port_text)
    except ValueError:
        return None
    if not host or not port:
        return None
    return {
        'name': fragment or host,
        'type': 'socks5',
        'server': host,
        'port': port,
    }


_PARSERS = {
    'vmess': parse_vmess,
    'vless': parse_vless,
    'ss': parse_ss,
    'trojan': parse_trojan,
    'hysteria2': parse_hysteria2,
    'hysteria': parse_hysteria2,
    'socks': parse_socks,
    'socks5': parse_socks,
}


def parse_link(line: str) -> Optional[dict]:
    """解析单条订阅链接 → Clash 节点模型；无法识别时返回 None"""
    line = line.strip()
    if not line:
        return None
    scheme, _, _ = line.partition('://')
    scheme = scheme.lower()
    parser = _PARSERS.get(scheme)
    if parser is None:
        return None
    return parser(line)