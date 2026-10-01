"""CNB 影子流水线测试：spiderpy 输出路径与平台产物对比。"""

from pathlib import Path

import yaml

from pytest import approx

from sub_aggregator import shadow_compare


def _write_clash(path: Path, *endpoints: tuple[str, str, str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    proxies = [
        {
            "name": name,
            "type": proto,
            "server": server,
            "port": port,
        }
        for name, proto, server, port in endpoints
    ]
    payload = {
        "mixed-port": 7890,
        "proxies": proxies,
        "proxy-groups": [
            {"name": "G", "type": "select", "proxies": [item["name"] for item in proxies]}
        ],
        "rules": ["MATCH,G"],
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")


def test_compare_shadow_outputs_counts_overlap_and_appends_history(tmp_path):
    github_dir = tmp_path / "github"
    shadow_dir = tmp_path / "cnb_shadow"

    _write_clash(
        github_dir / "daily.yaml",
        ("[D]US-0001", "http", "1.1.1.1", 8080),
        ("[D]JP-0002", "socks5", "2.2.2.2", 1080),
    )
    _write_clash(
        shadow_dir / "daily.yaml",
        ("[D]US-0001", "http", "1.1.1.1", 8080),
        ("[D]DE-0003", "http", "3.3.3.3", 8080),
    )
    _write_clash(
        github_dir / "residential.yaml",
        ("[R]US-0001", "http", "4.4.4.4", 8080),
    )
    _write_clash(
        shadow_dir / "residential.yaml",
        ("[R]US-0001", "http", "4.4.4.4", 8080),
        ("[R]GB-0002", "socks5", "5.5.5.5", 1080),
    )
    _write_clash(
        github_dir / "spiderpy.yaml",
        ("United States 1", "http", "6.6.6.6", 8080),
    )
    _write_clash(
        shadow_dir / "spiderpy.yaml",
        ("United States 1", "http", "6.6.6.6", 8080),
        ("Germany 2", "http", "7.7.7.7", 3128),
    )
    (github_dir / "residential-socks4.yaml").write_text(
        "# header\n8.8.8.8:1080  # US ISP A\n9.9.9.9:1080  # JP ISP B\n",
        encoding="utf-8",
    )
    (shadow_dir / "residential-socks4.yaml").write_text(
        "# header\n8.8.8.8:1080  # US ISP A\n",
        encoding="utf-8",
    )

    result = shadow_compare.compare_and_write(
        github_dir=github_dir,
        github_spiderpy=github_dir / "spiderpy.yaml",
        shadow_dir=shadow_dir,
        shadow_spiderpy=shadow_dir / "spiderpy.yaml",
    )

    daily = result["artifacts"]["daily.yaml"]
    assert daily["github"]["count"] == 2
    assert daily["cnb"]["count"] == 2
    assert daily["shared"] == 1
    assert daily["github_only"] == 1
    assert daily["cnb_only"] == 1
    assert daily["jaccard"] == approx(1 / 3, abs=1e-6)

    socks4 = result["artifacts"]["residential-socks4.yaml"]
    assert socks4["github"]["count"] == 2
    assert socks4["cnb"]["count"] == 1
    assert socks4["shared"] == 1

    assert (shadow_dir / "comparison.json").exists()
    assert (shadow_dir / "comparison.md").exists()
    history_lines = (shadow_dir / "comparison-history.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(history_lines) == 1

    # 第二次运行只追加一行历史，当前报告始终覆盖为最新结果。
    shadow_compare.compare_and_write(
        github_dir=github_dir,
        github_spiderpy=github_dir / "spiderpy.yaml",
        shadow_dir=shadow_dir,
        shadow_spiderpy=shadow_dir / "spiderpy.yaml",
    )
    history_lines = (shadow_dir / "comparison-history.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(history_lines) == 2


def test_spiderpy_generator_supports_custom_output(monkeypatch, tmp_path):
    scripts_dir = Path(__file__).resolve().parent.parent / "scripts"
    monkeypatch.syspath_prepend(str(scripts_dir))

    import generate

    output = tmp_path / "shadow-spiderpy.yaml"
    args = generate.parse_args(["--output", str(output)])

    assert args.output == output
