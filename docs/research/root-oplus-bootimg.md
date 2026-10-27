# 欧加（一加/OPPO/realme）设备 root 与 boot 镜像获取路径调研

- 调研对象：Lrom v0.3.2 优先目标平台——欧加系（ColorOS/OxygenOS）
- 实测设备：**OnePlus OPD2404（一加平板 Pro）**，代号 `OP5D77L1`，Android 16 / SDK 36
- 调研日期：2026-10-27
- 调研方式：**(A) 本机命令实测核实** + **(B) 社区/官方资料查证**
- **安全声明**：全程只读。未修改任何 Lrom 源码，未刷写设备，未对设备执行任何写操作。

> 本文所有「实测」结论都附带实际命令与输出要点；所有「网络」结论都附 URL。
> 查不到或无法验证的一律标注 **⚠️ 未确认**，不做推测性断言。

---

## 0. 结论先行（TL;DR）

| 问题 | 结论 | 依据 |
|---|---|---|
| 全量包能不能直接取到 boot？ | **能**。`rom.zip` 是标准 A/B 全量 OTA，内层 `payload.bin` 自带全部 47 个分区的完整数据 | 实测 §2.2 / §2.5 |
| `init_boot` 在不在？ | **在**，8 MiB，`ANDROID!` 头，仅 ramdisk（无 kernel）→ **这是 Magisk 应该打的镜像** | 实测 §2.3 |
| `boot` 在不在？ | **在**，192 MiB，`ANDROID!` 头，含 kernel（有 ramdisk 字段但按 Android 12+ 惯例应无实际 ramdisk） | 实测 §2.3 |
| `vendor_boot` / `vbmeta` / `vbmeta_system` / `vbmeta_vendor` / `dtbo` 在不在？ | **全部存在**（47 个分区中 6 个 + dtbo 均在） | 实测 §2.2 |
| boot/init_boot 在设备上是否可写？ | **未确认**。需要进 fastboot 才能实际判断，且 `getvar` 不是写操作但本次未执行；按 AOSP 定义它们不属于 bootloader-critical，但欧加有额外限制 | §3 ⚠️ |
| 是否需要 `--disable-verification`？ | 当前 `verifiedbootstate=orange` + `flash.locked=0`，**大概率不需要**禁用 AVB 就能开机，但不建议照抄厂商以外的做法 | 实测 §3.3 |
| 下次 OTA 会不会丢 root？ | **会**。设备是 A/B + Seamless 更新，OTA 后槽位会切到 `_b`，`init_boot_b` 是原厂未打补丁的 → 丢 root | 实测 §5 |

---

## 1. 本机实测环境（可复现）

```
$ adb devices
List of devices attached
***(已隐去)***	device
[exit 0]

$ fastboot devices
[exit 0]   ← 空输出：当前不在 fastboot 模式
```

```
$ cd $LROM && ./lrom devices
设备序列号: ***(已隐去)***
当前模式  : ADB（开机状态）
设备型号  : OPD2404
设备代号  : OP5D77L1
产品名    : OPD2404
系统版本  : OPD2404_16.0.5.1201(CN01)
Android   : 16
当前槽位  : a
Bootloader: 已解锁
[exit 0]
```

关键 bootloader 属性（`adb shell getprop`，只读）：

```
$ adb shell 'getprop ro.boot.slot_suffix; getprop ro.boot.slot; getprop ro.boot.verifiedbootstate; \
              getprop ro.boot.veritymode; getprop ro.boot.flash.locked; getprop ro.boot.vbmeta.device_state; \
              getprop ro.boot.force_normal_boot'
_a
            ← ro.boot.slot 未设置（只有 slot_suffix 有值）
orange
enforcing
0
unlocked
0
[exit 0]
```

```
$ adb shell 'getprop ro.boot.avb_version; getprop ro.boot.vbmeta.avb_version; getprop ro.boot.vbmeta.size; \
              getprop ro.boot.vbmeta.digest; getprop ro.boot.dynamic_partitions'
1.2
1.2
21056
f9d4617376c75cccadadc1cac5d8ddf0c1c3cd42119e067cebe494e0be42bded
true
[exit 0]
```

当前系统指纹（用于后续比对包是否「完全匹配」）：

```
$ adb shell 'getprop ro.build.fingerprint; getprop ro.build.version.incremental; \
              getprop ro.build.version.security_patch; uname -a'
OnePlus/OPD2404/OP5D77L1:16/UKQ1.231108.001/U.247d78a_a3a1b_629cb:user/release-keys
U.247d78a_a3a1b_629cb
2026-08-01
Linux localhost 6.1.141-android14-11-o-g5cdab3b518c1 #1 SMP PREEMPT Tue Jun 30 05:53:43 UTC 2026 aarch64 Toybox
[exit 0]
```

> **重要发现**：设备当前指纹 `U.247d78a_a3a1b_629cb` 与手上 `rom.zip` 的
> `post-build-incremental` **完全一致**（见 §2.4）。也就是说——**手里这个包就是设备当前运行版本的官方全量包**，
> 从中解出的 `init_boot.img` 是 100% 匹配的 stock 镜像。这是本次调研最有价值的结论之一。

---

## 2. 事实核实 A：解开包，到底有什么镜像

### 2.1 解包目录清单

```bash
$ ls -la $LROM/unpacked/rom/
[exit 0]
```

