---
name: 代理订阅双平台重组记录与操作手册
description: 记录 GitHub 正式线与 CNB 影子线的重组目标、方法、产物、定时频率、验证结论和新增订阅步骤
type: project
created: 2026-10-02
updated: 2026-10-02
---

# 代理订阅双平台重组记录与操作手册

## 一、重组目标

这次重组把原本分散的免费代理、v2ray 合并订阅和综合 Clash 配置，整理成两个独立运行、可对照的平台：

- GitHub Actions 是正式产物线，客户端优先使用 jsDelivr CDN 地址。
- CNB 是影子对照线，在 CNB 的网络和运行环境中独立采集、测活、回写，不覆盖正式产物。

必须满足的要求：

- 住宅订阅用于注册等需要住宅 IP 的场景，mihomo 连续 3 轮测活通过。
- 日常订阅用于普通浏览，mihomo 连续 2 轮测活通过。
- 综合 Clash 订阅由 `scripts/generate.py` 生成。
- 两边都能定时运行、自动提交，并提供无需登录的 raw 或 CDN 地址。
- 两边节点数量不要求一致，因为源站响应、网络出口和测活时间不同。

## 二、关键位置

远端仓库：

- GitHub：`pangshuiyou-art/spiderpy-clash-sub`
- CNB：`clash_v2rayN/clash_v2rayN`

本地项目根目录：

- `E:\代理`

这次使用过的托管工作区：

- `E:\TraeCache\.codex\worktrees\aggregate-subs\代理`

关键文件：

- GitHub 住宅/日常流水线：`.github/workflows/aggregate-sub.yml`
- GitHub 综合订阅流水线：`.github/workflows/update-sub.yml`
- CNB 影子流水线：`.cnb.yml`
- 住宅/日常聚合入口：`scripts/sub_aggregator/build_aggregated_subs.py`
- 综合 Clash 入口：`scripts/generate.py`
- 聚合源配置：`config/aggregator_sources.yaml`
- GitHub 住宅 ASN 白名单：`config/residential_asns.yaml`
- CNB 影子住宅 ASN 白名单：`config/cnb_shadow_residential_asns.yaml`

注意：托管工作区可能不包含 `AGENTS.md`、`CLAUDE.md` 和 `.memory/`。进入这类工作区时，仍必须回到项目根目录读取规则，不能因为工作区里没有规则文件就跳过。

## 三、处理方法

住宅和日常订阅的数据流：

1. 从 `config/aggregator_sources.yaml` 读取代理池源和订阅链接源。
2. 合并候选节点并去重。
3. 剔除扫描噪音和明显无效端点。
4. 做源侧粗筛和 TCP 预筛。
5. 补充国家、ASN、机房或住宅元数据。
6. 结合数据中心 CIDR、hosting 标记和 ASN 白名单判定住宅 IP。
7. 使用 mihomo 多轮测活。
8. 按国家、区域和策略组命名。
9. 校验 YAML、节点数和策略组。
10. 提交产物并刷新 jsDelivr 缓存。

当前测试参数：

```text
--residential-rounds 3
--daily-rounds 2
--max-residential-test 1500
--max-daily-test 4000
```

## 四、正式产物和地址

GitHub 正式产物路径：

- `data/free_proxy/residential.yaml`
- `data/free_proxy/daily.yaml`
- `data/free_proxy/residential-socks4.yaml`
- `clash.yaml`

客户端优先使用 jsDelivr：

```text
https://cdn.jsdelivr.net/gh/pangshuiyou-art/spiderpy-clash-sub@main/data/free_proxy/residential.yaml
https://cdn.jsdelivr.net/gh/pangshuiyou-art/spiderpy-clash-sub@main/data/free_proxy/daily.yaml
https://cdn.jsdelivr.net/gh/pangshuiyou-art/spiderpy-clash-sub@main/clash.yaml
```

GitHub raw 只作为调试备用：

