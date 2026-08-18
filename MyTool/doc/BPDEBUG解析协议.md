# BPDEBUG 解析后 `$C*` 语句 · 逐字段含义字典

> **这是你要的那份**：BPDEBUG 二进制/日志经解析（解码器 / 模组 debug_print）后，文本里出现的 `$C*` / `$CH*` 语句，**每一个逗号分隔字段的含义**。
>
> **权威性图例**
> - ✅ **仓库解析器实取**：字段含义来自本仓库真实解析代码（`apps/.../BpDebugDecoder.cpp`、`modules/agc_analysis/agc_processor.py`、`modules/flag_analysis/flag_processor.py`、`flag_config.py`），逐字段对应，可信。
> - ⚠️ **按 SDK 结构体反推**：仓库无 Unicore debug 手册，字段含义按 `libs/APDebugPkgSDK/SDK_pak_decode/IDDebugBPInfo.cpp` 的 `BPInfo*` 结构体对应 + 你的样本结构推断，供参考。
>
> **架构一句话**：这些 `$C*` 语句是**解析后文本里出现的语句**（模组固件 debug_print 经解码器输出，或二进制 RawObs 经解码器转成 `$CHOBS`）。仓库里**真正逐字段解析**的只有 `$CHOBS`/`$CNAGC`/`$CNJAM`/`$CNRCV`(scene_mask)/`$CNTIM`；其余 `$CN*` 仅被识别/计数，字段按 SDK 反推。
>
> **新增「类型 / 字节」列说明**：`$C*` 语句本身是**逗号分隔文本**，在字节流里没有固定偏移；本列标注的是每个字段的**底层二进制类型与字节宽度**，便于数值解析与单位换算。约定：`U8`=1B、`U16`=2B、`U32`=4B、`S32`=有符号4B、`Q8`=8bit 定点小数（值÷256）、`bitmap(N)`=N-bit 位图（hex 编码）。**RawObs 的逐字节偏移（offset 0–43）见 `bpdebug_data_format_reference.md` §2**，不在本文件重复。

---

## 1. `$CHOBS` —— RawObs 文本镜像（解析后生成）✅

来源：`apps/workbench/src/parse/BpDebugDecoder.cpp:47-48`（`parseObsLine`，字段顺序与解析索引一一对应）。

```
$CHOBS,ch,prn,sig,STATE,cn0,pld,snr,fld,
       code_freq,code_nco,code_count,
       carr_freq,carr_nco,carr_count,
       comp_code,comp_carr_f,comp_carr_p,ms,week[,...]
```

| # | 字段 | 类型 / 字节 | 含义 | 解析处理 |
|---|------|------------|------|---------|
| 1 | `ch` | U8 | 通道号 | `channelId = parts[1]` |
| 2 | `prn` | U8 | 卫星 PRN | `prn = parts[2]` |
| 3 | `sig` | U8 (频点ID) | **频点 ID**（→ 查信号名映射） | `signalId = parts[3]`；经 `bpdebugSystemName/signalName` 转系统+信号名 |
| 4 | `STATE` | U32 (hex, 4B) | 跟踪状态（hex，原 RawObs `state` 字段） | `state = parts[4]`（16 进制）；`state!=0` → `fixFlag=1` |
| 5 | `cn0` | U16→float (÷100) | 载噪比原始值，**÷100 = dB-Hz** | `cn0 = parts[5]/100.0` |
| 6 | `pld` | U8 | PLD（PLL 锁定指示，无量纲） | `pllLock = parts[6]` |
| 7 | `snr` | U16→float (÷100) | 信噪比原始值，**÷100** | `snr = parts[7]/100.0` |
| 8 | `fld` | U8 | （保留/标志位，解析时跳过） | 跳过 |
| 9 | `code_freq` | U32 (4B) | 码频率（原始） | `codeFreqRaw = parts[9]` |
| 10 | `code_nco` | U32 (4B) | 码 NCO | `codeNcoRaw = parts[10]` |
| 11 | `code_count` | U32 (4B) | 码计数 | `codeCount = parts[11]` |
| 12 | `carr_freq` | S32 (4B, 有符号) | 载波频率（有符号）→ **多普勒** | `carrierFreqRaw = parts[12]`（s32）；多普勒 = `carrierFreqRaw × scale`（scale=采样率/2³²） |
| 13 | `carr_nco` | U32 (4B) | 载波 NCO | `carrierNcoRaw = parts[13]` |
| 14 | `carr_count` | U32 (4B) | 载波计数 | `carrierCount = parts[14]` |
| 15 | `comp_code` | S32 (4B) | 码相位补偿 | `codePhaseCompensate = parts[15]` |
| 16 | `comp_carr_f` | S32 (4B) | 载波频率补偿 | `carrierFreqCompensate = parts[16]` |
| 17 | `comp_carr_p` | S32 (4B) | 载波相位补偿 | `carrierPhaseCompensate = parts[17]` |
| 18 | `ms` | U32 (4B) | 毫秒计数 | `msCount = parts[18]` |
| 19 | `week` | U16 (GPS周) | GPS 周（扩展字段，符号位可能为负占位） | `week = parts[19]` |
| 20+ | （扩展累加字段） | 不定 | 视固件版本追加（如多普勒、累积载波相位等），解析器只读前 19 个 | — |