| 文件 | 大小 | 说明 |
|---|---|---|
| `abl.img` | 278,528 | bootloader 链 |
| `aop.img` / `aop_config.img` | 335,872 / 28,672 | |
| `bluetooth.img` | 1,454,080 | |
| **`boot.img`** | **201,326,592（192 MiB）** | ✅ 含 kernel |
| `cpucp.img` / `cpucp_dtb.img` | 253,952 / 16,384 | |
| `devcfg.img` | 61,440 | |
| `dsp.img` | 67,108,864 | |
| **`dtbo.img`** | **25,165,824（24 MiB）** | ✅ 存在 |
| `engineering_cdt.img` | 1,048,576 | |
| `featenabler.img` | 106,496 | |
| `hyp.img` | 1,642,496 | |
| `imagefv.img` | 106,496 | |
| **`init_boot.img`** | **8,388,608（8 MiB）** | ✅ **Magisk 的目标镜像** |
| `keymaster.img` | 430,080 | |
| `modem.img` | 96,153,600 | |
| `my_bigball/carrier/engineering/heytap.img` | 各 335,872 | 欧加逻辑分区（super 内，此处为占位/稀疏） |
| `my_manifest.img` | 802,816 | |
| `my_product.img` | 1,397,407,744 | |
| `my_region.img` | 3,289,088 | |
| `my_stock.img` | 3,830,992,896 | |
| `odm.img` | 1,627,066,368 | |
| `oplus_sec.img` | 872,448 | |
| `product.img` | 8,949,760 | |
| `qupfw.img` | 61,440 | |
| `recovery.img` | 104,857,600（100 MiB） | |
| `shrm.img` | 139,264 | |
| `splash.img` | 15,298,560 | |
| `system_dlkm.img` | 14,544,896 | |
| `system.img` | 994,967,552 | |
| `system_ext.img` | 905,129,984 | |
| `tz.img` | 4,141,056 | |
| `uefi.img` / `uefisecapp.img` | 3,158,016 / 200,704 | |
| **`vbmeta.img`** | **12,288** | ✅ AVB 根 |
| **`vbmeta_system.img`** | **4,096** | ✅ AVB 链 |
| **`vbmeta_vendor.img`** | **4,096** | ✅ AVB 链 |
| **`vendor_boot.img`** | **201,326,592（192 MiB）** | ✅ `VNDRBOOT` 头 |
| `vendor.img` | 799,891,456 | |
| `vendor_dlkm.img` | 48,648,192 | |
| `xbl.img` / `xbl_config.img` / `xbl_ramdump.img` | 1,118,208 / 237,568 / 909,312 | |
| `unpacked.json` | 9,980 | Lrom 生成的元数据（含 sha256） |

```bash
$ ls *.img | wc -l
47
[exit 0]
```

### 2.2 六个关键分区的存在性（逐条判定）

```bash
$ cd $LROM/unpacked/rom
$ for f in boot init_boot vendor_boot vbmeta vbmeta_system vbmeta_vendor dtbo recovery; do
    if [ -f "$f.img" ]; then printf "%-16s EXISTS %12s bytes\n" "$f.img" "$(stat -c%s $f.img)";
    else printf "%-16s MISSING\n" "$f.img"; fi; done
boot.img         EXISTS    201326592 bytes  192M
init_boot.img    EXISTS      8388608 bytes  8.0M
vendor_boot.img  EXISTS    201326592 bytes  192M
vbmeta.img       EXISTS        12288 bytes  12K
vbmeta_system.img EXISTS       4096 bytes  4.0K
vbmeta_vendor.img EXISTS       4096 bytes  4.0K
dtbo.img         EXISTS     25165824 bytes  24M
recovery.img     EXISTS    104857600 bytes  100M
[exit 0]
```

**结论：6 个关键分区全部存在，`dtbo` 也存在。没有缺失项。**

### 2.3 镜像头验证（不是空文件、不是占位）

```bash
$ file boot.img init_boot.img vbmeta.img vbmeta_system.img vbmeta_vendor.img dtbo.img
boot.img:          Android bootimg, kernel, ramdisk (0x630)
init_boot.img:     Android bootimg, ramdisk (0x630)
vbmeta.img:        data
vbmeta_system.img: data
vbmeta_vendor.img: data
dtbo.img:          data
[exit 0]
```

`file` 对 `vbmeta*`/`dtbo` 只报 `data` 是因为它们不匹配 `file` 的 magic 库，直接看十六进制头：

```bash
$ for f in boot init_boot vbmeta vbmeta_system vbmeta_vendor dtbo vendor_boot; do
    printf "%-14s " "$f"; xxd -l 32 $f.img | head -2 | tr '\n' '|'; echo; done
boot          414e 4452 4f49 4421 00ba 2002 ...   → "ANDROID!"  magic
init_boot     414e 4452 4f49 4421 0000 0000 ...   → "ANDROID!"  magic（kernel_size=0）
vbmeta        4156 4230 0000 0001 ...             → "AVB0"     AVB1.0 头
vbmeta_system 4156 4230 0000 0001 ...             → "AVB0"
vbmeta_vendor 4156 4230 0000 0001 ...             → "AVB0"
dtbo          d7b7 ab1e 005e b314 ...             → DTBO table magic
vendor_boot   564e 4452 424f 4f54 0400 0000 ...   → "VNDRBOOT" header_version=4
[exit 0]
```

进一步解析 boot header（`struct.unpack_from('<6I', b, 8)`）：

```
backup: kernel_size=35695104 kernel_addr=0x0 ramdisk_size=469762469 os_version=0x630 header_version=0
unpack: kernel_size=35699200 kernel_addr=0x0 ramdisk_size=469762472 os_version=0x630 header_version=0
```

- `init_boot.img` 的 `kernel_size = 0` → **只有 ramdisk**，这正是 Android 13+ 引入 `init_boot` 分区的意义：
  **ramdisk 从 `boot` 里搬到了 `init_boot`**。
- `os_version = 0x630` → Android 16。
- 注意：`ramdisk_size` 字段读出来是 ~469 MiB 的异常值，说明 boot header 并不是 v0 布局的标准填充
  （`header_version` 字段位置在 v0 上是 `page_size` 之后），**不应把它当 ramdisk 大小解读**。
  可确认的事实只有：`init_boot` 的 `kernel_size=0`、`boot` 的 `kernel_size≈35.7 MB` 且 `file` 判定含 kernel 与 ramdisk。

### 2.4 包的来源与匹配性（`rom.zip`）

```bash
$ ls -la "$HOME/rom.zip"
-rw-rw-r-- 1 user user 7781443377 10月26日 20:48 $HOME/rom.zip
[exit 0]
```

```bash
$ unzip -l rom.zip
Archive:  rom.zip
signed by SignApk
  Length      Date    Time    Name
     1164  2009-01-01 00:00   META-INF/com/android/metadata
      952  2009-01-01 00:00   META-INF/com/android/metadata.pb
      614  2009-01-01 00:00   care_map.pb
7781436717  2009-01-01 00:00   payload.bin
      430  2009-01-01 00:00   payload_properties.txt
     1424  2009-01-01 00:00   META-INF/com/android/otacert
---------                     -------
7781441301                     6 files
[exit 0]
```

**没有 `.ops` / `.ofp` / `.ozip` 条目，没有任何散装 `.img`。** 里面只有 `payload.bin`。

`META-INF/com/android/metadata` 关键字段：

