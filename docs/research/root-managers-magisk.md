# Magisk 系 / ramdisk 离线修补路线调研

> 调研对象：**「离线 patch boot 镜像」流派**——即在 PC 侧（或设备端 Magisk APP）对启动镜像做字节级修补，从而获得 root。
> 与「内核级 root」（KernelSU / SukiSU / APatch 等，见 `root-managers-kernelsu.md`）是**完全不同的两条路线**，请勿混用。
>
> 目标设备：OnePlus OPD2404（一加平板 Pro），代号 OP5D77L1，Android 16 / SDK 36，内核 `6.1.141-android14-11`，A/B 设备，bootloader 已解锁，当前无 root。
>
> 调研时间：2026-10-27。所有数字（star / 发布日期 / 版本号）均为当日实测值，来源 URL 就近标注；**无法查证的条目一律写「未确认」**。

---

## 0. 结论速览（先看这里）

| 问题 | 结论 | 置信度 |
|---|---|---|
| **Android 13+ 该 patch `boot.img` 还是 `init_boot.img`？** | **patch `init_boot.img`**（GKI 2.0 起 ramdisk 移出 boot）。仅当设备没有独立 `init_boot` 分区、或 ramdisk 落在 `vendor_boot` 时才不同 | 高（官方文档 + 官方检测脚本源码 + 本机实测三方交叉验证） |
| 这台机器（SD 8 Gen 3 / Android 16）具体 patch 哪个？ | **`init_boot.img`**（本机 boot.img 的 ramdisk_size = 0，init_boot.img 内才有 ramdisk） | 高 |
| Magisk 官方安装方式 | APP 内「安装 → 选择并修补一个文件」，**必须在设备上运行**；官方**明确禁止**跨设备 patch | 高 |
| 能否 PC 侧离线 patch？ | **可以**，用 `magiskboot` + `boot_patch.sh`（官方脚本，但非官方支持的安装流程）；v31.1 起 `magiskboot` 支持在 Linux/macOS 宿主机构建 | 中高 |
| Magisk Delta / Kitsune Mask 还活跃吗？ | **不活跃，事实上已消亡**：HuskyDG 账号与 `magisk-files` 仓库均已 404 | 高 |
| Magisk Alpha（vvb2060）活跃吗？ | **活跃**，2026-10-26 刚发布 `31100`，紧跟上游 v31.1 | 高 |
| 需要动 vbmeta 吗？ | 现代 A/B + AVB 设备通常**不需要**；Magisk 会 patch 镜像内部的 vbmeta（AVB1.0）| 中高 |

---

## 1. 三个 Magisk 系分支的现状

| 项目 | 仓库 / 页面 | Star | 最新版本 | 发布日期 | 活跃度 | 结论 |
|---|---|---:|---|---|---|---|
| **Magisk（官方）** | https://github.com/topjohnwu/Magisk | **63,501** | **v31.1** | 2026-10-26 | 最近 push 2026-10-25，持续维护 | ✅ 主线，唯一推荐 |
| **Magisk Alpha** | https://github.com/vvb2060/Magisk | **1,307** | 标签 `31100`（提交 `0972f67d-alpha`） | 2026-10-26 | 紧随上游发版 | 🟡 活跃，但为第三方 fork |
| **Magisk Delta / Kitsune Mask** | `HuskyDG/magisk-files` → **404** | — | — | — | ❌ **已消失** | ❌ 排除 |

### 1.1 Magisk 官方（topjohnwu/Magisk）

- 仓库：https://github.com/topjohnwu/Magisk —— star **63,501**，fork 19,038，未归档（数据取自 GitHub API，2026-10-27）。
- 最新 Release：**v31.1**，页面 https://github.com/topjohnwu/Magisk/releases/tag/v31.1 ，Atom feed 显示发布/更新于 **2026-10-26T17:21:45Z**（https://github.com/topjohnwu/Magisk/releases.atom ）。
- 该 Release 的产物（`expanded_assets` 页面实测）：`Magisk-v31.1.apk`、`v31.1.zip`、`app-debug.apk`
  （https://github.com/topjohnwu/Magisk/releases/expanded_assets/v31.1 ）。
- 近期版本节奏（据 releases.atom）：v31.1（2026-10-26）、v31.0（2026-09-04）、v30.7（2026-02-23）、v30.6（2025-12-01）、v30.5（2025-12-02）。

### 1.2 Magisk Alpha（vvb2060/Magisk）

- 仓库：https://github.com/vvb2060/Magisk —— star **1,307**，默认分支 `master`，最近 push **2026-10-26**，未归档。
- 最新 Release 标签 `31100`：https://github.com/vvb2060/Magisk/releases/tag/31100 ，发布 **2026-10-26 13:16**。
- 其 release notes（中文）自述的差异点：移除 addon.d 支持、`su` 二进制不再硬编码到 `/system/bin/`、`su` 参数原样透传给远程 shell、还原 boot 镜像后删除备份文件、不主动请求权限、Zygisk 的 `libzygisk.so` 改名为 `heapprofd_client.so`；并**附上上游 v31.1 的完整 changelog**，说明它是紧跟上游的 fork。
- 判断：**活跃，但属于第三方 fork**。版本号 `31100` 与上游 v31.1（versionCode 31100 量级）对齐。

### 1.3 Magisk Delta / Kitsune Mask（HuskyDG）—— 已消亡

实测（2026-10-27）：

| URL | HTTP 状态 |
|---|---|
| https://github.com/HuskyDG | **404** |
| https://github.com/HuskyDG/magisk-files | **404** |
| https://github.com/HuskyDG/Magisk | **404** |

GitHub 用户搜索 `HuskyDG` 无结果；仓库搜索 `kitsune mask magisk` 命中的全部是**第三方镜像/教程**，例如：
- https://github.com/reiryuki/Kitsune-Mask-Magisk-Delta-by-HuskyDG （16 star，非官方镜像，**无任何 release**）
- https://github.com/RobThePCGuy/Root-Bluestacks-with-Kitsune-Mask （289 star，BlueStacks 场景教程，非项目本体）

> **结论：Magisk Delta / Kitsune Mask 已不再有可用的官方来源，本路线排除。** 上游作者账号消失，任何现存 "Kitsune Mask" 下载都属于二手镜像，**存在被植入风险，不应进入自动化链路**。
> 备注：HuskyDG 是否迁移到其他平台（GitLab/自有站点）**未确认**——本环境无法访问 Google 系域名，未做进一步核实。

### 1.4 明确排除范围（避免跑偏）

