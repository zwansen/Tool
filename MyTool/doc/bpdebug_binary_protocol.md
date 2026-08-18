# BPDEBUG（CavBin）二进制协议 · 数据构成与解析过程（协议层精确版）

> **这份文档讲什么**：BPDEBUG 日志里**二进制数据本身**的协议——字节流里有什么消息、
> 每类消息的**精确字段布局**、每个字段的含义、以及解析过程。
> 不涉及 TTFF 功能统计，只到「字节流 → 消息 → 字段」这一层。
>
> **三份文档的分工**：
> | 文档 | 讲什么 |
> |------|--------|
> | `BPDEBUG解析协议.md` | ASCII 文本部分（`$C*` 语句）逐字段字典 |
> | **本文档** | **二进制部分（CavBin 帧）协议结构与解析** |
> | `bpdebug_trackinfo_pvt_reference.md` | ProtocolDecoder.dll 解析二进制后**输出的数据**字典 |
>
> **权威来源标注**
> - ✅✅ **SDK 源码**：`D:\Tool\APDebugPkgSDK\`（CavNav 解码器 + `gnss_signal.h` 信号 ID 枚举）
>   ——TrackInfo（`IDCavNavSatInfo.cpp` 的 `SatInfoBP`）、PVT（`pvt_meas_type.h` 的 `PvtMeas`/
>   `PvtChanMeas`/`SDK_MSG_PvtResult`）、信号 ID 原始值。**字段级权威**。
> - ✅ **仓库解析器**：`ttff_chobs_acq_report.py`（帧结构/校验和/RawObs 消费字段，实测 1.19GB 样本）。
> - ⚠️ **推断**：RawObs 44 字节条目中仓库未消费的后 32 字节，按 `$CHOBS` 文本字段顺序对照。

---

## 1. 日志数据构成

BPDEBUG 日志 = **二进制帧流** 与 **ASCII 文本行** 混排：

```
0xC7 0xE5 …二进制帧… $CHOBS,1,24,6,…  0xC7 0xE5 …二进制帧… #Receiver Reset:1,13F
       ├── 二进制帧（CavBin）──┤        ├─ ASCII 文本（模组 debug_print）─┤
```

| 部分 | 特征 | 内容 | 谁解析 |
|------|------|------|--------|
| **二进制帧** | `0xC7 0xE5` 同步头 + 校验和 | 观测（RawObs）、历元（EOE）、**TrackInfo（SatInfo）**、**PVT** 等 | 解码器 / `bpdebug_track_dump.exe`（DLL） |
| **ASCII 文本** | `$`/`#` 开头 | `$C*` 调试语句、`$xxGGA`、`#Receiver Reset` | 文本行扫描 |

---

## 2. CavBin 帧结构（逐字节）

```
| C7 | E5 | class | msg_id | len_lo | len_hi |  payload(len 字节)  | CK_A | CK_B |
  2B     1B     1B      2B(LE)       变长              1B     1B
```

- 帧头 **6 字节**，整帧 = `6 + length + 2`。
- **校验和（UBX 风格）**：对 `hdr[2:6] + payload` 逐字节累加：

```python
a = b = 0
for byte in hdr[2:6] + payload:
    a = (a + byte) & 0xFF
    b = (b + a) & 0xFF
# 要求 a == CK_A 且 b == CK_B
```

---

## 3. 消息类型表

| class_id | 含义 | msg_id | 消息 | 解析者 |
|----------|------|--------|------|--------|
| `0x80` | NAV（导航/观测） | `0x01` | **EOE** 历元结束 | 仓库纯 Python |
| `0x80` | NAV | `0x02` | **RawObs** 原始观测（44B/条，可分片） | 仓库纯 Python |
| `0x8F` | ASCII（调试输出） | `0x00` | **ASCII** 文本消息 | 仓库纯 Python |
| —（CavNav 系列） | 卫星/解算 | — | **TrackInfo（SatInfo）** / **PVT** | `ProtocolDecoder.dll` |

> TrackInfo / PVT 的消息 ID 未在仓库解析器范围内（由 DLL 处理），其**消息体结构**
> 见 §5 / §6（来自 SDK 权威）。

---

## 4. RawObs 观测消息（NAV 类 0x80 / msg 0x02）

**消息体**：`[meas_cnt: U8][continue_flag: U8][44B × meas_cnt]`

