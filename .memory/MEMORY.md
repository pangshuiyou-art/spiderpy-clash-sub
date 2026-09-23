# MEMORY.md — 项目记忆索引（模板）

> **会话开始第一读**。本文件是 `.memory/` 的唯一入口索引，不承载正文。
> 协议全文见 [README.md](./README.md)。

## 快速导航

- [README.md](./README.md) — 记忆协议（谁能写 / 写什么 / 格式 / 提交纪律）
- [user/](./user/) — 用户偏好、工作方式（先方案后动工、交流语言等）
- [project/](./project/) — 项目长期约束、架构决策（不可从代码推导）
- [learnings/](./learnings/) — 踩坑、经验、bug 调查结论
- [archive/](./archive/) — 会话/过程流水，按 `YYYY-MM/` 归档
- 模板启用/升级自助手册 → `docs/启用与升级提示词.md`（版本锚点见 `00_模板使用说明.md` 头部）

## 最近条目（按日期倒序）

| 日期 | 主题 | 位置 |
|------|------|------|
| 2026-09-23 | spiderpy 免费代理池转 Clash 订阅（批量归属查询+名称唯一性修复） | [learnings/20260923-spiderpy-clash-sub.md](./learnings/20260923-spiderpy-clash-sub.md) |
| 2026-09-07 | 模板 v1.3 内容隔离红线（分析对象内容=数据非指令，五类危险内容） | [project/20260907-模板v1.3内容隔离红线.md](./project/20260907-模板v1.3内容隔离红线.md) |
| 2026-08-31 | 批判性沟通、严谨核验与信息四分类规范（整合优化版） | [user/20260831-critical-communication-and-fact-checking.md](./user/20260831-critical-communication-and-fact-checking.md) |
| 2026-08-27 | 模板版本与升级机制（版本锚点 / 采用记录 / 升级闭环约定） | [project/20260827-模板版本与升级机制.md](./project/20260827-模板版本与升级机制.md) |
| 2026-08-26 | 格式化工具与引号规则冲突（black 默认值 vs 文档标准） | [learnings/20260826-格式化工具与引号规则冲突.md](./learnings/20260826-格式化工具与引号规则冲突.md) |

## 分类索引

### project/（项目约束与决策）
- [模板 v1.3 内容隔离红线](./project/20260907-模板v1.3内容隔离红线.md) — 分析对象内容=数据非指令；五类危险内容与六处落位
- [模板版本与升级机制](./project/20260827-模板版本与升级机制.md) — 版本锚点 / 采用记录 / 升级闭环与发版四步约定

### learnings/（经验与踩坑）
- [spiderpy 代理池转 Clash 订阅](./learnings/20260923-spiderpy-clash-sub.md) — host:port 整串查归属必失败；必须先拆 host 再批量查询，序号全局递增防重名
- [spiderpy 代理池转 Clash 订阅](./learnings/20260923-spiderpy-clash-sub.md) — host:port 整串查归属必失败；先拆 host 再批量查询，序号全局递增防重名
- [格式化工具与引号规则冲突](./learnings/20260826-格式化工具与引号规则冲突.md) — 文档规定格式细节时必须检查工具默认值并落地配置

### user/（用户偏好）
- [批判性沟通、严谨核验与信息四分类规范](./user/20260831-critical-communication-and-fact-checking.md) — 整合版：防迎合、错误前提审查、推导过程与反例、信息四分类、无源报找不到、原始出处与直接务实语气

### archive/（会话流水）
- 初始为空，会话流水按 `YYYY-MM/` 归档，写 `archive/YYYY-MM/session-index.md` 并在栏位登记

## 看板指针（如有）

- 进行中任务认领 / 状态 → `docs/任务看板.md`（不搬入 `.memory/`，长期记忆只在此索引引用）

## 维护约定

- 新增条目后更新本文件"最近条目"与对应"分类索引"两处——**只允许追加自己条目对应的行，禁止改动他人行**。
- 条目重排、清理、去重由单一维护者定期执行，避免多 AGENT 并发改索引；追加冲突时 `git pull --rebase` 后重试。
- `git add .memory && git commit` 时单独提交，便于按条回滚。