| 项目 | 为何排除 |
|---|---|
| **MagiskOnWSA / MagiskOnWSALocal** | 面向 **Windows Subsystem for Android**，把 Magisk 集成进 WSA 镜像，与物理设备线刷毫无关系。`LSPosed/MagiskOnWSA` 已 404；现存 `LSPosed/MagiskOnWSALocal`（https://github.com/LSPosed/MagiskOnWSALocal ）是 PC 上跑 WSA 的方案，**不适用** |
| **Magisk Delta / Kitsune Mask** | 项目已消失（见上） |
| **KernelSU / SukiSU / APatch** | 属内核级 root 路线，由 `root-managers-kernelsu.md` 负责，本文不展开 |
| **Magisk 模块（systemless 模块生态）** | 属装完 root 之后的事，与「如何 root」无关 |

---

## 2. 核心问题：Android 13+ 到底 patch `boot.img` 还是 `init_boot.img`？

### 2.1 一句话答案

**从 Android 13（LV ≥ 33）+ GKI 2.0 起，generic ramdisk 从 `boot` 分区**移出**，搬进独立的 `init_boot` 分区（部分机型搬进 `vendor_boot`）**。因此这类设备要 **patch `init_boot.img`**，而不是 `boot.img`。

### 2.2 官方依据一：Magisk 官方文档《Booting Shenanigans》

来源：https://github.com/topjohnwu/Magisk/blob/master/docs/boot.md （渲染版 https://topjohnwu.github.io/Magisk/details.html ）

原文（逐字引用）：

> "Starting with Android 13 (`LV >= 33`) and [Generic Kernel Image (GKI) 2.0](...), Google **decoupled the generic kernel from the generic ramdisk so that the `boot` partition contains only the GKI kernel, moving the generic first-stage ramdisk into a separate `init_boot` partition (or `vendor_boot` on some devices)**. From the booting perspective, these devices still use Boot Method C (2SI); the bootloader simply loads and concatenates the ramdisk(s) from `init_boot` / `vendor_boot` in memory before handing off to `/init`."

以及同文档的设备分类段落：

> "**On Android 13+ GKI devices, this ramdisk is located in `init_boot` (or `vendor_boot`) rather than `boot`.**"

### 2.3 官方依据二：Magisk 官方安装文档

来源：https://topjohnwu.github.io/Magisk/install.html （源文件 https://github.com/topjohnwu/Magisk/blob/master/docs/install.md ）

原文关键句：

> "**If your device has boot ramdisk, you need to patch the partition image that contains the ramdisk (`boot.img`, `init_boot.img`, or `vendor_boot.img`).** If your device does **NOT** have boot ramdisk, you need to patch `recovery.img`."

> "5. Flash the patched boot/init_boot/vendor_boot/recovery image to your device; for most devices, reboot into fastboot mode and flash with command:
> `fastboot flash boot ...` or **`fastboot flash init_boot ...`** or `fastboot flash vendor_boot ...` or `fastboot flash recovery ...`"

即：**官方文档并不写死 "patch boot.img"**，而是「patch **包含 ramdisk 的那个分区**」，并且在 Android 13+ GKI 设备上那个分区就是 `init_boot`。

### 2.4 官方依据三：Magisk 源码里的分区自动探测逻辑（最强证据）

来源：https://github.com/topjohnwu/Magisk/blob/master/scripts/util_functions.sh 中的 `find_boot_image()`：

```sh
# Returns whether the device is GKI 13+
is_gt_gki_13() {
  [ "$(uname -r | cut -d. -f1)" -ge 5 ] && uname -r | grep -Evq "android12-|^5\.4"
}

find_boot_image() {
  BOOTIMAGE=
  if $VENDORBOOT; then
    BOOTIMAGE="/dev/block/by-name/vendor_boot$SLOT"
  elif $RECOVERYMODE; then
    BOOTIMAGE=$(find_block "recovery$SLOT" "sos")
  elif [ -e "/dev/block/by-name/init_boot$SLOT" ] && is_gt_gki_13; then
    # init_boot is only used with GKI 13+. ...
    BOOTIMAGE="/dev/block/by-name/init_boot$SLOT"
  elif [ -e "/dev/block/by-name/boot$SLOT" ]; then
    # Standard location since AOSP Android 10+
    BOOTIMAGE="/dev/block/by-name/boot$SLOT"
  ...
```

**这段逻辑就是「该 patch 哪个分区」的权威判定顺序**，可直接作为 Lrom 的判定算法：

| 优先级 | 条件 | 目标分区 |
|---:|---|---|
| 1 | 用户显式要求 vendor_boot 模式 | `vendor_boot$SLOT` |
| 2 | recovery 模式（设备 boot 无 ramdisk） | `recovery$SLOT` |
| 3 | `init_boot$SLOT` **存在** 且 内核为 GKI-13+ | **`init_boot$SLOT`** |
| 4 | `boot$SLOT` 存在 | `boot$SLOT` |
| 5 | 旧式回退 | `ramdisk$SLOT` / `boot$SLOT` 等 |

注意 `is_gt_gki_13()` 的判据：内核主版本 ≥ 5，**且** 内核串不匹配 `android12-`、也不以 `5.4` 开头。
→ 我们这台机器内核 `6.1.141-android14-11`：主版本 6 ≥ 5 ✅，不含 `android12-` ✅，不是 `5.4` ✅ ⇒ **判定为 GKI 13+**；再叠加 `init_boot` 分区存在 ⇒ 结论 **patch `init_boot`**。

> 源码注释也解释了为什么需要这个额外判断：
> "init_boot is only used with GKI 13+. **It is possible that some devices with init_boot partition still uses Android 12 GKI or previous kernels**, so we need to explicitly detect that scenario."
> —— 即「有 init_boot 分区」本身**不足以**说明该 patch 它，必须叠加内核判据。这是 Lrom 实现时容易踩的坑。

### 2.5 本机实测交叉验证（由 Lead 提供，2026-10-27）

对 `unpacked/rom/boot.img` 与 `unpacked/rom/init_boot.img` 的实测：

| 镜像 | magic | header_version | page_size | kernel_size | ramdisk_size |
|---|---|---:|---:|---:|---:|
| `boot.img` | `ANDROID!` | **4** | 4096 | 35,699,200 | **0** |
| `init_boot.img` | `ANDROID!` | **4** | 4096 | 0 | **2,389,727**（LZ4 legacy 帧头 `02 21 4C 18` + CPIO） |

**三重证据完全一致**：
1. 官方文档说 Android 13+ GKI 把 ramdisk 移到 init_boot；
2. 官方源码 `find_boot_image()` 会选 init_boot；
3. 本机 boot.img 的 `ramdisk_size == 0`，ramdisk 只在 init_boot.img 里。

