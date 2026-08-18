# NMEA（Asc）协议数据格式参考手册

> 适用场景：ProtocolDecoder.dll 解码的四类协议之一（CavBin=BPDEBUG / SdkBin=APDEBUG / `$`=**NMEA** / `0xD3`=RTCM3）。
> 本文档对应 **`$` 开头的 NMEA-0183 / BD3 语句**，是 BPDEBUG 透传区里"通用 GNSS 输出"部分（非 BPDEBUG 特有，但常与之混在同一文件）。
>
> **权威来源标注**
> - ✅ 权威（仓库代码）：`apps/workbench/third_party/ProtocolDecoder/nmea_type.h`（`GGA`/`RMC`/`GSV`/`GSA`/`GLL`/`VTG`/`ZDA`/`GBS`/`DHV` 解析结构体 + 信号索引 + 系统卫星数常量）。
> - ✅ 权威（Unicore 协议手册）：标准 NMEA 字段顺序与单位（`Unicore.pdf`）。
> - ⚠️ 注意：NMEA 是**逗号分隔文本**，字段在字节流里无固定偏移；下方「类型/字节」列标注的是 ProtocolDecoder **解析后输出结构体**的字段类型与字节宽度（即你拿来分析用的数值类型），而非文本本身的定长字节。

---

## 0. 整体结构（一句话）

NMEA = **ASCII 文本行协议**，以 `$` 起始、逗号分隔字段、`*` + 两 hex 校验和结尾、`\r\n` 换行。ProtocolDecoder 按 talker 前缀区分系统，按语句类型解析成结构化数据（`NmeaInfo0183` / `NmeaInfoBD3`）。

---

## 1. 帧结构（NMEA-0183）

```
$ ── talker(2) ── type(3) ── ,field1,field2,…,fieldN ── *hh ── <CR><LF>
```

| 部分 | 类型 / 字节 | 说明 |
|------|------------|------|
| 起始符 | 1 字符 `$` | 语句开始 |
| talker | 2 字符 | 系统前缀（见 §2） |
| type | 3 字符 | 语句类型（`GGA`/`RMC`/`GSV`…） |
| 字段 | 变长 ASCII | 逗号分隔，空字段留空 |
| 校验和 | `*` + 2 hex | 从 `$` 后到 `*` 前所有字符异或 |
| 结尾 | `\r\n` | 回车换行（2 字节） |

---

## 2. Talker 前缀（系统标识）

| 前缀 | 系统 | 说明 |
|------|------|------|
| `GP` | GPS | 标准 GPS |
| `GB` | 北斗 BD3 | 北斗 |
| `GA` | Galileo | 伽利略 |
| `GL` | GLONASS | 格洛纳斯 |
| `GQ` | QZSS | 准天顶 |
| `GN` | 多系统组合 | 混合定位（最常见于 `$GNGGA`/`$GNGSV`） |
| `BD` | 北斗（私有） | BD3 专用语句（`$BDGGA` 等） |
| `CC`/`TI` 等 | 计算机/厂商 | 辅助 |

> 多系统合一时常以 `$GNGGA`、`$GNGSV` 出现；`$GPGGA` 只报 GPS。

---

## 3. `$GGA` —— 全球定位系统定位数据【✅ 权威】

> 来源：`nmea_type.h::Nmea0183::GGA` + `Unicore.pdf`。**定位质量/用星数**是 TTFF 判据之一（`qual` 从 0→≥1 ≈ 首定位）。

| # | 文本字段 | 解析类型 / 字节 | 含义 |
|---|---------|----------------|------|
| 1 | `hhmmss.ss` | `utc_hour`(U32)+`utc_minute`(U32)+`utc_second`(DOUBLE) | UTC 时间 |
| 2 | 纬度 | `lat`(DOUBLE) | ddmm.mmmm |
| 3 | N/S | char | 纬度半球 |
| 4 | 经度 | `lon`(DOUBLE) | dddmm.mmmm |
| 5 | E/W | char | 经度半球 |
| 6 | **定位质量** | `quality`(S32) | -1 无效 / 0 未定位 / 1 单点 / 2 差分 / 4 RTK 固定 / 5 RTK 浮点 / 6 估计(DR) |
| 7 | **定位卫星数** | `sat_num`(S32) | 参与定位的卫星数 |
| 8 | HDOP | `hdop`(DOUBLE) | 水平精度因子 |
| 9 | 海拔高 | `alt`(DOUBLE) | m |
| 10 | 椭球-海平面差 | `sep`(DOUBLE) | m |
| 11 | 差分龄期 | `diff_age`(DOUBLE) | s |
| 12 | 差分基站号 | `diff_station`(S32) | — |

