---
name: v2ray多订阅源合并与mihomo真实测活
description: 多订阅源合并脚本（格式嗅探五类、vless/vmess/ss双写法解析、ip-api批量归属、mihomo内核真实测活、Clash+v2rayN双输出）
type: learning
created: 2026-09-23
updated: 2026-09-24
---

# v2ray 多订阅源合并与 mihomo 真实测活（2026-09-23）

## 背景

用户有多个 GitHub 免费 v2ray 订阅源（如 daily_free_vpn 的 V2Ray.yml、Free-v2ray-Configs 的 configs_base64.txt），需要 GitHub Actions 定时拉取 → 解析 → 测活 → 产出稳定、有效、带国家归属的汇总订阅，本地 Clash / v2rayN 直接导入。要求：脚本支持任意新增源、自动识别源格式。

## 交付物

- `scripts/v2ray_merger/`：format_detector / link_parser / config_parser / geo_lookup / tester / merge_v2ray_subs（入口）
- `config/sources.yaml`：订阅源配置，加一行即新增源，无需改代码
- `scripts/v2ray_merger/update-v2ray-sub.yml`：GitHub Actions（workflow_dispatch + schedule 每6小时，下载 mihomo 内核 → 合并 → 真实测活 → 提交）
- 产物：`data/clash/clash_merged.yaml`（Clash）+ `data/clash/v2ray_merged.txt`（v2rayN）
- 测试：`tests/test_*.py` 共 77 例，覆盖率 77%（tester 全 Mock，不真实跑内核）

## 根因 / 踩坑（本次重点）

1. **vmess:// 与 ss:// 必须字符串级解析，不能用 urlparse 拆 host**：内层是 base64，可能含 `+` `/` `=` 字符，urlparse 会把 base64 截断导致解析挂。ss:// 有 SIP002（userinfo base64@host）与 legacy（整体 base64）两种写法都要兼容——legacy 需先 b64 解码出 `method:pass@host:port` 再拆。
2. **每行 base64 源判定顺序**：整体 base64 一旦能解码就不走"每行"判定；制造"整体解码失败但每行成功"样本（如首行放元数据行）才能命中 base64_per_line 分支。
3. **geo 查询不能传入 host:port 整串**（沿用 spiderpy 教训：必失败）；域名先解析为 IP 再批量查；纯 IP 直接可用（resolve_host 返回 None 时不可跳过，否则纯 IP 全被过滤）。
4. **Clash 节点名必须全局唯一**（沿用 spiderpy 教训）：命名规则「国家代码-全局递增序号」（US-001、JP-001…），国家用 ip-api countryCode 而非节点自带 emoji（不可靠）。
5. **v2rayN 订阅格式**：每行一个原始链接（明文 base64 链接列表），注意保留原始链接去重时取首条。

## 自愈机制 / 规范

- 格式嗅探五类：base64整文件 → base64每行 → Clash YAML（含 proxies:）→ v2rayN JSON（数组）→ 明文链接；任何一步失败自动降级，单源异常不中断整体
- 归属查询：ip-api.com/batch ≤100 IP/次分批；私网/保留地址跳过不查；整体失败降级为"未知"不中断
- mihomo 测活：写临时配置（external-controller 127.0.0.1:19090）→ 启动内核 → 等 /version 就绪 → 并发 8 调 /proxies/{name}/delay → 保留 delay≤max(默认1500ms) 节点；controller 未就绪抛异常由上游降级为"未测活节点"
- 输出先建父目录再写文件；最终无节点时报错退出

## 使用

```bash
# 本地仅合并（无内核时不测活）
python scripts/v2ray_merger/merge_v2ray_subs.py
# GitHub Actions 真实测活（workflow 已内置 mihomo 下载）
# 产物: data/clash/clash_merged.yaml + v2ray_merged.txt
```

## 根底（本次重点：mihomo 测活为什么在 GitHub 上没生效）

**现象**：GitHub Actions 生成的 clash_merged.yaml 有 1618 节点但全部无 `delay` 字段——测活环节被静默降级（保留全部未测活节点），用户误以为"没测活/没探测地区"（地区实际已查，节点全部带国家注释）。

