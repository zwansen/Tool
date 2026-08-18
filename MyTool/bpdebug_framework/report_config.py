# -*- coding: utf-8 -*-
"""报告配置：描述「解析哪些语句字段 + 生成哪些图表」。

核心思想：把 BPDEBUG 分析完全声明化——用户写一份配置（JSON 或 Python dict），
描述要提取的字段和要生成的图表，框架自动完成「解析 → 聚合 → 渲染 HTML 报告」。

支持两种配置来源：
  1. Python dict（推荐，可编程、可传自定义解析器）
  2. JSON 文件（简单场景；解析器只能用内置 bare/float/int/raw）

图表类型（chart["type"]）：
  line            折线图：字段随时间变化（可多文件叠加、多字段多 Y 轴）
  bar             柱状图：字段的分布/占比（如定位质量、卫星数分布）
  pie             饼图：字段值占比（如 is_nofix 定位/不定位比例）
  scatter         散点图：X 字段 vs Y 字段（如温度 vs 钟漂）
  table           统计表：各文件各字段的 min/max/mean/std/中位数
  nofix_timeline  不定位时间线：横轴时间，纵轴 0/1 标记不定位历元
"""

import json
import re
from pathlib import Path

CHART_TYPES = {"line", "bar", "pie", "scatter", "table", "nofix_timeline"}
PARSER_NAMES = {"bare", "float", "int", "raw"}


class ConfigError(Exception):
    pass


def _validate_fields(fields):
    """校验字段声明列表，返回规范化的 (name, FieldSpec 参数) 列表。"""
    out = []
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            raise ConfigError(f"fields[{i}] 必须是 dict")
        stmt = f.get("stmt") or f.get("statement")
        if not stmt:
            raise ConfigError(f"fields[{i}] 缺少 stmt（语句前缀，如 '$CNRCV'）")
        idx = f.get("index")
        if idx is None:
            raise ConfigError(f"fields[{i}] 缺少 index（字段索引，0-based）")
        name = f.get("name")
        if not name:
            # 未给名称时自动生成: stmt去掉$ + _字段索引
            name = re.sub(r"[^A-Za-z0-9]", "", stmt).lower() + "_" + str(idx)
        parser = f.get("parser", "bare")
        if isinstance(parser, str) and parser not in PARSER_NAMES:
            raise ConfigError(f"fields[{i}] 未知解析器 '{parser}'（可选 {sorted(PARSER_NAMES)} 或传函数）")
        take = f.get("take", "first")
        if take not in ("first", "last", "count", "all"):
            raise ConfigError(f"fields[{i}] 未知 take '{take}'（可选 first/last/count/all）")
        out.append({
            "name": name,
            "stmt": stmt,
            "index": int(idx),
            "parser": parser,
            "take": take,
        })
    return out


def _validate_charts(charts, field_names):
    """校验图表声明列表。"""
    out = []
    for i, c in enumerate(charts):
        if not isinstance(c, dict):
            raise ConfigError(f"charts[{i}] 必须是 dict")
        ctype = c.get("type")
        if ctype not in CHART_TYPES:
            raise ConfigError(f"charts[{i}] 未知类型 '{ctype}'（可选 {sorted(CHART_TYPES)}）")
        # 检查引用的字段存在（is_nofix / utc_time 为框架内置字段，放行）
        BUILTIN_FIELDS = {"is_nofix", "utc_time", "epoch_index"}
        refs = []
        for k in ("fields", "field", "x_field", "y_field"):
            v = c.get(k)
            if isinstance(v, str):
                refs.append(v)
            elif isinstance(v, list):
                refs.extend(v)
        for r in refs:
            if r not in field_names and r not in BUILTIN_FIELDS:
                raise ConfigError(f"charts[{i}] 引用了未声明的字段 '{r}'（可用: {field_names}）")
        out.append(c)
    return out


def load_config(config) -> dict:
    """接受 dict 或 JSON 文件路径，校验后返回规范化配置。"""
    if isinstance(config, (str, Path)):
        p = Path(config)
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    elif isinstance(config, dict):
        cfg = dict(config)
    else:
        raise ConfigError("config 必须是 dict 或 JSON 文件路径")

    # ---- 文件列表 ----
    files = cfg.get("files", [])
    if not files:
        raise ConfigError("缺少 files（要分析的 BPDEBUG 文件列表）")
    files_norm = []
    for i, f in enumerate(files):
        if isinstance(f, str):
            files_norm.append({"path": f, "name": Path(f).stem, "note": ""})
        elif isinstance(f, dict):
            p = f.get("path") or f.get("file")
            if not p:
                raise ConfigError(f"files[{i}] 缺少 path")
            files_norm.append({
                "path": p,
                "name": f.get("name") or Path(p).stem,
                "note": f.get("note", ""),
            })
        else:
            raise ConfigError(f"files[{i}] 必须是字符串或 dict")

    # ---- 字段 ----
    fields = _validate_fields(cfg.get("fields", []))
    if not fields:
        raise ConfigError("缺少 fields（要提取的字段声明）")
    field_names = [f["name"] for f in fields]

    # ---- 图表 ----
    charts = cfg.get("charts", [])
    if not charts:
        # 未声明图表时，自动为每个字段生成 line 图
        charts = [{"type": "line", "fields": [n], "title": n} for n in field_names]
    charts = _validate_charts(charts, field_names)

    return {
        "title": cfg.get("title", "BPDEBUG 数据分析报告"),
        "files": files_norm,
        "fields": fields,
        "charts": charts,
        "temp_csv": cfg.get("temp_csv", ""),
        "output_html": cfg.get("output_html", "BPDEBUG分析报告.html"),
        "show_nofix_default": bool(cfg.get("show_nofix_default", False)),
        "chart_height": int(cfg.get("chart_height", 420)),
    }
