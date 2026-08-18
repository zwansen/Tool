#!python3.11
# -*- coding: utf-8 -*-
"""GNSS 工具箱启动入口。

此文件用 .pyw 后缀，Windows 默认由 Python Launcher (pyw.exe -> pythonw.exe)
无控制台运行：双击即可弹出工具窗口，不闪黑窗、不依赖快捷方式。
启动异常由 main.py 的 sys.excepthook 记录到 main_crash.log（工程根目录）。
"""
import os
import sys

ROOT = r"D:\Tool\MyTool"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import runpy

runpy.run_path(os.path.join(ROOT, "main.py"), run_name="__main__")
