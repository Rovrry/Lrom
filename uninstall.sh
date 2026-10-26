#!/usr/bin/env bash
# Lrom 卸载脚本
#
# 用途：如果你运行过 ./install.sh 把 lrom 加入了系统 PATH，用本脚本清理。
#
# 注意：Lrom 本身是"克隆即用"的，不安装任何东西到系统。
# 如果你从未运行过 install.sh，直接删除仓库目录即可，无需本脚本。

set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${HOME}/.local/bin"
VENV="$SRC_DIR/.venv"

echo "==> Lrom 卸载"

removed=0

# 1) 移除 PATH 中的链接
if [ -L "$BIN_DIR/lrom" ] || [ -e "$BIN_DIR/lrom" ]; then
  rm -f "$BIN_DIR/lrom"
  echo "  ✓ 已移除 $BIN_DIR/lrom"
  removed=$((removed + 1))
fi

# 2) 移除 venv
if [ -d "$VENV" ]; then
  rm -rf "$VENV"
  echo "  ✓ 已移除虚拟环境 $VENV"
  removed=$((removed + 1))
fi

# 3) 询问是否移除用户配置
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/lrom"
if [ -d "$CONFIG" ]; then
  echo
  echo "  发现配置文件: $CONFIG"
  echo "  （包含工具路径、上次使用的 ROM 包等）"
  printf "  删除配置吗？(y/N): "
  read -r ans
  case "$ans" in
    [yY]*)
      rm -rf "$CONFIG"
      echo "  ✓ 已移除配置文件"
      removed=$((removed + 1))
      ;;
    *) echo "  保留配置文件" ;;
  esac
fi

# 4) 询问是否移除工作目录（解包产物 / 备份）
for d in "$SRC_DIR/work" "$SRC_DIR/backups"; do
  if [ -d "$d" ]; then
    size=$(du -sh "$d" 2>/dev/null | cut -f1)
    echo
    echo "  发现数据目录: $d ($size)"
    printf "  删除吗？(y/N): "
    read -r ans
    case "$ans" in
      [yY]*)
        rm -rf "$d"
        echo "  ✓ 已删除 $d"
        ;;
      *) echo "  保留 $d" ;;
    esac
  fi
done

echo
if [ "$removed" -eq 0 ]; then
  echo "  系统中没有找到 Lrom 的安装痕迹。"
  echo "  （Lrom 是克隆即用的，本就不需要安装）"
else
  echo "✓ 卸载完成"
fi

echo
echo "仓库目录仍在: $SRC_DIR"
echo "如需彻底删除，执行: rm -rf \"$SRC_DIR\""
