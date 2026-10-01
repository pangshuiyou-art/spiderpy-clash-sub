#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""命令行入口：对比 GitHub 正式产物与 CNB 影子产物。"""

import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from sub_aggregator import shadow_compare  # type: ignore
else:
    from . import shadow_compare


if __name__ == "__main__":
    raise SystemExit(shadow_compare.main())
