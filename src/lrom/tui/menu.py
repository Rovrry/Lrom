"""交互式主菜单。"""

from __future__ import annotations

import sys

MENU = """
  1) 分析 ROM 包        判断能否线刷
  2) 设备信息           查看当前连接的设备
  3) 生成刷写计划       把包转成可审阅的刷写计划
  4) 预演检查           模拟执行，不写设备
  5) 线刷全量包         执行刷写
  6) 环境自检           检查依赖与权限

  0) 退出
"""


def run_menu() -> int:
    print()
    print("  Lrom — Linux 原生 Android 线刷工具")
    print(MENU)
    try:
        choice = input("  请选择: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return 0

    if choice in ("1",):
        from ..core.package import inspect_package
        from ..cli import print_info

        path = input("  ROM 包路径: ").strip().strip("'\"")
        info = inspect_package(path)
        print_info(info)
        return 0
    if choice in ("2",):
        from ..cli import cmd_devices
        import argparse

        return cmd_devices(argparse.Namespace())

    print("  该功能尚未实现")
    return 0
