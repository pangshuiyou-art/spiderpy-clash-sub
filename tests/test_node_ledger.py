"""节点台账单元测试：读写、状态机、保留判定、稳定命名、产物集成

只覆盖纯逻辑，不发起网络请求、不启动 mihomo。
"""

import json
from datetime import datetime, timedelta, timezone

import yaml

from sub_aggregator import node_ledger as ledger
from sub_aggregator import output_builder

NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=timezone.utc)


def _entry(protocol: str = 'socks5', ip: str = '1.2.3.4', port: int = 1080,
           tier: str = 'residential', **overrides) -> dict:
    """构造一条台账条目（字段与 record_pass 产出的结构一致）"""
    entry = {
        'key': [protocol, ip, port],
        'protocol': protocol, 'ip': ip, 'port': port,
        'tier': tier,
        'first_seen': (NOW - timedelta(days=3)).isoformat(),
        'last_seen': (NOW - timedelta(days=1)).isoformat(),
        'last_verified': (NOW - timedelta(days=1)).isoformat(),
        'verified_count': 3, 'pass_streak': 3, 'fail_streak': 0,
        'best_delay_ms': 200, 'last_delay_ms': 250,
        'country_code': 'US', 'asn': 'AS100', 'isp': 'ACME',
        'ip_kind': 'residential', 'ip_confidence': 'high', 'relaxed': '',
        'sources': ['pool'], 'node_name': '[R]US-0007',
    }
    entry.update(overrides)
    return entry


def _entries(*items: dict) -> dict:
    return {tuple(item['key']): item for item in items}


# ---------------------------------------------------------------- 读写

def test_make_key_normalizes_protocol_and_falls_back_to_server():
    assert ledger.make_key({'protocol': 'SOCKS5', 'ip': '1.2.3.4', 'port': '1080'}) == \
        ('socks5', '1.2.3.4', 1080)
    # 链接型记录用 server 字段
    assert ledger.make_key({'protocol': 'vmess', 'server': '5.5.5.5', 'port': 443}) == \
        ('vmess', '5.5.5.5', 443)
    assert ledger.make_key({'protocol': '', 'ip': '', 'port': None}) == ('', '', 0)


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / 'known_nodes.jsonl'
    source = _entries(_entry(), _entry(ip='5.6.7.8', tier='daily', node_name='[D]US-0002'))

    ledger.save(path, source)
    loaded = ledger.load(path)

    assert loaded == source


def test_load_skips_corrupt_lines_and_missing_file(tmp_path):
    path = tmp_path / 'known_nodes.jsonl'
    good = _entry()
    path.write_text(
        json.dumps(good, ensure_ascii=False) + '\n'
        + '{broken json\n'
        + json.dumps({'key': ['only', 2]}) + '\n',
        encoding='utf-8')

    loaded = ledger.load(path)

    assert list(loaded) == [('socks5', '1.2.3.4', 1080)]
    assert ledger.load(tmp_path / 'missing.jsonl') == {}


# ---------------------------------------------------------------- 状态机

def test_record_pass_creates_then_strengthens_entry():
    entries: dict = {}
    record = {'protocol': 'http', 'ip': '9.9.9.9', 'port': 8080,
              'country_code': 'JP', 'asn': 'AS9', 'isp': 'NTT',
              'ip_confidence': 'high', 'sources': {'pool-a'}, 'node_name': '[R]JP-0001',
              'delay_ms': 300}

    ledger.record_pass(entries, record, 'residential', NOW)
    first = entries[('http', '9.9.9.9', 8080)]
    assert first['verified_count'] == 1 and first['pass_streak'] == 1
    assert first['best_delay_ms'] == 300 and first['node_name'] == '[R]JP-0001'
    assert first['sources'] == ['pool-a']

    record['sources'] = {'pool-a', 'pool-b'}
    record['delay_ms'] = 150
    ledger.record_pass(entries, record, 'residential', NOW + timedelta(hours=12))

    assert first['verified_count'] == 2 and first['pass_streak'] == 2
    assert first['best_delay_ms'] == 150 and first['last_delay_ms'] == 150
    assert first['sources'] == ['pool-a', 'pool-b']


def test_mark_fail_accumulates_and_pass_resets():
    entries = _entries(_entry())
    key = ('socks5', '1.2.3.4', 1080)

    ledger.mark_fail(entries, key, NOW)
    assert entries[key]['fail_streak'] == 1 and entries[key]['pass_streak'] == 0
    # 连败不动历史通过数
    assert entries[key]['verified_count'] == 3

    record = {'protocol': 'socks5', 'ip': '1.2.3.4', 'port': 1080, 'node_name': '[R]US-0007'}
    ledger.record_pass(entries, record, 'residential', NOW)
    assert entries[key]['fail_streak'] == 0 and entries[key]['pass_streak'] == 1

    # 不存在的键静默忽略
    ledger.mark_fail(entries, ('http', '0.0.0.1', 1), NOW)


