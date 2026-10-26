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
# ---------------------------------------------------------------------------
# 注意：Linux 内核自带 USB 驱动，不需要像 Windows 那样装驱动。
# 需要的是 adb / fastboot 这两个【命令行工具】。
missing=()
command -v adb >/dev/null 2>&1 || missing+=("adb")
command -v fastboot >/dev/null 2>&1 || missing+=("fastboot")

# 也检查 Lrom 配置文件里是否已指定可用路径
CONFIG="${XDG_CONFIG_HOME:-$HOME/.config}/lrom/config.json"
if [ ${#missing[@]} -gt 0 ] && [ -f "$CONFIG" ]; then
  for t in adb fastboot; do
    p=$(python3 -c "
import json,sys
try:
    print(json.load(open('$CONFIG')).get('tools',{}).get('$t',''))
except Exception: pass" 2>/dev/null || true)
    if [ -n "$p" ] && [ -x "$p" ]; then
      echo "==> $t 已由配置文件指定: $p"
      newmissing=()
      for m in "${missing[@]:-}"; do [ "$m" != "$t" ] && newmissing+=("$m"); done
      missing=("${newmissing[@]:-}")
    fi
  done
fi

if [ ${#missing[@]} -gt 0 ] && [ -n "${missing[0]:-}" ]; then
  echo
  echo "+--------------------------------------------------------------+"
  echo "| 未找到命令: ${missing[*]}"
  echo "+--------------------------------------------------------------+"
  cat <<'EOF'


说明: Linux 内核自带 USB 驱动, 无需像 Windows 那样安装驱动.
      需要的是 adb / fastboot 命令行工具.

【推荐】用系统包管理器安装:

  Debian / Ubuntu / Kali:
      sudo apt update
      sudo apt install adb fastboot android-sdk-platform-tools-common

  Arch:
      sudo pacman -S android-tools android-udev

  Fedora:
      sudo dnf install android-tools

注意 Kali/Debian 的坑:
  - 包名是 adb / fastboot, 不是 android-tools-adb (那是废弃空包)
  - android-sdk-platform-tools-common 提供 udev 规则,
    不装的话 adb/fastboot 需要 root 才能访问设备

【没有 sudo 时】手动下载到用户目录:

  mkdir -p ~/bin/platform-tools && cd ~/bin/platform-tools
  for p in android-libbase android-libcutils android-liblog \
           android-libziparchive android-libboringssl android-libsparse \
           adb fastboot; do apt-get download $p; done
  for f in *.deb; do dpkg-deb -x "$f" .; done

  然后在 ~/.config/lrom/config.json 里指定路径 (见 README)

安装后重新运行本脚本.
EOF
  echo
  echo "==> 继续安装（离线功能 inspect/unpack/plan/arb 仍可使用）"
  echo
fi

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 3) 提示：Lrom 其实不需要安装
# ---------------------------------------------------------------------------
cat <<'EOF'

────────────────────────────────────────────────────────────────────
  说明：Lrom 不需要安装！
────────────────────────────────────────────────────────────────────

  Lrom 是零第三方依赖的 Python 程序，克隆即可直接运行：

      ./lrom                # 交互式菜单
      ./lrom devices        # 子命令

  卸载方法：直接删除本仓库目录即可，不会在系统中留下任何文件。

  本安装脚本仅供希望把 lrom 加入 PATH 的用户使用
  （这样可以在任意目录直接输入 lrom）。
  如果你只是想在仓库目录内使用，可以直接 Ctrl+C 中止本脚本。

────────────────────────────────────────────────────────────────────

EOF

if [ "${1:-}" != "-y" ] && [ "${1:-}" != "--yes" ]; then
  printf "  继续安装到系统 PATH？(y/N): "
  read -r ans
  case "$ans" in
    [yY]*) ;;
    *) echo "  已取消。直接运行 ./lrom 即可使用。"; exit 0 ;;
  esac
fi

# ---------------------------------------------------------------------------
# 4) 创建 venv 并安装
# ---------------------------------------------------------------------------
if [ ! -d "$VENV" ]; then
  echo "==> 创建虚拟环境"
  python3 -m venv "$VENV"
fi

"$VENV/bin/pip" install --quiet --upgrade pip 2>/dev/null || true
"$VENV/bin/pip" install --quiet -e "$SRC_DIR" 2>/dev/null || {
  echo "警告: 网络问题，改用离线安装（Lrom 无第三方依赖）"
  "$VENV/bin/pip" install --quiet --no-index --no-build-isolation -e "$SRC_DIR"
}
echo "==> 安装完成"

# ---------------------------------------------------------------------------
# 5) 链接到 ~/.local/bin
# ---------------------------------------------------------------------------
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
echo "卸载方法: 运行 ./uninstall.sh"
echo
echo "下一步:"
echo "  lrom                 # 交互式菜单"
echo "  lrom selfcheck       # 环境自检"
