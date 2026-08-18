# APDEBUG（SdkBin）协议数据格式参考手册

> 适用场景：你工作区里 ProtocolDecoder.dll 解码的四类协议之一（CavBin=BPDEBUG / SdkBin=APDEBUG / `$`=NMEA / `0xD3`=RTCM3）。
> 本文档对应 **SdkBin = APDEBUG 包**，是 `$CN*` ASCII 调试语句的**二进制祖源**（更权威）。
>
> **权威来源标注**
> - ✅ 权威（仓库代码）：所有结构体定义来自
>   `libs/APDebugPkgSDK/include/ks_msg_types.h`、`libs/APDebugPkgSDK/include/pvt_meas_type.h`、
>   `libs/APDebugPkgSDK/common/sdk_decoder.h`、`libs/APDebugPkgSDK/SDK_pak_decode/IDDebugBPInfo.cpp`、
>   `libs/APDebugPkgSDK/APDebugPkg.cpp`。
> - ⚠️ 推断：SdkBin 帧头的精确字节布局（同步字/版本/长度）依据解码器 `CSdkDecoder::InputSdkPkg` 行为反推，
>   源码未公开头结构，故帧头部分标 ⚠️。

---

## 0. 整体结构（一句话）

APDEBUG = **二进制 SDK 包流**，以同步字 `0xEB 0x90` 划分，每个包含「包ID(SdkMsgID) + 结构体化的二进制消息体」。
它承载定位结果、观测、星历、调试诊断等，是 BPDEBUG `$CN*` 文本语句的**更底层、更完整的同一份数据**。

```
0xEB 0x90 包头(版本/tag/msg_id/长度 ⚠推断) ── payload(结构体) ── 校验
```

---

## 1. 帧结构（⚠ 推断，依据 `APDebugPkg.cpp` / `sdk_decoder`）

| 字段 | 类型 / 字节 | 说明 |
|------|------------|------|
| 同步字 | `U16` = `0xEB 0x90` | SdkBin 标志（与 CavBin `0xC7 0xE5`、RTCM3 `0xD3` 区分） |
| 包版本 | `U8` | SDK 包版本 |
| tag | `U32` | bb_tag 计数，贯穿所有消息，用于按历元对齐（全局 `g_bb_tag`） |
| msg_id | `U8` | 见 §2 `SdkMsgID` 枚举 |
| body_len | `U16` / `U32` ⚠ | 消息体长度 |
| body | 变长 | 对应消息的结构体（见 §3–§7） |
| 校验 | 变长 ⚠ | SDK 包级校验 |

> 注意：APDEBUG 每条消息体都带自己的 `bb_tag`（U32，4B），与 CavBin 的 EOE `tag` 同源，可按 tag 把不同消息ID归并到同一历元。

---

## 2. 消息类型表（`SdkMsgID` 枚举，来自 `sdk_decoder.h`）