| 字段 | 类型 | 含义 |
|------|------|------|
| `meas_cnt` | U8 | 本帧观测条目数 |
| `continue_flag` | U8 | 分片标志，bit0=1 表示本历元还有后续帧（需累加） |

**44 字节观测条目**：

| 条目偏移 | 类型 | 字段 | 含义 | 权威 |
|---------|------|------|------|------|
| 0 | U8 | `ch` | 通道号 | ✅ |
| 1 | U8 | `prn` | 卫星 PRN | ✅ |
| 2 | U8 | `sig` | 频点 ID（权威表见 §5.4） | ✅ |
| 3 | U8 | `STATE` | 信号状态字 | ✅ |
| 4~5 | U16 | `cn0` | 载噪比，÷100 = dB-Hz | ✅ |
| 6~7 | U16 | `pld` | 锁相环指示 | ✅ |
| 8~9 | U16 | `snr` | 信噪比 | ✅ |
| 10~11 | U16 | `fld` | 标志位 | ✅ |
| 12~15 | S32 | `code_freq` | 码频率（Hz） | ⚠️ 按 `$CHOBS` 顺序推断 |
| 16~19 | U32 | `code_nco` | 码 NCO | ⚠️ 推断 |
| 20~23 | U32 | `code_count` | 码计数 | ⚠️ 推断 |
| 24~27 | S32 | `carr_freq` | 载波频率（Hz） | ⚠️ 推断 |
| 28~31 | U32 | `carr_nco` | 载波 NCO | ⚠️ 推断 |
| 32~35 | U32 | `carr_count` | 载波计数 | ⚠️ 推断 |
| 36~37 | U16 | `comp_code` | 补偿码相位 | ⚠️ 推断 |
| 38~39 | S16 | `comp_carr_f` | 补偿载波频率 | ⚠️ 推断 |
| 40~41 | U16 | `comp_carr_p` | 补偿载波相位 | ⚠️ 推断 |
| 42~43 | U16 | `ms`/`week` | 周内毫秒/周（低字节） | ⚠️ 推断 |

> 合计 44 字节。✅ = 仓库实取字段（PRN/频点/CN0 等用于数星）；
> ⚠️ = 无 ProtocolDecoder 结构体原文，按 `$CHOBS` 字段顺序对照（`BPDEBUG解析协议.md` §1），供参考。

---

## 5. TrackInfo（SatInfo）消息 —— 卫星级状态（✅✅ SDK 权威）

**来源**：`D:\Tool\APDebugPkgSDK\SDK_pak_decode\IDCavNavSatInfo.cpp`（`Parser_IDCavNavSatInfo`）。
这是 DLL 解析出「星历有效/参与解算」曲线的底层消息。

### 5.1 消息体布局（按解析顺序）

```
system_num: U8                                    // 本消息包含几个系统
对每个系统（共 system_num 个）：
  system_id: U8                                   // 系统 ID（见 §5.5）
  sat_en_byte: U8                                 // 卫星使能位图字节数（通常 8）
  sat_en: U8×sat_en_byte (U64)                    // 64bit 位图：bit i = 卫星 i 是否在本消息
  对每个置位卫星（sat_idx = i + k×32, k=0..1, i=0..31）：
    sat_state: U32                                // ★ 卫星状态字（见 §5.2）；=0 则跳过
    若 sat_state & 0x80000000：                   // bit31 置位才有仰角/方位
      el: FLOAT（弧度，×180/π=度）
      az: FLOAT
    track_num = sat_state & 0x00FF                // ★ 低 8 位 = 本星跟踪信号数
    对 j = 0..track_num-1（每个跟踪信号）：
      signal_id: U8                               // 频点 ID（§5.4）
      pld: U8                                     // 锁相环指示
      cn0: U16                                    // 载噪比（0.01dB-Hz）
      bb_state: U32                               // 基带状态
      pvt_state: U32                              // ★ 信号级状态字（§5.3）
      ext_state: U16                              // 扩展状态
```

### 5.2 `sat_state`（U32，卫星级）位定义

| 位 | 掩码 | 含义 | 来源 |
|----|------|------|------|
| bit 31 | `0x80000000` | 本星带仰角/方位角数据（el/az 存在） | ✅✅ SDK 解析分支 |
| bit 29 | `0x20000000` | **星历有效**（星历可用，可进解算候选） | ✅ DLL 输出 `eph_mask` |
| bit 27 | `0x08000000` | **参与解算**（正在参与定位解算） | ✅ DLL 输出 `fix_mask` |
| bit 11~0 | `0x0FFF` | 低 12 位含跟踪状态信息 | ✅ IDDebugPvtInfo 打印 `[11:0]` |
| bit 7~0 | `0x00FF` | **跟踪信号数 track_num**（决定随后读几个信号） | ✅✅ SDK 解析 |

