# -*- coding: utf-8 -*-
"""钟漂分析：数据降采样 + JS 数据文件生成。

绘图数据用 min-max 降采样（保留极值、曲线流畅）；
tooltip 查询数据用全量原始值（每个历元精确对应）。
输出结构兼容前端 HTML 报告的 window.DATA / window.TEMP_DATA。
"""
import json
import numpy as np


def minmax_sample(ts, val, window=10):
    """min-max 窗口降采样：每 window 个点取 min/max 两个点（保留极值）。

    自动剔除 None 值对（字段缺失的历元），保证 ts/val 对齐。
    """
    pairs = [(t, v) for t, v in zip(ts, val) if v is not None and t is not None]
    if not pairs:
        return [], []
    ts, val = [p[0] for p in pairs], [p[1] for p in pairs]
    n = len(ts)
    out_ts, out_val = [], []
    i = 0
    while i < n:
        j = min(i + window, n)
        seg_ts = ts[i:j]
        seg_val = val[i:j]
        if len(seg_val) == 0:
            break
        kmin = int(np.argmin(seg_val))
        kmax = int(np.argmax(seg_val))
        if kmin == kmax:
            out_ts.append(seg_ts[kmin]); out_val.append(seg_val[kmin])
        else:
            out_ts.append(seg_ts[kmin]); out_val.append(seg_val[kmin])
            out_ts.append(seg_ts[kmax]); out_val.append(seg_val[kmax])
        i = j
    order = np.argsort(out_ts)
    return [int(out_ts[k]) for k in order], [float(out_val[k]) for k in order]


def build_series_data(rows, use_epoch_axis=False):
    """rows: parse_bpdebug_file 的返回列表。

    返回 (series, all_ts)：
      series: {'flashclkdrifft': {...}, 'curclkdrifft': {...}, 'recvclkdrifft': {...}}
      每字段 {fix_ts, fix_val, nofix_ts, nofix_val, all_val, n_nofix}
      all_ts: 全量时间戳（三字段共享同一 CNRCV 历元）

    use_epoch_axis=True 时横轴改用历元序号（0,1,2,…），不取 GGA/RMC 时间，
    所有历元（包括无时间历元）都有坐标；否则使用 UTC 时间轴，
    无时间的历元（utc_time=None）剔除并保持 all_ts 与各 all_val 对齐。
    """
    if use_epoch_axis:
        ts = [r['epoch_index'] for r in rows]
        keep = list(range(len(rows)))
    else:
        # 批量转换 datetime -> epoch ms
        ts = _dt_to_ms_batch([r['utc_time'] for r in rows])
        keep = [i for i, t in enumerate(ts) if t is not None]

    n_nofix = sum(1 for r in rows if r['is_nofix'] == 1)  # 统计口径：全部不定位历元
    fix_idx = [i for i in keep if rows[i]['is_nofix'] == 0]
    nofix_idx = [i for i in keep if rows[i]['is_nofix'] == 1]

    fix_ts_all = [ts[i] for i in fix_idx]
    nofix_ts_all = [ts[i] for i in nofix_idx]

    out = {}
    for name in ['flashclkdrifft', 'curclkdrifft', 'recvclkdrifft']:
        fix_val_full = [rows[i][name] for i in fix_idx]
        nofix_val_full = [rows[i][name] for i in nofix_idx]
        all_val = [rows[i][name] for i in keep]
        # fix 降采样绘图
        f_ts, f_val = minmax_sample(fix_ts_all, fix_val_full, window=10)
        out[name] = {
            'fix_ts': f_ts, 'fix_val': f_val,
            'nofix_ts': nofix_ts_all, 'nofix_val': nofix_val_full,
            'all_val': all_val,
            'n_nofix': n_nofix,
        }
    return out, [ts[i] for i in keep]


_ISO_CACHE = {}


def _dt_to_ms_batch(dt_list):
    """批量将 datetime 对象转为 epoch ms（带缓存）。"""
    result = []
    uncached = []
    for dt in dt_list:
        if dt is None:
            result.append(None)
            continue
        key = dt.timestamp()
        v = _ISO_CACHE.get(key)
        if v is None:
            uncached.append(dt)
            result.append(None)
        else:
            result.append(v)
    for dt in uncached:
        _ISO_CACHE[dt.timestamp()] = int(dt.timestamp() * 1000)
    for i, dt in enumerate(dt_list):
        if dt is not None:
            result[i] = _ISO_CACHE[dt.timestamp()]
    return result


def write_data_js(file_path, payload):
    """写 window.DATA = {...}; 形式的 js 文件。"""
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('window.DATA = ' + json.dumps(payload) + ';')


def write_temp_js(file_path, temp):
    """写 window.TEMP_DATA = {...}; 形式的 js 文件。"""
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write('window.TEMP_DATA = ' + json.dumps(temp) + ';')