| msg_id | 枚举名 | 消息体结构体 | 说明 |
|--------|--------|-------------|------|
| 0x10 | `IDRtcmMsm5` | RTCM MSM5 | RTCM 多系统 MSM5 观测 |
| 0x11 | `IDRtcmEphBbs` | — | RTCM BDS 星历 |
| 0x12 | `IDRtcmEphGps` | — | RTCM GPS 星历 |
| 0x13 | `IDRtcmEphGls` | — | RTCM GLO 星历 |
| 0x14 | `IDRtcmEphGal` | — | RTCM GAL 星历 |
| 0x15 | `IDRtcmEphQzss` | — | RTCM QZSS 星历 |
| 0x16 | `IDRtcmStream` | — | RTCM 原始流 |
| **0x20** | `IDCavNavPvtResult` | `SDK_MSG_PvtResult` | **定位结果**（位置/速度/状态/卫星数）—— 见 §3 |
| **0x21** | `IDCavNavPvtMeas` | `PvtMeas` + `PvtChanMeas[]` | **通道观测**（伪距/载波/多普勒/锁时/state）—— 见 §4 |
| 0x22 | `IDCavNavEph` | `SDK_MSG_*_Ephemeris` | 星历（GPS/BDS/GLO/SBAS/GAL/NavIC）—— 见 §6 |
| 0x23 | `IDCavNavEoe` | `EoeData` | 历元结束标记（同 CavBin EOE） |
| 0x24 | `IDCavNavIono` | `SDK_MSG_*_Iono` | 电离层参数 —— 见 §6 |
| 0x25 | `IDCavNavUtc` | `SDK_MSG_*_Utc_Param` | UTC 参数 —— 见 §6 |
| 0x26 | `IDCavNavTgd` | `SDK_MSG_*_Tgd` | 群延迟 TGD —— 见 §6 |
| 0x27 | `IDCavNavTracking` | `TrackInfo` | 跟踪信息（同 CavBin TrackInfo：sat_state/el/az/cn0） |
| 0x28 | `IDCavNavHeading` | `HeadingInfo` | 双天线定向（yaw/pitch/baseline） |
| 0x29 | `IDCavNavGrid` | — | PPP 格网 |
| 0x2A | `IDCavNavSatInfo` | — | 卫星信息 |
| 0x2B | `IDCavNavHas` | `SDK_MSG_E6_HAS` | Galileo E6 HAS —— 见 §7 |
| 0x2C | `IDCavNavPPPB2b` | `SDK_MSG_PPPB2b` | BDS PPP-B2b —— 见 §7 |
| 0x2D | `IDCavNavAlm` | `SDK_MSG_*_Almanac` | 历书 —— 见 §6 |
| 0x2E | `IDCavNavQzssL6` | `SDK_MSG_QzssL6` | QZSS L6 —— 见 §7 |
| 0x2F | `IDPvtSolInfo` | — | PVT 解算信息 |
| 0x30 | `IDBBStart` | — | 基带启动 |
| 0x31 | `IDVersion` | — | 版本信息 |
| **0x32** | `IDDebugBPInfo` | `BPInfo*`（见 §5） | **调试诊断**（复位/可见星/捕获/接收机状态）—— 见 §5 |
| 0x33 | `IDDebugPVTInfo` | `PvtInfo` | 调试 PVT 信息（pvt_state 判定见 BPDEBUG 文档） |
| 0x34–0x39 | Flash/Nic | — | Flash 操作 / NIC 信息 |
| 0x3A | `IDLBandInfo` | `LBand_Info` | L-Band 通道信息 —— 见 §7 |
| 0x3B | `IDLBandData` | `LBand_Data` | L-Band 数据 —— 见 §7 |
| 0x3C | `IDThreadStatistics` | — | 线程统计 |
| 0x3D | `IDCskInfo` | — | CSK 信息 |
| 0xF0 | `IDRtcmStation` | `RtcmStation*` | RTCM 基站信息（见 RTCM3 文档） |
| 0xF1 | `IDRtcmMeas` | `RtcmMeas` | RTCM 观测（见 RTCM3 文档） |
| 0xF2 | `IDDoRtk` | — | RTK 解 |
| 0xF3 | `IDRtkTiming` | `RtkTiming` | RTK 时延统计 |
| 0xF6 | `IDRtcmMon` | — | RTCM 监视 |
| 0xF7 | `IDRtkCore` | — | RTK 核心 |
| 0xF8 | `IDRtkSat` | — | RTK 卫星 |
| 0xF9 | `IDRtkSignal` | — | RTK 信号 |
| 0xFE | `IDRawRTCM` | — | 原始 RTCM |
| 0xFF | `IDNull` | — | 空 |

---

## 3. `SDK_MSG_PvtResult`（msg 0x20）—— 定位结果【✅ 权威】

> 来源：`pvt_meas_type.h`。这是你做「各时刻定位质量 / TTFF」最直接的数据源，比数 CavBin GGA 更稳。

