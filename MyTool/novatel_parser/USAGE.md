# 通用二进制消息解析工具使用说明

## 1. 工具文件

| 文件 | 作用 |
|------|------|
| `generic_novatel_parser.py` | 解析脚本（通常不需要修改） |
| `message_definitions.json` | 字段定义配置文件（日常只改这里） |
| `msg_{id}_{name}.csv` | 解析输出结果 |

---

## 2. 运行命令

```powershell
python generic_novatel_parser.py <二进制日志文件> -c message_definitions.json -o <输出目录>
```

常用参数：

| 参数 | 含义 |
|------|------|
| `-c <json>` | 指定字段定义文件，默认 `message_definitions.json` |
| `-o <目录>` | 指定输出目录，默认和输入文件同目录 |
| `--raw-only` | 只输出原始数值，不输出 `*_name` / `*_desc` 枚举解释列 |

示例：

```powershell
python "C:\Users\yaoyu\Documents\Codex\2026-07-20\yo\outputs\generic_novatel_parser.py" `
  "C:\Users\yaoyu\Desktop\psrdel.log" `
  -c "C:\Users\yaoyu\Documents\Codex\2026-07-20\yo\outputs\message_definitions.json" `
  -o "C:\Users\yaoyu\Documents\Codex\2026-07-20\yo\outputs"
```

---

## 3. JSON 配置结构

```json
{
  "sync": "AA4412",
  "header": [ ... ],
  "enums": { ... },
  "messages": { ... }
}
```

- `sync`：消息同步头，十六进制字符串，通常固定为 `AA4412`。
- `header`：通用消息头字段定义，所有消息类型共用。
- `enums`：枚举映射表，可被字段引用。
- `messages`：各种消息体定义，按 `messageId` 区分。

---

## 4. 支持的字段类型

| 类型 | 长度 | 说明 |
|------|------|------|
| `UChar` | 1 | 无符号字节 |
| `Char` | 1 | 有符号字节 |
| `UShort` | 2 | 无符号短整型 |
| `Short` | 2 | 有符号短整型 |
| `ULong` | 4 | 无符号长整型 |
| `Long` | 4 | 有符号长整型 |
| `Float` | 4 | 单精度浮点 |
| `Double` | 8 | 双精度浮点 |
| `Enum` | 需指定 `"length": 1/2/4` | 枚举 |
| `bytes` | 需指定 `"length": N` | 原始字节，输出 hex |

所有多字节类型都按 **小端（little-endian）** 解析。

---

## 5. 如何新增一种消息类型

假设你拿到一条新语句，消息 ID 是 `47`，名称为 `PSRPOS`，结构如下：

> 消息 ID 是该语句在协议中固定的值，等于 header 中 `messageId` 字段的取值。可以从协议手册查到，或者先用本工具解析出一条该语句的 header，读出 `messageId`。

| 字段 | 类型 | 长度 |
|------|------|------|
| solStatus | Enum | 4 |
| posType | Enum | 4 |
| lat | Double | 8 |
| lon | Double | 8 |
| height | Double | 8 |
| RSV | Float | 4 |

只需要在 `message_definitions.json` 的 `messages` 段里添加：

```json
"47": {
  "name": "PSRPOS",
  "fields": [
    {"name": "solStatus", "type": "Enum", "length": 4, "enum": "solStatus"},
    {"name": "posType", "type": "Enum", "length": 4, "enum": "velType"},
    {"name": "lat", "type": "Double"},
    {"name": "lon", "type": "Double"},
    {"name": "height", "type": "Double"},
    {"name": "RSV", "type": "Float"}
  ]
}
```

运行解析器后，会自动生成 `msg_47_PSRPOS.csv`。

---

## 6. 如何新增或修改枚举表

所有枚举都写在 `enums` 段里。字段通过 `"enum": "表名"` 来引用。

### 6.1 简单写法：只有 ASCII 名

```json
"port": {
  "32": "COM1",
  "64": "COM2",
  "96": "COM3",
  "160": "COM4"
}
```

这种写法 `_name` 和 `_desc` 列会显示相同内容。

### 6.2 完整写法：ASCII 名 + 中文描述

```json
"solStatus": {
  "0": {"name": "SOL_COMPUTED", "desc": "已解出"},
  "1": {"name": "INSUFFICIENT_OBS", "desc": "观测量不足"},
  "2": {"name": "NO_CONVERGENCE", "desc": "未收敛"},
  "4": {"name": "COV_TRACE", "desc": "协方差矩阵的迹超过最大值（迹 > 1000 米）"}
}
```

解析器会自动输出 `solStatus_name`（ASCII）和 `solStatus_desc`（中文）两列。

### 6.3 范围写法：连续值都是同一个含义

```json
"velType": {
  "3-7": {"name": "RSV", "desc": "保留"},
  "8": {"name": "DOPPLER_VELOCITY", "desc": "利用实时多普勒计算速度"},
  "9-15": {"name": "RSV", "desc": "保留"}
}
```

键的格式必须是 `a-b` 的整数范围。

### 6.4 默认值：未命中任何项时显示什么

```json
"someEnum": {
  "0": "OK",
  "1": "WARN",
  "_default": {"name": "RSV", "desc": "保留"}
}
```

如果不写 `_default`，未命中会显示 `UNKNOWN(value)`。

---

## 7. 字段如何引用枚举

```json
{"name": "solStatus", "type": "Enum", "length": 4, "enum": "solStatus"}
```

- `type` 必须是 `Enum`。
- `length` 必须是 `1`、`2` 或 `4`。
- `enum` 指向 `enums` 里的键名。

---

## 8. 输出列说明

- 默认输出：原始数值 + `*_name` + `*_desc`（如果枚举表有中文描述）。
- `--raw-only`：只输出原始数值，适合严格对照二进制字段。
- 每种 `messageId` 单独生成一个 CSV，列顺序严格按照 JSON 定义。
- `CRC` 由工具自动解析，不需要在 `messages` 字段里定义。

---

## 9. 注意事项

1. 字段名不要重复，否则 CSV 列会互相覆盖。
2. `header` 里的 `messageId` 字段用于分发消息类型，必须保留。
3. 遇到未知 `messageId`，工具会输出 `payloadHex` 列保存原始 payload。
4. 所有改动只需要在 `message_definitions.json` 里完成；`generic_novatel_parser.py` 通常不用动。

---

## 10. 快速修改清单

```text
□ 新增消息类型：在 messages 里加 "messageId": {"name": "...", "fields": [...]}
□ 修改字段顺序：调整 fields 数组里的顺序
□ 新增枚举表：在 enums 里加 "表名": {"值": "含义"}
□ 修改枚举含义：直接编辑 enums 里的值
□ 增加范围映射：用 "a-b": {"name": "...", "desc": "..."}
□ 设置未命中默认值：在枚举表里加 "_default": "..."
□ 只想看原始数字：运行命令时加 --raw-only
```