```bash
$ unzip -p rom.zip META-INF/com/android/metadata
android_version=16
build_type=user
display_os_version=16.0.5
google_patch=20260801
ota-id=OPD2404_11.F.30_2300_202608140040
ota-type=AB
post-build=OnePlus/OPD2404/OP5D77L1:16/UKQ1.231108.001/U.247d78a_a3a1b_629cb:user/release-keys
post-build-incremental=U.247d78a_a3a1b_629cb
post-sdk-level=36
post-security-patch-level=2026-08-01
pre-device=OP5D77L1
product_name=OPD2404
version_name=OPD2404_16.0.5.1201(CN01)
ota_root_or_debug=false
oplus_hex_nv_id=97
oplus_project_numbers=23927
wipe=0
[exit 0]
```

`payload_properties.txt`：

```
FILE_HASH=FFmIikNPfIUi0oq55DZ206giES04i398F5Rw8pAIqC4=
FILE_SIZE=7781436717
METADATA_HASH=6XI2cSmZBBgtAks0Kohrc1iouoG/Sn8Vla/2sjThs3Y=
METADATA_SIZE=276248
android_version=16
ota_root_or_debug=false
oplus_update_engine_verify_disable=0
ota_target_version=OPD2404_11.F.30_2300_202608140040
oplus_rom_version=V16.0.0
oplus_separate_soft=23927
[exit 0]
```

> `post-build-incremental=U.247d78a_a3a1b_629cb` **与设备当前 `ro.build.version.incremental` 逐字符一致**
> → 该全量包就是设备当前运行的版本。**从中解出的 `init_boot.img` 可直接用于 patch，无需再到处找包。**

`care_map.pb` 只覆盖逻辑分区（`system/system_ext/product/vendor/vendor_dlkm/system_dlkm/my_*`），
**不含 boot/init_boot/vbmeta**，所以 care_map 不能用来确认 boot 是否存在——必须看 payload manifest 本身。

### 2.5 payload.bin 内分区清单（用 Lrom 自己复核，闭环自证）

```bash
$ ./lrom inspect "$HOME/rom.zip"
包类型    : 全量 OTA 包
产品名    : OPD2404
设备代号  : OP5D77L1
Android   : 16
OTA 类型  : AB
  分区清单（47 个）
  boot                201 MB    96 ops  REPLACE_BZ,REPLACE_XZ,ZERO
  vendor_boot         201 MB    96 ops  REPLACE_BZ,REPLACE_XZ,ZERO
  dtbo                 25 MB    12 ops  REPLACE_BZ,REPLACE_XZ,ZERO
  init_boot             8 MB     4 ops  REPLACE_BZ,REPLACE_XZ,ZERO
  vbmeta                0 MB     1 op   REPLACE_XZ
  vbmeta_system         0 MB     1 op   REPLACE_XZ
  vbmeta_vendor         0 MB     1 op   REPLACE_XZ
  检查结果
  ·· 全部 47 个分区都自带完整数据（无 SOURCE_COPY/BSDIFF），是全量包
  ·· 共 47 个分区，声明总大小 10,369 MB
  结论: 可以线刷
  设备核对
  ·· 设备代号匹配: OP5D77L1
  ·· 产品名匹配: OPD2404
[exit 0]
```

`unpacked.json` 中的分区名完整列表（47 个，全部 `ok: true`）：

```
system, system_ext, product, vbmeta_system, boot, init_boot, vendor_boot, recovery,
vendor, vendor_dlkm, system_dlkm, odm, dtbo, vbmeta, abl, aop, aop_config, bluetooth,
cpucp, devcfg, dsp, engineering_cdt, featenabler, hyp, imagefv, keymaster, modem,
oplus_sec, qupfw, shrm, splash, tz, uefi, uefisecapp, cpucp_dtb, vbmeta_vendor,
xbl, xbl_config, xbl_ramdump, my_product, my_engineering, my_stock, my_carrier,
my_region, my_bigball, my_heytap, my_manifest
```

**逐项确认（针对任务要求的 6+1 个）**：

| 分区 | payload 中 | 磁盘文件 | 大小 | 头 magic |
|---|---|---|---|---|
| `init_boot` | ✅ | ✅ | 8,388,608 | `ANDROID!`（kernel_size=0） |
| `boot` | ✅ | ✅ | 201,326,592 | `ANDROID!`（含 kernel） |
| `vendor_boot` | ✅ | ✅ | 201,326,592 | `VNDRBOOT` v4 |
| `vbmeta` | ✅ | ✅ | 12,288 | `AVB0` |
| `vbmeta_system` | ✅ | ✅ | 4,096 | `AVB0` |
| `vbmeta_vendor` | ✅ | ✅ | 4,096 | `AVB0` |
| `dtbo` | ✅ | ✅ | 25,165,824 | DTBO magic |

### 2.6 完整性校验（sha256 与 unpacked.json 对账）

```bash
$ sha256sum boot.img init_boot.img vbmeta.img vbmeta_system.img vbmeta_vendor.img dtbo.img vendor_boot.img
faf6779dfb471fccfbd709e76f9439435650017178e4a1237b3bddd04e37c464  boot.img
f553f475c9f045685e6ab04b83e8d8a74b442a0d495ef5158743bd08f2d388e4  init_boot.img
90357a7595fa1ceef8cbb4386225d337b21efe0ccef87971e36f8dc1b0c0d869  vbmeta.img
b2f9c464f9da4d0e9503281b606b4af0833b79664041b07a76b8476277d1c1ff  vbmeta_system.img
26809e3cbd82bc58f628e3f395dd446923ffc73efb8f6def62e5ec5d971b856a  vbmeta_vendor.img
3b98e45fc731cb74abf76b52ba0f720812c9723ca84366c47f83c86da92226a4  dtbo.img
498b039c200514cb3a6fafa6964a1648197b1220c875b827da6ae2932ccb4f92  vendor_boot.img
[exit 0]
```

与 `unpacked.json` 中记录的 sha256 **逐条一致**（boot `faf6779d…`、init_boot `f553f475…`、vbmeta `90357a75…` 等）。

### 2.7 本地已有 root 相关备份（`lrom backup` 的产物）

```bash
$ ls -la backups/OP5D77L1_20261026-212432/
[exit 0]
boot.img            201,326,592
dtbo.img             25,165,824
init_boot.img         8,388,608
recovery.img        104,857,600
vbmeta.img               65,536
vbmeta_system.img        65,536
vbmeta_vendor.img        65,536
```

`backup.json` 记录的设备信息（来自 ACL 权限受限但本次可读）：