⇒ **本机唯一正确目标：`init_boot.img`**。若去 patch `boot.img`，会因为「没有 ramdisk、需要从零创建 ramdisk」（见 §3.3）而得到不生效或 bootloop 的结果。

> 反向印证：issue #8835 里，一台 ramdisk 位于 `vendor_boot/vendor_ramdisk/init_boot.cpio` 的小米平板，作者 patch 了 boot.img + 关掉 vbmeta 验证，**一切无报错但开机没 root**；维护者 yujincheng08 回复 "Your vendor_boot.img has a `vendor_ramdisk/init_boot.cpio`. Please try patch this cpio."
> 来源：https://github.com/topjohnwu/Magisk/issues/8835
> —— 这正是「patch 错分区，静默失效」的典型案例。

---

## 3. Boot image 格式与 patch 原理

### 3.1 Header 版本（v0 / v1 / v2 / v3 / v4）

Magisk 的 `native/src/boot/bootimg.hpp` 直接搬用了 AOSP `mkbootimg` 的头定义（源码内注释给出上游链接：
`https://android.googlesource.com/platform/system/tools/mkbootimg/+/refs/heads/android12-release/include/bootimg/bootimg.h`）。
来源：https://github.com/topjohnwu/Magisk/blob/master/native/src/boot/bootimg.hpp

头文件里对 v0–v2 的布局注释（逐字）：

```
 * When the boot image header has a version of 0 - 2, the structure of the boot
 * image is as follows:
 *
 * +-----------------+
 * | boot header     | 1 page
 * +-----------------+
 * | kernel          | m pages
 * +-----------------+
 * | ramdisk         | n pages
 * +-----------------+
 * | second stage    | o pages
 * +-----------------+
 * | extra blob      | x pages (non standard)
 * +-----------------+
 * | recovery dtbo   | p pages
 * +-----------------+
 * | dtb             | q pages
```

各版本要点：

| header 版本 | 结构 | ramdisk 在哪 | 备注 |
|---|---|---|---|
| **v0** | header / kernel / ramdisk / second / (extra) / recovery_dtbo / dtb | `boot` 内 ramdisk 段 | 老设备；`page_size` 字段与 `extra_size` 复用 |
| **v1** | 同 v0，新增 `recovery_dtbo` | `boot` 内 | 新增 `header_version` 字段 |
| **v2** | 同 v1，新增 `dtb` 段 | `boot` 内 | |
| **v3** | 移除 ramdisk 之外的冗余；**去掉 dtb/recovery_dtbo** | `boot` 内 `ramdisk_size` | Android 11+ 起常见 |
| **v4** | v3 + `boot signature`（AVB）段 | `boot` 内 | Android 12+ 起常见；**本机 boot.img / init_boot.img 均为 v4** |
| **vendor_boot v3** | 独立 `VENDOR_BOOT_MAGIC` 镜像，含 `vendor_ramdisk` 段 + `vendor_ramdisk_table` | `vendor_boot` 内 | 分割的 vendor ramdisk（含 DLKM） |
| **vendor_boot v4** | v3 + 尾部 `bootconfig` 段 | `vendor_boot` 内 | |

源码依据（同一文件）：
```c
struct boot_img_hdr_v3 {
    uint8_t magic[BOOT_MAGIC_SIZE];
    uint32_t kernel_size;  /* size in bytes */
    uint32_t ramdisk_size; /* size in bytes */
    uint32_t os_version;
    uint32_t header_size;
    uint32_t reserved[4];
    uint32_t header_version;
    char cmdline[BOOT_ARGS_SIZE + BOOT_EXTRA_ARGS_SIZE];
} __attribute__((packed));
```
vendor_boot 的 ramdisk 表类型定义：`VENDOR_RAMDISK_TYPE_NONE / PLATFORM / RECOVERY / DLKM`，并有段落说明 "Version 4 of the vendor boot image also adds a bootconfig section to the end of the image."

> ⚠️ **说明**：AOSP 官方页面（`source.android.com`、`android.googlesource.com`）在本机网络环境下**不可达**（连接超时，见 §7 限制说明）。上述结构性事实均转引自 Magisk 源码内**逐字复制的 AOSP 头文件**，可信度高，但**"以 AOSP 原文措辞"的引用未直接核验**，标记为「转引」。

### 3.2 ramdisk 在「boot / init_boot / vendor_boot」三处的分布判据

来自 Magisk `boot_patch.sh` 的实际查找顺序（https://github.com/topjohnwu/Magisk/blob/master/scripts/boot_patch.sh ）：

```sh
##################
# Ramdisk Checks #
##################
unset RAMDISK
for path in ramdisk.cpio vendor_ramdisk/init_boot.cpio vendor_ramdisk/ramdisk.cpio; do
  if [ -e $path ]; then
    RAMDISK=$path
    break
  fi
done
```

因为 `magiskboot unpack` 会把 boot/init_boot/vendor_boot 各自解出一套文件：
- 解 boot/init_boot → 得到 `ramdisk.cpio`
- 解 vendor_boot → 得到 `vendor_ramdisk/init_boot.cpio` 或 `vendor_ramdisk/ramdisk.cpio`

`magiskboot unpack` 的返回值还专门用来区分 vendor_boot（见官方 tools 文档）：**`0:valid`、`1:error`、`3:vendor_boot`**。
而 `boot_patch.sh` 里对应：
```sh
./magiskboot unpack "$BOOTIMAGE"
case $? in
  0 ) ;;
  3 ) ui_print "- Vendor boot image detected"; VENDORBOOT=true ;;
  * ) abort "! Unable to unpack boot image" ;;
esac
```

### 3.3 「没有 ramdisk 就自己造一个」——这正是 patch 错分区的隐患

`boot_patch.sh` 同一段：

```sh
if [ -n "$RAMDISK" ]; then
  ./magiskboot cpio $RAMDISK test
  STATUS=$?
  SKIP_BACKUP=""
else
  # No ramdisk found, create one from scratch
  RAMDISK=ramdisk.cpio
  # Could be stock A only legacy SAR, or some Android 13 GKIs
  STATUS=0
  SKIP_BACKUP="#"
fi
```

→ **如果没有找到 ramdisk，Magisk 不会报错，而是"从零创建一个"**。对本机而言，用 `boot.img` 去跑就会走到这个分支：造出一个空 ramdisk 塞进 boot.img，刷进去因为 bootloader 并不会从 `boot` 里取 ramdisk 给 `init` 用（该角色已由 `init_boot` 承担），于是 **"patch 成功、刷写成功、开机无 root"**。这是最容易被误判成「Magisk 不支持这台机器」的情形。

