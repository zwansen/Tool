"""集中管理各功能的结果输出目录。

所有功能的结果文件统一落在项目根 <root>/output/<feature>/ 下，
避免各种结果文件（报告、CSV、解析产物等）散落到项目根目录造成污染。

页面在 __init__ 里设置 self._output_feature_key（见 FEATURE_DIRS 的键），
未显式配置输出时，get_effective_output_dir / default_output_path 会自动回退到这里。
"""

from datetime import datetime
from pathlib import Path

from app.paths import get_project_root

# 顶层输出根目录：<项目根>/output
OUTPUT_ROOT = get_project_root() / "output"

# 功能键 -> 子目录名（同时用于“打开输出目录”的落点）
FEATURE_DIRS = {
    "ttff": "ttff",
    "duplicate_detect": "duplicate_detect",
    "time_continuity": "time_continuity",
    "ublox": "ublox",
    "true_coord": "true_coord",
    "novatel": "novatel",
    "dop": "dop",
    "ksconverter": "ksconverter",
    "ttff_merged": "ttff_merged",
    "rtk_viewer": "rtk_viewer",
    "clock_drift": "clock_drift",
}


def get_output_root(create: bool = True) -> Path:
    """返回顶层输出目录；create=True 时确保存在。"""
    if create:
        OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    return OUTPUT_ROOT


def get_feature_output_dir(feature_key: str, create: bool = True) -> Path:
    """返回某功能专属的输出目录（<root>/output/<subdir>）。

    feature_key 未知时回退为以 key 本身作子目录名，避免崩溃。
    create=True 时确保目录存在。
    """
    sub = FEATURE_DIRS.get(feature_key, feature_key)
    d = OUTPUT_ROOT / sub
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def ensure_output_root(create: bool = True) -> Path:
    """确保（仅）顶层 output/ 目录存在，返回该路径。"""
    return get_output_root(create=create)


def run_timestamp() -> str:
    """返回用于运行目录名的紧凑时间戳：YYYYMMDD_HHMMSS。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def make_run_dir(feature_key: str) -> Path:
    """为“一次运行”创建专属结果目录，并返回该路径（已确保存在）。

    目录结构：<root>/output/<feature>/<feature>_report_<YYYYMMDD_HHMMSS>[/_N]
    即每次运行都新建一个带生成时间的子文件夹，避免多次运行结果互相覆盖或混杂。
    同一秒内再次运行会在时间后缀后追加 _2、_3… 以区分，绝不覆盖已有结果。

    feature_key 未知时回退为以 key 本身作功能名，避免崩溃。
    """
    sub = FEATURE_DIRS.get(feature_key, feature_key)
    base = OUTPUT_ROOT / sub / f"{sub}_report_{run_timestamp()}"
    d = base
    n = 2
    while d.exists():
        d = OUTPUT_ROOT / sub / f"{base.name}_{n}"
        n += 1
    d.mkdir(parents=True, exist_ok=True)
    return d