### 5.3 `pvt_state`（U32，信号级）位定义

| 位 | 掩码 | 含义 |
|----|------|------|
| bit 31 | `0x80000000` | **可参与位置解**（信号满足解算条件；≠实际参与解算） |

> 一颗星多个频点各有一个 `pvt_state`，所以"按信号统计"的数值 ≥ "按星统计"。

### 5.4 信号 ID 权威表（`gnss_signal.h`，与仓库 `FREQUENCY_MAP` 一致 ✅）

| ID | 频点 | ID | 频点 |
|----|------|----|------|
| 1 | B1I | 14 | E1 |
| 3 | B1C | 15 | E5a |
| 5 | B2a | 16 | G1 |
| 6 | B2b | 17 | G2 |
| 7 | B3I | 22 | QZL1 |
| 11 | L1 | 23 | QZL2 |
| 12 | L2 | 24 | QZL5 |
| 13 | L5 | 29 | SBASL1 |
| 21 | B2I | 31 | IRNSSL5 |
| 25 | E5b / 26 E6 / 28 QZL6 / 30 SBASB1A | 255 | 无效 |

### 5.5 系统 ID（`SystemID`）

`BDS / GPS / GLS(格洛纳斯) / GAL / QZSS / IRNSS / SBAS`，字节值见 `IDCavNavSatInfo.cpp`
（`SYS_BD3`/`SYS_GPS`/`SYS_GLO`/`SYS_GAL`/`SYS_QZS`/`SYS_IRNSS`/`SYS_SBAS`）。

---

## 6. PVT 消息（✅✅ SDK 权威，`pvt_meas_type.h`）

### 6.1 `PvtMeas`（观测历元头）

| 字段 | 类型 | 含义 |
|------|------|------|
| `bb_tag` | U32 | tag 计数 |
| `antenna_index` | U8 | 天线序号 |
| `msm_system_mask` | U8 | 系统掩码（bit0-GPS,1-BDS,2-GLS,3-GAL,4-QZSS,5-IRNSS） |
| `gps/bds/gls/gal/irnss_time_state` | U8×5 | 各系统时间质量（7=精确） |
| `…_time_adjust_ms` | S8×5 | 各系统时间调整 |
| `gls_leap_year/day_in_week/day_number` | — | 格洛纳斯日历信息 |
| `gps/bds/gal/irnss_week` | S16×4 | 各系统周 |
| `…_local_time_ms` | S32×5 | 各系统虚拟本地时 |
| `…_rcv_time` | DOUBLE×5 | 各系统解算时间 |
| `clk_drift` | DOUBLE | 钟漂 m/s |
| `error_flag` | U32 | 错误码 |
| `meas_count` | U16 | 通道观测量数 |
| `meas_pointer` | PTR | 通道观测量（见 `PvtChanMeas`） |

### 6.2 `PvtChanMeas`（每通道观测量）

| 字段 | 类型 | 含义 |
|------|------|------|
| `bb_chan_id` | U8 | 基带通道号 |
| `signal` | U8 | 信号（频点 ID） |
| `prn` | U8 | PRN |
| `slot_freq` | S8 | 格洛纳斯频点号 -7~6 |
| `frame_id` | S8 | 当前子帧号 |
| `state` | U16 | ★ 状态位（见下） |
| `psr` / `psr_smooth` | DOUBLE | 伪距 / 平滑伪距（米） |
| `adr` | DOUBLE | 载波相位（米） |
| `doppler_speed` | DOUBLE | 多普勒（m/s） |
| `compensate_meter` | DOUBLE | 钟差补偿距离 |
| `cn0` | U16 | 载噪比（0.01 dB-Hz） |
| `pld` | U16 | 锁相环指示 |
| `lock_time_ms` | U32 | 锁定时间 |

**`state`（U16）位定义**：`0x1`=伪距有效、`0x2`=多普勒有效、`0x4`=载波相位有效、
`0x8`=**可以参与解算**、`0x10`=仰角较低。

### 6.3 `SDK_MSG_PvtResult`（解算结果）