---

## 4. `$RMC` —— 推荐最小定位信息【✅ 权威】

> 来源：`nmea_type.h::Nmea0183::RMC`。**提供日期**，是跨天时间解析的基准。

| # | 文本字段 | 解析类型 / 字节 | 含义 |
|---|---------|----------------|------|
| 1 | `hhmmss.ss` | `utc_hour`/`utc_minute`(U32)+`utc_second`(DOUBLE) | UTC 时间 |
| 2 | 状态 | `status`(char) | A 有效 / V 警告 |
| 3–6 | 纬度/经度 | `lat`/`lon`(DOUBLE) | 含 N/S、E/W |
| 7 | 地面速度 | `speed`(DOUBLE) | m/s |
| 8 | 地面航向 | `course`(DOUBLE) | 度 |
| 9 | **日期 `ddmmyy`** | `year`/`month`/`day`(S32) | 跨天解析日期基准 |
| 10 | 磁偏角 | `mag_variation`(DOUBLE) | 度 |
| 11 | 定位模式 | `pos_mode`(char) | N/A/E/D/F/R |
| 12 | 导航状态 | `nav_status`(char) | V=不提供 |

---

## 5. `$GSV` —— 可见卫星【✅ 权威，ProtocolDecoder 已结构化】

> 来源：`nmea_type.h::Nmea0183::GSV`（**已被解析成每星矩阵**，是"各时刻可见星/仰角方位角"最干净的数据源）。
> 单条 GSV 文本最多 4 颗星；`GSV` 结构体按系统存 `cn0[signal][sat]`、`el[sat]`、`az[sat]`、`mask[signal]`。

| # | 文本字段 | 解析类型 / 字节 | 含义 |
|---|---------|----------------|------|
| 1 | 总消息数 | （多段计数） | 本次 GSV 共几条 |
| 2 | 本条序号 | — | 第几条 |
| 3 | **可见卫星总数** | 经 `mask` 统计 | 天空中可见星数 |
| 4–7 | 星1 PRN/仰角°/方位°/SNR | `cn0`(S32)/`el`(S32)/`az`(S32)/`mask`(U32/U64) | 每组 4 字段，最多 4 颗 |

> **系统容量常量**（`nmea_type.h`）：GPS 37 / BDS 63 / GLS 28 / GAL 50 / QZSS 10 / IRNSS 14 / SBAS 39。
> **信号容量**：GPS 4 / BDS 13 / GLS 2 / GAL 4 / QZSS 4 / IRNSS 1。
> 复用：把每个历元 `mask` 置位 bit 数相加 = **可见星数**；`el`/`az` 给出几何分布。

---

## 6. `$GSA` / `$GSI` —— 当前活动卫星与精度因子【✅ 权威】

> 来源：`nmea_type.h::Nmea0183::GSA`（0183）/`NmeaBD3::GSI`（BD3 同义）。按系统给出可用卫星位图 + DOP。

| # | 文本字段 | 解析类型 / 字节 | 含义 |
|---|---------|----------------|------|
| 1 | 运行模式 | `op_mode`(char) | M 手动 / A 自动 |
| 2 | 导航模式 | `nav_mode`(S32) | 1 无定位 / 2 二维 / 3 三维 |
| 3–14 | PRN 列表（各系统） | `gps_svid_mask`(U32)/`bds_svid_mask`(U64)/`gls_svid_mask`(U32)/`gal_svid_mask`(U64)/`qzss_svid_mask`(U32)/`irnss_svid_mask`(U32) | 各系统参与定位卫星位图 |
| 15–17 | PDOP/HDOP/VDOP | `pdop`/`hdop`/`vdop`(DOUBLE) | 精度因子 |

> BD3 变体 `GSI` 额外有 `tdop`（时间精度因子）。

---

## 7. `$VTG` —— 对地航向与速度【✅ 权威】

| # | 文本字段 | 解析类型 / 字节 | 含义 |
|---|---------|----------------|------|
| 1–2 | 真北航向° | `course_true`(DOUBLE) | — |
| 3–4 | 磁北航向° | `course_mag`(DOUBLE) | — |
| 5–6 | 速度（m/s） | `speed`(DOUBLE) | 对地速度 |
| 7 | 定位模式 | `pos_mode`(char) | N/A/E/D/… |

---

## 8. `$ZDA` —— UTC 时间/日期【✅ 权威，备选时间源】

