"""封装 True/Lon_lat.py 与 True/true_process.py"""

import sys
from pathlib import Path
from typing import Callable

from app.paths import get_project_root

ROOT = get_project_root()
sys.path.insert(0, str(ROOT / "True"))

import Lon_lat
import true_process


def run(
    input_path: str,
    output_path: str,
    mode: str = "dd",
    log_callback: Callable[[str], None] = print,
):
    """
    转换 Inertial Explorer 数据。
    mode: 'dm' -> 度分格式, 'dd' -> 十进制度, 'gga' -> 生成 GGA 语句
    """
    log_callback(f"输入: {input_path}")
    log_callback(f"输出: {output_path}")
    log_callback(f"模式: {mode}")

    if not Path(input_path).exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    if mode in ("dm", "dd"):
        Lon_lat.convert_inertial_explorer_file(input_path, output_path, mode)
    elif mode == "gga":
        df = true_process.read_inertial_explorer_file(input_path)
        true_process.save_gga_results(df, output_path)
    else:
        raise ValueError(f"不支持的模式: {mode}")

    log_callback(f"完成: {output_path}")
    return output_path
