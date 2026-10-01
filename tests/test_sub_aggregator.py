"""聚合层单元测试：源清单、归一化、住宅放宽、拆分、命名、产物结构

这些用例只覆盖纯逻辑，不发起网络请求、不启动 mihomo。
"""

import base64
import json

import pytest
import yaml

from sub_aggregator import naming, normalizers, output_builder, pipeline, residential, source_registry


def _vmess_link(server: str = '1.2.3.4', port: int = 443) -> str:
    """构造一条最小可用的 vmess 订阅链接"""
    payload = {
        'v': '2', 'ps': 'seed', 'add': server, 'port': str(port), 'id': 'b831381d-6324-4d53-ad4f-8cda48b30811',
        'aid': '0', 'scy': 'auto', 'net': 'ws', 'path': '/ws', 'host': 'example.com', 'tls': 'tls',
    }
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
    return f'vmess://{body}'


# ---------------------------------------------------------------- 源清单

def test_load_registry_reads_kinds_and_policy(tmp_path):
    path = tmp_path / 'sources.yaml'
    path.write_text(yaml.safe_dump({
        'sources': [
            {'name': 'pool', 'kind': 'proxypool', 'url': 'https://x/1', 'enabled': True},
            {'name': 'links', 'kind': 'link', 'url': 'https://x/2', 'enabled': True},
            {'name': 'off', 'kind': 'link', 'url': 'https://x/3', 'enabled': False},
            {'name': 'no-url', 'kind': 'link', 'enabled': True},
        ],
        'auxiliary': [{'name': 'cidr', 'url': 'https://x/c', 'type': 'cidr_blacklist'}],
        'residential_policy': {'mobile_as_residential': False},
    }, allow_unicode=True), encoding='utf-8')

    sources, auxiliary, policy = source_registry.load_registry(path)

    # 关闭与缺 url 的源都要被过滤
    assert [s['name'] for s in sources] == ['pool', 'links']
    assert len(auxiliary) == 1
    # 未在配置里写的策略项保持默认值
    assert policy['mobile_as_residential'] is False
    assert policy['unknown_with_asn_as_residential'] is True
    assert source_registry.split_by_kind(sources) == {
        'proxypool': [sources[0]], 'link': [sources[1]],
    }


def test_split_by_kind_rejects_unknown_kind():
    with pytest.raises(ValueError, match='kind 非法'):
        source_registry.split_by_kind([{'name': 'x', 'kind': 'mystery'}])


def test_default_config_is_valid():
    """仓库内真实配置必须能解析，且两类源都存在（防止后续误改）"""
    sources, auxiliary, _ = source_registry.load_registry(
        source_registry.Path(__file__).resolve().parent.parent / 'config' / 'aggregator_sources.yaml')
    grouped = source_registry.split_by_kind(sources)
    assert grouped['proxypool'] and grouped['link']
    assert any(item.get('type') == 'cidr_blacklist' for item in auxiliary)


def test_aggregated_layer_owns_free_proxy_output_dir():
    """整合后住宅组/日常组由聚合层直接写 data/free_proxy/（客户端链接不变）"""
    assert pipeline.DEFAULT_OUT_DIR.name == 'free_proxy'
    assert pipeline.DEFAULT_OUT_DIR.parent.name == 'data'


# ---------------------------------------------------------------- 归一化

def test_normalize_link_maps_records_for_clash_and_testing():
    records = normalizers.normalize_link(_vmess_link(), {'name': 'seed'})

    assert len(records) == 1
    record = records[0]
    assert record['record_kind'] == 'link'
    assert record['protocol'] == 'vmess'
    assert record['type'] == 'vmess'
    # ip 与 server 同步，测活与命名逻辑无需为链接型分支
    assert record['ip'] == record['server'] == '1.2.3.4'
    assert record['port'] == 443
    assert record['sources'] == {'seed'}


def test_normalize_link_skips_unparsable_and_bad_ports():
    assert normalizers.normalize_link('not-a-link', {'name': 'seed'}) == []
    assert normalizers.normalize_link('', {'name': 'seed'}) == []


