"""pytest 根 conftest：项目根入 sys.path（脚本式测试目录化后统一入口）。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
