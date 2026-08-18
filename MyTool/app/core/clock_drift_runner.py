# -*- coding: utf-8 -*-
"""钟漂分析 Runner：工具箱 UI 的工作线程入口。

职责：
1. 解析多个 BPDEBUG 文件（clock_drift_parser.parse_bpdebug_file）
2. 可选解析温度 CSV（clock_drift_parser.parse_temp_file）
3. 生成每个文件的数据 js（data_<key>.js，全量 tooltip + 降采样绘图）
4. 可选生成温度 js（data_temp.js）
5. 生成 HTML 报告（clock_drift_report.render_report），支持自定义标题
6. 拷贝 echarts.min.js 到输出目录

输出目录解析逻辑与 TTFF 一致：
- 用户指定 output_dir 则用之；否则写入 output/clock_drift/
"""
import json
import shutil
from pathlib import Path

from app.output_dirs import get_feature_output_dir, make_run_dir
from app.paths import get_project_root

from clock_drift.clock_drift_parser import parse_bpdebug_file, parse_temp_file
from clock_drift.clock_drift_data import build_series_data, write_data_js, write_temp_js
from clock_drift.clock_drift_report import render_report

FEATURE_KEY = "clock_drift"
ECHARTS_SRC = get_project_root() / "ttff_tool" / "echarts.min.js"

# 文件颜色（多文件时按顺序分配）
FILE_COLORS = ["#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#d62728", "#17becf", "#8c564b", "#e377c2"]

DEFAULT_TITLE = "BPDEBUG 接收机钟漂与温度联合分析"


def _default_output_dir(output_dir: str) -> Path:
    """未指定输出目录时，回退到 output/clock_drift/<clock_drift>_report_<时间>/。"""
    if output_dir and str(output_dir).strip():
        p = Path(output_dir)
        p.mkdir(parents=True, exist_ok=True)
        return p
    return make_run_dir(FEATURE_KEY)


def run_clock_drift(files, settings, output_dir="", log_callback=None):
    """执行钟漂分析，返回报告 HTML 路径。

    files: list[dict]，每项 {file: 路径, name: 显示名, note: 备注}
    settings: dict，含 title / temp_csv / max_points 等
    """
    log = log_callback or (lambda msg: None)

    out_dir = _default_output_dir(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    title = settings.get("title") or DEFAULT_TITLE
    temp_csv = settings.get("temp_csv") or ""
    use_epoch_axis = bool(settings.get("use_epoch_axis"))

    # ---- 1. 解析各 BPDEBUG 文件 ----
    payloads = []
    active = []
    for i, f in enumerate(files):
        fpath = f.get("file", "")
        if not fpath or not Path(fpath).exists():
            log(f"[跳过] 文件不存在: {fpath}")
            continue
        name = (f.get("name") or Path(fpath).stem).strip()
        note = (f.get("note") or "").strip()
        color = FILE_COLORS[i % len(FILE_COLORS)]
        key = f"f{i + 1}"

        log(f"[解析] {Path(fpath).name} -> {name} ...")
        rows, stats = parse_bpdebug_file(fpath, log_callback=log)
        if not rows:
            log(f"[错误] {Path(fpath).name} 未解析到有效数据")
            continue
        log(f"       历元 {stats['total_epochs']}, 输出 {stats['output_points']}, "
            f"不定位 {stats['nofix_epochs']}, 时间 {stats['time_start']} -> {stats['time_end']}")

        series, all_ts = build_series_data(rows, use_epoch_axis=use_epoch_axis)
        # 防御：只取有效时间戳计算范围（build_series_data 已剔除无时间历元）
        valid_ts = [t for t in all_ts if t is not None]
        payload = {
            "name": name,
            "note": note,
            "axis": "epoch" if use_epoch_axis else "time",
            "range": [int(valid_ts[0]), int(valid_ts[-1])] if valid_ts else [0, 0],
            "all_ts": all_ts,
            "flash": series["flashclkdrifft"],
            "cur": series["curclkdrifft"],
            "recv": series["recvclkdrifft"],
        }
        js_name = f"data_{key}.js"
        write_data_js(out_dir / js_name, payload)
        log(f"       已生成 {js_name} ({len(all_ts)} 全量点)")

        payloads.append(payload)
        active.append({"key": key, "label": name, "note": note, "color": color})

    if not payloads:
        raise RuntimeError("没有成功解析任何 BPDEBUG 文件，无法生成报告")

    # ---- 2. 可选温度 ----
    temp_available = False
    temp_load_js = ""
    temp_legend = ""
    temp_desc = ""
    if temp_csv and Path(temp_csv).exists():
        log(f"[温度] 解析 {Path(temp_csv).name} ...")
        temp = parse_temp_file(temp_csv, log_callback=log)
        if temp and temp["ts"]:
            write_temp_js(out_dir / "data_temp.js", temp)
            temp_available = True
            temp_load_js = (
                "(function(){ var s = document.createElement('script'); s.src = 'data_temp.js';\n"
                "  s.onload = function(){ tempData = window.TEMP_DATA; };\n"
                "  document.body.appendChild(s); })();"
            )
            temp_legend = '⚫ 温度 T1 (顶部, 黑色曲线) —— 测试温度变化曲线 (UTC 时间)<br>'
            temp_desc = '温度曲线(顶部) + '
            log(f"       温度点 {len(temp['ts'])}, 已生成 data_temp.js")
        else:
            log("[警告] 温度文件解析失败或为空，将按恒温处理（不绘制温度曲线）")

    if not temp_available:
        temp_legend = ''
        temp_desc = '未提供温度曲线（按恒温处理）｜ '

    # ---- 3. 时间范围 ----
    t0 = min(p["range"][0] for p in payloads)
    t1 = max(p["range"][1] for p in payloads)
    if use_epoch_axis:
        time_range = f"历元序号 {t0} ~ {t1}"
    else:
        time_range = _fmt_range(t0, t1)

    # ---- 4. 生成 HTML ----
    chart_h = 950 if temp_available else 820
    html = render_report(
        title=title,
        time_range=time_range,
        active=active,
        temp_available=temp_available,
        temp_legend=temp_legend,
        temp_desc=temp_desc,
        temp_load_js=temp_load_js,
        chart_h=chart_h,
        axis_mode="epoch" if use_epoch_axis else "time",
    )
    html_name = settings.get("output_html") or "钟漂变化分析报告.html"
    if not html_name.lower().endswith(".html"):
        html_name += ".html"
    out_html = out_dir / html_name
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)

    # ---- 5. 拷贝 echarts ----
    if ECHARTS_SRC.exists():
        shutil.copy2(ECHARTS_SRC, out_dir / "echarts.min.js")
    else:
        log("[警告] 未找到 echarts.min.js，报告图表将无法渲染")

    log(f"完成 -> HTML: {out_html}")
    return str(out_html)


def _fmt_range(t0: int, t1: int) -> str:
    import datetime

    def _f(ms):
        dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
        return dt.strftime("%m-%d %H:%M:%S")

    return f"{_f(t0)} ~ {_f(t1)} UTC"