> 样本对照：`$CHOBS,0,14,11,03616976,4108,98,4108,0,168990262,808648704,7156,-278186,1226188311,-250511593,-1,0,0,268832785,-1,0,268832785`
> → ch=0, prn=14, sig=11(GPS L1), cn0=41.08 dB-Hz, pld=98, carr_freq=-278186（×scale=多普勒 Hz）。

---

## 2. `$CNAGC` —— 自动增益控制（AGC）✅

来源：`modules/agc_analysis/agc_processor.py:138`（格式注释 + `parse_mixed_file` 实取）。

```
$CNAGC,<channel>,<reserved>,<agc_stat>,<gain>,<ival>
```

| # | 字段 | 类型 / 字节 | 含义 | 解析处理 |
|---|------|------------|------|---------|
| 1 | `channel` | U8 | AGC 通道号（0–3） | `channel = parts[6]` |
| 2 | `reserved` | U32 (hex, 4B) | 保留（hex，如 `C70A0808`） | 不解析物理量 |
| 3 | `agc_stat` | U32 (hex, 4B) 位域 | AGC 状态字（hex），内含 3 段：<br>• `bits[31:22]` = **BIAS_I**（10 位有符号）<br>• `bits[21:12]` = **BIAS_Q**（10 位有符号）<br>• `bits[11:0]` = **CUR_POWER**（12 位无符号） | `agc_stat=int(hex,16)`；`bias_i=(>>22)&0x3FF`、`bias_q=(>>12)&0x3FF`（有符号）、`cur_power=&0xFFF` |
| 4 | `gain` | U8 (索引) | 增益索引（查校准表取除数） | `gain=parts[8]`；`NOR_POWER = CUR_POWER / 校准表[gain]`（默认表 `[16,35,81,115,163,230]`） |
| 5 | `ival` | U8 / U32 | 采样间隔 / 校验相关 | 不参与物理量 |

> 输出物理量：`(bias_i, bias_q, cur_power, gain, nor_power, ts)`。

---

## 3. `$CNJAM` —— 逐通道 AGC 干扰/EMI 检测状态 ✅

来源：`modules/agc_analysis/agc_processor.py:51-54`（字段顺序注释）。

```
$CNJAM,<ch>,<band>,<state>,<gain>,<normal_gain>,<nor_q8>,<base_q8>,<ratio_q8>,
       <active>,<warm_done>,<warm>,<healthy>,<pos_ok>,<band_max_cn0>,<emi>,<blk_emi>,<arm_age>
```

