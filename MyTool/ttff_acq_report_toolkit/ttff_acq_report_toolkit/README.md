# TTFF 冷启动 BPDEBUG 分析报告工具包

给人类 / AI 用：从 BPDEBUG `.log` 目录生成 HTML 报告（RawObs 上星、星历有效、参与解算按星色带、可参与位置解）。

本包自包含；**不依赖**把流程集成进 GnssStudio（那是后续事项）。

## 目录

```
ttff_acq_report_toolkit/
  README.md                    ← 本说明
  ttff_chobs_acq_report.py     ← 主入口
  acq_report_template.html
  acq_report.js
  bin/
    bpdebug_track_dump.exe     ← 链 ProtocolDecoder.dll
    ProtocolDecoder.dll
    Qt6Core.dll
```

## AI 必须遵守

1. **TrackInfo / 星历 / 参与解算必须走 `bin/bpdebug_track_dump.exe`（DLL）**，禁止手写 TrackInfoExt 二进制解析。
2. **勿混淆语义：**
   - 星历有效 = `sat_state & 0x20000000`（bit29，卫星级）
   - 参与解算 = `sat_state & 0x08000000`（bit27，卫星级）
   - 可参与位置解 = `pvt_state & 0x80000000`（bit31，信号级）≠ 参与解算
3. **X 轴用相对 Reset 秒（10Hz）**，不用 UTC。复位：`#Receiver Reset:N,8000013F`。
4. 频点用报告里的**菜单多选绘制**，不要依赖图例开关频点。

## 用法

在任意目录执行（把路径换成实际位置）：

```bash
# 预览：每文件最多 5 次冷启动 → <log目录>/acq_report_preview/
python "<本包>/ttff_chobs_acq_report.py" --input "<含.log的目录>" --preview

# 限流
python "<本包>/ttff_chobs_acq_report.py" -i "<log目录>" --max-cycles 2 -o "<log目录>/acq_report_preview"

# 全量 → <log目录>/acq_report/
python "<本包>/ttff_chobs_acq_report.py" -i "<log目录>"

# 只改 HTML/JS 后重套模板（不重新扫大 log）
python "<本包>/ttff_chobs_acq_report.py" --render-only "<out>/report_data.full.json" -o "<out>"

# 仅 RawObs（不要星历/参与解算）
python "<本包>/ttff_chobs_acq_report.py" -i "<log目录>" --skip-track --preview
```

打开产物：`<out>/index.html`。

### 常用参数

| 参数 | 含义 |
|------|------|
| `-i` / `--input` | log 目录或单个 `.log` |
| `-o` / `--output` | 输出目录 |
| `--preview` | max-cycles=5，默认 `acq_report_preview` |
| `--max-cycles N` | 每文件最多 N 次冷启动 |
| `--skip-track` | 跳过 DLL |
| `--track-dump-exe` | 指定 dump exe（默认用本包 `bin/`） |
| `--cold-suffix` | 默认 `13F` |
| `--render-only JSON` | 用 `report_data.full.json` 重渲染 |

`bin/bpdebug_track_dump.exe` 运行时工作目录会设为 exe 所在目录，以便加载同目录 DLL。

## 产出结构

```
<out>/
  index.html
  acq_report.js
  report_data.js
  report_meta.json
  report_data.full.json
  data/<文件名>/r<Reset号>.js
```

Track 缓存（可选复用）：`<log目录>/_track_dump_cache/*.v2.track.json`。

## 报告里有什么

1. RawObs 各频点在视星数（频点菜单）
2. 星历有效 / 参与解算颗数曲线（多文件叠加）
3. 按星色带：灰=跟踪，黄=星历，绿=参与解算（单文件下钻，默认滤 B2b）
4. 可参与位置解（信号 bit31，频点菜单）
5. 历元卫星列表（默认可切「参与解算」）

## 故障排查

| 现象 | 处理 |
|------|------|
| 无星历/参与解算图 | 检查 `bin/` 三件套是否齐全；勿加 `--skip-track` |
| dump 启动失败 | 同目录需 `ProtocolDecoder.dll` + `Qt6Core.dll` |
| 体积太大/太慢 | 先 `--max-cycles 2` 或 `--preview` |
| 只改了界面 | `--render-only .../report_data.full.json` |
