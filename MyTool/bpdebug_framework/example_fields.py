# -*- coding: utf-8 -*-
"""
示例：用 bpdebug_framework 分析 BPDEBUG 中的任意语句任意字段。

只需两步：
  1. 用 FieldSpec 声明「分析哪条语句的哪个字段」
  2. 创建 BPDebugFrame 解析，得到每历元记录

历元切分、UTC 时间、跨天、不定位打点全部由框架自动处理，无需重复描述。
"""
import os
import sys

# 确保可直接运行（python example_fields.py）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bpdebug_framework.bpdebug_framework import BPDebugFrame, FieldSpec, parse_bpdebug


# ======================================================================
# 示例 A：分析 GGA 卫星数 + GSV 总星数（观察每历元可见星数变化）
# ======================================================================
def example_sky_view(fname):
    fields = [
        FieldSpec('$GNGGA', 7, 'sv_in_use', 'int'),       # GGA 第7字段: 参与解算卫星数
        FieldSpec('$GPGSV', 3, 'gsv_total', 'int'),       # GSV 第3字段: 可见总星数(第一条)
        FieldSpec('$GNGGA', 0, 'gga_count', 'raw', take='count'),  # 每历元 GGA 条数
    ]
    rows, stats = parse_bpdebug(fname, fields)
    print(f"[A] 共 {len(rows)} 历元, {stats['nofix_epochs']} 不定位")
    print("    前3历元:", [(r['utc_time'].isoformat(), r['sv_in_use'], r['gsv_total']) for r in rows[:3]])


# ======================================================================
# 示例 B：分析 CNRCV 中所有可用字段（第14~20字段的原始值）
# ======================================================================
def example_cnrcv_raw(fname):
    fields = [
        FieldSpec('$CNRCV', i, f'cnrcv_f{i}', 'raw')
        for i in range(14, 21)
    ]
    rows, stats = parse_bpdebug(fname, fields)
    print(f"[B] 共 {len(rows)} 历元")
    print("    前2历元 CNRCV 字段14-20:",
          [{k: rows[j][k] for k in rows[j] if k.startswith('cnrcv_f')} for j in range(2)])


# ======================================================================
# 示例 C：自定义解析器 —— 提取 GGA 中的时间字段为纯字符串
# ======================================================================
def example_custom_parser(fname):
    def _hex_parse(x):
        try:
            return int(x, 16) if x else None
        except Exception:
            return None

    fields = [
        FieldSpec('$CNRCV', 7, 'hex_field7', _hex_parse),   # 自定义函数解析
        FieldSpec('$GNGGA', 6, 'fix_quality', 'int'),       # GGA 第6字段: 定位质量(0/1/2)
    ]
    rows, stats = parse_bpdebug(fname, fields)
    print(f"[C] 共 {len(rows)} 历元")
    print("    前2历元:", [(r['utc_time'].isoformat(), r['hex_field7'], r['fix_quality']) for r in rows[:2]])


if __name__ == '__main__':
    import sys
    fname = sys.argv[1] if len(sys.argv) > 1 else 'bpdebug_framework/testdata/fake1.txt'
    example_sky_view(fname)
    example_cnrcv_raw(fname)
    example_custom_parser(fname)