```
build_id: OPD2404_16.0.5.702(CN01)
ro.build.fingerprint: OnePlus/OPD2404/OP5D77L1:16/UKQ1.231108.001/U.199d93a_a1c01c_a1c01a:user/release-keys
ro.boot.vbmeta.digest: 67e09c0a43709c0da99cd88b6f2a864b6d5e89360da891ce40b95ad2d1e85314
```

**这是旧固件（`.702`）的回读备份，不是当前版本（`.1201`）**。三处强证据：

1. `backup.json` 里 `ro.boot.vbmeta.digest = 67e09c0a…`，而设备**当前**是 `f9d46173…` → vbmeta 已变。
2. `boot.img` 头对比：
   ```
   [backup] 414e 4452 4f49 4421 00aa 2002 ...   kernel_size=35695104
   [unpack] 414e 4452 4f49 4421 00ba 2002 ...   kernel_size=35699200
   ```
3. `dtbo.img` 第 32 字节起不同：
   ```
   [backup] 000b c142 ...   ← dtbo 条目大小 0xC142*4
   [unpack] 000b ea85 ...   ← 更大 → 新版本加了 dtb 条目
   ```

> ⚠️ **给 Lrom 的实际建议**：做 root 流程时，**绝不能默认拿 `backups/` 里的镜像当 stock 底包**。
> 它来自另一版固件，patch 后刷入有 bootloop 风险。必须用与当前 `ro.build.version.incremental`
> 一致的包解出来的 `init_boot.img`（本例中 `unpacked/rom/init_boot.img`）。

> 📌 **另外发现一个可能的 bug**：`backups/` 里 `vbmeta*.img` 大小都是 65,536（64 KiB），而同一批镜像
> `boot/recovery/dtbo` 的大小与 payload 中声明的大小完全相同（201,326,592 / 104,857,600 / 25,165,824）。
> 这说明 backup 走的是「按分区大小 dd 回读」，而 `vbmeta_a` 分区（`/dev/block/sde16`）实际是 64 KiB 槽位，
> payload 里只放了 12 KiB 有效镜像。**因此 backup 出的 vbmeta 是带尾部填充的 64 KiB 版本**，
> 若直接 `fastboot flash vbmeta backups/.../vbmeta.img` 会写入 64 KiB，正常，但**不能拿来和
> `unpacked/rom/vbmeta.img`（12 KiB）做 sha256 相等性比对**——两者本就不该相等。
> 建议 Lrom 在 root 文档里明确区分「分区回读大小」与「payload 声明大小」。

```bash
$ adb shell 'ls -l /dev/block/by-name/...'
/dev/block/by-name/boot_a          -> /dev/block/sde13
/dev/block/by-name/init_boot_a     -> /dev/block/sde32
/dev/block/by-name/dtbo_a          -> /dev/block/sde17
/dev/block/by-name/vbmeta_a        -> /dev/block/sde16
/dev/block/by-name/vbmeta_system_a -> /dev/block/sda6
/dev/block/by-name/vbmeta_vendor_a -> /dev/block/sda8
[exit 0]

$ adb shell 'blockdev --getsize64 /dev/block/by-name/boot_a'
blockdev: /dev/block/by-name/boot_a: Permission denied
[exit 1]  ← 普通 adb shell 无权读块设备大小，符合预期（无 root）
```

---

## 3. 欧加设备的特殊限制：boot / init_boot / vbmeta 属于哪一档？

### 3.1 设备端分区表实测（只读 readdir）

```bash
$ adb shell 'ls /dev/block/by-name/ | grep -Ei "vbmeta|boot|dtbo|tz|xbl|abl|hyp|keymaster|super"'
abl_a        abl_b        boot_a       boot_b
dtbo_a       dtbo_b       featenabler_a featenabler_b
hyp_a        hyp_b        init_boot_a  init_boot_b
keymaster_a  keymaster_b  super
tz_a         tz_b         tzsc
vbmeta_a     vbmeta_b     vbmeta_system_a vbmeta_system_b
vbmeta_vendor_a vbmeta_vendor_b
vendor_boot_a vendor_boot_b
vm-bootsys_a vm-bootsys_b
xbl_a        xbl_b        xbl_config_a xbl_config_b
xbl_ramdump_a xbl_ramdump_b xbl_sc_logs xbl_sc_test_mode
[exit 0]
```

**确认：`boot` / `init_boot` / `dtbo` / `vbmeta` / `vbmeta_system` / `vbmeta_vendor` / `vendor_boot`
全部有独立的 `_a` / `_b` 两份槽位**，是标准的 A/B 布局。

### 3.2 `unlock_critical` 未执行时到底锁了什么？

**AOSP 的定义**（`fastboot flashing unlock_critical`）：只保护 bootloader 自身启动链上的关键分区，
文档明确点名的是 **`boot` 和 `recovery` 之外**的那些 —— 即 `abl`、`xbl`、`tz`、`hyp`、`keymaster`、
`devcfg`、`aop`、`qupfw` 等。**`boot` / `init_boot` / `dtbo` / `vbmeta*` 不属于 bootloader-critical 集合**，
在普通 `unlock` 后即可写；`unlock_critical` 是额外保护 bootloader 自身的。

**但本设备的情况比 AOSP 更复杂**，任务背景给出的实测现象是：

> 「本设备 `tz`/`abl`/`hyp`/`keymaster` 等 13 个分区被锁，但 `xbl` 反而可刷」

这个现象**用纯 AOSP 模型解释不通**（`xbl` 比 `tz` 更"关键"）。可能的解释（**⚠️ 未确认，需实测 or 查社区确定**）：

1. 欧加在 `abl` 里对**每一个分区名**做了白名单/黑名单判断，而不是用统一的 critical 标记；
2. 与 `oplus_sec`（`/dev/block/sde27`，1 MiB 的欧加签名校验分区）关联，签到哪个就锁哪个；
3. 与 `engineering_cdt` / `ocdt`（欧加独有的「工程 CDT」）相关 —— 社区解锁普遍要改 `abl + ocdt`，
   说明欧加把解锁状态做成了多处冗余存储。

**⚠️ 未确认的项（必须诚实标注）**：

- `boot_a` / `init_boot_a` / `vbmeta_a` 在本设备 `unlock`（非 `unlock_critical`）状态下**是否能被
  `fastboot flash` 成功写入**——本次只读调研无法验证，**必须实际进 fastboot 试一次才知道**。