| # | 字段 | 类型 / 字节 | 含义 | 解析关注 |
|---|------|------------|------|---------|
| 1 | `ch` | U8 | 通道 | 分组键 |
| 2 | `band` | U8 (0–3) | 频段（0=未配置,1=L1,2=L2L5,3=B3L6） | `AGC_BAND_NAMES` 映射 |
| 3 | `state` | U8 | 检测器状态 | — |
| 4 | `gain` | U8 | 增益 | — |
| 5 | `normal_gain` | U8 | 正常增益 | — |
| 6 | `nor_q8` | Q8 定标 (÷256) | 归一化值（Q8 定标） | — |
| 7 | `base_q8` | Q8 定标 (÷256) | 基准值（Q8） | — |
| 8 | `ratio_q8` | Q8 定标 (÷256) | 比值（Q8） | — |
| 9 | `active` | U8 (bool) | **未否决的原始 ACTIVE**（干扰活跃） | 重点 |
| 10 | `warm_done` | U8 (bool) | 预热完成 | — |
| 11 | `warm` | U8 (bool) | 预热中 | — |
| 12 | `healthy` | U8 (bool) | 健康 | — |
| 13 | `pos_ok` | U8 (bool) | 定位正常 | — |
| 14 | `band_max_cn0` | U8 | 频段最大 CN0 | — |
| 15 | `emi` | U8 | **EMI 分类状态** | 重点 |
| 16 | `blk_emi` | U8 (bool) | 阻塞 EMI | — |
| 17 | `arm_age` | U8 / U32 | 触发年龄 | — |

---

## 4. `$CNRCV` —— 接收机状态 + 场景标志 ✅

来源：`modules/flag_analysis/flag_processor.py:147-160`（仅取末字段 `scene_mask`）、`flag_config.py`（12 位定义）。语句自身无时间，继承同文件 GGA/RMC 或同行 `$CNTIM`。

```
$CNRCV,<...前导字段...>,<scene_mask>
```

| 项 | 类型 / 字节 | 含义 |
|----|------------|------|
| 末字段 `scene_mask` | U32 (hex, 4B, 低12位有效) | `%08X` 十六进制，**抗干扰/欺骗场景标志位图**（语句无时间，靠 GGA/RMC/CNTIM 打戳） |

**`scene_mask` 12 位定义（与 `$KXPVT` 同源，同文件优先 `$KXPVT`）**

| bit | 宏 | 含义 | 分组 |
|-----|----|------|------|
| 0x0001 | TUNNEL_SPOOFY | 隧道欺骗 | 欺骗/干扰 |
| 0x0002 | POS_BAD | 定位质量差 | 质量/跳变 |
| 0x0004 | UNDER_OVERPASS | 高架下 | 场景/环境 |
| 0x0008 | GENERATIVE_JAMMING | 生成式欺骗 | 欺骗/干扰 |
| 0x0010 | POS_GAP | 位置跳变 | 质量/跳变 |
| 0x0020 | POS_GAP_FIX | 跳变修复中 | 质量/跳变 |
| 0x0040 | SHIELD | 屏蔽 | 场景/环境 |
| 0x0080 | HEIGHT_JUMP | 高程跳变 | 质量/跳变 |
| 0x0100 | AGC_JAMMING | AGC 干扰 | 欺骗/干扰 |
| 0x0200 | TUNNEL | 隧道转发 | 场景/环境 |
| 0x0400 | SPOOFY | 欺骗(SPOOFY) | 欺骗/干扰 |
| 0x0800 | EMI | EMI 干扰 | 欺骗/干扰 |

---

## 5. `$CNTIM` —— GPS 绝对时间基准 ✅

来源：`modules/flag_analysis/flag_processor.py:70-71`（正则 `$CNTIM,\d+,\d+,(\d+),([0-9.]+)`）。

```
$CNTIM,<qual>,<sys>,<week>,<sow>,...
```

| # | 字段 | 类型 / 字节 | 含义 | 解析处理 |
|---|------|------------|------|---------|
| 1 | `qual` | U8 | 质量/来源指示 | — |
| 2 | `sys` | U8 | 系统指示 | — |
| 3 | `week` | U16 (GPS周) | **GPS 周**（周内秒基准） | `week = parts[3]` |
| 4 | `sow` | float (周秒, 浮点) | **周内秒**（Seconds Of Week，浮点） | `sow = parts[4]` → 绝对时间 |

> 用途：BPDEBUG 里除 GGA/RMC 外**唯一的绝对时间基准**，常给无时间的 `$CNRCV` 打戳（注意 `$CNRCV` 打印在 `$CNTIM` 之前，需缓存后打）。

---

