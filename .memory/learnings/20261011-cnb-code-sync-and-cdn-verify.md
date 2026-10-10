---
name: cnb代码同步与CDN验证的坑
description: CNB 是独立远端，改 scripts 后必须合并 cnb/main 再推 cnb 才触发影子线；jsDelivr 新鲜度验证受本地网络间歇 RST 干扰
type: learning
created: 2026-10-11
updated: 2026-10-11
---

# CNB 代码同步与 CDN 验证的坑（2026-10-11）

## 结论速览

- **CNB 代码更新必须单独推 `cnb` 远端**：本地推 origin（GitHub）不会同步到 CNB 仓库；CNB 的 push 触发只认 cnb.cool 仓库的提交。
- 本地 main 与 cnb/main 长期分叉（CNB bot 每轮回写影子产物）：直接 push 会被拒，需先 `git fetch cnb main && git merge cnb/main`（产物文件与 scripts 无重叠，合并干净）再 `git push cnb main`，保快进。
- 合并后的 main **不要推回 origin**：origin 布局里没有 `data/cnb_shadow`，推上去会污染 GitHub 仓库结构。CNB bot 提交留在本地等下一次全量同步（既有模式：`chore: 同步 …` 提交）。
- jsDelivr 新鲜度验证：purge 接口返回 `status: finished` 只代表任务被受理，边缘传播有分钟级延迟；且本机直连 `cdn.jsdelivr.net`/`raw.githubusercontent.com` 会出现间歇性 RST（WinError 10054），单次抓到旧内容不能断言 CDN 没刷新——用 GitHub API contents 端点核验仓库权威内容，CDN 新鲜度换稳定网络再看 `Age` 头。

## 证据链（【已确认事实】）

- 2026-10-10 推送 d47343a 仅到 origin，CNB 构建列表无对应 push 记录；合并 cnb/main（f576d52）后推送 16e2ea8，影子构建 `cnb-8vh-1k4jh8o89` 即触发并成功。
- 手动 purge 返回 finished（18:46:48Z）后本机抓 CDN 仍见旧生成时间（10:16:02Z），随后同域名连接被 RST——本机视角不可靠，GitHub API 核验仓库内容为新版。

## 边界与注意

- `git merge cnb/main` 产生的合并提交只进本地与 cnb；若未来要把全量同步推 origin，先确认 `data/cnb_shadow` 是否已被接受进 GitHub 布局。
- CNB 构建排队（pending）可达 15 分钟以上，别把排队当失败；回写 push 构建（event=push、pipeline 被 ifModify 跳过）是正常噪声。

## 关联

- [[代理订阅双平台重组记录与操作手册]]
- [[CNB额度核算与产物回写自触发]]
- [[节点保留机制落地记录]]