```text
https://raw.githubusercontent.com/pangshuiyou-art/spiderpy-clash-sub/main/data/free_proxy/residential.yaml
https://raw.githubusercontent.com/pangshuiyou-art/spiderpy-clash-sub/main/data/free_proxy/daily.yaml
https://raw.githubusercontent.com/pangshuiyou-art/spiderpy-clash-sub/main/clash.yaml
```

CNB 影子产物路径：

- `data/cnb_shadow/free_proxy/residential.yaml`
- `data/cnb_shadow/free_proxy/daily.yaml`
- `data/cnb_shadow/free_proxy/residential-socks4.yaml`
- `data/cnb_shadow/spiderpy.yaml`
- `data/cnb_shadow/comparison.json`
- `data/cnb_shadow/comparison.md`
- `data/cnb_shadow/comparison-history.jsonl`

CNB raw 地址：

```text
https://cnb.cool/clash_v2rayN/clash_v2rayN/-/git/raw/main/data/cnb_shadow/free_proxy/residential.yaml
https://cnb.cool/clash_v2rayN/clash_v2rayN/-/git/raw/main/data/cnb_shadow/free_proxy/daily.yaml
https://cnb.cool/clash_v2rayN/clash_v2rayN/-/git/raw/main/data/cnb_shadow/spiderpy.yaml
```

CNB raw 模板：

```text
https://cnb.cool/{owner}/{repo}/-/git/raw/{ref}/{path}
```

GitHub Actions 不会自动把正式产物同步到 CNB。CNB 仓库中的 `data/free_proxy/...` 和 `clash.yaml` 不能当成 GitHub 正式线的实时结果。

## 五、定时频率

GitHub：

- `update-sub.yml` 生成 `clash.yaml`，cron 为 `0 * * * *`，名义上每小时一次。
- `aggregate-sub.yml` 生成住宅、日常和 socks4 清单，cron 为 `43 3,9,15,21 * * *`。
- 换算成北京时间，住宅和日常的计划时间是 `05:43、11:43、17:43、23:43`。
- GitHub schedule 可能排队、延迟甚至丢弃，不能只看计划时间判断脚本是否失败。

CNB：

```yaml
"crontab: 23 1,13 * * *":
  - <<: *shadow-pipeline
```

- UTC 时间为 `01:23、13:23`。
- 北京时间为 `09:23、21:23`。
- 名义频率为每 12 小时一次。

修改 `.cnb.yml` 后，必须调用接口同步定时器：

```text
POST https://api.cnb.cool/clash_v2rayN/clash_v2rayN/-/build/crontab/sync/main
```

该接口需要 `repo-cnb-trigger:rw` 权限。这次返回同步成功，但新表达式的首次定时验证要等北京时间 `09:23`。

## 六、这次验证结果

GitHub 综合订阅：

- 2026-10-01 23:54（北京时间）的定时任务成功。
- run 编号：`36887885876`。
- 回写提交：`0ae0ba933`。
- jsDelivr 刷新步骤成功。

GitHub 住宅和日常：

- 23:43 的计划任务到 00:06 仍未出现在 Actions 列表，判断为 schedule 延迟或丢弃。
- 2026-10-02 00:07 手动触发 `aggregate-sub.yml`。
- run 编号：`36889615882`。
- 00:15 全部步骤成功。
- 回写提交：`e0bb5e588`。
- 住宅为 65 节点，日常为 628 节点，生成时间均为北京时间 00:15:23。

CNB 影子线：

- 修复后的完整流水线编号为 `cnb-e6t-1k3rdvcjc`，状态成功。
- 长测活阶段约 21.5 分钟，整条流水线约 22 分钟。
- 回写提交为 `3b7f9f6a515e12f912000283cecb4cbb42e866aa`。
- 影子住宅为 28 节点，影子日常为 156 节点。
- 影子综合配置可正常匿名拉取，当时统计 147 个代理条目。

## 七、排障结论

