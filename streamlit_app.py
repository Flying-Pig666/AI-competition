# -*- coding: utf-8 -*-
"""Streamlit Cloud 部署入口(main file 填 streamlit_app.py,纯 ASCII 路径)。

真正的应用在 ai初版示例/demo/app.py——仓库路径含中文,Cloud 直接填中文
main file 可能踩 URL 编码的坑,故用本文件做引导:切入 demo 目录后原样执行。
"""
import os
import runpy
import sys

_DEMO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai初版示例", "demo")
sys.path.insert(0, _DEMO)
os.chdir(_DEMO)
runpy.run_path(os.path.join(_DEMO, "app.py"), run_name="__main__")