`bb_tag`、`interval_ms`、`sv_number`（定位卫星数）、`pvt_fix_type`（定位类型）、
`rtk_status`、`pos_ecef_xyz`(DOUBLE)、`vel_ecef_xyz`(DOUBLE)、`std_*`、`cov_*`、
`hdop/vdop/tdop`、`heading_degree`、`clk_drift`、以及**保护级别 PL**（`pl_pos_east/north/up`、
`pl_time`、`pl_tmir` 等）。

---

## 7. EOE 消息（NAV 0x80 / msg 0x01）

- **历元结束**标记（End Of Epoch）。观测按 10Hz 输出，每个 EOE 表示一个采样时刻。
- 解析器以它打时间点：从冷启动复位起，每个 EOE 记一个点，随后清空本历元观测缓存。

---

## 8. ASCII 文本消息（ASCII 0x8F / msg 0x00）

消息体即文本，常见内容：

| 内容 | 含义 | 正则 |
|------|------|------|
| `$CHOBS,ch,prn,sig,STATE,cn0,…` | RawObs 文本镜像（字段字典见 `BPDEBUG解析协议.md`） | — |
| `$xxGGA,…` | 定位语句（首次定位时刻/质量） | `\$\w{2}GGA,…` |
| `#Receiver Reset:N,HEX` | 冷启动复位标记 | `#Receiver Reset:(\d+),([0-9A-Fa-f]+)` |

> ASCII 文本除作为消息体外，也**裸排在帧间**，解析器在帧空隙里同样扫描。

---

## 9. 解析过程（代码级）

```
逐块读取（64MB/块）
  ├─ 找同步头 0xC7 E5
  │    ├─ 同步头前字节 → ASCII 扫描（Reset/GGA/$C*）
  │    ├─ 帧头 6 字节 → length → payload + CK_A/CK_B
  │    │    ├─ 校验通过 → 按 class/msg 分发：
  │    │    │    RawObs → 解析 44B 条目（PRN/频点/CN0），分片累加
  │    │    │    EOE    → 10Hz 打点，清空历元观测
  │    │    │    ASCII  → 文本扫描
  │    │    └─ 校验失败 → 跳 1 字节继续（容忍噪声伪同步头）
  │    └─ 块尾帧不完整 → leftover 与下块拼接
  └─ 文件末 → 收尾
```

---

## 10. 与 ProtocolDecoder.dll 的分工

| 数据 | 解析器 | 输出 |
|------|--------|------|
| RawObs / EOE / ASCII（§4/7/8） | 仓库纯 Python（`FileAnalyzer`） | 冷启动循环、在视星曲线 |
| **TrackInfo / PVT**（§5/6） | `ProtocolDecoder.dll`（经 `bpdebug_track_dump.exe`） | `.track.json`（星历有效/参与解算/可参与位置解） |

> 注意：**仓库纯 Python 解析器目前只消费 §4/§7/§8**（数星用）；
> §5 TrackInfo / §6 PVT 的解析完全依赖 DLL。两份协议文档（本文档 + `bpdebug_trackinfo_pvt_reference.md`）
> 一个讲"消息结构"，一个讲"DLL 输出"，互为表里。

---

## 11. 解析后输出结构（应用层，非协议层）

> 提醒：以下不是二进制解析的"原始"结果，而是解析器**按冷启动功能聚合**后的结构。
> 协议层的原始解析结果 = 上述各消息逐帧解出的字段（§4~§8）。

- **CycleStats**（每冷启动循环）：`total_curve`（在视星 10Hz 曲线）、`freq_curves`、
  `freq_prn_curves`、`first_acq_epochs`、`time_to_k_total`（到 1/4/8/12/16/20/24/32 颗耗时）、
  `fix_epoch`/`ttff_s`（首次定位）等——**TTFF 功能视角**。
- **DLL 输出**（`.track.json`）：`eph_total_curve`/`fix_total_curve`/`pvt_total_curve`、
  `sats[].spans` 等——**基于 §5 TrackInfo 消息**，逐字段见 `bpdebug_trackinfo_pvt_reference.md`。

---

## 12. 真实样本速查（LG690P_tongxian_1.log，1.19GB）

- EOE 帧 **116126** 个（≈10Hz），TrackInfo 帧 **13677** 个，冷启动 **63** 次
- 纯 Python 解析（无 DLL）约 95s；DLL 完整解析数小时（已缓存则秒出）
