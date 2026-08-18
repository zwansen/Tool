# BPDEBUG 通用解析框架与配置驱动 HTML 报告生成 — 使用与功能说明

> 适用对象：`bpdebug_framework` 包。
> 既可作为基础解析库使用（直接拿 rows），也可作为「配置 → HTML 报告」生成器使用。

---

## 1. 框架定位

BPDEBUG 数据分析中，**按 `$CHEND` 切分历元 + 提取 UTC 时间 + 跨天检测 + 不定位历元 10Hz 打点** 是所有分析共用的基础。本框架把这部分固化为可复用层，提供两层使用方式：

| 层级 | 场景 | 接口 |
|------|------|------|
| **基础层** | 需要每历元记录做自定义分析 | `BPDebugFrame` + `parse_bpdebug()` |
| **报告层** | 配置驱动生成 HTML 报告 | `generate_report(config)` |

两层都基于统一的 `FieldSpec` 声明式字段提取——用户只需描述「分析哪条语句的哪个字段」，历元逻辑全部由框架自动处理。

---

## 2. 历元解析基础（框架自动处理）

1. **历元切分**：以 `$CHEND` 语句为每个历元的结束标志。
2. **历元时间**：优先取历元内 `$GNRMC` / `$GNGGA` 的时间字段（UTC `HHMMSS.mmm`）。
3. **日期维护（跨天）**：`$GNRMC` 第 9 字段（`DDMMYY`）维护当前 UTC 日期；若时间回绕超过 12 小时视为跨天（+1 天）。
4. **不定位历元**：历元内无有效 GGA/RMC 时间时标记为 `is_nofix=1`，时间从最近有效历元按 **10Hz（0.1 秒/历元）** 打点补充；无任何参考时间的历元 `utc_time=None`。
5. **损坏语句防护**：时间字段严格校验 `^\d{6}(\.\d+)?$` 且 `h<24, m<60, s<60`，乱码时间（如 `210333939202`）不会造成日期跳变。

---

## 3. 基础层：直接解析拿数据

### 3.1 快速上手

```python
from bpdebug_framework import BPDebugFrame, FieldSpec

# 1. 声明要提取的字段（语句前缀 + 字段索引 + 解析方式）
fields = [
    FieldSpec('$CNRCV', 10, 'flashclkdrifft', 'bare'),   # CNRCV 第11字段
    FieldSpec('$CNRCV', 11, 'curclkdrifft', 'bare'),
    FieldSpec('$CNRCV', 12, 'recvclkdrifft', 'bare'),
]

# 2. 解析
frame = BPDebugFrame(fields)
rows = frame.parse_file('log.txt')

# 3. rows: 每历元一条记录
for r in rows[:3]:
    print(r['utc_time'].isoformat(), r['is_nofix'],
          r['flashclkdrifft'], r['curclkdrifft'], r['recvclkdrifft'])
```

### 3.2 一行式入口

```python
from bpdebug_framework import parse_bpdebug

rows, stats = parse_bpdebug('log.txt', [
    FieldSpec('$GNGGA', 7, 'sv_in_use', 'int'),
    FieldSpec('$GNGGA', 6, 'fix_quality', 'int'),
])
# stats: {'total_epochs': n, 'output_rows': n, 'nofix_epochs': n, 'time_start': '...', 'time_end': '...'}
```

### 3.3 FieldSpec 参数

| 参数 | 说明 |
|------|------|
| `stmt_prefix` | 语句前缀，如 `'$CNRCV'`、`'$GNGGA'`、`'$GPGSV'`（不区分大小写，前缀匹配） |
| `field_index` | 字段索引（0-based，`split(',')` 后；0 是语句名本身） |
| `name` | 输出字段名（每历元 records 的 key） |
| `parser` | `'bare'`（数值(置信度)取括号前）/ `'float'` / `'int'` / `'raw'` / 自定义函数 `fn(str)->value` |
| `take` | `'first'`=取历元内第一条（默认）/ `'last'`=取最后一条 / `'count'`=计数 / `'all'`=全部（返回列表） |
| `min_len` | 该语句最少字段数（防御字段缺失，默认 `field_index+1`） |

### 3.4 便捷方法

```python
frame.to_csv(rows, 'out.csv')   # CSV 输出 (utc_time, is_nofix, epoch_index, <各字段>)
frame.stats                    # 解析统计信息 dict
```

### 3.5 温度解析（可选）

```python
from bpdebug_framework import parse_temp_file
temp = parse_temp_file('温度.csv')
# temp = {'ts': [ms], 'val': [T1]} 或 None
# CSV 格式: Index, Time(北京时间), t(seconds), T1, Tenv
```

---

## 4. 报告层：配置驱动生成 HTML

### 4.1 设计理念