## 6. `$CHDAI` / `$CHDAQ` —— 通道 Doppler / 质量累加器（BPDEBUG 专有）⚠️

```
$CHDAI,ch,prn,sig,<resv>,<hex>
$CHDAQ,ch,prn,sig,<resv>,<hex>
```

| # | 字段 | 类型 / 字节 | 含义 |
|---|------|------------|------|
| 1 | `ch` | U8 | 通道 |
| 2 | `prn` | U8 | 卫星 PRN |
| 3 | `sig` | U8 (频点ID) | 频点 ID |
| 4 | （保留/计数） | U8 | 当前仅计数行数，**未解析物理量** |
| 5 | `hex` | U32 (hex, 4B) | 累加器原始值（hex 编码） |

---

## 7. `$CNSCH` —— 信号调度 / 计划跟踪卫星位图 ⚠️

```
$CNSCH,<mode>,<sig>,<low>,<high>
```

| # | 字段 | 类型 / 字节 | 含义 | SDK 对应 |
|---|------|------------|------|---------|
| 1 | `mode` | U8 | 调度模式（0=常规） | `bp_config_*` |
| 2 | `sig` | U8 (频点ID) | 频点 ID | — |
| 3 | `low` | U64 (hex, 64bit 位图) | 计划跟踪卫星位图低段（hex） | `sch_low` |
| 4 | `high` | U64 (hex, 64bit 位图) | 计划跟踪卫星位图高段（hex） | `sch_high` |

> 每位对应一颗星（按 PRN 排列），置 1 = 该信号计划跟踪此星。样本：`$CNSCH,0,1,h3FFFFFC3FEF,h0`。

---

## 8. `$CNVIW` —— 可见星位图（搜星数来源）⚠️

```
$CNVIW,<sig>,<maybe_inview>,<certain_inview>
```

| # | 字段 | 类型 / 字节 | 含义 | SDK 对应 |
|---|------|------------|------|---------|
| 1 | `sig` | U8 (频点ID) | 频点 ID | — |
| 2 | `maybe_inview` | bitmap(192bit) = U32×6 (hex) | **候选可见星位图**（hex，192 bit 覆盖全系统） | `BPInfoSatVis.maybe_inview[6]` |
| 3 | `certain_inview` | bitmap(192bit) = U32×6 (hex) | **确认可见星位图**（hex） | `BPInfoSatVis.certain_inview[6]` |

> 置位 bit 数 = 该频点可见星数。**这是"各时刻搜星数"最干净的数据源之一**（自带同帧时间）。样本信号1 `maybe`=63 bit。

---

## 9. `$CNEPH` / `$CNALM` —— 星历 / 历书有效位图 ⚠️

```
$CNEPH,<sig>,<eph_valid_1>,<eph_valid_2>,...,<eph_valid_7>
$CNALM,<sig>,<alm_valid_1>,...,<alm_valid_7>
```

| # | 字段 | 类型 / 字节 | 含义 | SDK 对应 |
|---|------|------------|------|---------|
| 1 | `sig` | U8 (频点ID) | 频点 ID | — |
| 2–8 | `eph_valid[7]` / `alm_valid[7]` | bitmap = U32×7 (hex, 每系统一段) | 各系统星历/历书有效位图（7×U32，按系统位段） | `BPInfoEphAlm.eph_valid[7]` / `alm_valid[7]` |

---

## 10. `$CNRTC` / `$CNCTL` —— RTC 时间 / 跟踪控制配置 ⚠️

```
$CNRTC,<...>          # RTC 时间与 PPS 时间
$CNCTL,<...>          # 跟踪/控制配置（等同 bp_config_low/high 各位）
```

| 语句 | 类型 / 字节 | 含义 | SDK 对应 |
|------|------------|------|---------|
| `$CNRTC` | U32 (计数) / U8 (PPS状态) | RTC 计数器 / PPS（脉冲）时间同步状态 | `BPInfoRTC` |
| `$CNCTL` | U32×2 (hex, bp_config_low/high) | 跟踪环路/控制参数位配置 | `BPInfoConfig.bp_config_*` |

---

## 11. `$CNACQ` / `$CNACR` —— 捕获 / 搜索星列表（搜星数来源）⚠️