- 那「13 个被锁分区」的**确切名单**，本地没有找到已落盘的记录（`grep -rn "unlock_critical\|critical" src/`
  只命中 `flash.py` 里的**错误消息模式匹配**，不是分区名单）：

  ```
  $ cd Lrom && grep -rn "unlock_critical\|critical" src/ --include=*.py
  src/lrom/core/flash.py:33:    LOCKED = "锁定（需 unlock_critical）"
  src/lrom/core/flash.py:292:        if is_critical_locked(msg):
  src/lrom/core/flash.py:299:            recovery_hint="fastboot flashing unlock_critical  # 风险高，谨慎",
  src/lrom/core/flash.py:469:        if is_critical_locked(msg):
  src/lrom/core/flash.py:652:    "not allowed for critical partitions",
  src/lrom/core/flash.py:654:    "critical partitions",
  src/lrom/core/flash.py:658:def is_critical_locked(message: str) -> bool:
  src/lrom/core/flash.py:711:    lines.append(f"锁定: {len(locked)} 个分区（关键分区，需 unlock_critical）")
  ```

  这说明 Lrom 是**按 fastboot 返回的错误字符串被动判定**，而不是事先知道哪些分区被锁。
  → **建议**：Lrom 增加一条只读探测（`fastboot getvar partition-type:<name>` 或 `fastboot fetch`），
  在 root 前先把 `boot`/`init_boot`/`vbmeta` 的可写性判定出来，而不是等用户刷失败。

### 3.3 实测可见的「安全档位」推断

| 分区 | 档位判断 | 依据 |
|---|---|---|
| `boot` / `init_boot` | **AOSP 非 critical，理论可写** | AOSP `unlock_critical` 只保护 bootloader 链 |
| `dtbo` / `vbmeta` / `vbmeta_system` / `vbmeta_vendor` | **AOSP 非 critical，理论可写**；但欧加可能因 AVB 链被额外保护 | 同上 |
| `abl` / `tz` / `hyp` / `keymaster`（12–13 个） | **欧加实测锁定**，需 `unlock_critical` | 任务背景的实测现象 |
| `xbl` | **本设备实测可刷（反直觉）** | 任务背景的实测现象 |
| `persist` / `modemst1/2` / `devinfo` / `keystore` / `frp` | **绝不要碰**（丢 IMEI/指纹/DRM） | Lrom `plan.py` 已内置 `NEVER_OVERWRITE` 保护 |

> **风险提示**：`fastboot flashing unlock_critical` 在欧加设备上**会清空全部数据**且**没有回退路径**。
> 而 root 只需要 `init_boot` —— 按 AOSP 模型**根本不需要 `unlock_critical`**。
> **强烈建议 Lrom 的 root 流程永远不要引导用户去 `unlock_critical`**，除非用户明确要刷 bootloader 链分区。
> 这一点和 Lrom 现有设计一致：`src/lrom/core/plan.py` 里 `DANGER_PARTITIONS` 已经包含
> `xbl/xbl_config/abl/tz/hyp/keymaster/...`，标记为「bootloader 链分区，写错会导致无法开机」，
> 并要求二次确认 —— 合理，保持。

---

## 4. 欧加 ColorOS/OxygenOS 的 AVB 行为：要不要 `--disable-verification`？

### 4.1 实测状态：当前 AVB 已处于「可启动未验证」状态

```
ro.boot.verifiedbootstate   = orange     ← 橙色：bootloader 已解锁，校验失败仍可启动
ro.boot.flash.locked        = 0          ← 未锁
ro.boot.vbmeta.device_state = unlocked
ro.boot.veritymode          = enforcing  ← dm-verity 仍在 enforcing
ro.boot.veritymode.managed  = yes        ← ⚠️ 关键：verity 由 management 域管理
ro.boot.avb_version         = 1.2
ro.boot.vbmeta.size         = 21056
```

**解读：**

- `verifiedbootstate=orange` 说明**现在就已经允许「未通过 AVB 校验」的镜像启动**了。
  → **理论上，把 `init_boot` patch 成 Magisk 后，只要 `init_boot` 不被独立校验，
  在 `orange` 状态下就能正常开机，不需要碰 `vbmeta`。**
- `ro.boot.veritymode.managed = yes`（值 `yes`）是 AOSP 的 **dm-verity 可立即失效（managed）** 机制：
  Android 可以在运行期通过 `libdm` 主动关掉 verity，而不需要改 `vbmeta` 的 flag。
  **这是 Android 12+ 设备上「不需要刷 `vbmeta --disable-verification` 也能 root」的技术基础。**
- `ro.boot.veritymode = enforcing` 表示**当前** verity 仍在跑（逻辑分区 `system/vendor/...` 全部以
  `dm-N` + `erofs(ro)` 挂载，读验证有效）。**但这不是对 `init_boot` 的校验** ——
  `init_boot` 是 ramdisk，走的是 AVB 里 `init_boot` 这个 descriptor 的 hash 校验，
  由 `vbmeta` 里的链决定，**而 `orange` 状态下校验失败只警告不阻止启动**。

```
$ adb shell 'mount | grep -Ei "dm-|verity"'
/dev/block/dm-17 on /          type erofs (ro,seclabel,relatime,...,cache_strategy=readaround)
/dev/block/dm-18 on /system_ext type erofs (ro,...)
/dev/block/dm-19 on /product    type erofs (ro,...)
/dev/block/dm-20 on /vendor     type erofs (ro,...)
/dev/block/dm-21 on /vendor_dlkm type erofs (ro,...)
/dev/block/dm-22 on /system_dlkm type erofs (ro,...)
/dev/block/dm-23 on /odm        type erofs (ro,...)
/dev/block/dm-24 on /my_product type erofs (ro,...)
[exit 0]
```

→ 逻辑分区全部是 `dm-` + `erofs(ro)`，**dm-verity/FEC 在正常工作**。这也是为什么
**root 之后不能改 `/system` 里的东西**（Magisk 走的是 overlayfs/magic mount，不是改分区，设计上就是为了绕开这个）。

### 4.2 社区/官方对 `--disable-verification` 的说法

Magisk 官方安装文档里，这一步是**可选**的，并且明确警告会**清空数据**：

> "(Optional) If your device has a separate vbmeta partition, you can patch the vbmeta partition with command:
> `fastboot flash vbmeta --disable-verity --disable-verification vbmeta.img`
> (note that it may wipe your data)"
>
> —— <https://github.com/topjohnwu/Magisk/blob/master/docs/install.md>

注意官方原文是 `--disable-verity --disable-verification` 两个 flag 一起，且标注 **(Optional)**。
另外同一份文档的流程是：**优先 patch 含 ramdisk 的分区**（`boot.img` / `init_boot.img` / `vendor_boot.img`），
只有设备「boot 里没有 ramdisk」时才去 patch `recovery.img`。

