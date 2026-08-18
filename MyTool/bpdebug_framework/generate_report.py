# -*- coding: utf-8 -*-
"""命令行入口：按 JSON 配置生成 BPDEBUG 分析 HTML 报告。

用法：
    python generate_report.py config.json [output_dir]

config.json 示例（report_config.py 中详细说明）：
    {
      "title": "卫星可见性分析",
      "files": [{"path": "log1.txt", "name": "接收机A", "note": "常温"}],
      "fields": [
        {"stmt": "$GNGGA", "index": 7, "name": "sv_in_use", "parser": "int"},
        {"stmt": "$GNGGA", "index": 6, "name": "fix_quality", "parser": "int"}
      ],
      "charts": [
        {"type": "line", "fields": ["sv_in_use"], "title": "参与解算卫星数"},
        {"type": "pie", "fields": ["fix_quality"], "title": "定位质量占比"},
        {"type": "table", "fields": ["sv_in_use", "fix_quality"], "title": "统计汇总"}
      ],
      "temp_csv": "",
      "output_html": "报告.html"
    }
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bpdebug_framework.report_engine import generate_report


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    config = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else ""
    result = generate_report(config, output_dir=output_dir)
    print(f"报告已生成: {result}")


if __name__ == "__main__":
    main()
