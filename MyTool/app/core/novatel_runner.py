"""封装 novatel_parser/generic_novatel_parser.py：JSON 配置驱动的通用二进制解析。"""

import csv
import json
import sys
from pathlib import Path
from typing import Callable, Optional

from app.paths import get_project_root

ROOT = get_project_root()
PARSER_DIR = ROOT / "novatel_parser"
sys.path.insert(0, str(PARSER_DIR))

import generic_novatel_parser as gnp  # noqa: E402


DEFAULT_CONFIG = PARSER_DIR / "message_definitions.json"


def default_config_path() -> str:
    """返回内置字段定义文件路径（供 UI 作为默认值展示）。"""
    return str(DEFAULT_CONFIG)


def run(
    input_path: str,
    output_dir: str = "",
    config_path: str = "",
    raw_only: bool = False,
    log_callback: Callable[[str], None] = print,
    progress_callback: Optional[Callable[[int], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> str:
    """解析 NovAtel 风格二进制日志，按消息类型分别导出 CSV。"""
    src = Path(input_path)
    if not src.is_file():
        raise FileNotFoundError(f"输入文件不存在: {input_path}")

    cfg_file = Path(config_path) if config_path else DEFAULT_CONFIG
    if not cfg_file.is_file():
        raise FileNotFoundError(f"字段定义文件不存在: {cfg_file}")

    log_callback(f"输入文件: {src}")
    log_callback(f"字段定义: {cfg_file}")
    log_callback(f"枚举解释: {'关闭（仅原始值）' if raw_only else '开启'}")

    with open(cfg_file, "r", encoding="utf-8") as f:
        config = json.load(f)

    data = src.read_bytes()
    log_callback(f"文件大小: {len(data) / 1024:.1f} KB，开始扫描同步头…")

    records = gnp.parse_all(data, config)
    if not records:
        raise RuntimeError(
            "未解析到任何消息。请确认输入是对应协议的二进制日志，且字段定义中的 sync 同步头正确。"
        )
    log_callback(f"共解析到 {len(records)} 条消息")

    if output_dir:
        out_dir = Path(output_dir)
    else:
        from app.output_dirs import make_run_dir

        out_dir = make_run_dir("novatel")
    out_dir.mkdir(parents=True, exist_ok=True)

    # 按 messageId 分组，每种消息类型单独一个 CSV
    groups: dict = {}
    for rec in records:
        groups.setdefault((rec["messageId"], rec["messageName"]), []).append(rec)

    log_callback("-" * 46)
    total_groups = len(groups)
    for i, ((msg_id, msg_name), recs) in enumerate(sorted(groups.items()), 1):
        if should_stop and should_stop():
            log_callback("[信息] 收到停止请求，提前结束")
            break

        rows = []
        for idx, rec in enumerate(recs, 1):
            row = gnp.build_row(rec, config, raw_only)
            row["msg_index"] = idx
            rows.append(row)

        safe_name = str(msg_name).replace(" ", "_").replace("/", "_")
        csv_path = out_dir / f"msg_{msg_id}_{safe_name}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        log_callback(f"  [{i}/{total_groups}] {csv_path.name}  ({len(rows)} 行)")
        if progress_callback:
            progress_callback(int(i / total_groups * 100))

    log_callback("-" * 46)
    log_callback(f"共 {len(records)} 条消息 / {total_groups} 种类型")
    log_callback(f"输出目录: {out_dir}")
    return str(out_dir)
