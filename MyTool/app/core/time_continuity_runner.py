"""封装 Time_Continuity/time_continuity.py"""

import re
import sys
from pathlib import Path
from typing import Callable

from app.paths import get_project_root
from app.core import report_builder

# 将模块所在目录加入路径
sys.path.insert(0, str(get_project_root() / "Time_Continuity"))

import time_continuity

_LINE_RE = re.compile(r"at line (\d+)")
_DELTA_RE = re.compile(r"found ([\d.]+)s")
_TIME_RE = re.compile(r"between .+? and (.+?)(?:\s*\[[^\]]*\])?$")


def _parse_anomalies(struct: dict) -> list[dict]:
    """把引擎返回的各类型 gap 字符串解析为结构化异常记录，供时间轴/表格使用。"""
    anomalies: list[dict] = []
    sections = struct.get("sections", {})
    for sec, gaps in sections.items():
        for s in gaps:
            m = _LINE_RE.search(s)
            line = int(m.group(1)) if m else None
            if "found" in s and "between" in s:
                severity = "time"
                dm = _DELTA_RE.search(s)
                tm = _TIME_RE.search(s)
                delta = f"{dm.group(1)}s" if dm else None
                time = tm.group(1).strip() if tm else None
            else:
                severity = "quality"
                delta = None
                time = None
            anomalies.append({
                "atype": sec,
                "line": line,
                "time": time,
                "delta": delta,
                "severity": severity,
                "detail": s,
            })
    return anomalies


def run(input_path: str, output_path: str, freq: int = 10, log_callback: Callable[[str], None] = print):
    """检查 GPS 日志时间连续性。

    返回 dict：
      - result_path: 文本报告落盘路径（供“打开输出目录”）
      - report_text: 文本报告正文
      - html_path:   可视化 HTML 报告路径（供结果预览渲染）
    进度（输入/输出/频率/完成）仍走 log_callback，不污染预览。
    """
    log_callback(f"输入: {input_path}")
    log_callback(f"输出: {output_path}")
    log_callback(f"频率: {freq} Hz")

    if not Path(input_path).exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    struct = time_continuity.analyze_gps_log(input_path, output_path, freq=freq)
    log_callback(f"完成: {output_path}")

    # 文本报告正文（引擎已写入 output_path）
    report_text = ""
    try:
        report_text = Path(output_path).read_text(encoding="utf-8", errors="ignore")
    except Exception:
        report_text = ""

    # 可视化 HTML 报告
    anomalies = _parse_anomalies(struct)
    summary = {k: len(v) for k, v in struct.get("sections", {}).items()}
    html_path = str(Path(output_path).with_suffix(".html"))
    meta = {
        "input_name": Path(input_path).name,
        "output_name": Path(output_path).name,
        "freq": freq,
        "time_threshold_s": struct.get("time_threshold_s"),
    }
    html_text = report_builder.build_time_continuity_html(
        meta, summary, anomalies, struct.get("total_lines", 0)
    )
    try:
        Path(html_path).write_text(html_text, encoding="utf-8")
    except Exception:
        html_path = ""

    return {"result_path": output_path, "report_text": report_text, "html_path": html_path}
