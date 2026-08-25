"""启用模板时的占位符兜底检查脚本。

遍历模板默认的关键文件，输出所有未替换的 {{...}} 占位符，
退出码：0=无占位符，1=存在占位符，便于 CI / 启用流程自动阻塞。

默认检查范围（按模板结构设定，启用后可按需扩充 DEFAULT_PATTERNS）：
- AGENTS.md / CLAUDE.md
- pyproject.toml.sample / requirements.txt.sample
- docs/**/*.md、scripts/**/*.sample、scripts/**/*.py
- .memory/**/*.md（含 archive 子目录）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 默认检查的 glob 模式（启用模板后按需增删）
DEFAULT_PATTERNS: list[str] = [
    'AGENTS.md',
    'CLAUDE.md',
    '*.sample',
    'docs/**/*.md',
    'scripts/**/*.sample',
    'scripts/**/*.py',
    '.memory/**/*.md',
]

PLACEHOLDER_RE = re.compile(r'\{\{[^}]+\}\}')


def collect_files() -> list[Path]:
    """收集所有需要检查的文件（去重、排序）。"""
    files: set[Path] = set()
    for pattern in DEFAULT_PATTERNS:
        for path in PROJECT_ROOT.glob(pattern):
            if path.is_file():
                files.add(path)
    return sorted(files)


def scan_file(file_path: Path) -> list[tuple[int, str]]:
    """扫描单个文件，返回 (行号, 占位符匹配串) 列表。"""
    matches: list[tuple[int, str]] = []
    try:
        text = file_path.read_text(encoding='utf-8')
    except UnicodeDecodeError:
        return matches
    for line_no, line in enumerate(text.splitlines(), start=1):
        for match in PLACEHOLDER_RE.findall(line):
            matches.append((line_no, match))
    return matches


def main() -> int:
    files = collect_files()
    total_hits = 0
    print(f'检查 {len(files)} 个文件中是否存在未替换的 {{...}} 占位符...\n')

    for file_path in files:
        hits = scan_file(file_path)
        if not hits:
            continue
        rel = file_path.relative_to(PROJECT_ROOT)
        print(f'[FOUND] {rel}')
        for line_no, match in hits:
            print(f'  L{line_no:<4} {match}')
            total_hits += 1

    if total_hits == 0:
        print('✅ 未发现占位符，模板已完整启用。')
        return 0
    print(f'\n❌ 共发现 {total_hits} 处未替换占位符，请按上列清单处理。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