**根因链**（对照 E:\any-auto-register 代理中心同款踩坑 20260908）：
1. 固定的 external-controller 端口(19090)与本机其它 mihomo/Clash 实例冲突 → 端口绑定失败但 mihomo **不退出变僵尸** → controller 永远不就绪 → 30s 超时抛 RuntimeError → tester 外层 except 降级保留全量节点。
2. `httpx.Client()` 默认 `trust_env=True`，经环境代理访问 127.0.0.1 controller 可能 502/超时（本地 Clash 开着时实测复现）→ controller 应直连。
3. 无启动后归属校验：健康检查被旧实例顶包应答 → "假活"。

**修复（scripts/v2ray_merger/tester.py，commit ebaf67f）**：随机空闲端口(_pick_free_port) + 启动前端口预检(_ensure_port_free：mihomo 残留回收、非 mihomo 报错) + controller **直连**(httpx trust_env=False) + 启动后归属校验(_listener_includes) + finally 三层清理（句柄→端口反查→配置目录）。任何一步失败显式抛 RuntimeError，由上游决定处理，不再静默降级。

**根因之二（真正决定成败的一环，commit 80bd306）**：给 mihomo 测活的节点必须是 **Clash proxy 格式**——vmess 要 `alterId`（0 也要）、节点名必须唯一。此前直接把 link_parser 的原始中间态（`alter_id`/`country`/`raw_link`/同名）喂给 tester，mihomo 启动即 fatal（`proxy 15: key 'alterId' missing`/`duplicate name`）→ controller 永不就绪 → 降级保留全部 → 产物几千条全无 delay。修复：抽 `_to_clash_proxy()`（build 与测活共用），流程改为 去重→归属→**唯一命名→转 proxy→测活**→输出。实测全量 1551 节点：32 存活带 delay，链路闭环。

## 已知边界

- GitHub runner 在美国，测活视角为"GitHub→节点"，延迟数对国内用户偏高属正常
- 私有仓库 Actions 额度约 2000 分钟/月，全量测活一次约数分钟
- schedule 任务 60 天静默停用（同 spiderpy 教训），失效手动 Run 一次恢复

## 最终闭环（2026-09-23 再补）——workflow 已跑通 + 视角局限结论

- **workflow 端到端成功（commit fde1436 起稳定）**：mihomo 下载改固定版本 v1.19.10（未认证 API 动态取版本会被限流→URL 404 是 exit code 1 根因）+ 下载后 `file|grep gzip` 校验；测活全灭不再 return 1（避免误报失败）。远端产物 679 全存活且带 `delay=xx ms`（写在**行尾注释**里，非字段——按 `p.get('delay')` 检查会误判"无 delay"，须按文本 `count('delay=')`）。
- **测活视角根本局限（用户指出，已确认）**：GitHub 公共 runner 固定美国出口，测的是"美国→节点"；国内用户使用是"国内→节点"，免费源对国内常近于全 timeout。GitHub 无法直接换国内机房（除非自托管 runner，runs-on self-hosted 挂国内常开机器）。
- **Gitee 免费版评估（用户质询，结论不适合）**：GiteeGo 流水线免费版"限时免费试用"不可靠；raw 链接有防盗链 (403) 风险。替代方案：**本机 Windows 计划任务**定时跑 `merge_v2ray_subs.py --mihomo <本地内核>`（国内真实视角）或 GitHub 自托管 runner。
- **国内落地已定（2026-09-24 补记）**：用户拍板走 **CNB（cnb.cool，腾讯云国内机房）**，仓库 `clash_v2rayN/clash_v2rayN`，`.cnb.yml` 每 6 小时定时跑国内视角测活并回写产物；订阅地址 `https://cnb.cool/clash_v2rayN/clash_v2rayN/-/git/raw/main/data/clash/clash_merged.yaml`（注意 CNB 直链格式为 `/-/git/raw/`，`/raw/`、`/blob/` 均 404）。CNB 额度与自触发坑详见 [20260924-cnb-quota-and-self-trigger.md](./20260924-cnb-quota-and-self-trigger.md)