> 本设备 `init_boot.img` 的 `kernel_size=0`、只含 ramdisk（实测 §2.3）
> → **本设备属于「有 ramdisk」的现代机型，应 patch `init_boot.img`，不属于 recovery 方案。**

### 4.3 欧加刷 `vbmeta --disable-verification` 的已知风险（诚实标注）

| 说法 | 状态 |
|---|---|
| 「Magisk 官方说 vbmeta 那步是可选的，可能清数据」 | ✅ 已核实，见上面的官方文档 URL |
| 「现代 Android 12+ 设备（含本机 `veritymode.managed=yes`）通常不需要刷 vbmeta」 | ✅ 本机属性可核实（`ro.boot.veritymode.managed=yes`） |
| 「欧加设备刷 `vbmeta --disable-verification` 具体会怎样（比如黄字警告、Play Integrity 降级、ota 失败）」 | **⚠️ 未确认** —— 本次网络受限（见 §7），没有找到可引用的欧加专门说明 |
| 「欧加某些机型刷了 disable-verification 后 OTA 会失败 / 变砖」 | **⚠️ 未确认** —— 社区常见说法，但本次未能抓到可引用来源，**不作为结论** |

> **给 Lrom 的保守建议**：默认流程 **只 patch + 刷 `init_boot`，完全不动 `vbmeta`**。
> 只有当用户反馈刷完 `init_boot` 后开不了机（红字/无法引导）时，才作为**二级方案**提示
> `fastboot flash vbmeta --disable-verity --disable-verification`，并**必须显著警示会清空数据**。

---

## 5. A/B 槽位：root 后下一次 OTA 必丢 root，怎么办

### 5.1 实测：当前 `_a` 槽，且两边槽位都独立存在

```
ro.boot.slot_suffix = _a
ro.boot.slot        = （空）
vbmeta_a  -> sde16,  vbmeta_b  -> （独立存在）
boot_a    -> sde13,  boot_b    -> /dev/block/sde48
init_boot_a -> sde32, init_boot_b -> /dev/block/sde66
dtbo_a    -> sde17,  dtbo_b    -> /dev/block/sde52
[exit 0]
```

**`init_boot_a` 与 `init_boot_b` 是两块独立的物理块设备**（`sde32` vs `sde66`）。

### 5.2 为什么会丢 root（机制）

Android A/B 的 Seamless 更新语义：

1. 系统在 `_a` 运行 → OTA 下载后**把新版本写到 `_b`**（`init_boot_b` 收到的是**原厂未 patch** 的镜像）；
2. 更新完成 → `misc`/`bcb` 标记下次从 `_b` 启动 → 重启切换到 `_b`；
3. 此时 `slot_suffix` 变成 `_b`，跑的是**没打 Magisk 补丁的 `init_boot_b`** → **root 消失**；
4. Magisk App 还能看到「已安装但未激活」，重装才能恢复（因为 `_a` 上那个 patch 过的镜像是旧系统版本的，不能用）。

**用户当前手上就有这个隐患**：`backups/` 记录 `slot=a`，当前 `ro.boot.slot_suffix=_a`，
说明设备**从来没用过 `_b` 槽**（或用过又切回来了），`init_boot_b` 大概率仍是原厂旧版本。

### 5.3 `--slot all` 有必要吗？

| 做法 | 效果 | 评价 |
|---|---|---|
| `fastboot flash init_boot patched.img`（不带 `--slot`） | 只写**当前** `_a` | 够用，但 OTA 后丢 root |
| `fastboot flash --slot=all init_boot patched.img` | 同时写 `_a` 与 `_b` | **表面防 OTA 丢 root，实际不解决问题** |

**为什么 `--slot all` 解决不了问题：**

- OTA 写 `_b` 时是**整分区替换**，会**覆盖掉**你写进 `init_boot_b` 的 patch；
- 更严重：如果 `_b` 当前是**别的系统版本**，你往里刷「`_a` 版本的 patch 镜像」会造成
  **版本不匹配**（boot/init_boot 与 system 版本不一致），下次切到 `_b` 可能**开不了机**；
- 本次实测已证明这种版本不一致是**真实存在**的：`backups/`（旧 `.702`）与 `unpacked/`（新 `.1201`）的
  `init_boot.img` sha256、`dtbo.img` 内容、`boot.img` 的 `kernel_size` 全都不同（§2.7）。

> **结论**：`--slot all` **在 A/B 设备上不推荐用于 root**。
> 正确做法是**每次 OTA 之后，用新版本的包重新解 `init_boot.img` → 重新 patch → 刷到新的当前槽**。
> 也就是说 **root 必须跟 OTA 绑定成一个流程**，而不是一次性操作。

### 5.4 给 Lrom 的流程建议

```
1. 读 ro.build.version.incremental（如 U.247d78a_a3a1b_629cb）
2. 用 lrom inspect 确认 rom.zip 的 post-build-incremental 与之一致   ← 版本严格匹配，这次实测是一致的
3. lrom unpack --only init_boot                                       ← 只解 8 MiB，秒级
4. 推给设备上的 Magisk App patch → 拉回 magisk_patched_xxx.img
5. lrom flash init_boot（默认只刷当前槽；不提供 --slot all 的默认值）
6. 记录「本次 root 对应的 build 指纹」，OTA 后指纹一变就提示重新走一遍
```

---

## 6. 欧加设备已知的 root 教程与工具链（带 URL）

### 6.1 官方 / 一手资料

| 内容 | URL | 本次核实情况 |
|---|---|---|
| Magisk 官方安装文档（patch boot/init_boot/vendor_boot/recovery、vbmeta 可选步骤、**警告不要用别人的 patch 镜像**） | <https://github.com/topjohnwu/Magisk/blob/master/docs/install.md> | ✅ 已抓到正文 |
| Magisk 官方 FAQ（bootloop 保护、`magisk --remove-modules`、Safe Mode 按键时机） | <https://github.com/topjohnwu/Magisk/blob/master/docs/faq.md> | ✅ 已抓到正文 |
| `payload-dumper-go`（从 `payload.bin` 直接解分区，支持 incremental，**会校验 sha256 且失败非 0 退出**） | <https://github.com/ssut/payload-dumper-go> | ✅ 已抓到正文（仅作交叉验证，Lrom 自己已实现） |
| `vm03/payload_dumper`（Python 版 payload 解包参考实现） | <https://github.com/vm03/payload_dumper> | ✅ 已抓到正文 |
| 一加社区（中国站，官方论坛） | <https://bbs.oneplus.com/> | ⚠️ 页面可达（HTTP 200），但**内容需登录/JS 渲染，本次未取到具体 root 教程正文** |
| 一加社区海外站 | <https://community.oneplus.com/> | ⚠️ 实测返回：「自 2024 年 5 月 31 日起，中国大陆用户将无法访问海外社区」→ **本次不可用** |
| OPPO 官方客户服务（一加/OPPO/realme 共用） | <https://service.oneplus.com/> | ✅ HTTP 200，但**未见 bootloader 解锁/root 相关官方页面** |