### 3.4 ramdisk 的实际 patch 动作

`boot_patch.sh` 中的核心（逐字）：

```sh
./magiskboot compress=xz magisk magisk.xz
./magiskboot compress=xz init-ld init-ld.xz
echo "KEEPVERITY=$KEEPVERITY"      > config
echo "KEEPFORCEENCRYPT=$KEEPFORCEENCRYPT" >> config
echo "RECOVERYMODE=$RECOVERYMODE" >> config
echo "VENDORBOOT=$VENDORBOOT"     >> config
[ -n "$PREINITDEVICE" ] && echo "PREINITDEVICE=$PREINITDEVICE" >> config
[ -n "$SHA1" ] && echo "SHA1=$SHA1" >> config

./magiskboot cpio $RAMDISK \
  "add 0750 init magiskinit" \
  "mkdir 0750 overlay.d" \
  "mkdir 0750 overlay.d/sbin" \
  "add 0644 overlay.d/sbin/magisk.xz magisk.xz" \
  "add 0644 overlay.d/sbin/init-ld.xz init-ld.xz" \
  "patch" \
  "$SKIP_BACKUP backup ramdisk.cpio.orig" \
  "mkdir 000 .backup" \
  "add 000 .backup/.magisk config" \
  || abort "! Unable to patch ramdisk"
```

要点：
- 用 `magiskinit` **替换 ramdisk 里的 `/init`**（`add 0750 init magiskinit`）；
- `magisk` 与 `init-ld` 以 xz 压缩后放进 `overlay.d/sbin/`；
- `.backup/.magisk` 里存一份 config（含 `SHA1=`，用于「还原原厂镜像」功能）；
- `magiskboot cpio ... patch` 做 ramdisk 内的深度改造。

### 3.5 dtb 与 kernel 的附带 patch

```sh
for dt in dtb kernel_dtb extra; do
  if [ -f $dt ]; then
    if ! ./magiskboot dtb $dt test; then abort "! ... patched by old (unsupported) Magisk"; fi
    if ./magiskboot dtb $dt patch; then ui_print "- Patch fstab in boot image $dt"; fi
  fi
done
```

- `magiskboot dtb <file> patch`：**在 dtb 的 fstab 里移除 verity/avb**，可配 `KEEPVERITY` 环境变量。
- kernel 侧另有 Samsung RKP / defex / PROCA 的 hexpatch，以及 legacy SAR 的 `skip_initramfs → want_initramfs`——**这些与一加/高通设备无关，对本项目可忽略**。
- 若 kernel 无需 patch，脚本会 `rm -f kernel`（保留原始内核，避免某些设备 bootloop）。

### 3.6 AVB / Verity / FEC 与 vbmeta

| 概念 | 说明 | 对本设备的影响 |
|---|---|---|
| **AVB（Android Verified Boot）** | boot 分区若为 header v4，镜像尾部带 AVB1.0 签名段 | Magisk `magiskboot repack` 会一并处理/重签；v31.1 修复了「AVB 1.0 ECDSA 签名校验与签名改用 ASN.1 DER 编码」 |
| **vbmeta 分区** | 存放各分区 hash/签名描述符；改过 boot 分区就会 hash 不匹配 | 通常需要关闭校验，或用 Magisk 的方式在镜像内 patch |
| **dm-verity / forceencrypt** | `KEEPVERITY` / `KEEPFORCEENCRYPT` 两个开关控制 | Magisk 默认在 SAR 设备上 `KEEPVERITY=true`（见 `get_flags()`） |
| **FEC** | 前向纠错（在 AVB 里附带的 spare/纠错数据） | 由 `avbtool`/magiskboot 配合处理；本路线中由 Magisk 内部处理，**未单独验证** |

**关于 `--disable-verification`**：Magisk 官方安装文档第 6 步（原文）：

> "(Optional) If your device has a separate `vbmeta` partition, you can patch the `vbmeta` partition with command:
> `fastboot flash vbmeta --disable-verity --disable-verification vbmeta.img` (**note that it may wipe your data**)"

注意三点（容易以讹传讹的地方）：
1. 官方标为 **Optional**，并且是「patch **vbmeta 分区**」，不是必须步骤；
2. 官方**明确警告可能清除数据**；
3. 选项名是 `--disable-verity` **与** `--disable-verification` 两个（网上常见的只写 `--disable-verification` 是残缺写法）。

**Magisk 自己的策略**（`util_functions.sh` 的 `get_flags()`）：
```sh
if [ -n "$(find_block vbmeta vbmeta_a)" ]; then
  PATCHVBMETAFLAG=false
else
  PATCHVBMETAFLAG=true
  ui_print "- No vbmeta partition, patch vbmeta in boot image"
fi
```
→ **有独立 vbmeta 分区时，Magisk 不去动镜像内的 vbmeta flag**；没有独立 vbmeta 时才在 boot 镜像内部 patch。
`PATCHVBMETAFLAG=true` 时 `magiskboot repack` 会「把 boot 镜像 vbmeta header 里所有 disable flag 置位」（见官方 tools 文档）。

> ⚠️ 一加/OPPO 的 fastboot 会有额外的 `vbmeta`、`vbmeta_system`、`vbmeta_vendor` 等分区，且部分机型 fastboot 对 `--disable-verification` 支持不完整。**本机具体是否需要动 vbmeta 未实测，标记「未确认」**——建议 Lrom 的实现遵循 "先不改 vbmeta，失败再考虑" 的保守顺序。

---

## 4. 自动化可行边界：PC 侧离线 patch 怎么做

### 4.1 官方立场：**不支持**离线/跨设备 patch

Magisk 官方安装文档有明确警告（逐字）：

> "**Warning: NEVER flash patched image shared by others or patch image on another device even if they have the same device model! You may need to do a full data wipe to recover your device. ALWAYS patch boot image on the same device where you want to install Magisk.**"

**为什么？**（基于源码的合理解释，非官方原文表述）
- `boot_patch.sh` 会把 `SHA1=$(magiskboot sha1 "$BOOTIMAGE")` 写进 `.backup/.magisk`，这个 SHA1 对应「原厂镜像」，用于后续「还原原厂镜像」。跨设备无意义。
- `PREINITDEVICE`（pre-init 存储分区，如 `metadata` / `cache`）只能通过**运行时** `magisk --preinit-device` 探测（脚本里：`$BOOTMODE && [ -z "$PREINITDEVICE" ] && PREINITDEVICE=$(./magisk --preinit-device)`）。PC 侧无法得知，只能留空（Magisk 会在首次启动时由 APP 补做「环境修复」）。
- 官方文档第 7 步也印证了这点："Reboot and launch Magisk app ... you will see a prompt asking for environment fix; click and wait for the reboot"。