| # | 字段 | 类型 / 字节 | 含义 |
|---|------|------------|------|
| 1 | `bb_tag` | `U32` / 4 | tag 计数（历元对齐） |
| 2 | `interval_ms` | `U32` / 4 | 解算间隔，ms |
| 3 | `antenna_index` | `U8` / 1 | 天线序号 |
| 4 | `sv_number` | `U8` / 1 | **主天线定位卫星数** |
| 5 | `sv_number_dual` | `U8` / 1 | 副天线定位卫星数（双天线模式） |
| 6 | `pvt_fix_type` | `U8` / 1 | 定位类型（0=无，1=单点，2=RTK浮点，3=RTK固定…） |
| 7 | `rtk_status` | `U8` / 1 | RTK 状态 |
| 8 | `heading_status` | `U8` / 1 | 航向状态 |
| 9 | `pos_status` | `U8` / 1 | 位置状态 |
| 10 | `vel_status` | `U8` / 1 | 速度状态 |
| 11 | `time_status` | `U8` / 1 | 时间状态（7=准确，6=预测） |
| 12 | `drift_status` | `U8` / 1 | 钟漂状态 |
| 13 | `time_source` | `U8` / 1 | UTC 时间系统 |
| 14 | `leap_second_status` | `U8` / 1 | 闰秒状态 |
| 15 | `gps_leap_second` | `S8` / 1 | GPS 闰秒 |
| 16 | `bds_leap_second` | `S8` / 1 | BDS 闰秒 |
| 17 | `year` | `U8` / 1 | 年（0xFF 无效，实际=year+2000） |
| 18 | `month` | `U8` / 1 | 月 |
| 19 | `day` | `U8` / 1 | 日 |
| 20 | `hour` | `U8` / 1 | 时 |
| 21 | `minute` | `U8` / 1 | 分 |
| 22 | `second` | `U8` / 1 | 秒 |
| 23 | `milli_second` | `U16` / 2 | 毫秒 |
| 24 | `rtk_station_id` | `U16` / 2 | RTK 基站 ID |
| 25 | `age_ms` | `S32` / 4 | RTK 差分龄期，ms |
| 26 | `height_mod` | `FLOAT` / 4 | 高程异常 |
| 27 | `baseline_length_rtk` | `FLOAT` / 4 | RTK 基线长度 |
| 28 | `baseline_length_heading` | `FLOAT` / 4 | 航向基线长度 |
| 29 | `heading_degree` | `FLOAT` / 4 | 航向角，度 |
| 30 | `clk_drift` | `FLOAT` / 4 | 钟漂，m/s |
| 31 | `pos_ecef_x/y/z` | `DOUBLE`×3 / 24 | ECEF 坐标，m |
| 34 | `vel_ecef_x/y/z` | `DOUBLE`×3 / 24 | ECEF 速度，m/s |
| 37 | `std_pos_ecef_x/y/z` | `FLOAT`×3 / 12 | ECEF 坐标标准差，m |
| 40 | `cov_pos_ecef[6]` | `FLOAT`×6 / 24 | ECEF 坐标协方差矩阵 |
| 46 | `std_vel_ecef_x/y/z` | `FLOAT`×3 / 12 | ECEF 速度标准差 |
| 49 | `cov_vel_ecef[6]` | `FLOAT`×6 / 24 | ECEF 速度协方差 |
| 55 | `std_heading_degree` | `FLOAT` / 4 | 航向角标准差 |
| 56 | `hdop` | `FLOAT` / 4 | HDOP |
| 57 | `vdop` | `FLOAT` / 4 | VDOP |
| 58 | `tdop` | `FLOAT` / 4 | TDOP |
| 59 | `pl_valid_flag` | `U32` / 4 | 保护级别有效标识 |
| 60 | `pl_tmir` … `pl_heading` | `FLOAT`×10 / 40 | 各类保护级别（PL） |

> **与 TTFF 关系**：`pvt_fix_type` 从 0/无定位 跳到 ≥1（单点/RTK）的历元 ≈ 首次定位 → TTFF 终点；`sv_number` = 主天线定位用星数。

---

## 4. `PvtMeas` + `PvtChanMeas`（msg 0x21）—— 通道观测【✅ 权威】

> 来源：`pvt_meas_type.h`。这是 APDEBUG 版的「RawObs」——每个通道一条 `PvtChanMeas`。

### 4.1 `PvtChanMeas`（每颗星一条）

| # | 字段 | 类型 / 字节 | 含义 |
|---|------|------------|------|
| 1 | `bb_chan_id` | `U8` / 1 | 基带通道序号 |
| 2 | `signal` | `U8` / 1 | 信号（sig ID，与 CavBin RawObs `sig` 同表） |
| 3 | `prn` | `U8` / 1 | PRN |
| 4 | `slot_freq` | `S8` / 1 | GLO 频点号 -7~6 |
| 5 | `frame_id` | `S8` / 1 | 当前子帧号 |
| 6 | `psr_gross_error` | `U8` / 1 | 伪距粗差参考（m，>255 置255） |
| 7 | `state` | `U16` / 2 | 状态位（见下） |
| 8 | `psr` | `DOUBLE` / 8 | 伪距，m |
| 9 | `psr_smooth` | `DOUBLE` / 8 | 平滑伪距 |
| 10 | `adr` | `DOUBLE` / 8 | 载波相位，m |
| 11 | `doppler_speed` | `DOUBLE` / 8 | 多普勒，m/s |
| 12 | `compensate_meter` | `DOUBLE` / 8 | 钟差补偿距离 |
| 13 | `cn0` | `U16` / 2 | 载噪比，**0.01 dB-Hz**（即 ÷100 = dB-Hz） |
| 14 | `pld` | `U16` / 2 | 锁相环指示（PLD） |
| 15 | `lock_time_ms` | `U32` / 4 | 锁定时间，ms |

