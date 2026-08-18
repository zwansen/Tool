# RTCM3 协议数据格式参考手册

> 适用场景：ProtocolDecoder.dll 解码的四类协议之一（CavBin=BPDEBUG / SdkBin=APDEBUG / `$`=NMEA / `0xD3`=**RTCM3**）。
> 本文档对应 **RTCM 3.x 差分/星历流**，通常由基准站或网络 RTK 服务器播发，被接收机用于差分/RTK。
>
> **权威来源标注**
> - ✅ 权威（仓库代码）：`apps/workbench/third_party/ProtocolDecoder/rtcm_type.h`（`RtcmMeas`/`RtcmChanMeas`/`RtcmStationPos`/`RtcmStationInfo` 结构体）；
>   `libs/APDebugPkgSDK/common/sdk_decoder.h` 的 `SdkMsgID` RTCM 相关枚举（0x10–0x16、0xF0–0xF6、0xFE）。
> - ✅ 权威（RTCM 3.x 标准）：消息类型目录与帧结构。
> - ⚠️ 标准 RTCM3 字段用 DF（Data Field）编号，下方给出常见 DF 编号便于对照 ICD。

---

## 0. 整体结构（一句话）

RTCM3 = **二进制差分/精密改正流**，以同步字节 `0xD3` 起始，含 10 bit 消息长度 + 12 bit 消息类型 + 变长消息体 + 24 bit CRC。消息体分 **观测（MSM）/ 星历 / 基站信息 / SSR 改正** 四大类。

---

## 1. 帧结构（RTCM 3.x）

```
0xD3 | [6b 保留][10b 长度] | [12b 消息类型] | 消息体(长度字节) | [24b CRC]
 1B        2B                    → 共 3B 头         变长              3B
```

| 部分 | 类型 / 字节 | 说明 |
|------|------------|------|
| 同步字节 | `U8` = `0xD3` | RTCM3 标志（区别于 CavBin `0xC7 E5`、SdkBin `0xEB 0x90`） |
| 保留 + 长度 | 2 字节（高 6 bit 保留=0，低 10 bit = 消息体长度） | 消息体字节数 |
| 消息类型 | 12 bit（在下一字节继续） | 见 §2 消息类型目录 |
| 消息体 | 变长 | 具体消息内容 |
| CRC | 24 bit（3 字节） | CRC-24Q 校验 |

> ProtocolDecoder 在 `InputSdkPkg` 中把 RTCM3 包经 `IDRtcmMeas`(0xF1)/`IDRtcmStation`(0xF0) 等分支解出下方结构体。

---

## 2. 消息类型目录

### 2.1 ProtocolDecoder 直接解出的 RTCM 消息（`SdkMsgID`）

| msg_id | 枚举名 | 含义 |
|--------|--------|------|
| 0x10 | `IDRtcmMsm5` | RTCM MSM5 多系统观测（最常用观测消息） |
| 0x11 | `IDRtcmEphBbs` | RTCM BDS 星历 |
| 0x12 | `IDRtcmEphGps` | RTCM GPS 星历 |
| 0x13 | `IDRtcmEphGls` | RTCM GLONASS 星历 |
| 0x14 | `IDRtcmEphGal` | RTCM Galileo 星历 |
| 0x15 | `IDRtcmEphQzss` | RTCM QZSS 星历 |
| 0x16 | `IDRtcmStream` | RTCM 原始流 |
| 0xF0 | `IDRtcmStation` | RTCM 基站信息（→ `RtcmStation*`） |
| 0xF1 | `IDRtcmMeas` | RTCM 观测（→ `RtcmMeas`/`RtcmChanMeas`） |
| 0xF2 | `IDDoRtk` | RTK 解 |
| 0xF3 | `IDRtkTiming` | RTK 时延统计 |
| 0xF6 | `IDRtcmMon` | RTCM 监视 |
| 0xFE | `IDRawRTCM` | 原始 RTCM（未解码透传） |

### 2.2 标准 RTCM3 消息类型（常用）

