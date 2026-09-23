#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pytest 共享夹具：把 scripts/v2ray_merger 加入 import 路径"""
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / 'scripts' / 'v2ray_merger'
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))