⇒ **结论：离线 patch 在技术上可行，但属于「社区做法 / 非官方支持路径」**。它的产物**仍然需要设备端首次开机时再由 Magisk APP 完成环境修复**。

### 4.2 `magiskboot` 能否单独编译 / 有静态构建？

| 问题 | 答案 | 依据 |
|---|---|---|
| `magiskboot` 是不是独立可执行？ | 是。它就是一个独立的 native 二进制 | https://github.com/topjohnwu/Magisk/blob/master/docs/tools.md ：「magiskboot /* binary */」 |
| 能否在 Linux/macOS 宿主机编译？ | **能**。v31.1 changelog：「[MagiskBoot] Support building `magiskboot` natively on Linux and macOS hosts」 | https://github.com/topjohnwu/Magisk/blob/master/docs/changes.md |
| 用什么构建？ | Rust crate。`native/src/boot/Cargo.toml` 里 `[[bin]] name = "magiskboot"; path = "main.rs"`，`[lib] crate-type = ["staticlib", "rlib"]` | https://github.com/topjohnwu/Magisk/blob/master/native/src/boot/Cargo.toml |
| 官方是否提供「Linux x86_64 静态 magiskboot」下载？ | **未确认**。Release 资产只有 `Magisk-v31.1.apk` / `v31.1.zip` / `app-debug.apk`，未见独立 magiskboot 产物 | https://github.com/topjohnwu/Magisk/releases/expanded_assets/v31.1 |
| 那 PC 侧从哪里拿 magiskboot？ | 从 **APK 里解出来**（`lib/<abi>/libmagiskboot.so` 实为可执行 ELF），或从 `v31.1.zip` 里取 `arm64-v8a` 的对应文件，或自行 `cargo build` | — |

> 💡 **给 Lrom 的实用建议（重要）**：若要 PC 侧跑 `magiskboot`，最省事的路线是**从 APK 提取 x86_64 版**（`lib/x86_64/libmagiskboot.so`），因为 APK 内各 ABI 都有产物——这属于**推断，未实测**，需在实现阶段验证。
> 另一条：既然 v31.1 支持宿主机构建，可以跟进是否已有社区提供的预编译宿主版二进制（**未确认**）。

### 4.3 官方命令行用法：`magiskboot` unpack / repack / cpio

依据：https://github.com/topjohnwu/Magisk/blob/master/docs/tools.md （Magisk 官方 Tools 文档）

关键命令（逐字摘自官方 usage）：

```
Usage: ./magiskboot <action> [args...]
Supported actions:
  unpack [-n] [-h] <bootimg>
    Unpack <bootimg> to its individual components ... Supported components:
    kernel, kernel_dtb, ramdisk.cpio, second, dtb, extra, and recovery_dtbo.
    Return values:  0:valid  1:error  3:vendor_boot
  repack [-n] <origbootimg> [outbootimg]
    Repack boot image components using files from the current directory
    to [outbootimg], or 'new-boot.img' if not specified.
    If env variable PATCHVBMETAFLAG is set to true, all disable flags in
    the boot image's vbmeta header will be set.
    ChromeOS boot images will be automatically re-signed with the
    developer keys bundled in the executable.
  verify <bootimg> [x509.pem]
    Check whether the boot image is signed with AVB 1.0 signature.
  sign-avb1 <bootimg> [name] [x509.pem pk8]
    Sign <bootimg> with AVB 1.0 signature.
  extract <payload.bin> [partition] [outfile]
    Extract [partition] from <payload.bin> to [outfile].
    If [partition] is not specified, then attempt to extract either
    'init_boot' or 'boot'. Which partition was chosen can be determined
    by whichever 'init_boot.img' or 'boot.img' exists.
  hexpatch <file> <hexpattern1> <hexpattern2>
  cpio <incpio> [commands...]
  dtb <file> <action> [args...]
  split [-n] <file>
  sha1 <file>
  cleanup
  compress[=format] <infile> [outfile]
  decompress <infile> [outfile]
```

`cpio` 子命令（官方文档）：
```
  exists ENTRY              Return 0 if ENTRY exists, else return 1
  ls [-r] [PATH]            List PATH ("/" by default)
  rm [-r] ENTRY             Remove ENTRY
  mkdir MODE ENTRY          Create directory ENTRY in permissions MODE
  ln TARGET ENTRY           Create a symlink to TARGET named ENTRY
  mv SOURCE DEST            Move SOURCE to DEST
  add MODE ENTRY INFILE     Add INFILE as ENTRY in permissions MODE
  extract [ENTRY OUT]       Extract ENTRY to OUT, or all entries to cwd
  test                      Test the cpio's status
        Return values:  0:stock  1:Magisk  2:unsupported
  patch                     Apply ramdisk patches
        Configure with env variables: KEEPVERITY KEEPFORCEENCRYPT
  backup ORIG [-n]          Create ramdisk backups from ORIG
  restore                   Restore ramdisk from ramdisk backup stored within incpio
```

**`magiskboot` 的 repack 设计要点（官方原文）**：

> "For repacking, **the original boot image is required so the original headers can be used**, changing only the necessary entries such as section sizes and checksum. All sections will be compressed back to the original format if required."

以及：
> "For unpacking, it parses the header and extracts all sections in the image, decompressing on-the-fly if compression is detected in any sections."

→ 对 Lrom 的含义：**必须保留原厂 init_boot.img 作为 repack 的 `origbootimg`**，不能只拿组件重新生成（否则头部字段/压缩格式会丢）。

`magiskboot` 原生支持的压缩格式（官方）：`gzip`、`lz4`、`lz4_legacy`、`lz4_lg`、`lzma`、`xz`、`bzip2`。
**本机 init_boot.img 的 ramdisk 是 LZ4 legacy 帧头（`02 21 4C 18`）** —— 属于官方支持列表 ✅。

`extract` 子命令还有一个对 Lrom 极有价值的行为（官方原文）：

> "If [partition] is not specified, then attempt to extract either **'init_boot' or 'boot'**. Which partition was chosen can be determined by whichever 'init_boot.img' or 'boot.img' exists."

→ 即 `magiskboot extract payload.bin` **优先吐 init_boot**，这与 §2 的结论一致，可直接用于「从 OTA payload.bin 里提取正确的待 patch 镜像」。

