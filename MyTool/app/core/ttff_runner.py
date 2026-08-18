"""封装 ttff_tool/ttff_analyzer.py（函数式 API）。

GNSS ToolBox 的 TTFF 页面原本依赖已删除的 TTFF 模块，现改为调用用户新增的
ttff_tool 工具：

    ttff_analyzer.analyze_file / summarize / build_payload / build_report_html

新引擎按日志中的复位标志定位每次冷/热启动起点，再查找后续首个有效 $GNGGA
定位语句计算 TTFF，最终生成 HTML 统计报告 + JSON 明细。

本模块对外提供 run_config(files, settings, output_dir)，完全对接
ttff_tool/ttff_config.json 的结构：每个文件可独立配置 reset_marker / name /
note，settings 提供输出文件名与 default_date。界面层即该 config 的编辑器，
“保存配置”会把界面内容写回 JSON，“运行”则按该结构逐文件分析。
"""

import datetime
import json
import sys
from pathlib import Path
from typing import Callable

from app.paths import get_project_root

ROOT = get_project_root()
TTFF_TOOL = ROOT / "ttff_tool"
sys.path.insert(0, str(TTFF_TOOL))

from ttff_analyzer import (  # noqa: E402
    analyze_file,
    summarize,
    build_payload,
    build_report_html,
    _safe_filename,
    DEFAULT_EPOCH,
)

_ECHARTS = TTFF_TOOL / "echarts.min.js"
_ECHARTS_CDN = "https://cdn.bootcdn.net/ajax/libs/echarts/5.5.1/echarts.min.js"
_LOG_SUFFIXES = (".log", ".txt", ".nmea")

DEFAULT_OUTPUT_HTML = "TTFF统计报告.html"
DEFAULT_OUTPUT_JSON = "ttff_results.json"
DEFAULT_DATE = "040826"


def _resolve_output_dir(output_dir: str, files: list[dict]) -> Path:
    """确定报告输出目录。"""
    if output_dir:
        p = Path(output_dir)
        if p.is_dir() or not p.suffix:
            return p
        return p.parent
    # 未指定：写入本次运行专属目录 output/ttff/ttff_report_<时间>/
    from app.output_dirs import make_run_dir

    for f in files:
        fp = f.get("file")
        if fp and Path(fp).exists():
            return make_run_dir("ttff")
    return make_run_dir("ttff")


def run_config(
    files: list[dict],
    settings: dict,
    output_dir: str = "",
    log_callback: Callable[[str], None] = print,
):
    """按 config 结构计算 TTFF（首次定位时间）。

    files:     [{"file":..., "reset_marker":..., "name":..., "note":...}, ...]
    settings:  {"output_html":..., "output_json":..., "default_date":...}
    output_dir: 报告输出目录（缺省取首个文件所在目录）
    """
    if not files:
        raise ValueError("未提供任何输入文件（files 为空）")

    out_dir = _resolve_output_dir(output_dir, files)
    out_dir.mkdir(parents=True, exist_ok=True)

    output_html_name = settings.get("output_html") or DEFAULT_OUTPUT_HTML
    output_json_name = settings.get("output_json") or DEFAULT_OUTPUT_JSON
    default_date = str(settings.get("default_date") or DEFAULT_DATE)

    out_html = out_dir / output_html_name
    out_json = out_dir / output_json_name

    log_callback(f"输出目录: {out_dir}")
    log_callback(f"默认日期: {default_date}")

    analyzed = []
    for spec in files:
        file_path = spec.get("file", "")
        marker = spec.get("reset_marker") or "$RESET"
        name = spec.get("name") or Path(file_path).stem
        note = spec.get("note", "")
        if not file_path or not Path(file_path).exists():
            log_callback(f"[跳过] 文件不存在: {file_path}")
            continue
        log_callback(f"分析: {name}  ({Path(file_path).name})")
        cycles, meta = analyze_file(file_path, marker, default_date)
        summary = summarize(cycles)
        analyzed.append({
            "file": file_path,
            "name": name,
            "marker": marker,
            "note": note,
            "cycles": cycles,
            "meta": meta,
            "summary": summary,
        })
        log_callback(
            f"  -> 复位 {summary['n_resets']} 次, "
            f"恢复 {summary['n_ok']} 次 ({summary['recovery_rate']}%)"
        )

    if not analyzed:
        raise FileNotFoundError("没有任何可分析的文件（请检查 files 配置与路径）")

    # 明细 JSON（合并 + 逐文件）
    detail = {}
    for fd in analyzed:
        cycles_save = [{k: v for k, v in c.items()} for c in fd["cycles"]]
        detail[fd["file"]] = {"summary": fd["summary"], "cycles": cycles_save}
    with open(out_json, "w", encoding="utf-8") as fh:
        json.dump(detail, fh, ensure_ascii=False, indent=1)
    log_callback(f"完成 -> JSON(合并): {out_json}")

    # 逐文件明细 JSON：每个被分析的文件单独输出一个，便于核对单个日志
    used = set()
    for idx, fd in enumerate(analyzed, 1):
        base = _safe_filename(fd["name"] or Path(fd["file"]).stem)
        per_name = f"ttff_{idx:02d}_{base}.json"
        while per_name in used or (out_dir / per_name).exists():
            per_name = f"ttff_{idx:02d}_{base}_{len(used)}.json"
        used.add(per_name)
        per_path = out_dir / per_name
        with open(per_path, "w", encoding="utf-8") as fh:
            json.dump({
                "file": fd["file"], "name": fd["name"], "marker": fd["marker"],
                "summary": fd["summary"],
                "cycles": detail[fd["file"]]["cycles"],
            }, fh, ensure_ascii=False, indent=1)
        log_callback(f"完成 -> JSON(单文件): {per_path}")

    # HTML 报告（内嵌 echarts；缺失时回退 CDN 外链）
    if _ECHARTS.exists():
        with open(_ECHARTS, "r", encoding="utf-8") as fh:
            echarts_js = fh.read()
        echarts_cdn = None
    else:
        log_callback("警告: 未找到 echarts.min.js，报告图表将使用 CDN 外链（需联网）")
        echarts_js = ""
        echarts_cdn = _ECHARTS_CDN

    payload = build_payload(analyzed, DEFAULT_EPOCH)
    gen_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = build_report_html(
        payload, out_json.name, echarts_js, gen_time, echarts_cdn_url=echarts_cdn
    )
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(html)

    log_callback(f"完成 -> HTML: {out_html}")
    log_callback(f"完成 -> JSON: {out_json}")
    return str(out_html)