def test_build_retest_records_filters_caps_and_backfills():
    fresh = _entry(ip='1.1.1.1')                                   # 在役
    stale = _entry(ip='2.2.2.2', last_verified=(NOW - timedelta(days=9)).isoformat())
    exhausted = _entry(ip='3.3.3.3', fail_streak=3)                # 已达淘汰线
    overdue = _entry(ip='4.4.4.4', last_verified=(NOW - timedelta(days=8)).isoformat())
    entries = _entries(fresh, stale, exhausted, overdue)

    records = ledger.build_retest_records(entries, NOW)

    assert [r['ip'] for r in records] == ['1.1.1.1']
    record = records[0]
    assert record['record_kind'] == 'proxypool' and record['from_ledger'] is True
    assert record['ledger_tier'] == 'residential'
    # 元数据回填：重测时无需再查 ip-api
    assert record['country_code'] == 'US' and record['asn'] == 'AS100'


def test_build_retest_records_caps_per_tier(monkeypatch):
    monkeypatch.setattr(ledger, 'RETEST_CAPS',
                        {'residential': 2, 'daily': 3, 'residential_socks4': 2})
    entries = _entries(
        *[_entry(ip=f'10.0.0.{index}', tier='daily', node_name=None,
                 last_verified=(NOW - timedelta(hours=index)).isoformat())
          for index in range(1, 6)])  # 5 条日常在役，index 越小验证越新

    records = ledger.build_retest_records(entries, NOW)

    assert len(records) == 3  # 达到 daily 上限
    # 按末次验证时间降序：最近验证的优先回炉
    assert [r['ip'] for r in records] == ['10.0.0.1', '10.0.0.2', '10.0.0.3']


def test_retention_eligibility_thresholds():
    def eligible(**overrides) -> bool:
        return ledger.is_retention_eligible(_entry(**overrides), NOW)

    assert not eligible(verified_count=2, fail_streak=1)   # 履历不足
    assert not eligible(fail_streak=0)                     # 没失败谈不上保留
    assert eligible(fail_streak=1)
    assert eligible(fail_streak=2)
    assert not eligible(fail_streak=3)                     # 达淘汰线
    assert not eligible(last_verified=(NOW - timedelta(days=8)).isoformat())
    assert not eligible(tier='mystery')                    # 未知档位


def test_retention_records_exclude_names_and_sort():
    entries = _entries(
        _entry(ip='1.1.1.1', fail_streak=1, node_name='[R]US-0009'),
        _entry(ip='2.2.2.2', fail_streak=1, node_name='[R]US-0003'),
        _entry(ip='3.3.3.3', fail_streak=1, node_name='[R]US-0005', tier='daily'),
        _entry(ip='4.4.4.4', fail_streak=0),               # 未失败，不进保留组
        _entry(ip='5.5.5.5', fail_streak=1, verified_count=1),  # 履历不足
    )

    records = ledger.retention_records(entries, 'residential', NOW,
                                       exclude_names={'[R]US-0009'})

    assert [r['node_name'] for r in records] == ['[R]US-0003']
    record = records[0]
    assert record['ip'] == '2.2.2.2' and record['port'] == 1080
    assert record['last_verified_date'] == (NOW - timedelta(days=1)).isoformat()[:10]


def test_prune_evicts_by_streak_age_and_cap():
    entries = _entries(
        _entry(ip='1.1.1.1', fail_streak=3),                        # 连败淘汰
        _entry(ip='2.2.2.2', last_verified=(NOW - timedelta(days=9)).isoformat()),  # 过期淘汰
        _entry(ip='3.3.3.3'),                                       # 在册
    )
    for index in range(4, 10):  # 凑足条目让总量裁剪真正触发
        entries[('socks5', f'9.9.9.{index}', 1)] = _entry(ip=f'9.9.9.{index}')
    original_cap = ledger.MAX_ENTRIES
    ledger.MAX_ENTRIES = 5
    try:
        stats = ledger.prune(entries, NOW)
    finally:
        ledger.MAX_ENTRIES = original_cap

    assert ('socks5', '1.1.1.1', 1080) not in entries
    assert ('socks5', '2.2.2.2', 1080) not in entries
    assert ('socks5', '3.3.3.3', 1080) in entries
    assert stats['dropped_streak'] == 1 and stats['dropped_age'] == 1
    # 9 条淘汰 2 条后剩 7 条，超限再裁 2 条
    assert stats['dropped_cap'] == 2 and stats['remaining'] == 5