写一份 **配置（dict 或 JSON）**，描述：
- 要分析哪些文件
- 要提取哪些字段
- 要生成哪些图表（line / bar / pie / scatter / table / nofix_timeline）

框架自动完成「**配置 → 解析 → 聚合 → 渲染 HTML**」全流程。

### 4.2 支持的图表类型

| type | 说明 | 必需字段 |
|------|------|----------|
| `line` | 折线图：字段随时间变化（可多文件叠加、多字段同图） | `fields`（字段名列表）, 可选 `y_name` |
| `bar` | 柱状图：字段值的分布（多文件分组） | `fields`（单个字段名） |
| `pie` | 饼图：字段值占比（如定位质量 0/1/2） | `fields`（单个字段名） |
| `scatter` | 散点图：X 字段 vs Y 字段 | `x_field`, `y_field` |
| `table` | 统计表：min/max/mean/std/中位数/计数/不定位数 | `fields`（字段名列表） |
| `nofix_timeline` | 不定位时间线：横轴时间、纵轴 0/1 | 无（自动用内置 `is_nofix` 字段） |

### 4.3 配置字段完整 schema

```json
{
  "title": "报告标题（页面 title + 顶部 h1）",
  "files": [
    {
      "path": "log1.txt",
      "name": "接收机A（默认取文件名）",
      "note": "可选，备注（图例显示）"
    },
    {
      "path": "log2.txt"
    }
  ],
  "fields": [
    {
      "stmt": "$CNRCV",
      "index": 10,
      "name": "flashclkdrifft（未填则自动生成 stmt+index 形式）",
      "parser": "bare | float | int | raw（默认 raw）",
      "take": "first | last | count | all（默认 first）"
    }
  ],
  "charts": [
    {"type": "line", "fields": ["field_a", "field_b"], "title": "标题", "y_name": "Y 轴名（可选）"},
    {"type": "bar", "fields": ["field_a"], "title": "标题"},
    {"type": "pie", "fields": ["field_a"], "title": "标题"},
    {"type": "scatter", "x_field": "x", "y_field": "y", "title": "标题"},
    {"type": "table", "fields": ["f1", "f2"], "title": "统计表标题"},
    {"type": "nofix_timeline", "title": "不定位时段"}
  ],
  "temp_csv": "温度.csv（可选；留空则无温度曲线）",
  "output_html": "报告.html（默认 BPDEBUG分析报告.html）",
  "show_nofix_default": true/false（默认 false；true 时显示不定位点一键开关）",
  "chart_height": 420（每个图 div 的高度 px，默认 420）
}
```

**内置字段**（无需声明，可直接引用）：
- `is_nofix`：不定位标记（0/1）
- `utc_time`：datetime 对象（基础层）/ epoch ms（报告层数据 js）
- `epoch_index`：0-based 历元序号

### 4.4 CLI 用法

```bash
python generate_report.py config.json [output_dir]
```

示例：`python generate_report.py examples/综合示例配置.json ./out`

### 4.5 Python API 用法

```python
from bpdebug_framework import generate_report

config = {
    "title": "卫星可见性分析",
    "files": [{"path": "log1.txt", "name": "接收机A"}],
    "fields": [
        {"stmt": "$GNGGA", "index": 7, "name": "sv_in_use", "parser": "int"},
        {"stmt": "$GNGGA", "index": 6, "name": "fix_quality", "parser": "int"}
    ],
    "charts": [
        {"type": "line", "fields": ["sv_in_use"], "title": "参与解算卫星数"},
        {"type": "pie", "fields": ["fix_quality"], "title": "定位质量占比"},
        {"type": "table", "fields": ["sv_in_use", "fix_quality"], "title": "统计汇总"}
    ]
}
html_path = generate_report(config, output_dir="./out")
```

### 4.6 输出产物

| 文件 | 说明 |
|------|------|
| `<output_html>`（如 `报告.html`） | 交互式 HTML 报告 |
| `data_f1.js` … `data_fN.js` | 各文件数据（line/scatter 图表用） |
| `data_temp.js` | 温度数据（仅填写 `temp_csv` 时生成） |
| `echarts.min.js` | ECharts 库（自动拷贝） |

---

## 5. 文件结构

```
D:\Tool\MyTool\bpdebug_framework\
├── __init__.py              # 包导出（BPDebugFrame / FieldSpec / parse_bpdebug / parse_temp_file / load_config / generate_report / ConfigError）
├── bpdebug_framework.py     # 核心框架：历元解析 + FieldSpec + parse_bpdebug + parse_temp_file
├── report_config.py         # 报告配置 schema（files/fields/charts 校验）
├── report_engine.py         # 报告生成引擎（解析 + 聚合 + HTML 模板）
├── generate_report.py       # CLI 入口（支持 JSON 配置）
├── example_fields.py        # 基础层示例（不生成报告）
├── examples\
│   └── 综合示例配置.json    # 报告层示例配置（6 种图表全部展示）
└── Framwork使用与功能说明.md # 本文档
```

