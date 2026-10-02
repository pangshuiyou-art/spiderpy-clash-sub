---
name: CNB额度核算与产物回写自触发
description: 腾讯云 CNB 免费额度仅 160 核时/月，8 核构建机单次约 1.22 核时，定时间隔存在硬下限；产物回写提交会再次触发同一流水线导致消耗翻倍，用 Pipeline 的 ifModify 拦截；附 CNB OpenAPI 与内置变量速查
type: learning
created: 2026-09-24
updated: 2026-09-24
---

# CNB（腾讯云原生构建）额度核算与产物回写自触发（2026-09-24）

## 结论速览

- 免费额度 **160 核时/月**（8 核机折算 20 小时），超额 0.125 元/核时；额度不足时预冻结会**立即终止任务**（不是扣钱，是跑不动）
- 实测单次完整测活构建 **1.22 核时**（8 核 × 547 秒，取自接口字段 `metricCoreHours`）
- 定时间隔因此存在硬下限：`间隔 ≥ 30×24×单次核时 ÷ 160` ≈ **6 小时**
  - 6 小时 = 120 次/月 ≈ **146 核时**（占额度 91%）
  - 3 小时 = 240 次/月 ≈ **293 核时**（超 83%，约 16 天耗尽）
- 产物 `git push` 回 main 时，**这个提交会再次触发同一流水线**（用构建记录的 sha 比对确认），使消耗翻倍
- 拦截手段：push 流水线加 `ifModify`（只列代码/配置文件），产物回写只改 `data/**` 故不命中；**定时任务与页面手动触发会忽略 ifModify 检查**，照常执行

## 证据链（【已确认事实】）

- 计费口径：官方《社区版计费说明》——`核时 = 核数 × 小时数`，8 核 16 GiB 免费额度可用 20 小时，超额 0.125 元/核时（[来源](https://cloud.tencent.cn/document/product/1785/116265)）
- 额度实查：`GET https://api.cnb.cool/{org}/-/charge/quota` → `ci_in_sec.total = 576000`（= 160 小时 = 160 核时），`dev_in_sec = 5760000`（= 1600 核时）
- 自触发：构建 `cnb-peh-1k37in1c0`（16:48:51Z，event=push）的 `sha=adbaa6af…`，正是上一轮构建 `cnb-0jg`（16:39，success）回写产物的提交 —— 回写触发成立

## CNB OpenAPI / 内置变量速查

- 列构建历史：`GET https://api.cnb.cool/{slug}/-/build/logs`（返回 total + data 数组，含 sn / status / event / sha / commitTitle / metricCoreHours）
- 查单次状态：`GET /{slug}/-/build/status/{sn}`；日志：`GET /{slug}/-/build/logs/{sn}`；触发：`POST /{slug}/-/build/start`（event 必须为 `api_trigger`）
- 同步定时任务：`POST /{slug}/-/build/crontab/sync/{branch}` —— **改 `.cnb.yml` 里的 crontab 后必须调用，才会同步到调度器**
- 鉴权：`Authorization: Bearer <令牌>` + `Accept: application/json`；令牌权限 `repo-code:rw`（推代码）/ `repo-cnb-trigger:rw`（触发与同步）/ `repo-cnb-history:r`
- 内置只读变量：`CNB_COMMIT_MESSAGE` / `CNB_COMMIT_MESSAGE_TITLE`（可用于识别"这是不是自己的回写提交"）/ `CNB_BRANCH` / `CNB_REPO_SLUG` / `CNB_TOKEN`（流水线临时令牌，可推本仓库）/ `CNB_TOKEN_USER_NAME`（固定 cnb）
- 条件能力：Pipeline 层级有 `ifNewBranch` / `ifModify`；Stage 与 Job 层级另有 `if`（shell 脚本，退出码 0 才执行）
- 触发规则：`ifModify` 只在"可统计文件变更"的事件生效（非新建分支的 push、commit.add、PR）；**crontab、页面手动触发等会忽略该检查**

## 本次改动（.cnb.yml）

```yaml
main:
  push:
    - ifModify:
        - .cnb.yml
        - scripts/**
        - config/**
      <<: *merge-pipeline
  "crontab: 0 */6 * * *":
    - <<: *merge-pipeline
```

## 边界与注意

- 文件变更统计上限 300 个文件（超限部分不参与匹配）
- 新建分支的 push 没有对比基准，会**忽略** `ifModify`，需要更严格时改用 `commit.add`
- 若哪天要提高频率（如回到 3 小时），必须先降构建机规格或压缩单次耗时，否则会超额度并被系统终止