GitHub schedule 不准点：

- workflow 处于 active 不代表计划运行一定准时出现。
- 先查全部 runs，再查 queued 和 in_progress。
- 超过明显延迟窗口且数据已经陈旧时，可以手动 `workflow_dispatch` 补跑。

jsDelivr 缓存：

- GitHub 提交成功不等于客户端立刻拿到新内容。
- workflow 成功提交后必须主动 purge。
- 验证时看 YAML 文件头的生成时间和响应头 `Age`。
- 不要给 CNB raw 随机追加 query 参数，可能造成 404 误报。

CNB 定时器：

- `.cnb.yml` 修改后必须调用 crontab sync 接口。
- 同步后查看构建列表中的 event，确认新表达式出现且旧表达式不再触发。

CNB 长任务：

- 聚合阶段需要显式 timeout，当前给 90 分钟。
- 测活过程必须持续输出心跳，避免 10 分钟无输出被误杀。
- 不能用缩短测活轮次的方式掩盖问题，因为那会改变订阅质量标准。

## 八、下次新增订阅的步骤

开工前先写清：

1. 使用场景是什么。
2. 客户端需要什么格式。
3. 是否要求住宅 IP。
4. 需要几轮测活。
5. 更新频率是多少。
6. 是新增代理源，还是新增输出订阅。

只新增代理源时：

1. 修改 `config/aggregator_sources.yaml`。
2. 手动运行聚合脚本。
3. 检查候选数、TCP 预筛数、住宅分类数和最终节点数。
4. 确认住宅和日常输出仍有完整策略组。
5. 等 GitHub 正式线和 CNB 影子线下次运行，或分别手动触发验证。

新增 Clash/YAML 输出时：

1. 定义输出路径和文件名。
2. 生成 `proxies`、`proxy-groups`、`rules`、端口和模式。
3. 增加空节点、空策略组和节点引用校验。
4. 更新 GitHub workflow 的运行、提交和 jsDelivr purge。
5. CNB 对照版本必须放在 `data/cnb_shadow/` 下。
6. 如进入平台对比，更新 `scripts/sub_aggregator/compare_shadow_outputs.py`。
7. 分别手动触发 GitHub 和 CNB。
8. 验证 raw/CDN 返回 200 且内容是 YAML，不是 HTML。
9. 检查生成时间、节点数、策略组和 mihomo 加载结果。
10. 修改 CNB 定时器后调用 crontab sync。

新增综合 Clash 配置时：

1. 修改 `scripts/generate.py`。
2. GitHub 正式输出为 `clash.yaml`。
3. CNB 影子输出为 `data/cnb_shadow/spiderpy.yaml`。
4. GitHub 由 `update-sub.yml` 每小时生成、提交和 purge。
5. CNB 由影子流水线的“生成影子 spiderpy 订阅”阶段生成。

## 九、最小验收清单

- 已在会话开始时读取 `.memory/MEMORY.md`。
- GitHub workflow 为 active。
- CNB `.cnb.yml` 语法有效。
- 手动触发可以成功，不只依赖 schedule。
- 产物落在预期路径。
- GitHub raw 和 jsDelivr 在 purge 后能拿到新内容。
- CNB raw 可以匿名返回 YAML。
- YAML 文件头的生成时间已更新。
- 节点数非零。
- 住宅为 3 轮测活，日常为 2 轮测活。
- 综合配置可以被 mihomo 加载。
- 下一次计划时间后复查定时运行。

## 十、给后续开发者或 AI 的短结论

这套架构不是让两个平台维护同一个文件，而是让同一套方法在两个平台独立产出并对照。正式客户端默认使用 GitHub 加 jsDelivr，CNB 的 `data/cnb_shadow` 用于验证国内和 CNB 环境下的采集测活质量。修改前先读项目规则和 `.memory/MEMORY.md`，新增订阅时先写清需求、质量门槛、频率、产物路径和客户端地址。