### 4.4 `boot_patch.sh`：官方提供、但非官方支持的离线入口

来源：https://github.com/topjohnwu/Magisk/blob/master/scripts/boot_patch.sh

文件头注释（逐字）：
```
# Usage: boot_patch.sh <bootimage>
#
# The following environment variables can configure the installation:
# KEEPVERITY, KEEPFORCEENCRYPT, PATCHVBMETAFLAG, RECOVERYMODE, LEGACYSAR
#
# This script should be placed in a directory with the following files:
# File name          Type      Description
# boot_patch.sh      script    A script to patch boot image for Magisk.
# util_functions.sh  script    A script which hosts all functions required
#                              for this script to work properly.
# magiskinit         binary    The binary to replace /init.
# magisk             binary    The magisk binary.
# magiskboot         binary    A tool to manipulate boot images.
# init-ld            binary    The library that will be LD_PRELOAD of /init
```

**依赖极轻**：只需要 `bash/sh` + 5 个文件（2 个脚本 + 3 个二进制）。

但有一个**必须注意的 PC 侧坑**：
```sh
$BOOTMODE && [ -z "$PREINITDEVICE" ] && PREINITDEVICE=$(./magisk --preinit-device)
```
`BOOTMODE` 未置位时不会执行 → **在 PC 上跑是安全的**（不会尝试触碰设备）。

第二个坑：`boot_patch.sh` 用的是 `/system/bin/sh` 与 Android 的 busybox 语义（如 `chmod -R 755`、`cp -af`）。在标准 Linux 上多数可跑，但**未做实测**（标记「未确认」）。

**推荐的最小离线流程（基于上述官方脚本的拼装，非官方文档步骤）**：

```bash
# 0) 准备：工作目录里需有 boot_patch.sh / util_functions.sh /
#    magiskinit / magisk / init-ld / magiskboot（从 APK 或 zip 中按 ABI 取出）
# 1) 确认目标分区（关键！本机必须是 init_boot）
# 2) 执行
cd workdir
mkdir -p out
sh boot_patch.sh /path/to/init_boot.img     # 产出 new-boot.img
# 3) 刷写（A/B：显式指定 slot，或 --slot=all）
fastboot flash init_boot out/new-boot.img
```

> ⚠️ 需注意 ABI 匹配：设备是 arm64，`boot_patch.sh` 里调用的是 `./magiskboot`、`./magiskinit` 等**本机可执行文件**。在 x86_64 的 PC 上跑脚本，塞进去的必须是 **x86_64 版**的 `magiskboot`（因为它只是在 PC 上解包/重打包），而 `magiskinit` / `magisk` / `init-ld` 是**要放进目标设备 ramdisk 的**，必须是 **arm64（arm64-v8a）版**。二者 ABI 不同，混了会失败。**这一点是本项目 PC 侧 patch 最容易翻车的地方，属推断，需实现时验证。**

### 4.5 其他现成命令行 patcher 项目

| 项目 | 地址 | Star | 性质 | 是否适用本路线 |
|---|---|---:|---|---|
| **AnyKernel3** | https://github.com/osm0sis/AnyKernel3 | **1.5k** | "AnyKernel, Evolved" — 可刷写的 zip 模板，用 `anykernel.sh` 在刷机时修改 ramdisk，把内核替换进现有 ROM | 🟡 用于内核分发/必须走 recovery 刷 zip；本项目是 fastboot 线刷流程，**不直接适用** |
| **avbroot** | https://github.com/chenxiaolong/avbroot | **980** | 「Tool for manipulating and re-signing Android A/B OTAs」，含 avbroot + `avbroot ota patch`，可**可复现地**修改 A/B OTA 并用自定义密钥重签 | 🟢 **对 Lrom 有参考价值**：它处理的就是「改 A/B OTA + 重签 AVB」这半边；但它是 OTA 级（不是单 boot 镜像），且需自签名密钥（需设备支持自定义 AVB 公钥才有意义） |
| **android-simg2img** | https://github.com/anestisb/android-simg2img | **847** | sparse image ↔ raw image 转换 | 🟡 只解决格式转换，不解决 patch |
| **AOSP `mkbootimg`/`unpack_bootimg`** | `android.googlesource.com/platform/system/tools/mkbootimg`（**本环境不可达**） | — | 官方参考实现（Python） | 🟡 可作格式校验/交叉验证，**但本环境无法访问，未核验** |

> **关于 bootimg-tools**：网上常提到的 `bootimg-tools` 多为 2013–2015 年的老仓库，只支持 v0/v1 头、且不含 AVB/压缩格式（lz4/xz）支持，**无法处理 v4 + LZ4 的现代镜像**。本次调研**未找到仍在维护、且完整支持 v4 + AVB1.0 + LZ4 legacy 的简洁 CLI patcher 项目**（除 magiskboot 与 avbroot 外）。标记：**未确认是否存在其他可用的现代 CLI patcher**。

---

## 5. A/B 设备槽位处理

| 事项 | 说明 | 依据 |
|---|---|---|
| Magisk 内部用 `$SLOT` 拼分区名 | `BOOTIMAGE="/dev/block/by-name/init_boot$SLOT"` | https://github.com/topjohnwu/Magisk/blob/master/scripts/util_functions.sh |
| fastboot 侧写法 | 官方文档写作 `fastboot flash init_boot ...`；文档对 slot 后缀的措辞曾引发 issue #7571 | https://github.com/topjohnwu/Magisk/issues/7571 |
| issue #7571 的提问与处理 | 用户问 `[_x]` 是 `_a` 还是 `_b`、能否用 `--slot=all`；维护者 canyie 提交 "Update install guide"（commit `6dd28f9`），topjohnwu 又补 commit `c013a34`，issue 以 #7611 关闭 | 同上 |
| 升级 OTA 后保留 root | 官方 OTA 指南：**先 Restore Images** → 装 OTA → **Install to Inactive Slot (After OTA)** → 重启 | https://github.com/topjohnwu/Magisk/blob/master/docs/ota.md |
| 官方 OTA 指南的适用范围警告 | "OTA updates are only supported on devices with A/B partitions (Seamless System Updates). ... **The exact workflow in this document is only tested on Pixel devices**, as procedures for other devices may vary." | 同上 |
| 官方强调 | "**NOTE: In order to apply OTAs, you HAVE to make sure you haven't modified any read-only partitions yourself**" | 同上 |

