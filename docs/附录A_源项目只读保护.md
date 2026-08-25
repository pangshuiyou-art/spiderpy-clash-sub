# 附录A：源项目只读保护（可选启用）

> **适用场景**：本项目（以下简称"新项目"）从另一项目（{{源项目}}）重构/复刻而来，{{源项目}} 文件必须**零修改**。
> **不适用则跳过**：若本项目没有重构复刻来源，删除本附录与 `scripts/verify/verify_readonly.py`，并在 `AGENTS.md`「核心约束」中删除相关引用。

## 一、为什么需要这个附录

AI 在重构复刻类项目中最常犯的错是：把源项目文件**直接改或写入**（修 bug、粘贴配置、跑测试），污染源项目、破坏新项目与源的对应关系。
本附录用「规则 + 校验脚本」把"不敢改"变成**可验证**：任何阶段前后跑一次脚本，Source 的 git 状态与核心文件哈希一比对，改动立刻暴露。

## 二、只读规则（写入 AGENTS.md / CLAUDE.md 专用红线节）

| 操作 | 允许 |
|------|------|
| ❌ 修改 {{源项目}} 任何 `.py` / `.json` / `.md` / `.txt` 文件 | ✅ 只读读取源代码、配置、数据文件 |
| ❌ 在 {{源项目}} 目录创建任何新文件 | ✅ 复制核心文件到新项目（复制后源文件不动） |
| ❌ 删除或重命名 {{源项目}} 任何文件 | ✅ 新项目内修改副本 |
| ❌ 对 {{源项目}} 仓库执行 git stash / reset / clean | — |

## 三、启用步骤（每项目一次）

1. 复制 `scripts/verify/verify_readonly.py.sample` 到新项目 `scripts/verify/verify_readonly.py`（去 `.sample` 后缀）。
2. 编辑脚本顶部配置：
   - `SOURCE_ROOT`：{{源项目}}绝对路径
   - `HASH_RECORD`：哈希记录文件路径（默认 `scripts/verify/{{源项目}}_hash_record.txt`）
   - `CORE_FILES`：需要哈希比对的核心文件相对路径清单（源项目内）
3. 在 `CLAUDE.md` 项目专属红线节，把「5.X 源项目只读保护」示例骨架替换为实际内容。
4. 在 `AGENTS.md`「禁止事项」「核心约束」「修改前自检」「每 5 轮自查」等节的 `{{只读目录}}` 占位替换为 {{源项目}} 目录。

## 四、阶段校验（每次开发阶段开始/结束必跑）

```bash
python scripts/verify/verify_readonly.py --before   # 阶段开始前：确认源项目 git 干净 + 记录哈希
python scripts/verify/verify_readonly.py --after    # 阶段结束后：确认源项目未被改动 + 哈希一致
python scripts/verify/verify_readonly.py --compare  # 仅计算并打印当前哈希（排查用）
```

- 脚本退出码：0=通过，1=失败。`--before` 会更新哈希记录文件；`--after` 与记录比对。
- 失败即终止阶段，查明并恢复后再继续。

## 五、职责分工小结

| 文件 | 职责 |
|------|------|
| `docs/附录A_源项目只读保护.md` | 本说明（写规则 + 启用步骤） |
| `scripts/verify/verify_readonly.py.sample` | 校验脚本模板（复制后改名 `verify_readonly.py` 并配置） |
| `scripts/verify/{{源项目}}_hash_record.txt` | 运行时生成的哈希记录（纳入 .gitignore） |

> 哈希记录文件按 `.gitignore` 中 `{{源项目}}_hash_record.txt` 建议忽略，不入库；如需审计留痕可反选跟踪。