| # | 文本字段 | 解析类型 / 字节 | 含义 |
|---|---------|----------------|------|
| 1 | `hhmmss.ss` | `utc_hour`/`utc_minute`(U32)+`utc_second`(DOUBLE) | UTC 时间 |
| 2–4 | 日/月/年 | `year`/`month`/`day`(S32) | 日期 |
| 5–6 | 时区 时/分 | `zone_hour`/`zone_minute`(S32) | 本地时区 |

---

## 9. `$GLL` —— 地理定位（经纬度+时间）【✅ 权威】

| # | 文本字段 | 解析类型 / 字节 | 含义 |
|---|---------|----------------|------|
| 1–2 | 纬度/经度 | `lat`/`lon`(DOUBLE) | 含 N/S、E/W |
| 3–5 | UTC 时间 | `utc_hour`/`utc_minute`(U32)+`utc_second`(DOUBLE) | — |
| 6 | 状态 | `status`(char) | V 无效 / A 有效 |
| 7 | 定位模式 | `pos_mode`(char) | N/A/E/D |

---

## 10. `$GBS` —— GNSS 卫星故障偏差【✅ 权威】

| # | 文本字段 | 解析类型 / 字节 | 含义 |
|---|---------|----------------|------|
| 1–3 | UTC 时间 | `utc_hour`/`utc_minute`(U32)+`utc_second`(DOUBLE) | — |
| 4–6 | 偏差（纬/经/高） | `err_lat`/`err_lon`/`err_hae`(DOUBLE) | m |
| 7 | 故障卫星 | `svid`(S32) | — |
| 8–10 | 漏检概率/偏差/标准差 | `miss_prob`/`bias`/`stddev`(DOUBLE) | — |
| 11–12 | 系统/信号标识 | `system_id`/`signal_id`(S32) | （0183 版） |

---

## 11. `$DHV` —— 三维速度（BD3 补充）【✅ 权威】

> 来源：`nmea_type.h::NmeaBD3::DHV`（0183 不含三维速度，借用 BD3 的 DHV 补速度输出）。

| # | 文本字段 | 解析类型 / 字节 | 含义 |
|---|---------|----------------|------|
| 1–3 | UTC 时间 | `utc_hour`/`utc_minute`(U32)+`utc_second`(DOUBLE) | — |
| 4 | 三维速度 | `speed3d`(DOUBLE) | m/s |
| 5–7 | x/y/z 速度 | `vx`/`vy`/`vz`(DOUBLE) | m/s |
| 8–12 | 二维/最大/平均/全程/有效速度 | `speed2d`/`max_speed3d`/`mean_speed3d`/`mean_speed3d_all`/`valid_speed3d`(DOUBLE) | — |

---

## 12. ProtocolDecoder 的 NMEA 输出结构

| 输出结构 | 包含语句 | 说明 |
|---------|---------|------|
| `NmeaInfo0183` | GGA/GLL/GSA/GSV/RMC/VTG/ZDA/GBS + DHV(BD3 借) | 标准 0183 解析结果 |
| `NmeaInfoBD3` | GGA/GLL/GSI/GSV/RMC/VTG/ZDA/GBS/DHV | BD3 变体（GSA→GSI） |

> `NmeaMsgID` 枚举：GGA=0, GLL=1, GSA=2, GSI=3, GSV=4, RMC=5, VTG=6, ZDA=7, GBS=8, DHV=9。
> **信号索引**（`Nmea0183::SignalIndex`，用于 GSV 的 `cn0[signal][sat]` 第一维）：
> - GPS: L1CA=0,L2CL=1,L2CM=2,L5=3
> - BDS: B1I=0,B2I=1,B3I=2,B1C=3,B2a=4,B2b=5,S1=6,S2C=7,B1A=8,B3A=9,B3AE=10,B3Q=11,S2A=12
> - GLS: G1=0,G2=1；GAL: E1=0,E5a=1,E5b=2；QZSS: L1CA=0,L1S=1,L2CM=2,L2CL=3

---

## 13. 与 BPDEBUG 的关系

- NMEA 是**通用 GNSS 输出**，任何日志都有，不是 BPDEBUG 特有——它只是和 `$CN*`/`$CHOBS` 一起被塞进 CavBin 的 `0x8F/0x00` ASCII 透传区。
- 时间轴：CavBin 解析器依赖 `$GGA`/`$RMC` 取墙钟时间；若遇复位窗口 GGA 时间缺失，可改用 APDEBUG 的 `gps_local_time_ms`（§ APDEBUG 文档 §4.2）或 CavBin `EOE.sampleRateHz`。
- 可见星：`$GSV` 已被 ProtocolDecoder 结构化（`el`/`az`/`cn0` 矩阵），比数私有 `$CNVIW` 文本更规整，是"各时刻搜星数"的优选源。
