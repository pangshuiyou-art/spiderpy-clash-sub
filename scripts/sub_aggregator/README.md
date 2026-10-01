# 订阅聚合层（sub_aggregator）

把多个免费代理池与订阅链接源聚合成两份 Clash 订阅：**住宅组**（注册场景）与**日常组**（按国家区域分组）。

本层是住宅组 / 日常组的**唯一产出方**：产物直接写 `data/free_proxy/`，与客户端既有的
`data/free_proxy/{residential,daily}.yaml` 链接一致，整合后无需改动任何订阅地址。
原 `free-proxy-sub.yml` 的 `schedule` 已停用（仅保留手动触发），避免两条工作流互相覆盖同一组文件。

## 产物

| 文件 | 内容 | 节点名 |
| --- | --- | --- |
| `data/free_proxy/residential.yaml` | 住宅组，注册场景用 | `[R]<国家>-<序号>` |
| `data/free_proxy/daily.yaml` | 日常组，含按国家拆分的子策略组 | `[D]<国家>-<序号>` |
| `data/free_proxy/residential-socks4.yaml` | 住宅 socks4 纯文本清单（非 Clash 配置） | `[R4]<国家>-<序号>` |

节点名前缀 `[R]` / `[D]` 沿用项目既有约定，下游（如 any-auto-register）按正则分流。
socks4 走独立前缀 `[R4]`，且因 mihomo/Clash 不支持该协议，清单只列 `ip:port` 供 Python/curl 使用。

## 数据流

```
代理池源 ─┬─ 去重 → 元数据传播 → 剔除扫描噪音 → 源侧粗筛
          ├─ TCP 预筛 → ip-api 补元数据
          └─ 住宅分类（CIDR 段库 + hosting + ASN 白名单）→ 放宽策略
                                                    ↓
订阅链接源 ─── 去重 → 归属地查询 ────────────────→ 拆分住宅 / 日常
                                                    ↓
                     socks4 分流（独立协议层）→ mihomo 多轮测活（住宅 3 / 日常 2 轮）
                                                    ↓
                                     命名 → 写三份产物 → 结构校验 + 内核静态校验
```

**链接型节点只进日常组**：订阅链接的 `server` 多为中转/CDN 入口，用 IP 归属判定「住宅」没有意义。

## 用法

```bash
pip install -r scripts/sub_aggregator/requirements.txt

# 正式运行（需要 mihomo 内核做真实测活）
python scripts/sub_aggregator/build_aggregated_subs.py --mihomo ./mihomo

# 离线调试：跳过 TCP 预筛与 ip-api 查询
python scripts/sub_aggregator/build_aggregated_subs.py --skip-tcp --skip-ipapi
```

常用参数：

| 参数 | 说明 |
| --- | --- |
| `--config` | 源清单路径，默认 `config/aggregator_sources.yaml` |
| `--whitelist` | 住宅 ASN 白名单，默认 `config/residential_asns.yaml` |
| `--out-dir` | 产物目录，默认 `data/subscriptions` |
| `--residential-rounds` / `--daily-rounds` | 测活轮数，默认 3 / 2 |
| `--skip-tcp` / `--skip-ipapi` | 调试开关，跳过网络密集环节 |
| `--max-residential-test` / `--max-daily-test` | 送入测活的条数上限（0 为不限） |

未提供 `--mihomo` 时脚本会跳过真实测活，并把两组产物留空——宁可不产出，也不推送未经验证的节点。

## 新增源

只需编辑 `config/aggregator_sources.yaml`，不用改代码：

```yaml
sources:
  - name: 你的源名字
    kind: link            # link = 订阅链接；proxypool = ip:port 代理池
    url: https://example.com/sub.txt
    enabled: true
```

`proxypool` 源需要额外声明 `format`（`csv` / `json` / `plain`）与 `fields` 字段映射；
`plain` 格式用 `protocol` 指定固定协议。有元数据的源走源侧粗筛，无元数据的源靠 TCP 预筛降量。

## 住宅判定

判定分三层，全部集中在 `residential.py`，放宽策略由源清单的 `residential_policy` 控制：

1. 源方标注 `ip_type=residential` 直接采信（高置信）。
2. CIDR 数据中心段库命中 → 直接排除。
3. ip-api 的 `hosting` / `mobile` 元数据 + ASN 白名单交叉判定。

放宽策略默认开启两项：移动网络按住宅处理、无结论但有 ASN 证据的条目按低置信收回住宅。
低置信条目的 ASN 进白名单的 `pending_review`，人工确认后再转正。

## 测试

```bash
python -m pytest tests/test_sub_aggregator.py -q
```

测试只覆盖纯逻辑，不发起网络请求、不启动 mihomo。
