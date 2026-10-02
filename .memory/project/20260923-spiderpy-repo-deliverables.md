---
name: spiderpy-clash-sub远端仓库交付范围与工作流状态
description: 远端仓库只保留交付文件（spiderpy+v2ray_merger），GitHub Actions 已自动跑通；本地项目文件（记忆/docs/tests/规范）不得推送
type: project
created: 2026-09-23
updated: 2026-09-23
---

# spiderpy-clash-sub 远端仓库交付纪律（2026-09-23）

## 背景

本地工作区 `e:\代理` 是模板项目 + 采集工具集；远端 `https://github.com/pangshuiyou-art/spiderpy-clash-sub` 是**独立交付仓库**。首次推送因 `--allow-unrelated-histories` 合并把本地全部文件带入远端，被用户指出后已清理（commit `c33dd0b` 移除 36 个文件）。

## 结论（不可从代码推导，必须记住）

1. **远端仓库只保留交付文件**：
   - spiderpy 原有：`.github/workflows/update-sub.yml`、`clash.yaml`、`scripts/generate.py`
   - v2ray 合并：`.github/workflows/update-v2ray-sub.yml`、`config/sources.yaml`、`scripts/v2ray_merger/*`、`data/clash/clash_merged.yaml`、`data/clash/v2ray_merged.txt`
   - `.gitignore`（防 workflow 误提交敏感文件）
2. **本地项目文件一律不推送**：`.memory/`、`docs/`、`tests/`、`AGENTS.md`、`CLAUDE.md`、`00_模板使用说明.md`、模板配置样例（`*.sample`）、`scripts/verify/`、`collectors/`、`experiments/`、`scripts/spiderpy_clash_sub_generate.py`、`scripts/update-sub.yml`
3. **GitHub Actions 已自动跑通**：远端出现 `b33afbf chore: 更新合并代理订阅`，v2ray 合并工作流端到端成功（下载 mihomo → 合并 → 真实测活 → 提交产物）。当前产物节点数约 1591。
4. **推送手法**：本地改动只用 `git add <具体文件>`（绝不 `-A`/`.`），只暂存交付范围内的文件；推送前可视需要 `git ls-tree -r --name-only origin/main` 核对远端文件清单。
5. **推送纪律（既有）**：产物只在用户明确指示时推送；本地提交是默认交付端点。本次用户明确指示"推送时不要再推送错"。

## 风险提示

- 若本地工作区与远端历史再次不同源，直接 push 会失败/带入杂文件；应先 `git fetch` 看清远端结构再决定合并方式，避免再次 `--allow-unrelated-histories` 全量合并。
- 远端 `clash_merged.yaml` 生成内容存在 `ws-opts.path` 混入整段 URL 参数、空 Host 等解析问题，需随 link_parser 修复后由 workflow 重新生成（见 learnings 20260923-v2ray-merge-mihomo-test 后续更新）。