def test_normalize_proxypool_tags_source_kind():
    records = normalizers.normalize_proxypool(
        '1.2.3.4:8080\n', {'name': 'pool', 'format': 'plain', 'protocol': 'http'})

    assert len(records) == 1
    assert records[0]['record_kind'] == 'proxypool'
    assert records[0]['sources'] == {'pool'}


# ---------------------------------------------------------------- 住宅放宽

def test_relax_promotes_mobile_and_unknown_but_blocks_datacenter_asn():
    records = [
        # 移动网络：ip-api 常标 hosting=True，放宽后收回住宅
        {'ip': '10.0.0.1', 'asn': 'AS100', 'ip_kind': 'datacenter'},
        # 未知但有 ASN 证据
        {'ip': '10.0.0.2', 'asn': 'AS200', 'ip_kind': 'unknown'},
        # 本轮 ip-api 明确判为机房的 ASN：不得被放宽
        {'ip': '10.0.0.3', 'asn': 'AS300', 'ip_kind': 'datacenter'},
        # 源方明确标注：可信，不参与放宽
        {'ip': '10.0.0.4', 'asn': 'AS400', 'ip_kind': 'datacenter', 'ip_type': 'datacenter'},
        # 已是住宅：不动
        {'ip': '10.0.0.5', 'asn': 'AS500', 'ip_kind': 'residential', 'ip_confidence': 'high'},
    ]
    metadata = {
        '10.0.0.1': {'mobile': True, 'hosting': True, 'asn': 'AS100'},
        '10.0.0.3': {'hosting': True, 'asn': 'AS300'},
    }
    policy = {'mobile_as_residential': True,
              'unknown_with_asn_as_residential': True,
              'exclude_datacenter_asns': True}

    stats = residential.relax_residential(records, metadata, policy)

    assert stats == {'by_mobile': 1, 'by_unknown_asn': 1, 'blocked_datacenter_asn': 1}
    assert (records[0]['ip_kind'], records[0]['ip_confidence']) == ('residential', 'medium')
    assert records[0]['relaxed'] == 'mobile'
    assert (records[1]['ip_kind'], records[1]['ip_confidence']) == ('residential', 'low')
    assert records[2]['ip_kind'] == 'datacenter'          # 机房 ASN 被拦下
    assert records[3]['ip_kind'] == 'datacenter'          # 源方标注优先
    assert records[4]['ip_confidence'] == 'high'          # 原住宅状态不变


def test_relax_respects_disabled_policy():
    records = [{'ip': '10.0.0.1', 'asn': 'AS100', 'ip_kind': 'datacenter'}]
    policy = {'mobile_as_residential': False,
              'unknown_with_asn_as_residential': False,
              'exclude_datacenter_asns': True}

    stats = residential.relax_residential(records, {'10.0.0.1': {'mobile': True}}, policy)

    assert stats == {'by_mobile': 0, 'by_unknown_asn': 0, 'blocked_datacenter_asn': 0}
    assert records[0]['ip_kind'] == 'datacenter'


def test_split_records_separates_kinds_and_keeps_residential_socks4():
    records = [
        {'protocol': 'http', 'ip_kind': 'residential'},
        {'protocol': 'socks5', 'ip_kind': 'datacenter'},
        {'protocol': 'vmess', 'ip_kind': 'unknown'},
        {'protocol': 'socks4', 'ip_kind': 'residential'},   # Clash 不支持，单独成组
        {'protocol': 'socks4', 'ip_kind': 'datacenter'},    # 机房 socks4 无价值，丢弃
    ]

    groups = residential.split_records(records)

    assert [r['protocol'] for r in groups['residential']] == ['http']
    # 日常组不含住宅条目（用户决策：两组不重复）
    assert [r['protocol'] for r in groups['daily']] == ['socks5', 'vmess']
    # 住宅 socks4 保留给非 Clash 场景（Python/curl）
    assert [r['ip_kind'] for r in groups['residential_socks4']] == ['residential']