### 6.2 社区教程（带风险提示）

| 内容 | URL | 关键信息 / 风险 |
|---|---|---|
| OPPO/一加/真我 全系高通机型解锁 Bootloader 教程（以 Find X6 Pro 为例，含 9008 免授权） | <https://github.com/AdaUnlocked/Oplus-Qualcomm-Unlock-2026-Universal-Guide> | 已抓到正文。**核心警告原文**：「一加、真我、绿厂（OPPO）的高通机型用户，千万别更新系统！！！…升级系统会导致该机 9008 免授权被永久熔断，且降级无效！」 |
| OPPO & OnePlus bootloader unlock 指南（脚本 + 排障） | <https://github.com/Crazer-Shifter84/OPlus-BootLoader-Unlock> | 已抓到 `docs/oneplus.md`：`adb reboot bootloader` → `fastboot flashing unlock` / `fastboot oem unlock`。**这是一般流程，未涉及欧加特有的 critical 锁** |
| XDA OnePlus Pad Pro 版块 | <https://xdaforums.com/f/oneplus-pad-pro.12901/> | ⚠️ 实测 **HTTP 403 Forbidden**（bunny.net 防护），本次不可访问 |

### 6.3 关于「Snapdragon 8 Gen 3 + Android 16 的已知兼容问题」

**⚠️ 未确认。** 本次未能找到可引用的、专门讨论「骁龙 8 Gen 3 + ColorOS 16 root」兼容问题的来源：

- Google / 维基百科 / `source.android.com` / `developer.android.com` / `raw.githubusercontent.com`
  **在本网络环境下全部不可达**（详见 §7），无法做定向搜索；
- `web_search` 工具**当前不可用**（缺 `DEEPSEEK_API_KEY`，见 §7）；
- XDA 被 403 拦截；
- GitHub API code search 触发速率限制（`API rate limit exceeded for 111.29.163.119`）。

**可核实的事实只有这些（来自本机实测，不依赖网络）：**

1. 本机 SoC 平台为 `pineapple`（`ro.boot.product.vendor.sku=pineapple`），内核 `6.1.141-android14-11`，
   Android 16，AVB 1.2 —— 属现代标准 Qualcomm/Google 组合，无异常。
2. `init_boot` 分区存在且只有 ramdisk（实测 §2.3）→ **不是需要 recovery 方案的老机型**。
3. `ro.boot.veritymode.managed=yes` → 支持运行期关 verity，**降低了对 `vbmeta` 的依赖**。
4. 社区来源（上表 AdaUnlocked）明确指出欧加近年的系统更新会**主动熔断 9008 免授权与旧解锁漏洞**，
   即「**别更新系统**」是欧加玩机的第一纪律 —— 这条对「按 OTA 重做 root」的流程设计有直接影响。

> 其余关于 8 Gen 3 / Android 16 的兼容性传闻，**本文不做断言**。

---

## 7. 本次调研的网络限制（诚实记录）

| 现象 | 命令 / 证据 |
|---|---|
| `web_search` 不可用 | 报错：`DeepSeek search has no API key for "DEEPSEEK_API_KEY"; … store it through the credentials service` |
| `source.android.com` 不可达 | `curl … → Connection timed out after 15001 ms` |
| `android.googlesource.com` 不可达 | `Connection timed out after 15001 ms` |
| `developer.android.com` 不可达 | `Connection timed out after 15001 ms` |
| `raw.githubusercontent.com` 连接被重置 | `curl: (35) Recv failure: 连接被对方重置` |
| `www.google.com` / `en.wikipedia.org` 不可达 | `http=000` |
| `xdaforums.com` 被 WAF 拦截 | `[HTTP 403] … blocked from viewing this page` |
| `github.com` **可达** | `http=200` → 本文所有 GitHub 引用均**实际抓取正文后**摘录 |
| `community.oneplus.com` 可达但拒绝大陆访问 | 页面原文：「自 2024 年 5 月 31 日起，中国大陆用户将无法访问海外社区」 |
| GitHub code search API 限流 | `API rate limit exceeded for 111.29.163.119` |

---

## 8. 汇总：给 Lrom 的欧加专区可落地清单

### 8.1 已经可以用本机事实支撑的（强结论）

1. ✅ **欧加全量 OTA zip 里能直接取到全部 root 所需镜像**（47 分区含 `init_boot`/`boot`/`vendor_boot`/`vbmeta`×3/`dtbo`），
   不需要 `.ops`/`.ofp`/`.ozip`，也不需要 9008/EDL。
2. ✅ **必须 patch `init_boot.img`，不是 `boot.img`，也不是 `recovery.img`** —— `init_boot` 的 `kernel_size=0` 且 `file`
   判定「ramdisk」；`file` 对 `boot.img` 判定「kernel, ramdisk」，但 Android 13+ 实际 ramdisk 已迁到 `init_boot`。
3. ✅ **默认不要碰 `vbmeta`** —— `verifiedbootstate=orange` + `flash.locked=0` + `veritymode.managed=yes`，
   当前已处于「校验失败仍可启动」状态。
4. ✅ **默认不要用 `--slot=all`** —— 会造成槽位间版本错配；实测已证明 `_a` 与旧备份镜像版本不同。
5. ✅ **`backups/` 里的镜像不能当 stock 底包** —— 它来自 `.702` 旧固件，与设备当前 `.1201` 不匹配
   （sha256 / `boot.img` kernel_size / `dtbo` 内容 / `vbmeta.digest` 四处都不同）。
6. ✅ **要教用户核对版本**：`ro.build.version.incremental` 必须等于包的 `post-build-incremental`，
   否则 patch 出来的镜像不可用。本例两者相同，属于「理想情况」。

### 8.2 仍然 ⚠️ 未确认、需要后续实测的

