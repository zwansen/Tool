"""封装 Same_detect/same_detect.py 与 same_detect_90.py"""

import io
import sys
from pathlib import Path
from typing import Callable

from app.paths import get_project_root
from app.core import report_builder

ROOT = get_project_root()
sys.path.insert(0, str(ROOT / "Same_detect"))

import same_detect
import same_detect_90


def run(
    input_path: str,
    output_path: str,
    similarity: float | None = None,
    verbose: bool = False,
    log_callback: Callable[[str], None] = print,
):
    """检测文本文件重复行。similarity 为 None 时仅检测完全重复。

    返回 dict：
      - result_path: 文本报告落盘路径（供“打开输出目录”）
      - report_text: 文本报告正文（引擎打印内容，已与运行日志隔离）
      - html_path:   可视化 HTML 报告路径（供结果预览渲染）
    引擎会把报告直接 print 到 stdout，这里临时重定向 stdout 捕获报告，
    使其不污染“运行日志”（运行日志只保留 log_callback 发的进度信息）。
    """
    log_callback(f"输入: {input_path}")
    log_callback(f"输出: {output_path}")
    if similarity is not None:
        log_callback(f"相似度阈值: {similarity}")

    if not Path(input_path).exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    old_argv = sys.argv
    old_stdout = sys.stdout
    captured = io.StringIO()
    struct = None
    try:
        sys.argv = [
            "same_detect.py",
            input_path,
            "-o",
            output_path,
        ]
        if verbose:
            sys.argv.append("-v")

        # 重定向引擎打印的检测报告，避免其进入运行日志
        sys.stdout = captured
        if similarity is not None:
            sys.argv.extend(["-s", str(similarity)])
            struct = same_detect_90.check_duplicates()
        else:
            struct = same_detect.check_duplicates()
    finally:
        sys.stdout = old_stdout
        sys.argv = old_argv

    report_text = captured.getvalue()
    log_callback(f"完成: {output_path}")

    # 可视化 HTML 报告
    html_path = str(Path(output_path).with_suffix(".html"))
    meta = {
        "input_name": Path(input_path).name,
        "output_name": Path(output_path).name,
    }
    if isinstance(struct, dict):
        html_text = report_builder.build_duplicate_html(meta, struct)
        try:
            Path(html_path).write_text(html_text, encoding="utf-8")
        except Exception:
            html_path = ""
    else:
        html_path = ""

    return {"result_path": output_path, "report_text": report_text, "html_path": html_path}
