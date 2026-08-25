# 模板增强清单（DSXT 反馈回填）

> **用途**：记录 DSXT 项目相对本模板的增量，逐条给出"去留决策"，作为模板升级的审计依据与回填索引。
> **生成时间**：2026-08-26
> **增强源**：`E:\Users\Administrator\PycharmProjects\DSXT`（仅此一个源，按用户指示）
> **决策负责人**：用户（A/B 类入模板，C 类仅示例化，D 类并入资产）

## 一、增量分类与总表

| 分类 | 含义 | 处理 |
|------|------|------|
| A 类 通用纪律 | 与项目无关的强制规则/流程 | ✅ **内嵌**模板正文 |
| B 类 通用模式 | 场景化机制（如重构复刻时只读保护） | ✅ **附录**章节 + 脚本模板，按需启用 |
| C 类 项目特有 | DSXT 业务红线/配置 | ❌ 不进规则；提炼为 {{占位}} **填写示例** |
| D 类 可直接资产 | 完整 gitignore、快速上手命令等 | ✅ **并入**对应资产文件 |

| 编号 | 增量内容 | 来源（DSXT 位置） | 分类 | 去留决策 | 去向 |
|------|----------|-------------------|------|----------|------|
| INC-01 | Git 危险操作禁令 + 允许直接执行白名单 | AGENTS.md「Git 危险操作禁令」 | A | ✅ 内嵌 | 模板 AGENTS.md「Git 操作纪律」 |
| INC-02 | 禁止事项红线清单（eval/裸except/嵌套三元/缩写/缺注解/嵌套>3层） | AGENTS.md「禁止事项」 | A | ✅ 内嵌（去 DSXT 化） | 模板 AGENTS.md「禁止事项」 |
| INC-03 | 修改前强制自检清单（8 项） | AGENTS.md「执行强制检查」 | A | ✅ 内嵌 | 模板 AGENTS.md「修改前自检」 |
| INC-04 | 测试失败处理流程（停止→报告→等指示→通过才提交） | AGENTS.md「测试失败处理」 | A | ✅ 内嵌 | 模板 AGENTS.md「测试未过纪律」 |
| INC-05 | 约束恢复提醒：每 5 轮自查 + 会话结束最终检查 | AGENTS.md「约束恢复提醒」 | A | ✅ 内嵌 | 模板 AGENTS.md「长会话自查」 |
| INC-06 | 常见陷阱 ❌/✅ 对照示例（模板给通用例） | AGENTS.md「常见陷阱」 | A | ✅ 内嵌（去业务化） | 模板 AGENTS.md「常见陷阱」 |
| INC-07 | 技术栈锁定表升级为 约束/原因/位置 | AGENTS.md「核心约束 1」 | A | ✅ 内嵌 | 模板 CLAUDE.md §2 与 AGENTS.md |
| INC-08 | 分层依赖方向 + 三条逆向依赖禁令 | AGENTS.md「核心约束 2」 | A | ✅ 内嵌 | 模板 CLAUDE.md §3 与 AGENTS.md |
| INC-09 | 提交规范带 scope 枚举 + 提交前检查清单 | AGENTS.md「提交规范」 | A | ✅ 内嵌（scope 供填写） | 模板 AGENTS.md「提交规范」 |
| INC-10 | 编码标准细化（单引号/LF/行长≤120/缩写对照） | CLAUDE.md §4 | A | ✅ 内嵌 | 模板 CLAUDE.md §4 |
| INC-11 | 文件存放硬性规定（核对纠正） | 模板 CLAUDE.md 第 26-36 行与 AGENTS.md 均已完整包含该节，DSXT 无额外增量 | 免处理 | ✅ 原样保留，不重复修改 | 无需改动 |
| INC-12 | 快速上手命令（venv/ruff/black/pytest） | AGENTS.md「快速上手」 | D | ✅ 并入 | 模板 AGENTS.md「快速上手」（命令去业务化） |
| INC-13 | 源项目只读保护规则 + 校验脚本（git status + SHA-256 哈希） | 5.1/5.3 节 + scripts/verify/verify_firstpj_readonly.py | B | ✅ 附录（脱敏模板化） | 新增 docs/附录A_源项目只读保护.md + scripts/verify/verify_readonly.py.sample |
| INC-14 | 查漏补缺看板：认领标记 + 冲突提示区 + 归档链接 | docs/查漏补缺.md | B | ✅ 并入 | 模板 docs/任务看板.md |
| INC-15 | .gitignore 完整版（敏感 *.key/*.pem、备份 *.bak_*、源项目目录、运行时状态） | .gitignore | D | ✅ 并入 | 模板 gitignore.example |
| INC-16 | 记忆条目实战写法（硬约束条目、learnings 根因+修复） | .memory/project、.memory/learnings | C | ✅ 示例化 | 00_模板使用说明.md 与 .memory/README.md 补充说明 |
| INC-17 | DSXT 业务红线（FirstPj 路径、方案C、DPAPI、违规词、AI 供应商） | CLAUDE.md §5-7、.memory/project | C | ❌ 删除业务内容，保留"红线写法"骨架 | 模板 CLAUDE.md §5-8 提供 {{占位}} 范例 |
| INC-18 | 关键模块速查表 + 项目身份完整写法 | AGENTS.md「项目身份」「关键模块速查」 | C | ❌ 项目特有；模板保留"填写示例"说明 | 00_模板使用说明.md |

## 二、去 DSXT 化映射（确保模板零业务残留）

| DSXT 原内容 | 模板化后 |
|-------------|----------|
| `FirstPj` / `E:\Users\...\FirstPj\` | `{{源项目路径}}` / {{源项目名}} |
| `verify_firstpj_readonly.py` | `verify_readonly.py.sample` |
| `firstpj_hash_record.txt` | `{{源项目}}_hash_record.txt`（运行时生成，脚本内定义） |
| DrissionPage / FastAPI / React 版本 | 保留行格式，版本留空由各项目填 |
| 方案C / DPAPI / 违规词 / AI 供应商 | 删除；在"红线写法示例"节中示范"怎么写这类业务红线" |
| `docs/查漏补缺.md` | `docs/任务看板.md`（模板既有名） |
| scope: account/login/collection... | `scope: <按项目填写，如 account、login、...>` |

## 三、模板目标结构（升级后）

```
E:\PJdemo\
├── AGENTS.md                       # 升级：+INC-01~09,12 内嵌
├── CLAUDE.md                       # 升级：+INC-07,08,10,11 内嵌；§5-8 红线写法示例
├── .memory/                        # 保留；README 补记忆条目写法说明（INC-16）
│   ├── MEMORY.md
│   ├── README.md
│   ├── user/ project/ learnings/ archive/
├── .claude/settings.json           # 不动（SessionStart 注入记忆）
├── docs/
│   ├── 任务看板.md                  # 升级：+认领纪律 +冲突提示区 +归档链接（INC-14）
│   └── 附录A_源项目只读保护.md       # 新增：B 类附录（INC-13）
├── scripts/verify/
│   └── verify_readonly.py.sample   # 新增：脱敏校验脚本模板（INC-13）
├── gitignore.example               # 升级：完整版（INC-15）
└── 00_模板使用说明.md               # 升级：启用/删除/示例说明（INC-16,18）
```

## 四、验收标准（自我验证阶段）

1. 全文无 `DSXT`、`FirstPj`、`DrissionPage` 等业务字样（脚本 .sample 内 `{{源项目_}}` 占位除外）。
2. 占位符统一用 `{{...}}`，复制后逐个可替换；无遗漏的裸业务名词。
3. 附录与示例章节是"可选项"，说明文档写明"不需要就删除"。
4. 模板复制到空目录 → 按 00_模板使用说明.md 填占位 → 删除说明与附录 → 可独立使用。
5. 校验脚本 `verify_readonly.py.sample` 填 {{源项目路径}} 后 `--before/--after` 可运行。

## 五、未采纳项（记录在案，供未来参考）

- DSXT 的 Web 前端/部署/多 Worker 相关内容：纯项目形态，不入模板。
- `scripts/` 数十个 diag/migrate/deploy 脚本：均为 DSXT 具体业务，不通用，不入模板。
- `p0_demo/`、`logs/`、`data/` 实体内容：运行时产物，不入模板。