**给 Lrom 的建议**：
1. **不要默认 `--slot=all`**。一加的 OTA 通常只更新 active slot，盲目双写会让两个 slot 状态不一致，反而增加变砖面。
2. 更稳的做法：先读 `fastboot getvar current-slot`，**只写当前 slot**，并把「另一 slot 的原厂镜像」作为回滚保险。
3. 但注意：如果你想让「切到另一 slot 后依然有 root」，就需要两个 slot 都写。**这是策略选择，不是技术必需**——建议在 Lrom 里做成显式开关。

> ⚠️ `fastboot getvar current-slot` 在一加设备上的实际输出格式需实测，**未确认**。

---

## 6. 风险点与恢复

### 6.1 vbmeta / AVB 校验导致不启动

| 现象 | 原因 | 对策 |
|---|---|---|
| 刷完卡 fastboot（`orange state` / 校验失败） | 修改后的 init_boot hash 与 vbmeta 描述符不匹配 | 用 Magisk 方式 patch（它会在镜像内处理 AVB1.0）；必要时按官方 Optional 步骤刷 `vbmeta.img` 并加 `--disable-verity --disable-verification`（**可能清数据**） |
| 刷完能进系统但无 root | **patch 错分区**（最可能）或 ramdisk 从零创建（§3.3） | 确认目标是 `init_boot`；确认 `ramdisk_size > 0` |
| 开机 bootloop | 目标分区抓错（如把 init_boot 刷进 boot）、或压缩格式处理错 | 刷回原厂镜像 |

### 6.2 bootloop 的常见原因（结合源码）

1. **patch 错了分区**（§2.5、issue #8835 的实证）。
2. **拿已 patch 过的镜像再 patch**：`boot_patch.sh` 会 `cpio test` 判断，返回 `1` 走 restore 分支可自愈；返回 `2`（"patched by unsupported programs"）则 `abort`。
3. **用了别人的 patched image**（官方明令禁止，§4.1）。
4. **repack 时丢了原厂头部**：官方强调必须给 `repack` 传 `origbootimg`（§4.3）。
5. **`PREINITDEVICE` 缺失**：PC 侧 patch 无法探测，首次开机需 APP 做环境修复；不做则部分机型异常。
6. **Samsung 专属的 kernel hexpatch 误伤**：`boot_patch.sh` 里对 kernel 的 RKP/defex/PROCA hexpatch 仅针对 Samsung，一加设备上应不命中——**但这是脚本逻辑，PC 侧跑需确认未误改 kernel**。

> v30.6（2025-12-01）changelog 有一条值得注意：「[MagiskInit] **Revert a change that could result in bootloops**」——说明 bootloop 是该路线的长期风险项。
> 来源：https://github.com/topjohnwu/Magisk/blob/master/docs/changes.md

### 6.3 用 stock image 恢复

**官方手段（推荐，设备端）**：Magisk APP → 「卸载 → 还原原厂镜像（Restore Images）」。这依赖安装时由 `boot_patch.sh` 备份进 ramdisk 的 `.backup/.magisk`（含 `SHA1=`）与 `stock_boot.img`。

**PC 侧恢复（Lrom 应当提供的能力）**：
```bash
fastboot flash init_boot /path/to/stock_init_boot.img
fastboot flash boot     /path/to/stock_boot.img    # 若也动过
# 需要时
fastboot flash vbmeta --disable-verity --disable-verification /path/to/stock_vbmeta.img
```
> 🔑 **这正好是 §0 那条 Lrom README 已强调的「刷前备份」的价值**：必须在动手前用 `dd` 备份 `boot` / `init_boot` / `vbmeta` 并记录 SHA-256。没有 stock init_boot.img 就没有回滚路径。

**Samsung 专属警告（不适用本机，但值得写进文档以示边界）**：官方 install.md 中有
> "**Never, ever** try to restore either `boot`, `init_boot`, `recovery`, or `vbmeta` partitions back to stock! You can brick your device by doing so..."
——这是 Samsung 因分区/签名机制特殊导致的；**不要把它误解为通用规则**，一加设备上刷回原厂镜像是标准恢复手段。

---

## 7. 本机（OPD2404 / Android 16 / 6.1.141-android14-11）已知与未知

### 7.1 已确认

| 项 | 值 | 来源 |
|---|---|---|
| 目标分区 | **`init_boot`** | §2.5 三重证据 |
| 镜像头版本 | v4，page_size 4096 | Lead 实测 |
| ramdisk 压缩 | LZ4 legacy（`02 21 4C 18`） | Lead 实测 + magiskboot 官方支持列表 |
| 内核判据 | `6.1.141-android14-11` ⇒ 满足 `is_gt_gki_13()` | `util_functions.sh` 源码 |
| Magisk 对 Android 16 的支持 | v30.7：「[MagiskInit] Support Android 16 QPR2 sepolicy format」「[Zygisk] Support Android 16 QPR2 and higher」；v30.3：「[MagiskPolicy] Support new sepolicy binary format introduced in Android 16 QPR2」 | changes.md |
| 16K page size（若有） | v28.0 起支持「16k page size」 | changes.md |
| OnePlus 相关修复 | v26.3：「[App] Fix root service unable to bind on OnePlus devices」；v24.0：「[MagiskInit] Better support for some OnePlus/Oppo/Realme devices」 | changes.md |

### 7.2 未确认 / 需实测

- ⬜ **一加 OPD2404 的 init_boot 是否被 ColorOS/OxygenOS 的 OTA 机制特殊处理**（未确认）。
- ⬜ **是否需要动 vbmeta**（未确认，建议保守）。
- ⬜ **`fastboot flash init_boot` 在 OxygenOS 的 fastboot 上是否直接可用**，还是需要 `init_boot_a` 后缀（未确认）。
- ⬜ **PC 上跑 `boot_patch.sh` 的实际可行性**（ABI 混用问题、`/system/bin/sh` 语义差异）——未实测。
- ⬜ **`magiskboot` 宿主（x86_64 Linux）预编译二进制是否有官方/可信来源**——未确认。
- ⬜ **Magisk 官方是否支持 Android 17 (API 37)**：v31.0 changelog 有「[Zygisk] Support Android 17 (API 37)」，说明部分组件已前瞻支持；但整机 Android 17 的 Magisk 兼容性**未确认**。
- ⬜ **HuskyDG 是否迁移到非 GitHub 平台**——未确认（Google 系域名不可达）。

---

## 8. 官方做法 vs 社区做法（务必分清）

