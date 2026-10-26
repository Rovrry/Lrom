"""菜单模块完整性测试。

背景：v0.3.0 发布时，我用脚本批量改造 menu.py，
导致 ask/ask_path/confirm/pause 四个函数被误删，
但静态语法仍能通过（Python 只在运行时解析名字），
结果用户点击菜单项 7 时才崩溃：

    NameError: name 'pause' is not defined

这类问题用语法检查发现不了，必须在 CI 里做名字解析检查。
"""

from __future__ import annotations

import ast
import builtins
import importlib
import inspect
from pathlib import Path

import pytest

MENU_PATH = Path(__file__).resolve().parents[2] / "src" / "lrom" / "tui" / "menu.py"


def _collect_defined(tree: ast.Module) -> set[str]:
    defined: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    defined.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            defined.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                defined.add(a.asname or a.name.split(".")[0])
        elif isinstance(node, (ast.comprehension,)):
            pass
    # 函数内部赋值也算（粗粒度，足以覆盖本模块）
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
    return defined


def _collect_called(tree: ast.Module) -> set[str]:
    """收集被调用的本地函数名。

    注意：要排除两种误报
      1. 关键字参数名（f(redraw=...) 里的 redraw 不是被调用的函数）
      2. 函数内定义的局部函数（它们不在模块级别，但确实存在）
    """
    called = {
        n.func.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    # 排除关键字参数名
    kwargs = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            for kw in n.keywords:
                if kw.arg:
                    kwargs.add(kw.arg)
    return called - kwargs


def test_no_undefined_module_level_calls():
    """菜单模块里被调用的本地函数必须都已定义。

    这条测试直接对应真实事故：pause() 被误删导致运行时报错。
    """
    src = MENU_PATH.read_text(encoding="utf-8")
    tree = ast.parse(src)

    defined = _collect_defined(tree)
    called = _collect_called(tree)
    missing = sorted(called - defined - set(dir(builtins)))

    assert not missing, (
        f"menu.py 中存在未定义的名字: {missing}\n"
        f"这会导致菜单运行时抛 NameError。"
    )


def test_menu_module_imports():
    """模块必须能正常导入。"""
    mod = importlib.import_module("lrom.tui.menu")
    assert hasattr(mod, "run_menu")


@pytest.mark.parametrize(
    "name",
    ["ask", "ask_path", "confirm", "pause", "build_menu", "print_header", "Session"],
)
def test_required_helpers_exist(name):
    """这些辅助函数是菜单的基础设施，缺一个就会崩。"""
    mod = importlib.import_module("lrom.tui.menu")
    assert hasattr(mod, name), f"menu 模块缺少 {name}"


@pytest.mark.parametrize(
    "name",
    [
        "_flow_unpack",
        "_flow_flash",
        "_menu_backup",
        "_menu_arb",
        "_menu_device_info",
        "_menu_reboot",
        "_menu_selfcheck",
        "_menu_about",
    ],
)
def test_menu_handlers_signature(name):
    """每个菜单处理函数必须是 (sess, mon) 两参数。"""
    mod = importlib.import_module("lrom.tui.menu")
    fn = getattr(mod, name, None)
    assert fn is not None, f"menu 模块缺少处理函数 {name}"
    params = list(inspect.signature(fn).parameters)
    assert params[:2] == ["sess", "mon"], f"{name} 签名应为 (sess, mon)，实际 {params}"


def test_all_handlers_referenced_in_run_menu():
    """run_menu 里注册的每个处理函数都必须存在。"""
    mod = importlib.import_module("lrom.tui.menu")
    tree = ast.parse(MENU_PATH.read_text(encoding="utf-8"))

    run_menu = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_menu"
    )
    src = ast.get_source_segment(MENU_PATH.read_text(encoding="utf-8"), run_menu) or ""

    for fn_name in (
        "_flow_unpack",
        "_flow_flash",
        "_menu_backup",
        "_menu_arb",
        "_menu_device_info",
        "_menu_reboot",
        "_menu_selfcheck",
        "_menu_about",
    ):
        assert fn_name in src, f"run_menu 未注册 {fn_name}"
        assert hasattr(mod, fn_name)


# ---------------------------------------------------------------------------
# Root 菜单项（v0.4.3 新增）
# ---------------------------------------------------------------------------


def test_root_menu_handlers_defined():
    """Root 三个菜单处理函数必须存在且可调用。"""
    from lrom.tui import menu

    for name in (
        "_menu_root",
        "_menu_fetch_manager",
        "_menu_root_rollback",
        "_find_stock_init_boot",
    ):
        fn = getattr(menu, name, None)
        assert fn is not None, f"菜单缺少 {name}"
        assert callable(fn), f"{name} 不可调用"


def test_root_menu_entries_in_dispatch():
    """菜单分发表必须包含 Root 三项。"""
    import inspect

    from lrom.tui import menu

    src = inspect.getsource(menu)
    for item in ('"7": _menu_root', '"8": _menu_fetch_manager', '"9": _menu_root_rollback'):
        assert item in src, f"分发表缺少 {item}"


def test_menu_lists_root_section():
    """渲染出的菜单必须包含 Root 一节和三个入口。"""
    from lrom.tui import menu

    class FakeSess:
        package = None
        unpacked = None
        image_dir = None
        work_dir = "root-work"

    text = menu.build_menu(FakeSess(), True)
    assert "Root" in text
    assert "获取 Root" in text
    assert "下载 Root 管理器" in text
    assert "回滚 Root" in text


def test_menu_entry_count_matches_dispatch():
    """菜单里编号的项与分发表里的键必须一致（防漏接）。"""
    import inspect
    import re

    from lrom.tui import menu

    class FakeSess:
        package = None
        unpacked = None
        image_dir = None
        work_dir = "root-work"

    text = menu.build_menu(FakeSess(), True)
    shown = set(re.findall(r"\)\s\S", text))
    # 从渲染文本里提取"数字)"形式的编号
    nums = set(re.findall(r"^\s+(\d+)\)", text, re.M)) | set(
        re.findall(r"\s(\d+)\)\s", text)
    )
    nums.discard("0")

    src = inspect.getsource(menu)
    # 分发表形如  "7": _menu_root   或  "2": lambda s_, m: ...
    dispatch_block = src[src.index("handlers = {") :]
    dispatch_block = dispatch_block[: dispatch_block.index("}")]
    dispatch = set(re.findall(r'"(\d+)":', dispatch_block))
    assert nums == dispatch, f"菜单编号 {sorted(nums)} 与分发 {sorted(dispatch)} 不一致"
