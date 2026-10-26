#!/usr/bin/env bash
# Lrom 安装脚本
#
# 用法: ./install.sh
#
# 会在项目内创建 venv 并把 lrom 命令链接到 ~/.local/bin

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SRC_DIR/.venv"
BIN_DIR="${HOME}/.local/bin"

echo "==> 安装目录: $SRC_DIR"

# 1) 检查 Python
if ! command -v python3 >/dev/null 2>&1; then
  echo "错误: 未找到 python3" >&2
  exit 1
fi
PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
echo "==> Python $PYVER"

# 2) 检查 adb / fastboot
missing=()
command -v adb >/dev/null 2>&1 || missing+=("adb")
command -v fastboot >/dev/null 2>&1 || missing+=("fastboot")
if [ ${#missing[@]} -gt 0 ]; then
  echo
  echo "警告: 未找到 ${missing[*]}"
  echo "  请安装（不需要 root）:"
  echo "    Debian/Ubuntu/Kali: sudo apt install adb fastboot"
  echo "    Arch:               sudo pacman -S android-tools"
  echo "    Fedora:             sudo dnf install android-tools"
  echo
  echo "  也可以稍后写配置: ~/.config/lrom/config.json"
fi

# 3) 创建 venv 并安装
echo "==> 创建虚拟环境"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -e "$SRC_DIR"
echo "==> 安装完成"

# 4) 链接到 ~/.local/bin
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/lrom" "$BIN_DIR/lrom"

echo
echo "✓ lrom 已安装到 $BIN_DIR/lrom"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    echo
    echo "注意: $BIN_DIR 不在 PATH 中，请加入 shell 配置："
    echo "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc   # 或 ~/.bashrc"
    ;;
esac
echo
echo "下一步:"
echo "  lrom devices"
echo "  lrom inspect <rom.zip>"