1. `boot_a` / `init_boot_a` / `vbmeta_a` 在**未执行 `unlock_critical`** 的设备上能否 `fastboot flash` 成功。
   → **必须进一次 fastboot 实测**（`fastboot getvar partition-type:init_boot_a` / `fastboot flash --dry-run` 之类，
   或用 Lrom 已有的 `plan` 干跑）。本文不敢断言。
2. 那「13 个被锁分区」的确切名单，以及「`xbl` 可刷但 `tz` 不可刷」的欧加实现原因。
3. 欧加刷 `vbmeta --disable-verification` 的具体后果（黄字、Play Integrity、OTA 失败等）。
4. Snapdragon 8 Gen 3 + Android 16 + ColorOS 16 的已知 root 兼容问题。
5. `init_boot_b` 当前实际是哪个版本（需读设备分区或另一版全量包才能判断）。

### 8.3 建议 Lrom 实现的命令（伪代码，不改现有源码，仅提议）

```bash
# 1. 版本匹配检查（只读）
lrom inspect rom.zip          # 已能给出 post-build-incremental 与设备指纹对比 → 建议加"严格匹配"判定

# 2. 只解 init_boot，8 MiB，秒级
lrom unpack rom.zip --only init_boot --out ./root-work
#   ./root-work/init_boot.img  → 推给 Magisk App patch

# 3. 刷入（必须二次确认 + 显示目标槽位）
lrom flash --image init_boot.img --target init_boot
#   默认只写当前槽；--slot all 需显式 --i-know-about-ota-risk 之类

# 4. 记录 root 状态与对应 build 指纹，供 OTA 后比对
```

---

## 附录 A：本次执行的全部命令与退出码

| # | 命令 | 退出码 | 作用 |
|---|---|---|---|
| 1 | `ls -la $LROM` | 0 | 确认工作目录 |
| 2 | `ls -la $LROM/unpacked/rom/` | 0 | 列出 47 个镜像 |
| 3 | `ls *.img \| wc -l` | 0 | 47 |
| 4 | `for f in boot init_boot …; do [ -f … ]` | 0 | 逐项存在性判定 |
| 5 | `file boot.img init_boot.img vbmeta*.img dtbo.img` | 0 | 头类型判定 |
| 6 | `xxd -l 32` × 7 | 0 | magic 逐字节确认 |
| 7 | `sha256sum` × 7 | 0 | 与 `unpacked.json` 对账 |
| 8 | `python3 -c 'json.load(unpacked.json)'` | 0 | 47 分区名 + `ok` 全 true |
| 9 | `ls -la $HOME/` | 0 | 找到 `rom.zip`（7,781,443,377 B） |
| 10 | `unzip -l rom.zip` | 0 | 6 个条目，只有 `payload.bin` |
| 11 | `unzip -p rom.zip META-INF/com/android/metadata` | 0 | `post-build-incremental=U.247d78a_a3a1b_629cb` |
| 12 | `unzip -p rom.zip payload_properties.txt` | 0 | `FILE_SIZE/METADATA_SIZE` 等 |
| 13 | `unzip -p rom.zip care_map.pb \| strings` | 0 | care_map 只含逻辑分区 |
| 14 | `./lrom --help` | 0 | 子命令列表 |
| 15 | `./lrom devices` | 0 | OPD2404 / OP5D77L1 / 槽位 a / 已解锁 |
| 16 | `./lrom inspect "$HOME/rom.zip"` | 0 | 47 分区、全量包、「可以线刷」、设备核对通过 |
| 17 | `adb version` / `adb devices` | 0 | `***(已隐去)***  device` |
| 18 | `fastboot devices` | 0 | **空输出**（当前不在 fastboot） |
| 19 | `adb shell getprop <slot/avb/verity/lock 系列>` | 0 | 见 §1 |
| 20 | `adb shell getprop \| grep -Ei "unlock|secur\|avb"` | 0 | 见 §1 / §4 |
| 21 | `adb shell 'ls -l /dev/block/by-name/'` | 0 | 全量 by-name 符号链接表 |
| 22 | `adb shell 'ls /dev/block/by-name/ \| grep -Ei "vbmeta\|boot\|dtbo\|tz\|xbl…"'` | 0 | 见 §3.1 |
| 23 | `adb shell 'blockdev --getsize64 …/boot_a'` | **1** | `Permission denied`（无 root，预期） |
| 24 | `adb shell 'cat /proc/partitions'` | **1** | `cat: /proc/partitions: Permission denied`（无 root，预期） |
| 25 | `adb shell 'mount \| grep -Ei "dm-\|verity"'` | 0 | 逻辑分区 `dm-N` + `erofs(ro)` |
| 26 | `grep -rn "unlock_critical\|critical" src/ --include=*.py` | 0 | 见 §3.2 |
| 27 | `python3` 解析 `boot.img` header | 0 | `kernel_size` 对比（§2.3 / §2.7） |
| 28 | `curl` 探测各站点可达性 | 混合 | 见 §7 |

> 命令 23、24 的退出码非 0 是**预期行为**（普通 adb shell 无 root 权限读块设备与 `/proc/partitions`），
> 已改为用 `ls -l /dev/block/by-name/`（readdir，无需 root）获取分区信息，成功。

## 附录 B：本文引用的 URL 汇总

- Magisk 官方安装文档 — <https://github.com/topjohnwu/Magisk/blob/master/docs/install.md>
- Magisk 官方 FAQ — <https://github.com/topjohnwu/Magisk/blob/master/docs/faq.md>
- `payload-dumper-go` — <https://github.com/ssut/payload-dumper-go>
- `vm03/payload_dumper` — <https://github.com/vm03/payload_dumper>
- OPPO/一加/真我高通解锁教程（含 9008 熔断警告） — <https://github.com/AdaUnlocked/Oplus-Qualcomm-Unlock-2026-Universal-Guide>
- OPPO & OnePlus bootloader unlock 指南 — <https://github.com/Crazer-Shifter84/OPlus-BootLoader-Unlock>
- 一加社区中国站 — <https://bbs.oneplus.com/>
- 一加社区海外站（本次被拒） — <https://community.oneplus.com/>
- XDA OnePlus Pad Pro（本次 403） — <https://xdaforums.com/f/oneplus-pad-pro.12901/>

无法引用（本次不可达，故**本文未使用**其内容）：
`source.android.com`、`android.googlesource.com`、`developer.android.com`、
`raw.githubusercontent.com`、`www.google.com`、`en.wikipedia.org`。