**`state` 位定义（U16）：**

| bit | 宏 | 含义 |
|-----|-----|------|
| 0 | `PVT_CHAN_MEAS_STATE_PSR_VALID` (0x1) | 伪距有效 |
| 1 | `PVT_CHAN_MEAS_STATE_DOPPLER_VALID` (0x2) | 多普勒有效 |
| 2 | `PVT_CHAN_MEAS_STATE_ADR_VALID` (0x4) | 载波相位有效 |
| 3 | `PVT_CHAN_MEAS_STATE_FIX_VALID` (0x8) | **可参与解算**（注释原文："可以参与解算（实际参与解算或者(仅由于被sig_mask未参与解算且inno<30m)）"） |
| 4 | `PVT_CHAN_MEAS_STATE_EL_LOW` (0x10) | 仰角较低 |
| 7 | `PVT_CHAN_MEAS_STATE_PSR_GROSSERROR` (0x80) | 伪距有较大粗差 |

> **重要**：`state` bit3 = "可参与解算" 与 CavBin `sat_state` bit27 = "参与解算"（你报告里那条）是**同源等价**——一个在观测层(PvtMeas)、一个在跟踪层(TrackInfo)，bit 语义一致。

### 4.2 `PvtMeas`（头部，含时间/系统标识）

| # | 字段 | 类型 / 字节 | 含义 |
|---|------|------------|------|
| 1 | `bb_tag` | `U32` / 4 | tag 计数 |
| 2 | `antenna_index` | `U8` / 1 | 天线序号 |
| 3 | `msm_system_mask` | `U8` / 1 | MSM 播发系统标识（bit0-GPS,1-BDS,2-GLS,3-GAL,4-QZSS,5-IRNSS） |
| 4 | `gps/bds/gls/gal/irnss_time_state` | `U8`×5 / 5 | 各系统时间质量 |
| 9 | `gps/bds/gls/gal/irnss_time_adjust_ms` | `S8`×5 / 5 | 各系统时间调整（ms） |
| 14 | `gls_leap_year` / `gls_day_in_week` | `S8`/`S8` / 2 | GLO 闰年/周内天 |
| 16 | `gls_day_number` | `S16` / 2 | GLO 4 年内天计数 |
| 17 | `gps/bds/gal/irnss_week` | `S16`×4 / 8 | 各系统 GPS 周 |
| 21 | `gps/bds/gls/gal/irnss_local_time_ms` | `S32`×5 / 20 | 各系统虚拟本地时 |
| 26 | `gps/bds/gls/gal/irnss_rcv_time` | `DOUBLE`×5 / 40 | 各系统解算时间 |
| 31 | `clk_drift` | `DOUBLE` / 8 | 解算钟漂，m/s |
| 32 | `error_flag` | `U32` / 4 | 错误码 |
| 33 | `reserve` | `U16` / 2 | 保留 |
| 34 | `meas_count` | `U16` / 2 | 通道观测量计数 |
| 35 | `meas_pointer` | `PTR` / 8 | 通道观测量指针（指向 `PvtChanMeas[]`） |

> **时间基准**：`gps_week` + `gps_rcv_time`(s) = GPS 周内秒；`gps_local_time_ms` = 本地毫秒时。复位后本地时从 0 重新计数 → 也是复位相对时间轴的好依据。

---

## 5. `IDDebugBPInfo`（msg 0x32）—— 调试诊断【✅ 权威】

> 来源：`SDK_pak_decode/IDDebugBPInfo.cpp`。**这是 CavBin `$CN*` ASCII 语句的二进制祖源**（更可靠，无需解析文本）。
> 结构体 `BPInfo` 聚合了若干子结构，解码时按 `bp_event` 等字段展开并打印（`$CN*` 系列文本即由这里 dump 而来）。

### 5.1 `BPInfoEvent` —— 复位检测金矿【✅】

| 字段 | 类型 / 字节 | 含义 |
|------|------------|------|
| `reset_cnt` | `U32` / 4 | **累计复位次数** |
| `reset_flag` | `U32` / 4 | 复位标志 |
| `reset_latch` | `U8` / 1 | 复位锁存 |