# ---------------------------------------------------------------- 稳定命名

def test_assign_stable_names_reuses_ledger_name_for_same_node():
    entries = _entries(_entry(node_name='[R]US-0007'))
    records = [{'protocol': 'socks5', 'ip': '1.2.3.4', 'port': 1080, 'country_code': 'US'}]

    ledger.assign_stable_names(records, entries, 'R')

    assert records[0]['node_name'] == '[R]US-0007'


def test_assign_stable_names_reallocates_when_tag_changes():
    """节点从日常转住宅（分类变化）：旧 [D] 名退役，按新 tag 重新分配"""
    entries = _entries(_entry(node_name='[D]US-0002'))
    records = [{'protocol': 'socks5', 'ip': '1.2.3.4', 'port': 1080, 'country_code': 'US'}]

    ledger.assign_stable_names(records, entries, 'R')

    assert records[0]['node_name'].startswith('[R]US-')
    assert records[0]['node_name'] != '[D]US-0002'


def test_assign_stable_names_fresh_nodes_continue_after_global_max():
    entries = _entries(
        _entry(ip='1.1.1.1', node_name='[R]US-0042'),
        _entry(ip='2.2.2.2', tier='daily', node_name='[D]JP-0100'),
    )
    records = [{'protocol': 'http', 'ip': '3.3.3.3', 'port': 80, 'country_code': 'DE'}]

    ledger.assign_stable_names(records, entries, 'R')

    assert records[0]['node_name'] == '[R]DE-0101'


def test_assign_stable_names_avoids_extra_names_and_duplicates():
    entries = _entries(_entry(ip='1.1.1.1', node_name='[R]US-0042'))
    records = [
        {'protocol': 'http', 'ip': '3.3.3.3', 'port': 80, 'country_code': 'DE'},
        {'protocol': 'http', 'ip': '4.4.4.4', 'port': 80, 'country_code': ''},
    ]

    # extra_names 模拟同文件里住宅组已占用的名字（跨档位防撞）
    ledger.assign_stable_names(records, entries, 'R', extra_names={'[R]XX-0043'})

    assert records[0]['node_name'] == '[R]DE-0044'
    assert records[1]['node_name'] == '[R]XX-0045'
    assert len({records[0]['node_name'], records[1]['node_name'], '[R]XX-0043'}) == 3


# ---------------------------------------------------------------- 产物集成

def test_pipeline_ledger_lifecycle_across_rounds(monkeypatch):
    """端到端小样（核心场景）：入账 → 源头消失仍被回炉且名字稳定 →
    失手进保留组 → 连败三轮退场"""
    from sub_aggregator import pipeline

    def fake_tcp(records):
        return list(records), {'alive': len(records), 'checked': len(records),
                               'input': len(records), 'skipped_private': 0}

    monkeypatch.setattr(pipeline.processor, 'tcp_prefilter', fake_tcp)

    entries: dict = {}
    key = ('http', '1.2.3.4', 8080)
    base = {'record_kind': 'proxypool', 'protocol': 'http', 'ip': '1.2.3.4',
            'port': 8080, 'country_code': 'US', 'delay_ms': 100, 'sources': {'pool'}}
    t0 = datetime(2026, 10, 6, tzinfo=timezone.utc)

    def run_round(offset_days: float, alive: bool) -> list[dict]:
        """一轮：回炉注入 →（模拟测活）→ 台账回写，返回当轮候选"""
        now = t0 + timedelta(days=offset_days)
        candidates: list[dict] = []
        sent = pipeline._inject_ledger_candidates(
            entries, False, candidates, [], [], now)
        if alive and candidates:
            ledger.assign_stable_names(candidates, entries, 'R')
            candidates[0]['delay_ms'] = 100 + offset_days
        pipeline._update_ledger(
            entries, [('residential', candidates if alive else [])], sent, now)
        return candidates

    # 第 1 轮：新鲜节点通过，入账（首号从 1 开始）
    first = dict(base)
    ledger.assign_stable_names([first], entries, 'R')
    assert first['node_name'] == '[R]US-0001'
    pipeline._update_ledger(entries, [('residential', [first])], set(), t0)
    assert entries[key]['verified_count'] == 1

    # 第 2 轮：源头没再列出 → 台账回炉重考，通过，名字不变
    candidates = run_round(1, alive=True)
    assert len(candidates) == 1 and candidates[0]['from_ledger'] is True
    assert candidates[0]['node_name'] == '[R]US-0001'
    assert entries[key]['verified_count'] == 2

    # 第 3 轮：再过一次，历史通过数达到保留组门槛
    run_round(2, alive=True)
    assert entries[key]['verified_count'] == 3

    # 第 4 轮：回炉失败 → 进保留组（观察期），主组没有它
    run_round(3, alive=False)
    retention = ledger.retention_records(entries, 'residential', t0 + timedelta(days=3))
    assert [r['node_name'] for r in retention] == ['[R]US-0001']
    assert entries[key]['fail_streak'] == 1

    # 第 5 轮：再失败 → 仍在保留组（连败 2 < 淘汰线 3），且继续被回炉
    candidates = run_round(4, alive=False)
    assert len(candidates) == 1
    assert ledger.retention_records(entries, 'residential', t0 + timedelta(days=4))

    # 第 6 轮：连续第三轮失败 → 达淘汰线，从台账删除，名字退役
    candidates = run_round(5, alive=False)
    assert len(candidates) == 1
    assert key not in entries
    assert ledger.retention_records(entries, 'residential', t0 + timedelta(days=5)) == []

    # 第 7 轮：已退场的节点不再回炉
    assert run_round(6, alive=True) == []

