"""封装 ublox/ubx_parser.py"""

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from app.paths import get_project_root

ROOT = get_project_root()
sys.path.insert(0, str(ROOT / "ublox"))

import ubx_parser


def run(
    input_path: str,
    output_path: str,
    verbose: bool = False,
    log_callback: Callable[[str], None] = print,
):
    """解析 u-blox UBX 二进制文件为 ASCII。"""
    log_callback(f"输入: {input_path}")
    log_callback(f"输出: {output_path}")

    if not Path(input_path).exists():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    results = ubx_parser.process_ubx_file(input_path)
    if not results:
        raise RuntimeError("未解析到有效消息")

    ubx_parser.save_results_to_asc(results, input_path)

    # save_results_to_asc 默认写入输入文件同目录的 .asc，若用户指定了不同路径则移动
    input_dir = Path(input_path).resolve().parent
    input_name = Path(input_path).stem
    default_output = input_dir / f"{input_name}.asc"

    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if default_output.resolve() != target.resolve():
        shutil.move(str(default_output), str(target))
        log_callback(f"已移动到: {target}")
    else:
        target = default_output

    log_callback(f"完成: {target}")
    return str(target)
