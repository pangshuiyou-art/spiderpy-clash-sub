---
name: 免费代理住宅/日常分组订阅流水线（第四条线）
description: 8 源 + 数据中心 CIDR 库整合成住宅/日常两套 Clash 订阅；踩坑：TCP 预筛把 4.8 万压到 1400、住宅三库交叉判定、mihomo 不支持 socks4 需独立协议层测活、candidates CSV 不得入库
type: learning
created: 2026-09-30
updated: 2026-09-30
---

# 免费代理住宅/日常分组订阅流水线（2026-09-30）

## 背景

在既有三条订阅线（spiderpy / v2ray 合并 / Thordata）之外新增第四条线：把 8 个免费代理源
（ProxyScrape、monosans、proxmint、Thordata、SpeedX http/socks5、hookzof、proxy-free）
加 1 个数据中心 CIDR 库（X4BNet）整合成住宅组与日常组两套 Clash 订阅，数据源分析见
`docs/代理源分析报告_20260930.md`。全部在 GitHub Actions 执行，本机不承担定时任务。

## 数据事实（实测）

- 原始记录约 48758 条 → ip:port 去重后 38038 → 剔除端口扫描噪音后 8890
- 端口扫描噪音特征：单个 IP 出现上千个端口（如 45.74.31.30 有 6192 个端口），实测全不可用
- 源侧粗筛后住宅侧 8337 / 日常侧 6332，TCP 预筛可达率仅 16.9%（1409/8335）
- 住宅 IP 在免费源里占比极低；住宅组实测存活 9 个、日常组 42 个，是数据天花板不是规则问题

## 三个关键设计（踩坑换来的）

1. **TCP 预筛是必需品**：不预筛直接送内核测活会跑几十分钟；先 TCP 连通性筛选把
   4.4 万压到约 1500，再送 mihomo，Actions 单次约 10 分钟（本地实测端到端 3.4 分钟）。
2. **住宅判定用三库交叉**：源 `ip_type` 标签 + 数据中心 CIDR 段库排除法 + ASN 白名单，
   并以 ip-api 的 `hosting` 字段作否决。白名单自学习（高置信入库、中置信进待审）会写回
   `config/residential_asns.yaml` 并由 workflow 提交。
3. **mihomo 不支持 socks4**（既有教训）：住宅 socks4 单独走协议层测活（CONNECT + TLS 握手，
   不依赖内核），单独产出 `residential-socks4.yaml` 明文清单，不混进 Clash 产物。

## 产物与仓库纪律

- 产物：`data/free_proxy/residential.yaml`、`daily.yaml`、`residential-socks4.yaml`
- rules 只留 `MATCH`，不引入 GEOIP 依赖（沿用 20260924 教训，避开客户端整份校验失败）
- **workflow 提交范围必须精确到产物文件**：脚本会在 out_dir 里额外写
  `candidates_residential.csv` / `candidates_daily.csv`（人工审查白名单用，约 120KB），
  用 `git add data/free_proxy/` 会把它们一并提交；应显式列出 3 个 YAML + ASN 白名单。
- **远端只保留交付文件**：`tests/` 属本地文件，不得推送（见
  `project/20260923-spiderpy-repo-deliverables.md`）。本次首推曾误带 tests，已用
  `git rm -r --cached tests` 修正。

## 交付状态

- 远端 `main` 已含 `scripts/free_proxy_merger/`（7 个模块）、`config/free_proxy_sources.yaml`、
  `config/residential_asns.yaml`、`.github/workflows/free-proxy-sub.yml`
- 本地测试 168 项全通过（`tests/` 保留在本地工作区）
- workflow：每 6 小时 + 手动触发，下载固定版本 mihomo v1.19.10，产物提交后 purge jsdelivr

## 推送环境（本机）

本地工作区 `E:\代理` 与远端历史不同源（本地 35 提交 / 远端 8 提交，合并基点 b32ae9b），
但 `config/`、`scripts/`、`.github/` 内容一致。直接 push 会失败或带入杂文件，
改用 `create_worktree` 在远端干净基线（28f6923）上开 `codex/free-proxy-sub` 分支提交，
再 `git push origin codex/free-proxy-sub:main` 快进合并，全程未触碰本地未跟踪文件。