解码（`IDDebugBPInfo.cpp:243-245`）来自一个 `bp_event` 字：
- `reset_cnt = (bp_event >> 7) & 0x01FF`（9 bit，0–511）
- `reset_flag = (bp_event >> 1) & 0x003F`（6 bit）
- `reset_latch = bp_event & 0x0001`

> **对你的 TTFF/多次复位场景**：`reset_cnt` 直接给出复位次数，比猜 GGA 中断或扫 `$GPTXT` 横幅都可靠；复位后 `gps_local_time_ms` 归零，可用作相对时间轴起点。

### 5.2 `BPInfoSatVis` —— 可见星【✅ 即 `$CNVIW` 祖源】

| 字段 | 类型 / 字节 | 含义 |
|------|------------|------|
| `maybe_inview[6]` | `U32`×6 / 24 | 候选可见星位图（192 bit，覆盖全系统） |
| `certain_inview[6]` | `U32`×6 / 24 | 确认可见星位图（192 bit） |

> 每组 192 bit 对应各系统的 PRN 位图；置位 bit 数 = 可见星数。`maybe`/`certain` 之分对应 `$CNVIW` 的两个位图参数。

### 5.3 `BPInfoAcq` —— 捕获/搜索星【✅ 即 `$CNACQ` 祖源】

| 字段 | 类型 / 字节 | 含义 |
|------|------------|------|
| `acq_scheduler_cnt` | `U8` / 1 | 捕获调度器计数（低 7 bit = K，实际调度信号数） |
| `acq_sat_list_low[20]` | `U32`×20 / 80 | 各信号正在捕获的卫星位图（低 32 bit） |
| `acq_sat_list_high[20]` | `U32`×20 / 80 | 各信号正在捕获的卫星位图（高 32 bit） |

> `acq_sat_list_low[i]` + `acq_sat_list_high[i]` 拼接成 64 bit 卫星位图；置位 bit 数 = 该信号正在**捕获/搜索**的星数。这正是你之前关心的"严格意义的搜星（acquisition）"数据。

### 5.4 `BPInfoEphAlm` —— 星历/历书有效【✅ 即 `$CNEPH`/`$CNALM` 祖源】

| 字段 | 类型 / 字节 | 含义 |
|------|------------|------|
| `eph_valid[7]` | `U32`×7 / 28 | 各系统星历有效位图 |
| `alm_valid[7]` | `U32`×7 / 28 | 各系统历书有效位图 |

### 5.5 `BPInfoRcv` —— 接收机状态【✅ 即 `$CNRCV` 祖源】

| 字段 | 类型 / 字节 | 含义 |
|------|------------|------|
| `tim_state` | `U16` / 2 | 时间状态 |
| `meas_count` | `U8` / 1 | 通道观测量计数 |
| （scene_mask） | `U32` | 抗干扰/欺骗场景标志（对应 `$CNRCV` 末字段，位定义见 BPDEBUG 文档 § 的 scene_mask 12 位宏） |

### 5.6 其余子结构

| 子结构 | 字段 | 类型 / 字节 | 说明 |
|--------|------|------------|------|
| `BPInfoCPU` | `cpu_info[20]` | `U64`×20 / 160 | 线程运行时间/CPU 时序（对应 `$CNCPU`） |
| `BPInfoFlash` | `BPInfoFlash` | — | NVM/Flash 状态（对应 `$CNNVM`） |
| `BPInfoChDel` | `ch_del_info` | — | 通道被删原因（if_last/ant/freq/sat_id/del_reason） |

---

## 6. 星历 / 历书 / UTC / TGD / IONO【✅ 权威】

> 来源：`pvt_meas_type.h`。各系统结构体字段一致，下表以 GPS 为例（`SDK_MSG_Gps_Ephemeris`），其余系统同构。

### 6.1 星历（`SDK_MSG_*_Ephemeris`，msg 0x22）