```
$CNACQ,<sys>,<sig>,<low>,<high>
$CNACR,<cnt>,<res>
```

| # | 字段 | 类型 / 字节 | 含义 | SDK 对应 |
|---|------|------------|------|---------|
| `$CNACQ`.1 | `sys` | U8 | 系统 | — |
| `$CNACQ`.2 | `sig` | U8 (频点ID) | 频点 ID | — |
| `$CNACQ`.3 | `low` | U64 (hex, 64bit 位图) | 正在搜索的卫星位图低段（hex，拼接 64 bit） | `BPInfoAcq.acq_sat_list_low` |
| `$CNACQ`.4 | `high` | U64 (hex, 64bit 位图) | 正在搜索的卫星位图高段（hex） | `BPInfoAcq.acq_sat_list_high` |
| `$CNACR`.1 | `cnt` | U32 | 累计捕获次数 | `acq_scheduler_cnt` |
| `$CNACR`.2 | `res` | U32 | 保留/结果 | — |

> 置位 bit 数 = 该信号**正在搜索（捕获）的星数**。重捕/复位时密集出现，本身就是复位/重捕标志。样本信号1 `low`=37 bit。

---

## 12. `$CNNVM` / `$CNWHL` / `$CNMON` / `$CNCPU` / `$CNRPS` / `$CNRPF` ⚠️

| 语句 | 类型 / 字节 | 含义 | SDK 对应 |
|------|------------|------|---------|
| `$CNNVM` | U32 (状态字) | NVM / Flash 状态（擦除时间、写入信息） | `BPInfoFlash` |
| `$CNWHL` | U16 / U32 | 轮速 / 里程（辅助定位） | — |
| `$CNMON` | U32 (计数) | 测量计数监视（各通道观测数统计） | — |
| `$CNCPU` | U32 (时序) | 线程运行时间 / CPU 占用时序 | `BPInfoCPU`（`cpu_info`/`mon_info`） |
| `$CNRPS` | U32 (状态) | RTK 状态（定位模式、卫星数、残差等） | — |
| `$CNRPF` | U32 (结果) | RTK 结果（固定/浮点、基线、精度） | — |

> 这些语句仓库仅识别/计数，**未逐字段解析**，字段结构按 SDK 结构体反推，具体分隔顺序以实际样本为准。

---

## 附：频点 ID（`sig`）→ 信号名 / 系统（解析 `$CHOBS`/`$CNSCH`/`$CNVIW`/`$CNACQ` 必查）

来源：`apps/workbench/src/parse/bpdebug_types.h`（`BpDebugSignalId` 权威枚举）。

| sig | 信号 | 系统 | sig | 信号 | 系统 |
|-----|------|------|-----|------|------|
| 1 | B1I | BEIDOU | 16 | G1 | GLONASS |
| 2 | B1CSBAS | BEIDOU | 17 | G2 | GLONASS |
| 3 | B1C | BEIDOU | 18 | S2C | SBAS |
| 4 | B1A | BEIDOU | 19 | S2A | SBAS |
| 5 | B2a | BEIDOU | 20 | S1 | SBAS |
| 6 | B2b | BEIDOU | 21 | B2I | BEIDOU |
| 7 | B3I | BEIDOU | 22 | QZL1 | QZSS |
| 8 | B3Q | BEIDOU | 23 | QZL2 | QZSS |
| 9 | B3A | BEIDOU | 24 | QZL5 | QZSS |
| 10 | B3AE | BEIDOU | 25 | E5b | GALILEO |
| 11 | L1 | GPS | 26 | E6 | GALILEO |
| 12 | L2 | GPS | 27 | LBAND | BEIDOU |
| 13 | L5 | GPS | 28 | L6 | BEIDOU |
| 14 | E1 | GALILEO | 29 | SBASL1 | SBAS |
| 15 | E5a | GALILEO | 30 | SBASB1A | SBAS |
| | | | 31 | IRNSSL5 | IRNSS |

> ⚠️ 注意：Python 温循解析器的 `FREQUENCY_MAP` 把 `sig=28` 标成 `QZL6`，但 C++ 权威定义是 `L6`(LBAND)，跨工具比对前请对齐。
