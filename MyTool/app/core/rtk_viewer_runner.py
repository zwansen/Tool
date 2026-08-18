"""RTK 3D 查看器 runner。

把 rtk_3d_viewer/rtk_3d_viewer.py（独立脚本）的能力接入工具箱：
- 多文件输入（NMEA 日志 / bag_*.txt / rosbag2 目录，自动识别）
- 可选真值文件（--truth 等价）：同图对比 + 水平/高程/速度误差计算
- 生成自包含离线 3D HTML（交互：旋转/平移/光标缩放/双击放大/超差着色/误差三轨图等）

用法由页面层（page_rtk_viewer.py）调用。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_RTV_DIR = ROOT / "rtk_3d_viewer"
if str(_RTV_DIR) not in sys.path:
    sys.path.insert(0, str(_RTV_DIR))

import rtk_3d_viewer as rtv  # noqa: E402


def _load_one(fp: str, name: str, ptype: str):
    """加载单个输入，返回 (points, 加载方式说明)；文件缺失返回 ([], "missing")。"""
    p = Path(fp)
    if rtv.is_bag_dir(p):
        pts_ = rtv.load_bag_pure(p, name)
        for pt in pts_:
            pt.setdefault("type", ptype)
        return pts_, "bag"
    if not p.is_file():
        return [], "missing"
    if rtv._looks_like_nmea_file(p):
        return rtv.load_nmea(p, name, ptype=ptype), "NMEA"
    return rtv.load_txt(p, name), "txt"


def run_rtk_viewer(
    input_paths: list[str],
    output_path: str,
    truth_path: str | None = None,
    log_callback=None,
) -> dict:
    """加载多文件（可含真值）并生成自包含 3D HTML。

    返回 {"result_path": str, "summary": str}；无有效数据时抛 ValueError。
    """
    _log = log_callback or (lambda m: print(m, flush=True))
    all_pts: list = []
    dataset_names: list[str] = []
    flags: list[str] = []

    # 真值文件（若有）先加载，标记 type=truth
    if truth_path:
        tp = Path(truth_path)
        pts_, tag = _load_one(str(tp), tp.stem, "truth")
        if pts_:
            dataset_names.append(tp.stem + "(真值)")
            all_pts.extend(pts_)
            flags.append(f"[真值] {tp.name}: {len(pts_)} pts ({tag})")
        else:
            _log(f"[警告] 真值文件无有效数据：{truth_path}")

    for i, fp in enumerate(input_paths):
        p = Path(fp)
        name = p.name if rtv.is_bag_dir(p) else p.stem
        ptype = "test" if truth_path else "solo"
        pts_, tag = _load_one(fp, name, ptype)
        if tag == "missing":
            _log(f"[警告] 文件不存在：{fp}")
            continue
        flags.append(f"[{'测试' if ptype == 'test' else '数据'}] {name}: {len(pts_)} pts ({tag})")
        dataset_names.append(name)
        all_pts.extend(pts_)

    if not all_pts:
        raise ValueError("没有解析到任何有效定位点（请检查输入文件格式）")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rtv.build_offline_html(all_pts), encoding="utf-8")

    for f in flags:
        _log("  " + f)
    _log(f"[结果] 共 {len(all_pts)} 点 → {out}")
    return {"result_path": str(out), "summary": f"共 {len(all_pts)} 点"}