def test_socks4_list_round_trip(tmp_path):
    """socks4 清单可写出且通过自检（格式沿用既有线）"""
    path = tmp_path / 'residential-socks4.yaml'
    records = [
        {'ip': '1.2.3.4', 'port': 1080, 'country_code': 'US', 'isp': 'ACME'},
        {'ip': '5.6.7.8', 'port': 9050, 'country_code': '', 'isp': ''},
    ]

    count = output_builder.write_socks4_list(path, records, rounds=3)

    assert count == 2
    assert output_builder.check_socks4_list(path) is None
    body = path.read_text(encoding='utf-8')
    assert '1.2.3.4:1080  # US ACME' in body
    assert '5.6.7.8:9050' in body


def test_socks4_list_check_rejects_bad_endpoint(tmp_path):
    path = tmp_path / 'bad.yaml'
    path.write_text('# 头\n1.2.3.4:abc\n', encoding='utf-8')

    problem = output_builder.check_socks4_list(path)

    assert problem is not None and '非法' in problem


# ---------------------------------------------------------------- 命名与分组

def test_assign_node_names_is_globally_unique_and_keeps_tag_prefix():
    records = [
        {'country_code': 'us'}, {'country_code': 'US'}, {'country_code': None},
    ]

    naming.assign_node_names(records, 'D')

    names = [r['node_name'] for r in records]
    assert names == ['[D]US-0001', '[D]US-0002', '[D]XX-0003']
    assert len(set(names)) == len(names)
    assert records[0]['country_code'] == 'US'


def test_group_by_country_orders_by_size_then_code():
    records = [
        {'country_code': 'US', 'node_name': 'a'},
        {'country_code': 'JP', 'node_name': 'b'},
        {'country_code': 'US', 'node_name': 'c'},
        {'country_code': 'DE', 'node_name': 'd'},
    ]

    grouped = naming.group_by_country(records)

    assert grouped == [('US', ['a', 'c']), ('DE', ['d']), ('JP', ['b'])]


def test_country_label_falls_back_to_code():
    assert naming.country_label('HK') == '香港'
    assert naming.country_label('ZZ') == 'ZZ'
    assert naming.country_label('') == 'XX'


# ---------------------------------------------------------------- 产物结构

def _sample_records():
    return [
        {'record_kind': 'proxypool', 'protocol': 'http', 'ip': '1.1.1.1', 'port': 8080,
         'country_code': 'US', 'node_name': '[D]US-0001'},
        {'record_kind': 'proxypool', 'protocol': 'socks5', 'ip': '2.2.2.2', 'port': 1080,
         'country_code': 'JP', 'node_name': '[D]JP-0002'},
    ]


def test_build_payload_includes_country_groups_and_match_rule():
    payload = output_builder.build_payload(_sample_records(), '日常', with_country_groups=True)

    assert payload['rules'] == ['MATCH,日常']
    # 不写 GEOIP 规则（客户端缺 GeoIP 库时整份配置会校验失败）
    assert all('GEOIP' not in rule.upper() for rule in payload['rules'])
    group_names = [group['name'] for group in payload['proxy-groups']]
    # 每组 1 条时按国家代码升序：JP 在 US 之前
    assert group_names == ['日常', '日常-手动', '日常-日本', '日常-美国']
    for group in payload['proxy-groups']:
        assert group['proxies']


def test_build_payload_without_country_groups_keeps_two_groups():
    payload = output_builder.build_payload(_sample_records(), '住宅')
    assert [g['name'] for g in payload['proxy-groups']] == ['住宅', '住宅-手动']


def test_written_subscription_passes_structure_check(tmp_path):
    path = tmp_path / 'daily.yaml'
    output_builder.write_subscription(
        path, _sample_records(), '日常', '日常节点', ['节点数: 2'], with_country_groups=True)

    assert output_builder.check_structure(path) is None
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    assert [p['name'] for p in data['proxies']] == ['[D]US-0001', '[D]JP-0002']