| 字段 | 类型 / 字节 | 含义 |
|------|------------|------|
| `flag` | `U16` / 2 | 有效标识（首字节） |
| `svid` | `U16` / 2 | 卫星号 |
| `health` | `U16` / 2 | 健康（1=不健康） |
| `urai` / `iode2` / `iode3` | `U16`/`U8`/`U8` | 精度指示/数据龄期 |
| `sat_type` / `eph_type` | `U8`/`U8` | 卫星类型/星历类型 |
| `iodc` | `U16` / 2 | 时钟数据龄期 |
| `week` | `S32` / 4 | 电文周 |
| `toc` / `toe` | `S32` / 4 | 钟差/星历参考时间 |
| `af0`/`af1`/`af2` | `DOUBLE`×3 | 卫星钟差 0/1/2 阶 |
| `sqrtA`/`ecc`/`w`/`delta_n`/`M0` | `DOUBLE`×5 | 轨道根数 |
| `omega0`/`omega_dot`/`i0`/`idot` | `DOUBLE`×4 | 升交点/倾角根数 |
| `cuc`/`cus`/`crc`/`crs`/`cic`/`cis` | `DOUBLE`×6 | 调和改正振幅 |
| `A`/`n`/`root_ecc`/`omega_t`/`Ek` | `DOUBLE`×5 | 派生量（防重复计算） |

### 6.2 历书（`SDK_MSG_*_Almanac`，msg 0x2D）、UTC（`SDK_MSG_*_Utc_Param`，msg 0x25）、TGD（`SDK_MSG_*_Tgd`，msg 0x26）、IONO（`SDK_MSG_*_Iono`，msg 0x24）

均为标量集合（`DOUBLE`/`U16`/`U8`/`S8`），字段含义已在源码注释中明确（a0/a1/a2 钟差参数、wn 参考周、tls 闰秒、tgd 群延迟、α/β 电离层系数等）。详细字段见 `pvt_meas_type.h` 对应结构体。

---

## 7. L-Band / HAS / QZSS-L6 / PPP-B2b【✅ 权威】

> 来源：`pvt_meas_type.h` / `sdk_decoder.h`。

| 消息 | 结构体 | 关键字段（类型/字节） |
|------|--------|----------------------|
| `IDLBandInfo` (0x3A) | `LBand_Info` | `bb_tag`(U32), `ch_num`(U8), `ch_info`(PTR) |
| L-Band 通道 | `LBand_Channel_Info` | `channel`(U8), `state`(U8), `speed`(U8), `PLD`(U8), `cn0`(U16÷100 dB-Hz), `carrier_freq`(U32), `doppler`(S32), `symbol_cnt`(U32) |
| `IDLBandData` (0x3B) | `LBand_Data` | `channel`(U8), `data[127]`(U32) |
| `IDCavNavHas` (0x2B) | `SDK_MSG_E6_HAS` | `prn`(U8), `status`(U8), `msg_type`(U8), `msg_len`(U8), `msg_data[32*53]`(U8) |
| `IDCavNavQzssL6` (0x2E) | `SDK_MSG_QzssL6` | `prn`(U8), `flag`(U8), `frame_data[250]`(U8) |
| `IDCavNavPPPB2b` (0x2C) | `SDK_MSG_PPPB2b` | `prn`(U8), `mes_type`(U8), `nav_data[16]`(U32) |

---

## 8. 与 BPDEBUG（`$CN*`）的关系

APDEBUG 与 CavBin（BPDEBUG）是**同一份固件数据的两种封装**：

| 你关心的量 | CavBin(BPDEBUG) 文本 | APDEBUG(SdkBin) 二进制 |
|-----------|---------------------|------------------------|
| 复位次数 | 无直接（需猜 GGA 中断 / 扫 `$GPTXT`） | **`IDDebugBPInfo.BPInfoEvent.reset_cnt`** ✅ |
| 可见星 | `$CNVIW` ⚠ 反推 | **`BPInfoSatVis.maybe/certain_inview`** ✅ |
| 捕获搜星 | `$CNACQ` ⚠ 反推 | **`BPInfoAcq.acq_sat_list_*`** ✅ |
| 星历/历书有效 | `$CNEPH`/`$CNALM` ⚠ | **`BPInfoEphAlm`** ✅ |
| 接收机场景状态 | `$CNRCV.scene_mask` ✅ | `BPInfoRcv` ✅ |
| 通道观测/CN0 | `$CHOBS` / 二进制 RawObs | **`PvtMeas.PvtChanMeas`** ✅（含 `state` bit3 参与解算） |
| 定位结果/用星数 | `$GPGGA` `qual`/`nsats` | **`SDK_MSG_PvtResult.sv_number`/`pvt_fix_type`** ✅ |

> **结论**：若你的数据里混有 APDEBUG 包，优先用这里的结构化字段（✅权威），绕开 `$CN*` 文本反推（⚠）和 GGA 时间中断问题。这与 BPDEBUG 文档 §8 的"私有 `$CN*` 语句"互为镜像。
