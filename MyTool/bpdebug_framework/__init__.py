# -*- coding: utf-8 -*-
"""BPDEBUG 通用解析框架包。

分层：
- bpdebug_framework   : 历元解析基础（切分/时间/跨天/打点）+ FieldSpec 声明式字段提取
- report_config       : 报告配置 schema（files/fields/charts 声明，JSON 或 dict）
- report_engine       : 报告生成引擎（解析 -> 聚合 -> 渲染 HTML，支持多种图表类型）

快速上手（配置驱动生成 HTML 报告）：
    from bpdebug_framework.report_engine import generate_report

    config = {
        "title": "卫星可见性分析",
        "files": [{"path": "log.txt", "name": "接收机A", "note": "常温"}],
        "fields": [
            {"stmt": "$GNGGA", "index": 7, "name": "sv_in_use", "parser": "int"},
            {"stmt": "$GNGGA", "index": 6, "name": "fix_quality", "parser": "int"},
        ],
        "charts": [
            {"type": "line", "fields": ["sv_in_use"], "title": "参与解算卫星数"},
            {"type": "pie", "fields": ["fix_quality"], "title": "定位质量占比"},
            {"type": "table", "fields": ["sv_in_use"], "title": "统计汇总"},
        ],
    }
    html = generate_report(config)
"""
from bpdebug_framework.bpdebug_framework import (
    BPDebugFrame,
    FieldSpec,
    parse_bpdebug,
    parse_temp_file,
)
from bpdebug_framework.report_config import load_config, ConfigError
from bpdebug_framework.report_engine import generate_report

__all__ = [
    "BPDebugFrame",
    "FieldSpec",
    "parse_bpdebug",
    "parse_temp_file",
    "load_config",
    "generate_report",
    "ConfigError",
]