| 类型 | 类别 | 说明 |
|------|------|------|
| 1001–1004 | GPS 经典观测 | 旧式 GPS 伪距/载波（已逐步被 MSM 取代） |
| 1005 / 1006 | 基站坐标 | 参考站 ECEF 天线坐标（1006 含天线高） |
| 1007 / 1008 | 天线描述 | 天线描述符/序列号、接收机描述符 |
| 1019 | GPS 星历 | GPS 卫星星历 |
| 1020 | GLONASS 星历 | GLO 卫星星历 |
| 1029 | 文本 | Unicode 文本消息 |
| 1033 | 接收机信息 | 接收机/天线类型与序列号 |
| 1042 | BDS 星历 | 北斗卫星星历 |
| 1044 | QZSS 星历 | 准天顶星历 |
| 1045 / 1046 | Galileo 星历 | GAL 星历 |
| 1057–1068 | SSR | 状态空间改正（轨道/钟差/码偏差/URA），用于 PPP/PPP-RTK |
| 1071–1077 | GPS MSM1–7 | GPS 多信号消息 |
| 1081–1087 | GLONASS MSM1–7 | GLO MSM |
| 1091–1097 | Galileo MSM1–7 | GAL MSM |
| 1101–1107 | BDS MSM1–7 | BDS MSM |
| 1111–1117 | QZSS MSM1–7 | QZSS MSM |
| 1121–1127 | SBAS MSM1–7 | SBAS MSM |

> **MSM 等级**：MSM1=伪距；MSM2=伪距+载波；MSM3=伪距+载波+多普勒；MSM4=+信号质量/锁时（最常用）；
> MSM5=+完整相位/码偏差；MSM6=+IRC；MSM7=完整。ProtocolDecoder 主解 **MSM5**。

---

## 3. `RtcmMeas`（msg 0xF1）—— RTCM 观测头部【✅ 权威】

> 来源：`rtcm_type.h::RtcmMeas`。

| # | 字段 | 类型 / 字节 | 含义（DF 编号） |
|---|------|------------|----------------|
| 1 | `rtcm_tag` | `U32` / 4 | tag 计数（历元对齐） |
| 2 | `reference_id` | `U16` / 2 | DF003 参考站 ID（U12） |
| 3 | `iod` | `U8` / 1 | DF409 数据龄期（U3） |
| 4 | `reserved` | `U8` / 1 | DF001 保留位（bit7） |
| 5 | `gps_time_ms` | `S32` / 4 | GPS 时（ms） |
| 6 | `bds_time_ms` | `S32` / 4 | BDS 时（ms） |
| 7 | `gls_time_ms` | `S32` / 4 | GLO 时（ms） |
| 8 | `gal_time_ms` | `S32` / 4 | GAL 时（ms） |
| 9 | `gls_day_in_week` | `U8` / 1 | GLO 周内天 |
| 10 | `header_flag` | `U8` / 1 | bit1~0:DF411 钟控 / bit3~2:DF412 外钟 / bit4:DF417 / bit7~5:DF418 平滑间隔 |
| 11 | `msm_state` | `U8` / 1 | bit0-GPS,1-BDS,2-GLS,3-GAL,bit6-multiple,bit7-init |
| 12 | `meas_count` | `U8` / 1 | 通道观测量计数 |
| 13 | `meas_pointer` | `PTR` / 8 | 通道观测量指针（→ `RtcmChanMeas[]`） |

---

## 4. `RtcmChanMeas`（每颗星一条，msg 0xF1）【✅ 权威】

> 来源：`rtcm_type.h::RtcmChanMeas`。与 APDEBUG `PvtChanMeas`、CavBin `RawObs` 字段高度对应。