---

## 6. 报告交互能力（HTML 模板内置）

| 操作 | 效果 |
|------|------|
| 滚轮 | 缩放 X 轴（时间轴） |
| Ctrl + 滚轮 | 缩放 Y 轴 |
| 鼠标左键拖动 | 平移 X 轴 |
| 底部滑块 | 缩放 / 平移 X 轴 |
| 工具栏框选放大 | 拖拽框选矩形区域放大 |
| 工具栏还原 | 恢复默认视图 |
| 悬停 | 显示该 X 坐标的所有系列值 |
| 文件按钮勾选 | 叠加 / 隐藏某个文件 |
| **显示不定位点** | 默认关闭；勾选后显示全部不定位散点（仅 line/nofix_timeline 有意义） |
| 多文件叠加 | 不同文件不同颜色（自动分配 10 种主色），图例可点击隐藏 |

---

## 7. 扩展场景示例

### 场景 1：钟漂分析（3 文件叠加 4 图 + 温度）

```json
{
  "title": "KK893 温循测试钟漂分析",
  "files": [{"path": "log1.txt", "name": "接收机A", "note": "常温"}],
  "fields": [
    {"stmt": "$CNRCV", "index": 10, "name": "flashclkdrifft", "parser": "bare"},
    {"stmt": "$CNRCV", "index": 11, "name": "curclkdrifft", "parser": "bare"},
    {"stmt": "$CNRCV", "index": 12, "name": "recvclkdrifft", "parser": "bare"}
  ],
  "charts": [
    {"type": "line", "fields": ["curclkdrifft"], "title": "当前钟漂"},
    {"type": "line", "fields": ["flashclkdrifft"], "title": "闪存钟漂"},
    {"type": "line", "fields": ["recvclkdrifft"], "title": "接收机钟漂"},
    {"type": "table", "fields": ["flashclkdrifft", "curclkdrifft", "recvclkdrifft"], "title": "统计汇总"}
  ],
  "temp_csv": "温度.csv",
  "show_nofix_default": true
}
```

### 场景 2：卫星可见性分析

```json
{
  "title": "卫星可见性分析",
  "files": [{"path": "log.txt"}],
  "fields": [
    {"stmt": "$GNGGA", "index": 7, "name": "sv_in_use", "parser": "int"},
    {"stmt": "$GPGSV", "index": 3, "name": "gsv_total", "parser": "int"},
    {"stmt": "$GNGGA", "index": 6, "name": "fix_quality", "parser": "int"}
  ],
  "charts": [
    {"type": "line", "fields": ["sv_in_use", "gsv_total"], "title": "可见卫星数"},
    {"type": "bar", "fields": ["sv_in_use"], "title": "卫星数分布"},
    {"type": "pie", "fields": ["fix_quality"], "title": "定位质量占比"},
    {"type": "scatter", "x_field": "gsv_total", "y_field": "sv_in_use", "title": "GSV vs GGA"},
    {"type": "table", "fields": ["sv_in_use", "gsv_total"], "title": "统计汇总"}
  ]
}
```

### 场景 3：每历元语句计数（统计 GGA/RMC/GSV 出现频率）

```python
from bpdebug_framework import FieldSpec, BPDebugFrame

fields = [
    FieldSpec('$GNGGA', 0, 'gga_count', 'raw', take='count'),
    FieldSpec('$GNRMC', 0, 'rmc_count', 'raw', take='count'),
    FieldSpec('$GPGSV', 0, 'gsv_count', 'raw', take='count'),
]
rows = BPDebugFrame(fields).parse_file('log.txt')
# 每条记录含 3 个字段：每个历元各类语句出现次数
```

---

## 8. 注意事项

- BPDEBUG 日志通常较大（10~14 GB），解析约需 3~4 分钟/文件，属正常现象。
- 多文件分析时，每个文件用同一份 fields 配置分别解析，结果按"颜色+名称+备注"区分。
- 温度 CSV 时间列是**北京时间**，框架自动转换为 UTC（-8 小时）。
- 图表 `fields` 引用的字段必须在 `fields` 声明中（除非是 `is_nofix` / `utc_time` / `epoch_index` 这些内置字段）。
- `parser` 在 JSON 配置中只能是内置名称（`bare`/`float`/`int`/`raw`），自定义函数仅在 Python API 中可用。
- 大量不定位散点（每文件 6~8 万）会卡顿，建议 `show_nofix_default=false`（默认）。

---

*文档生成日期：2026-08-14*