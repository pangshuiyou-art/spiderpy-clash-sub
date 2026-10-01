#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub 正式产物与 CNB 影子产物对比。

对比只基于最终产物中的协议三元组（type/server/port），不依赖各平台生成的
节点显示名。socks4 是纯文本清单，按 ip:port 对比。
"""

from __future__ import annotations

import argparse
import json
import time
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

ARTIFACTS = (
    ("daily.yaml", "clash"),
    ("residential.yaml", "clash"),
    ("spiderpy.yaml", "clash"),
    ("residential-socks4.yaml", "socks4"),
)


def _clash_endpoints(path: Path) -> set[tuple[str, str, int]]:
    if not path.exists():
        return set()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    endpoints: set[tuple[str, str, int]] = set()
    for item in data.get("proxies") or []:
        if not isinstance(item, dict):
            continue
        try:
            endpoints.add((str(item.get("type") or ""), str(item.get("server") or ""),
                           int(item.get("port") or 0)))
        except (TypeError, ValueError):
            continue
    return endpoints


def _socks4_endpoints(path: Path) -> set[tuple[str, str, int]]:
    if not path.exists():
        return set()
    endpoints: set[tuple[str, str, int]] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        host, _, port = line.rpartition(":")
        if host and port.isdigit():
            endpoints.add(("socks4", host, int(port)))
    return endpoints


def _endpoints(path: Path, kind: str) -> set[tuple[str, str, int]]:
    if kind == "socks4":
        return _socks4_endpoints(path)
    return _clash_endpoints(path)


def _distribution(endpoints: set[tuple[str, str, int]]) -> dict[str, dict[str, int]]:
    return {
        "by_protocol": dict(Counter(item[0] for item in endpoints)),
    }


def _side(path: Path, endpoints: set[tuple[str, str, int]]) -> dict[str, Any]:
    return {
        "path": str(path),
        "exists": path.exists(),
        "count": len(endpoints),
        **_distribution(endpoints),
    }


def compare_artifacts(
    *,
    github_dir: Path,
    github_spiderpy: Path,
    shadow_dir: Path,
    shadow_spiderpy: Path,
) -> dict[str, Any]:
    github_paths = {
        "daily.yaml": github_dir / "daily.yaml",
        "residential.yaml": github_dir / "residential.yaml",
        "spiderpy.yaml": github_spiderpy,
        "residential-socks4.yaml": github_dir / "residential-socks4.yaml",
    }
    shadow_paths = {
        "daily.yaml": shadow_dir / "daily.yaml",
        "residential.yaml": shadow_dir / "residential.yaml",
        "spiderpy.yaml": shadow_spiderpy,
        "residential-socks4.yaml": shadow_dir / "residential-socks4.yaml",
    }

    result: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
        "artifacts": {},
    }
    for filename, kind in ARTIFACTS:
        github_endpoints = _endpoints(github_paths[filename], kind)
        shadow_endpoints = _endpoints(shadow_paths[filename], kind)
        shared = github_endpoints & shadow_endpoints
        union = github_endpoints | shadow_endpoints
        result["artifacts"][filename] = {
            "github": _side(github_paths[filename], github_endpoints),
            "cnb": _side(shadow_paths[filename], shadow_endpoints),
            "shared": len(shared),
            "github_only": len(github_endpoints - shadow_endpoints),
            "cnb_only": len(shadow_endpoints - github_endpoints),
            "jaccard": round(len(shared) / len(union), 6) if union else 0.0,
        }
    return result


def _markdown(result: dict[str, Any]) -> str:
    lines = [
        "# GitHub / CNB 影子产物对比",
        "",
        f"生成时间: {result['generated_at']}",
        "",
        "| 产物 | GitHub | CNB | 重合 | GitHub独有 | CNB独有 | Jaccard |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for filename, item in result["artifacts"].items():
        lines.append(
            f"| {filename} | {item['github']['count']} | {item['cnb']['count']} | "
            f"{item['shared']} | {item['github_only']} | {item['cnb_only']} | "
            f"{item['jaccard']:.3f} |"
        )
    lines.extend([
        "",
        "说明：CNB 为影子环境，调度时间、源站响应和国内外网络不同，两侧结果不要求一致。",
        "重合度按协议、服务器、端口计算；节点名称不参与对比。",
        "",
    ])
    return "\n".join(lines)


def compare_and_write(
    *,
    github_dir: Path,
    github_spiderpy: Path,
    shadow_dir: Path,
    shadow_spiderpy: Path,
    report_dir: Path | None = None,
) -> dict[str, Any]:
    """写出当前 JSON/Markdown 报告，并向 JSONL 历史追加一行。"""
    result = compare_artifacts(
        github_dir=github_dir,
        github_spiderpy=github_spiderpy,
        shadow_dir=shadow_dir,
        shadow_spiderpy=shadow_spiderpy,
    )
    report_dir = report_dir or shadow_dir
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / "comparison.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (report_dir / "comparison.md").write_text(_markdown(result), encoding="utf-8")
    with (report_dir / "comparison-history.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(result, ensure_ascii=False, sort_keys=True) + "\n")
    return result


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对比 GitHub 与 CNB 影子产物")
    parser.add_argument("--github-dir", type=Path, required=True)
    parser.add_argument("--github-spiderpy", type=Path, required=True)
    parser.add_argument("--shadow-dir", type=Path, required=True)
    parser.add_argument("--shadow-spiderpy", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, default=None,
                        help="对比报告输出目录，默认与 shadow-dir 相同")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    result = compare_and_write(
        github_dir=args.github_dir,
        github_spiderpy=args.github_spiderpy,
        shadow_dir=args.shadow_dir,
        shadow_spiderpy=args.shadow_spiderpy,
        report_dir=args.report_dir,
    )
    for filename, item in result["artifacts"].items():
        print(
            f"{filename}: GitHub={item['github']['count']} "
            f"CNB={item['cnb']['count']} shared={item['shared']} "
            f"jaccard={item['jaccard']:.3f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