| # | 字段 | 类型 / 字节 | 含义 |
|---|------|------------|------|
| 1 | `signal` | `U8` / 1 | 信号（sig ID） |
| 2 | `prn` | `U8` / 1 | PRN |
| 3 | `slot_freq` | `S8` / 1 | GLO 频点号 -7~6；其他信号为 specific data（U4）；无效= -128(0x80) |
| 4 | `half_cycle_amb` | `U8` / 1 | 半周模糊指示 |
| 5 | `state` | `U16` / 2 | 状态（见 `RTCM_CHAN_MEAS_STATE_*` 定义） |
| 6 | `cn0` | `S16` / 2 | 载噪比，**0.01 dB-Hz**（0=无效） |
| 7 | `lock_time_ms` | `U32` / 4 | 锁定时间，ms |
| 8 | `reserved` | `U32` / 4 | 保留 |
| 9 | `psr` | `DOUBLE` / 8 | 伪距，m |
| 10 | `adr` | `DOUBLE` / 8 | 载波相位，m |
| 11 | `doppler_speed` | `DOUBLE` / 8 | 多普勒，m/s |

---

## 5. `RtcmStationPos` / `RtcmStationInfo`（msg 0xF0）—— 基站信息【✅ 权威】

> 来源：`rtcm_type.h`。对应 RTCM 1005/1006（坐标）与 1007/1008（天线/接收机描述）。

### 5.1 `RtcmStationPos`（DF003 + 天线坐标）

| # | 字段 | 类型 / 字节 | 含义（DF 编号） |
|---|------|------------|----------------|
| 1 | `antenna_ref_ecef_x/y/z` | `DOUBLE`×3 / 24 | 天线 ECEF 坐标，m |
| 4 | `antenna_height` | `DOUBLE` / 8 | 天线高，m |
| 5 | `reference_station_id` | `U16` / 2 | DF003 参考站 ID |
| 6 | `reference_station_mask` | `U16` / 2 | DF021~024, DF141 掩码 |
| 7 | `flag` | `U8` / 1 | 有效标志 |

### 5.2 `RtcmStationInfo`（DF030/031/032/033/228/230/232）

| # | 字段 | 类型 / 字节 | 含义（DF 编号） |
|---|------|------------|----------------|
| 1 | `flag` | `U8` / 1 | 有效标志 |
| 2 | `antenna_setup_id` | `U8` / 1 | DF031 天线设置 ID |
| 3 | `reference_station_id` | `U16` / 2 | DF003 参考站 ID |
| 4 | `antenna_descriptor[32]` | `char[32]` / 32 | DF030 天线描述符 |
| 5 | `antenna_serial_number[32]` | `char[32]` / 32 | DF033 天线序列号 |
| 6 | `receiver_type_descriptor[32]` | `char[32]` / 32 | DF228 接收机类型 |
| 7 | `receiver_firmware_version[32]` | `char[32]` / 32 | DF230 固件版本 |
| 8 | `receiver_serial_number[32]` | `char[32]` / 32 | DF232 接收机序列号 |

---

## 6. 与 APDEBUG / BPDEBUG 的关系

| 维度 | APDEBUG（SdkBin） | BPDEBUG（CavBin） | RTCM3 |
|------|------------------|-------------------|-------|
| 角色 | 接收机**内部**状态/结果 | 接收机**内部**观测调试 | **外部**基准站/网络播发的差分改正 |
| 同步字 | `0xEB 0x90` | `0xC7 0xE5` | `0xD3` |
| 观测 | `PvtMeas.PvtChanMeas` | `RawObs` / `TrackInfo` | `RtcmMeas.RtcmChanMeas`（MSM） |
| 定位结果 | `SDK_MSG_PvtResult` | （无，靠 GGA） | （无） |
| 星历 | `SDK_MSG_*_Ephemeris` | （无） | `1019/1020/1042/1045…` |
| 基站 | — | — | `1005/1006/1007/1033` |

> **关系**：RTCM3 是接收机做 RTK 的**输入源**；APDEBUG/BPDEBUG 是接收机**自身的内部数据**。
> 三者可在同一份采集文件里并存（ProtocolDecoder 按同步字分流），互相补充：
> - 用 RTCM3 看「基站播了什么、差分龄期多少」；
> - 用 APDEBUG `PvtResult.rtk_status` + `age_ms` 看「本机 RTK 解状态」；
> - 用 BPDEBUG `TrackInfo`/RawObs 看「本机跟踪细节」。
