---
name: spiderpy 免费代理池转 Clash 订阅
description: 从 demo.spiderpy.cn/all 拉取免费 HTTP 代理并生成 Clash 订阅 YAML，含国家归属批量查询与名称唯一性修复
type: learning
created: 2026-09-23
updated: 2026-09-23
---

## 背景

需要一个将 `http://demo.spiderpy.cn/all/`（开源项目 ProxyPool 的官方演示站）返回的免费 HTTP 代理列表转换为 Clash 订阅 YAML 的脚本。节点在 Clash 客户端里看不到国家、端点信息，体验差。

## 交付物

- `scripts/spiderpy_clash_sub_generate.py`：转换脚本（Python，httpx + PyYAML）
- `scripts/update-sub.yml`：GitHub Actions workflow（每小时定时 + 手动触发，提交生成的 clash.yaml）
- 远程仓库：https://github.com/pangshuiyou-art/spiderpy-clash-sub

## 根因（本次踩坑重点）

1. **运行卡死**：最初逐节点串行调 `ip-api.com/json/{ip}` 并 `sleep(1.2s)`，节点上百时需数分钟，GitHub Actions 一直 In progress。修复：改用 `ip-api.com/batch` 批量接口，一次请求最多 100 个 IP，另加 `_PRIVATE_NETS` 过滤私网/保留地址。
2. **节点重名（Clash 校验报 `proxy China 🇨🇳-01 is the duplicate name`）**：最隐蔽的 bug——收集待查询 host 时把 `host:port` 整串直接传给 `ipaddress.ip_address()`，必然抛 ValueError 被判"非法"，导致**归属查询从未真正执行过**，全部节点退化为 `HTTP-ip:port` 降级名；此前按 IP 维度的序号 `seen[host]` 在"不同 IP 同国家"时还会撞名。修复：先 `rsplit(':', 1)` 拆出纯 host 再收集，序号改全局递增 `enumerate(..., start=1)`，保证名称唯一。

## 自愈机制 / 规范

- 转换前统一走"拆 host:port → 过滤公网 → 批量查询 → 名称唯一化"五步，任何一步失败都降级为 `HTTP-<host>:<port>`（端点可见），不中断整体
- 生成结果必须校验 `unique`，重名即失败
- GitHub Actions 定时任务存在 60 天静默停用机制，失效时手动 Run 一次即可恢复

## 使用

```bash
# 在 GitHub Actions 环境（或联网环境）运行
python scripts/spiderpy_clash_sub_generate.py
# 产物: 仓库根目录 clash.yaml
```

Clash/v2rayN 订阅地址填 raw 链接后自动更新。