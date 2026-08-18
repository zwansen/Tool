"""封装 Calculate_DOP/DOP.py：从 NMEA GSV 语句解析卫星并解算 DOP。"""

import sys
from pathlib import Path
from typing import Callable, Optional

from app.paths import get_project_root

ROOT = get_project_root()
sys.path.insert(0, str(ROOT / "Calculate_DOP"))

import DOP  # noqa: E402


_GSV_MARK = "GSV"


def _read_text(path: Path) -> str:
    """按常见编码尝试读取文本，避免中文/BOM 日志读失败。"""
    for enc in ("utf-8-sig", "utf-8", "gbk", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def _extract_gsv(text: str) -> str:
    """从整段日志里挑出 GSV 语句，允许输入是原始接收机日志。"""
    return "\n".join(
        line.strip() for line in text.splitlines() if _GSV_MARK in line and line.lstrip().startswith("$")
    )


def run(
    input_path: str = "",
    gsv_text: str = "",
    output_path: str = "",
    output_format: str = "csv",
    log_callback: Callable[[str], None] = print,
) -> Optional[str]:
    """解析 GSV 并计算 DOP。

    input_path 与 gsv_text 二选一：给了文件就读文件，否则用粘贴的文本。
    output_format 可选 csv / json / python，留空 output_path 则只在日志里展示。
    """
    if input_path:
        p = Path(input_path)
        if not p.exists():
            raise FileNotFoundError(f"输入文件不存在: {input_path}")
        log_callback(f"读取文件: {p}")
        raw = _read_text(p)
    else:
        raw = gsv_text or ""
        log_callback("使用手动粘贴的 GSV 文本")

    gsv = _extract_gsv(raw)
    if not gsv.strip():
        raise ValueError("未在输入中找到任何 GSV 语句（形如 $GPGSV,... / $GBGSV,...）")

    line_count = len(gsv.splitlines())
    log_callback(f"提取到 GSV 语句 {line_count} 条")

    parsed = DOP.parse_gsv(gsv)
    if not parsed:
        raise ValueError("GSV 解析结果为空，请检查语句格式是否完整（需包含仰角与方位角）")

    log_callback("-" * 46)
    log_callback("各系统有效卫星数（含仰角+方位角）：")
    total = 0
    for sys_name, prn_dict in parsed.items():
        total += len(prn_dict)
        log_callback(f"  {sys_name:<8}: {len(prn_dict):>3} 颗")
    log_callback(f"  {'合计':<8}: {total:>3} 颗")

    log_callback("-" * 46)
    dop, err = DOP.calc_dop_from_parsed(parsed)
    if err:
        log_callback(f"[警告] {err}")
    else:
        log_callback("DOP 解算结果：")
        log_callback(f"  参与解算卫星数  count = {dop['count']}")
        log_callback(f"  位置精度因子    PDOP  = {dop['pdop']}")
        log_callback(f"  水平精度因子    HDOP  = {dop['hdop']}")
        log_callback(f"  垂直精度因子    VDOP  = {dop['vdop']}")

    fmt = (output_format or "csv").lower()
    body = DOP.format_for_dop(parsed, output_format=fmt)

    if not output_path:
        log_callback("-" * 46)
        log_callback(f"未指定输出文件，以下为 {fmt} 格式结果：")
        log_callback(body)
        return None

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    header = ""
    if err is None and fmt == "csv":
        header = (
            f"# count={dop['count']},pdop={dop['pdop']},hdop={dop['hdop']},vdop={dop['vdop']}\n"
        )
    out.write_text(header + body, encoding="utf-8")
    log_callback(f"已写出: {out}")
    return str(out)
