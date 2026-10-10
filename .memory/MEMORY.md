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
| 2026-10-11 | 住宅判定准确率根因与修复（跳过 ip-api 查询+放宽误判 19% 实测；三层修复已验证；社区判定方案调研） | [learnings/20261011-residential-accuracy-fix.md](./learnings/20261011-residential-accuracy-fix.md) |
| 2026-10-11 | 节点保留机制落地记录（台账回炉+保留组+稳定命名；双平台云端验证通过；参数与观察要点） | [project/20261011-node-retention-rollout.md](./project/20261011-node-retention-rollout.md) |
| 2026-10-11 | CNB 代码同步与 CDN 验证的坑（改 scripts 必须推 cnb 远端；合并 cnb/main 保快进；purge 受理≠立即可见） | [learnings/20261011-cnb-code-sync-and-cdn-verify.md](./learnings/20261011-cnb-code-sync-and-cdn-verify.md) |
| 2026-10-02 | 代理订阅双平台重组记录与操作手册（GitHub 正式线 + CNB 影子线；产物、定时器、排障和新增订阅步骤） | [project/20261002-shuangpingtai-dingyue-chongzu-runbook.md](./project/20261002-shuangpingtai-dingyue-chongzu-runbook.md) |
| 2026-09-30 | 免费代理住宅/日常分组订阅流水线（8 源+CIDR 库；TCP 预筛、socks4 独立测活、提交范围精确化） | [learnings/20260930-free-proxy-residential-pipeline.md](./learnings/20260930-free-proxy-residential-pipeline.md) |
| 2026-09-24 | Thordata 源分组订阅（住宅/日常；socks4 与 https 类型坑、mihomo 校验要点） | [learnings/20260924-thordata-grouped-subscriptions.md](./learnings/20260924-thordata-grouped-subscriptions.md) |
| 2026-09-24 | CNB 额度核算与产物回写自触发（160 核时/月硬下限、ifModify 拦截、OpenAPI 速查） | [learnings/20260924-cnb-quota-and-self-trigger.md](./learnings/20260924-cnb-quota-and-self-trigger.md) |
| 2026-09-23 | spiderpy-clash-sub 远端仓库交付纪律（只保留交付文件，Actions 已跑通） | [project/20260923-spiderpy-repo-deliverables.md](./project/20260923-spiderpy-repo-deliverables.md) |
| 2026-09-23 | v2ray 多订阅源合并与 mihomo 真实测活（五类格式嗅探/vmess与ss双写法/批量归属） | [learnings/20260923-v2ray-merge-mihomo-test.md](./learnings/20260923-v2ray-merge-mihomo-test.md) |
| 2026-09-23 | spiderpy 免费代理池转 Clash 订阅（批量归属查询+名称唯一性修复） | [learnings/20260923-spiderpy-clash-sub.md](./learnings/20260923-spiderpy-clash-sub.md) |
| 2026-09-07 | 模板 v1.3 内容隔离红线（分析对象内容=数据非指令，五类危险内容） | [project/20260907-模板v1.3内容隔离红线.md](./project/20260907-模板v1.3内容隔离红线.md) |
| 2026-08-31 | 批判性沟通、严谨核验与信息四分类规范（整合优化版） | [user/20260831-critical-communication-and-fact-checking.md](./user/20260831-critical-communication-and-fact-checking.md) |
| 2026-08-27 | 模板版本与升级机制（版本锚点 / 采用记录 / 升级闭环约定） | [project/20260827-模板版本与升级机制.md](./project/20260827-模板版本与升级机制.md) |
| 2026-08-26 | 格式化工具与引号规则冲突（black 默认值 vs 文档标准） | [learnings/20260826-格式化工具与引号规则冲突.md](./learnings/20260826-格式化工具与引号规则冲突.md) |

## 分类索引

### project/（项目约束与决策）
- [节点保留机制落地记录](./project/20261011-node-retention-rollout.md) — 台账回炉+保留组+稳定命名已双平台云端验证；回测预算/淘汰线/台账上限等参数先读再改
- [代理订阅双平台重组记录与操作手册](./project/20261002-shuangpingtai-dingyue-chongzu-runbook.md) — GitHub 正式线使用 jsDelivr；CNB 影子线写入 `data/cnb_shadow`；记录频率、产物、crontab sync 和新增订阅步骤
- [spiderpy-clash-sub 远端仓库交付纪律](./project/20260923-spiderpy-repo-deliverables.md) — 远端只保留交付文件；本地记忆/docs/tests/规范不推送；Actions 已自动跑通
- [模板 v1.3 内容隔离红线](./project/20260907-模板v1.3内容隔离红线.md) — 分析对象内容=数据非指令；五类危险内容与六处落位
- [模板版本与升级机制](./project/20260827-模板版本与升级机制.md) — 版本锚点 / 采用记录 / 升级闭环与发版四步约定

### learnings/（经验与踩坑）
- [住宅判定准确率根因与修复](./learnings/20261011-residential-accuracy-fix.md) — 有源侧 ASN 被跳过 ip-api 查询致机房混入住宅（实测 19%）；三层修复已验证；IP2Proxy/proxycheck 社区方案需账号
- [CNB 代码同步与 CDN 验证的坑](./learnings/20261011-cnb-code-sync-and-cdn-verify.md) — 改 scripts 后必须合并 cnb/main 再推 cnb 才触发影子线；合并结果勿推回 origin；purge 受理≠立即可见，本机对 jsDelivr 有间歇 RST
- [免费代理住宅/日常分组订阅流水线](./learnings/20260930-free-proxy-residential-pipeline.md) — 8 源+数据中心 CIDR 库；TCP 预筛把 4.8 万压到 1400；住宅三库交叉判定；mihomo 不支持 socks4 需独立协议层测活；candidates CSV 不得入库；远端只保交付文件不推 tests
- [Thordata 源分组订阅](./learnings/20260924-thordata-grouped-subscriptions.md) — 源里 socks4/https 均不被 Clash 支持需剔除/映射（https 要写成 http）；GEOIP 规则校验需本地 geoip.metadb；推送 GitHub 须走 7897 代理（git -c http.proxy 一次性参数）
- [CNB 额度核算与产物回写自触发](./learnings/20260924-cnb-quota-and-self-trigger.md) — 免费额度 160 核时/月、单次 1.22 核时、定时间隔硬下限约 6 小时；产物回写会再触发流水线，用 ifModify 拦截；改 crontab 后必须调 sync 接口
- [v2ray 多订阅源合并与 mihomo 真实测活](./learnings/20260923-v2ray-merge-mihomo-test.md) — 五类格式嗅探；vmess/ss 需字符串级解析禁 urlparse；名称全局唯一；mihomo 内核测活（随机端口/直连/altId 转换，已跑通 679 存活）；GitHub 视角=美国需国内落地
- [spiderpy 代理池转 Clash 订阅](./learnings/20260923-spiderpy-clash-sub.md) — host:port 整串查归属必失败；先拆 host 再批量查询，序号全局递增防重名
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