def test_check_structure_detects_duplicate_names_and_dangling_members(tmp_path):
    duplicated = tmp_path / 'dup.yaml'
    duplicated.write_text(yaml.safe_dump({
        'proxies': [
            {'name': 'X', 'type': 'http', 'server': '1.1.1.1', 'port': 80},
            {'name': 'X', 'type': 'http', 'server': '2.2.2.2', 'port': 80},
        ],
        'proxy-groups': [{'name': 'G', 'type': 'select', 'proxies': ['X']}],
        'rules': ['MATCH,G'],
    }, allow_unicode=True), encoding='utf-8')
    assert '重复' in output_builder.check_structure(duplicated)

    dangling = tmp_path / 'dangling.yaml'
    dangling.write_text(yaml.safe_dump({
        'proxies': [{'name': 'X', 'type': 'http', 'server': '1.1.1.1', 'port': 80}],
        'proxy-groups': [{'name': 'G', 'type': 'select', 'proxies': ['X', 'NOPE']}],
        'rules': ['MATCH,G'],
    }, allow_unicode=True), encoding='utf-8')
    assert '不存在的节点' in output_builder.check_structure(dangling)


def test_to_clash_proxy_supports_both_record_kinds():
    pool = output_builder.to_clash_proxy(_sample_records()[0])
    assert pool == {'name': '[D]US-0001', 'type': 'http', 'server': '1.1.1.1', 'port': 8080}

    link = normalizers.normalize_link(_vmess_link('3.3.3.3', 8443), {'name': 'seed'})[0]
    link['node_name'] = '[D]US-0009'
    proxy = output_builder.to_clash_proxy(link)
    assert proxy['name'] == '[D]US-0009'
    assert proxy['type'] == 'vmess'
    assert proxy['server'] == '3.3.3.3' and proxy['port'] == 8443
    assert proxy['uuid'] == 'b831381d-6324-4d53-ad4f-8cda48b30811'
    # 内部字段不得泄漏进产物
    assert 'record_kind' not in proxy and 'sources' not in proxy


def _link_record(server: str = '5.5.5.5', port: int = 8443) -> dict:
    record = normalizers.normalize_link(_vmess_link(server, port), {'name': 'seed'})[0]
    record['node_name'] = '[D]US-0001'
    return record


def test_test_proxies_are_pure_clash_config():
    """测活用的节点配置不得携带任何内部字段（曾因 _record 泄漏违反项目既有约束）"""
    proxies = pipeline._build_test_proxies([_link_record()])

    assert len(proxies) == 1
    proxy = proxies[0]
    allowed = {'name', 'type', 'server', 'port', 'uuid', 'cipher', 'alterId', 'network',
               'tls', 'servername', 'sni', 'flow', 'skip-cert-verify', 'ws-opts',
               'grpc-opts', 'h2-opts', 'reality-opts', 'plugin', 'password'}
    assert set(proxy) <= allowed
    # 测活用临时名，避免与最终命名规则耦合
    assert proxy['name'] == 'tmp-0001'


def test_link_multi_round_maps_results_back_by_index(monkeypatch):
    """多轮测活按索引回填，且不改动传入记录本身"""
    records = [_link_record('5.5.5.5'), _link_record('6.6.6.6')]
    # 打桩内核：第 1 条存活、第 2 条失败
    calls: list[list[str]] = []

    def fake_test_nodes(nodes, _mihomo_bin, _working_dir):
        calls.append([node['name'] for node in nodes])
        return {'tmp-0001': 123}

    monkeypatch.setattr(pipeline.tester_proxy, 'test_nodes', fake_test_nodes)

    kept = pipeline._test_link_multi_round(records, 'mihomo', 2, '测试')

    assert [r['server'] for r in kept] == ['5.5.5.5']
    assert kept[0]['delay_ms'] == 123
    assert kept[0]['rounds_passed'] == 2
    # 第二轮只送存活的第 1 条
    assert calls == [['tmp-0001', 'tmp-0002'], ['tmp-0001']]
    # 原始记录不被污染
    assert all('delay_ms' not in record for record in records)