def test_build_payload_appends_retention_group_and_passes_check(tmp_path):
    main = [
        {'record_kind': 'proxypool', 'protocol': 'http', 'ip': '1.1.1.1', 'port': 8080,
         'country_code': 'US', 'node_name': '[R]US-0001'},
    ]
    retention = [
        {'record_kind': 'proxypool', 'protocol': 'http', 'ip': '2.2.2.2', 'port': 8080,
         'country_code': 'US', 'node_name': '[R]US-0007'},
    ]

    payload = output_builder.build_payload(main, '住宅', retention_records=retention)

    names = [group['name'] for group in payload['proxy-groups']]
    assert names == ['住宅', '住宅-手动', '住宅-保留']
    # 主组只含当轮通过节点，保留组独立
    assert payload['proxy-groups'][0]['proxies'] == ['[R]US-0001']
    assert payload['proxy-groups'][2]['proxies'] == ['[R]US-0007']
    assert [p['name'] for p in payload['proxies']] == ['[R]US-0001', '[R]US-0007']

    path = tmp_path / 'residential.yaml'
    output_builder.write_subscription(path, main, '住宅', '住宅节点', ['说明'],
                                      retention_records=retention)
    assert output_builder.check_structure(path) is None


def test_build_payload_without_retention_keeps_original_groups():
    payload = output_builder.build_payload(
        [{'record_kind': 'proxypool', 'protocol': 'http', 'ip': '1.1.1.1', 'port': 8080,
          'country_code': 'US', 'node_name': '[R]US-0001'}], '住宅')

    assert [group['name'] for group in payload['proxy-groups']] == ['住宅', '住宅-手动']


def test_socks4_list_includes_retention_rows_with_verified_date(tmp_path):
    path = tmp_path / 'residential-socks4.yaml'
    main = [{'ip': '1.2.3.4', 'port': 1080, 'country_code': 'US', 'isp': 'ACME'}]
    retention = [{'ip': '5.6.7.8', 'port': 9050, 'country_code': 'JP', 'isp': '',
                  'last_verified_date': '2026-10-09'}]

    count = output_builder.write_socks4_list(path, main, rounds=3,
                                             retention_records=retention)

    assert count == 2
    assert output_builder.check_socks4_list(path) is None
    body = path.read_text(encoding='utf-8')
    assert '1.2.3.4:1080  # US ACME' in body
    assert '5.6.7.8:9050  # JP 验证:2026-10-09' in body
    assert '# 保留观察: 1 条' in body


def test_retention_payload_yaml_is_loadable_with_groups(tmp_path):
    """保留组节点必须同时出现在 proxies 里（组引用完整性）"""
    main = [{'record_kind': 'proxypool', 'protocol': 'socks5', 'ip': '1.1.1.1',
             'port': 1080, 'country_code': 'US', 'node_name': '[D]US-0001'}]
    retention = [{'record_kind': 'proxypool', 'protocol': 'socks5', 'ip': '2.2.2.2',
                  'port': 1080, 'country_code': 'JP', 'node_name': '[D]JP-0002'}]
    path = tmp_path / 'daily.yaml'
    output_builder.write_subscription(path, main, '日常', '日常节点', ['说明'],
                                      with_country_groups=True,
                                      retention_records=retention)

    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    group_names = [group['name'] for group in data['proxy-groups']]

    assert '日常-保留' in group_names
    assert output_builder.check_structure(path) is None
