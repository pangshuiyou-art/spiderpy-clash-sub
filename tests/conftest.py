#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pytest 共享夹具：把各脚本目录加入 import 路径"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for parts in (('scripts', 'v2ray_merger'), ('scripts', 'free_proxy_merger')):
    script_dir = ROOT.joinpath(*parts)
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
