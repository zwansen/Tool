# -*- coding: utf-8 -*-
"""
BPDEBUG 钟漂数据解析（基于 bpdebug_framework 通用框架）

本模块只声明「要提取哪些字段」：
  - flashclkdrifft / curclkdrifft / recvclkdrifft  <- $CNRCV 第 11/12/13 字段（括号前数值）
历元切分、UTC 时间提取、跨天检测、不定位 10Hz 打点等通用逻辑全部复用 bpdebug_framework，
无需重复实现。温度 CSV 解析也保留在此（独立于框架）。
"""
import datetime
import os

from bpdebug_framework.bpdebug_framework import BPDebugFrame, FieldSpec, parse_bpdebug

# 钟漂字段声明（$CNRCV 第 10/11/12 索引 = 显示的第 11/12/13 字段）
CLOCK_DRIFT_FIELDS = [
    FieldSpec('$CNRCV', 10, 'flashclkdrifft', 'bare'),
    FieldSpec('$CNRCV', 11, 'curclkdrifft', 'bare'),
    FieldSpec('$CNRCV', 12, 'recvclkdrifft', 'bare'),
]


def parse_bpdebug_file(fname, fallback_date=None, log_callback=None):
    """解析单个 BPDEBUG 文件，返回 (rows, stats)。

    rows: list[dict]，每项 {utc_time(datetime), is_nofix(0/1), epoch_index,
                             flashclkdrifft, curclkdrifft, recvclkdrifft}
    stats: {total_epochs, output_points, nofix_epochs, time_start, time_end}
    """
    log = log_callback or (lambda msg: None)
    frame = BPDebugFrame(
        CLOCK_DRIFT_FIELDS,
        fallback_date=fallback_date,
        log_callback=log,
    )
    rows = frame.parse_file(fname)
    stats = {
        'total_epochs': frame.stats.get('total_epochs', 0),
        'output_points': len(rows),
        'nofix_epochs': frame.stats.get('nofix_epochs', 0),
        'time_start': rows[0]['utc_time'].isoformat() if rows and rows[0]['utc_time'] else None,
        'time_end': rows[-1]['utc_time'].isoformat() if rows and rows[-1]['utc_time'] else None,
    }
    # 兼容旧调用方：rows 里 utc_time 需为 datetime（框架已保证）；保持 dict 结构
    return rows, stats


# ---------------------------------------------------------------------------
# 温度 CSV 解析（独立功能，不依赖框架）
# ---------------------------------------------------------------------------

def parse_temp_file(temp_csv, log_callback=None):
    """解析温度 CSV（可选）。

    格式：Index, Time(北京时间), t(seconds), T1, Tenv
    返回：{'ts': [ms], 'val': [T1]} 或 None（解析失败时）
    """
    import csv

    log = log_callback or (lambda msg: None)
    try:
        with open(temp_csv, 'r', errors='replace', newline='') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                log("[警告] 温度文件为空")
                return None
            header = [h.strip() for h in header]
            time_col = None
            t1_col = None
            for i, h in enumerate(header):
                hl = h.lower()
                if 'time' in hl:
                    time_col = i
                if hl == 't1':
                    t1_col = i
            if time_col is None:
                time_col = 1
            if t1_col is None:
                t1_col = 3
            ts_list = []
            val_list = []
            for row in reader:
                if len(row) <= max(time_col, t1_col):
                    continue
                t_str = row[time_col].strip()
                v_str = row[t1_col].strip()
                if not t_str or not v_str:
                    continue
                try:
                    dt = datetime.datetime.strptime(t_str, '%Y/%m/%d %H:%M:%S') - datetime.timedelta(hours=8)
                    val = float(v_str)
                except Exception:
                    continue
                ts_list.append(int(dt.timestamp() * 1000))
                val_list.append(val)
        if not ts_list:
            log("[警告] 温度文件无可解析数据")
            return None
        pairs = sorted(zip(ts_list, val_list), key=lambda p: p[0])
        return {'ts': [p[0] for p in pairs], 'val': [p[1] for p in pairs]}
    except Exception as exc:
        log(f"[错误] 温度文件解析失败: {exc}")
        return None
