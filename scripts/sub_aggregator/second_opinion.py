#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""proxycheck.io 第二意见：住宅判定的独立交叉复核

定位：ip-api 是主判定（hosting 字段），本模块用独立数据源对住宅候选做
类型级交叉复核，拦截同源盲区。v3 接口 network.type 取值
Residential / Business / Wireless / Hosting / null。

降级规则（2026-10-11 用住宅组 48 IP 标定后确定）：
- **只降 Hosting 类**：type='Hosting' 或 detections.hosting=True；
- Business 不降——标定显示它覆盖大量消费级 ISP 线路（中国联通家庭宽带、
  韩国电信、Viettel 等都是 Business），按字面降级会误杀 73% 节点；
- 两源互补实证：proxycheck 标 Hosting 的 6 个（Beget/Timeweb/Atlantic.net
  等真托管商）ip-api 都说 hosting=False；而 ip-api 标 hosting=True 的
  Uzbektelecom proxycheck 判 Business——单源各有盲区，这正是第二意见的价值。

配额：免费注册 1,000 次/天，每 IP 计 1 次；批量实测上限 10 IP/批
（25 个/批返回 HTTP 400）。预算由调用方控制（--proxycheck-budget）。
"""

import time

import httpx

API_URL = 'https://proxycheck.io/v3/'
BATCH_SIZE = 10
CHUNK_PAUSE_SEC = 2

# v3 响应里的非 IP 键
_META_KEYS = {'status', 'node', 'time', 'query_time'}


def query_types(ips: list[str], token: str, client: httpx.Client,
                batch_size: int = BATCH_SIZE) -> dict[str, dict]:
    """批量查询 IP 类型，返回 {ip: {'type', 'hosting', 'proxy', 'risk'}}（值可能为 None）

    单批失败只丢该批，不影响其余（失败 IP 由调用方按"无意见"处理）。
    """
    opinions: dict[str, dict] = {}
    unique = sorted({ip for ip in ips if ip})
    for index in range(0, len(unique), batch_size):
        chunk = unique[index:index + batch_size]
        data: dict = {}
        try:
            # 鉴权参数名是 key=（不是 token=）。曾因写错参数名导致云端运行器
            # 以未注册身份访问——来源是数据中心 IP 直接被 403 拒绝。
            response = client.get(API_URL + ','.join(chunk), params={
                'key': token, 'vpn': 1, 'risk': 1,
            }, timeout=30)
            if response.status_code != 200:
                # denied/error 的正文里带原因（配额耗尽 / 来源被封锁 / 参数错误）
                print(f'[!] proxycheck 批查询 HTTP {response.status_code}'
                      f'（{chunk[0]}...）: {response.text[:160]}')
            else:
                data = response.json()
        except Exception as exc:
            print(f'[!] proxycheck 批查询失败（{chunk[0]}...）: {exc}')
            data = {}
        for key, value in data.items():
            if key in _META_KEYS or not isinstance(value, dict):
                continue
            network = value.get('network') or {}
            detections = value.get('detections') or {}
            opinions[key] = {
                'type': network.get('type'),
                'hosting': detections.get('hosting'),
                'proxy': detections.get('proxy'),
                'risk': detections.get('risk'),
            }
        if index + batch_size < len(unique):
            time.sleep(CHUNK_PAUSE_SEC)
    return opinions


def is_hosting(opinion: dict) -> bool:
    """proxycheck 是否判定为托管/机房（type=Hosting 或 detections.hosting=True）"""
    if str(opinion.get('type') or '').strip().lower() == 'hosting':
        return True
    return opinion.get('hosting') is True


def apply_residential_verdict(records: list[dict], opinions: dict[str, dict]) -> dict:
    """按第二意见就地改判：proxycheck 判托管的住宅记录降级为机房（高置信）

    只动 ip_kind='residential' 的记录；无意见或无结论的维持现状。
    """
    stats = {'checked': 0, 'demoted': 0, 'kept': 0, 'no_opinion': 0}
    for record in records:
        if record.get('ip_kind') != 'residential':
            continue
        opinion = opinions.get(str(record.get('ip') or ''))
        if not opinion:
            stats['no_opinion'] += 1
            continue
        stats['checked'] += 1
        if is_hosting(opinion):
            ptype = str(opinion.get('type') or '') or 'detections.hosting'
            record['ip_kind'] = 'datacenter'
            record['ip_confidence'] = 'high'
            record['second_opinion'] = f'demoted:{ptype}'
            stats['demoted'] += 1
        else:
            stats['kept'] += 1
    return stats