| 做法 | 归类 | 备注 |
|---|---|---|
| Magisk APP 内「安装 → 选择并修补一个文件」 | ✅ **官方支持** | 需设备端运行 |
| `adb pull` 出 patched image 再 `fastboot flash init_boot` | ✅ **官方支持** | 官方 install.md 第 4–5 步 |
| 「安装到未使用的槽位（OTA 后）」 | ✅ **官方支持**（A/B） | 官方 OTA 指南，但仅 Pixel 测试过 |
| 直接 `fastboot boot patched.img` 临时启动试用 | 🟡 官方未写入 install.md | fastboot `boot` 是现代 A/B 常用技巧；**本调研未在 Magisk 官方文档中找到该步骤**，标记为社区做法 |
| 用 `magiskboot` 手工 unpack/repack | 🟡 **官方工具、非官方安装流程** | `magiskboot` 是官方二进制，但手工拼装流程不在 install.md 中 |
| PC 侧跑 `boot_patch.sh` | 🟡 **官方脚本、明确非官方支持** | 官方警告必须「在同一设备上 patch」 |
| 跨设备/下载别人的 patched image | ❌ **官方明令禁止** | install.md 大写警告 |
| 刷他人打包的「一键 root 包」/ 二手 Kitsune Mask 镜像 | ❌ **高风险，排除** | 作者账号已消失，产物无来源保证 |

---

## 9. 对 Lrom 的落地建议

1. **分区判定算法直接照抄 `find_boot_image()` 的优先级**（§2.4），并**同时**用「内核串判据 + 分区存在性」双重条件，不要只看分区是否存在。
2. **加一道自检**：解包目标镜像后，**校验 `ramdisk_size > 0`**。若为 0，说明选错了分区或该镜像本身无 ramdisk ⇒ 直接报错退出，而不是交给 Magisk「从零创建」。这一条能挡掉 §2.5 / #8835 那类静默失败。
3. **`magiskboot` 的取得方式**优先考虑「从官方 APK 按 ABI 提取」，并校验来源（GitHub Release URL）+ SHA-256。
4. **ABI 严格分离**：PC 侧工具用宿主 ABI，塞进 ramdisk 的 `magiskinit`/`magisk`/`init-ld` 用 `arm64-v8a`（§4.4）。
5. **保留原厂镜像作为 `repack` 的 `origbootimg`**，不要自行拼头。
6. **刷前强制备份** `boot` / `init_boot` / `vbmeta` + SHA-256 清单（Lrom README 已有此设计，此处再次确认其必要性）。
7. **slot 策略做成显式选项**，默认只写当前 slot。
8. **诚实告知用户**：离线 patch 后首次开机仍需 Magisk APP 完成环境修复；且这是非官方支持路径。

---

## 10. 参考来源汇总

**Magisk 官方（一手）**
- 安装文档：https://topjohnwu.github.io/Magisk/install.html ｜ 源文件 https://github.com/topjohnwu/Magisk/blob/master/docs/install.md
- 启动原理 / 分区分类：https://github.com/topjohnwu/Magisk/blob/master/docs/boot.md
- 工具文档（magiskboot 完整 CLI）：https://github.com/topjohnwu/Magisk/blob/master/docs/tools.md
- 变更日志：https://github.com/topjohnwu/Magisk/blob/master/docs/changes.md
- OTA 指南：https://github.com/topjohnwu/Magisk/blob/master/docs/ota.md
- 离线 patch 脚本：https://github.com/topjohnwu/Magisk/blob/master/scripts/boot_patch.sh
- 分区探测 / 参数逻辑：https://github.com/topjohnwu/Magisk/blob/master/scripts/util_functions.sh
- Boot image 头定义（转引 AOSP mkbootimg）：https://github.com/topjohnwu/Magisk/blob/master/native/src/boot/bootimg.hpp
- magiskboot Cargo 清单：https://github.com/topjohnwu/Magisk/blob/master/native/src/boot/Cargo.toml
- Releases：https://github.com/topjohnwu/Magisk/releases ｜ v31.1 资产 https://github.com/topjohnwu/Magisk/releases/expanded_assets/v31.1

**Magisk Alpha**
- 仓库：https://github.com/vvb2060/Magisk
- Release `31100`：https://github.com/vvb2060/Magisk/releases/tag/31100
- Release feed：https://github.com/vvb2060/Magisk/releases.atom

**Magisk Delta / Kitsune Mask（已失效）**
- https://github.com/HuskyDG （404）｜ https://github.com/HuskyDG/magisk-files （404）
- 第三方镜像（警示用）：https://github.com/reiryuki/Kitsune-Mask-Magisk-Delta-by-HuskyDG

**相关 issue**
- #8835 ramdisk 在 `vendor_boot/vendor_ramdisk/init_boot.cpio` → patch 错分区静默失效：https://github.com/topjohnwu/Magisk/issues/8835
- #7571 / #7611 安装文档 slot 措辞澄清：https://github.com/topjohnwu/Magisk/issues/7571
- #7231 Direct Install 在空 init_boot 分区上失败：https://github.com/topjohnwu/Magisk/issues/7231
- #7219 `ro.boot.vbmeta.device` 找不到：https://github.com/topjohnwu/Magisk/issues/7219

**其他工具**
- AnyKernel3：https://github.com/osm0sis/AnyKernel3
- avbroot：https://github.com/chenxiaolong/avbroot
- android-simg2img：https://github.com/anestisb/android-simg2img

**明确排除**
- MagiskOnWSALocal：https://github.com/LSPosed/MagiskOnWSALocal

---

## 11. 调研限制说明（透明记录）

| 限制 | 影响 | 处理 |
|---|---|---|
| `web_search` 工具不可用（缺 DEEPSEEK_API_KEY） | 无法做关键词检索，只能直连已知 URL | 已全程改用 `web_fetch` / `curl` 直取 URL |
| `source.android.com`、`android.googlesource.com` 不可达（连接超时） | **无法直接引用 AOSP 官方页面原文** | boot image 头结构一律转引自 Magisk 源码内逐字复制的 AOSP 头文件，并标注「转引」 |
| `raw.githubusercontent.com` 被 RST | 无法直取 raw 文件 | 改用 `github.com/.../blob/...?plain=1` + 页面内嵌 JSON 提取正文 |
| `api.github.com` 中途 403 限流 | 部分仓库元数据（fork/issue 计数、部分 release assets）改用 Atom feed / `expanded_assets` 页面替代 | 已在各表格标注数据来源 |
| Google 系域名整体不可达 | 未能核实 HuskyDG 是否迁往非 GitHub 平台 | 标记「未确认」 |

> **本文所有版本号、star 数、发布日期均来自上述 URL 的实测抓取（2026-10-27），未做任何估算或推测。**
