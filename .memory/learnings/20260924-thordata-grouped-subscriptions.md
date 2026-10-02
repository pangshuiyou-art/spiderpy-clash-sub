---
name: Thordata 源分组订阅（住宅/日常）
description: 把 Thordata 免费代理列表按用途筛成住宅组与日常组并生成 Clash 订阅；踩坑：socks4 与 https 类型均不被 Clash 支持需剔除/映射、GEOIP 规则校验需本地 mmdb、本机推送 GitHub 必须走 7897 代理
type: learning
created: 2026-09-24
updated: 2026-09-24
---

# Thordata 源分组订阅（住宅 / 日常）（2026-09-24）

## 背景

用户要在 `Thordata/awesome-free-proxy-list`（免费开放代理列表，自带 GeoIP/延迟档/匿名度/连续存活天数/IP 类型等元数据）之上做分组订阅：
① 住宅组（对注册 IP 有要求的场景）② 日常组（稳定、快速、安全）③ 供 `E:\any-auto-register`（mihomo 内核跑注册任务）消费。要求全部落在 GitHub，自动抓取过滤并产出订阅链接。

## 源的数据事实（已确认）

- 清单 `proxies/all.csv`（232 条）：字段 `ip,port,type,country,country_code,latency_ms,tier,anonymity,streak,asn,asn_org,ip_type,source`
- 类型分布：http 75 / **socks4 93** / socks5 60 / https 4；IP 类型：住宅 47（**其中 40 个是 socks4**）、机房、未知
- 面板 `docs/data/all.json` 与 CSV 同源（同 232 条）；面板的 fast/stable/high-anon 是同一批数据的子集视图
- 源自身每 6 小时 + 每天刷新（数据会波动：同一规则下日常组实测 49~72 个）

## 两个致命坑（内核校验抓出来的）

1. **socks4 不被 Clash/mihomo 支持**：`mihomo -t` 报 `unsupport proxy type: socks4` → 只要含一个就整份配置加载失败；源导出的 5 份 Clash 订阅（all/fast/stable/high-anon/socks5）**全都含 socks4，全都不能直接用**。
2. **`https` 也不是合法 Clash 类型**：源把「能代理 https 请求的 http 代理」标为 `type: https`，mihomo 同样报 `unsupport proxy type: https`；源自己导出 Clash 配置时把它并入 `http`（79 = 75 + 4 可印证），故我们也要映射为 `http`。

## 校验方法（可复用）

- `mihomo -t -f <配置> -d <目录>` 做静态校验；含 `GEOIP` 规则时需 `geoip.metadb`，本机网络下载不了（mihomo 直连 GitHub 超时）→ 用 curl 走代理手动下载放到 `-d` 目录即可通过。
- 产物必须过内核校验再推送，否则用户端就是"订阅校验失败/变更已撤销"。

## 交付物与分组规则

- `scripts/thordata_build.py`：抓 CSV → 分组 → 输出 Clash（`SOURCE_TYPE_TO_CLASH` 做类型映射；socks4 剔除；节点名带 `[R]`/`[D]` 前缀）
- `config/thordata_groups.yaml`：分组阈值（`require` 各字段为允许值列表 + `min_streak`）
- `.github/workflows/thordata-sub.yml`：每 6 小时 + 手动，`checkout@v6`/`setup-python@v6`
- 产物：`data/thordata/residential.yaml`（≈7 个）、`data/thordata/daily.yaml`（≈49-72 个），各带 url-test + select 两个策略组与最小分流规则
- 订阅链接（raw）：
  - `https://raw.githubusercontent.com/pangshuiyou-art/spiderpy-clash-sub/main/data/thordata/residential.yaml`
  - `https://raw.githubusercontent.com/pangshuiyou-art/spiderpy-clash-sub/main/data/thordata/daily.yaml`

## 消费端接法

- `E:\any-auto-register` 的代理中心支持 Clash YAML 订阅，协议白名单含 http/socks5（无 socks4）；用其「节点过滤正则」按 `^\[R\]` / `^\[D\]` 分流用途，失效节点靠它自带的「失败 3 次自动禁用、成功自动恢复」兜底。
- 住宅组规模受源限制（可用仅 7 个），这是数据天花板，不是过滤规则问题。

## 环境事实（本机）

- **直连 github.com 不通**（21s 超时），本机 7897 端口代理可用；git 未配置 http.proxy → 推送用一次性参数：`git -c http.proxy=http://127.0.0.1:7897 -c https.proxy=http://127.0.0.1:7897 push ...`（不改持久配置）。
- `raw.githubusercontent.com` 可直连拉取（与 github.com 不同链路），但**时通时断**（实测同一链接 200 ↔ 000 反复）。

## 订阅失败复盘（2026-09-24 补）

现象：把 GitHub 产物链接加进 Clash 客户端提示"订阅源失败"。两个根因（都已修）：

1. **产物 rules 写了 `GEOIP,CN,DIRECT`** → 客户端缺少 GeoIP 数据库时整份配置校验失败；对照客户端此前**成功订阅过**的 CNB 产物，其 rules 只有 `MATCH,汇总`。
   修复：rules 只留 `MATCH,<组名>`，并去掉 `udp` 字段、策略组名去 emoji，与已验证形态完全对齐。
2. **raw.githubusercontent.com 国内访问不稳定**（见上）→ 改用 jsdelivr 链：
   `https://cdn.jsdelivr.net/gh/<user>/<repo>@main/<path>`，并在 workflow 更新后调用
   `https://purge.jsdelivr.net/gh/<user>/<repo>@main/<path>` 刷新缓存（purge 返回 `status: finished` 即生效）。

其他要点：
- 输出 YAML 时**不要自定义 dumper 给所有字符串加引号**（会把顶层 key 也变成 `"mixed-port"`）；直接交给 PyYAML 默认策略即可，`[R]...` 这类以 `[` 开头的值会自动加引号。
- raw 的 CDN 有约 5 分钟缓存 → 验证线上内容要用 GitHub contents API（base64 直读）绕开 CDN，否则会误判"推送没生效"。
- 提高客户端兼容性的通用做法：**与已被证明可用的同类产物逐字段对齐**，而不是自己发挥。