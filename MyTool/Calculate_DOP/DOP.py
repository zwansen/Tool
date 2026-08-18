#!/usr/bin/env python3
"""
GSV 解析 → 提取 (PRN, El, Az) → 输出 DOP 计算所需格式
用法：把 GSV 语句贴进 raw_gsv_text，运行即可
"""

import re
import json
from math import radians, sin, cos
import numpy as np

# ============================================
# >>>>> 1. 把你的 GSV 语句贴在这里 <<<<<
# ============================================
raw_gsv_text = """
$GPGSV,3,1,10,05,17,115,35,10,26,306,44,13,00,000,31,15,51,054,48*77
$GPGSV,3,2,10,18,57,218,48,20,25,060,45,22,05,051,38,23,59,322,47*70
$GPGSV,3,3,10,24,70,126,49,32,00,000,35,,,,,,,,*76
$GLGSV,3,1,09,68,30,055,49,69,57,350,51,70,29,285,49,78,29,060,49*60
$GLGSV,3,2,09,79,27,120,48,83,16,191,46,84,49,244,51,85,37,310,49*6F
$GLGSV,3,3,09,91,00,000,34,,,,,,,,,,,,*53
$GAGSV,3,1,09,02,06,077,45,07,78,299,50,08,40,050,47,13,45,171,48*6B
$GAGSV,3,2,09,14,00,000,37,26,66,276,49,29,29,246,46,32,00,000,39*62
$GAGSV,3,3,09,33,22,319,42,,,,,,,,,,,,*5C
$GQGSV,1,1,02,03,39,162,45,07,42,163,40,,,,,,,,*76
$GBGSV,5,1,20,01,37,146,47,02,33,231,45,03,46,189,48,04,28,122,46*6B
$GBGSV,5,2,20,06,71,051,50,07,42,174,47,09,62,333,48,10,30,180,44*66
$GBGSV,5,3,20,13,26,297,46,14,69,021,51,21,50,062,50,22,00,000,43*6A
$GBGSV,5,4,20,26,31,144,47,28,10,321,42,33,13,280,37,34,19,207,39*6D
$GBGSV,5,5,20,36,05,033,41,38,31,269,46,39,32,075,48,42,61,317,49*66
"""

# ============================================
# >>>>> 2. 系统前缀 → 系统名映射 <<<<<
# ============================================
SYS_MAP = {
    'GP': 'GPS',
    'GL': 'GLONASS',
    'GA': 'Galileo',
    'GB': 'BDS',
    'GQ': 'QZSS',
    'GI': 'IRNSS',
    'GN': 'GNSS',
}

# ============================================
# >>>>> 3. 核心解析函数 <<<<<
# ============================================
def parse_gsv(gsv_text: str):
    """
    解析 GSV 文本，返回 dict:
    {
      'GPS':  {prn: (el, az, snr), ...},
      'BDS':  {prn: (el, az, snr), ...},
      ...
    }
    """
    results = {}  # sys_name → {prn: (el, az, snr)}

    lines = [l.strip() for l in gsv_text.strip().splitlines() if l.strip()]

    for line in lines:
        if not line.startswith('$') or '*' not in line:
            continue

        # 去掉校验和
        line = line.split('*')[0]

        parts = line.split(',')
        if len(parts) < 4:
            continue

        # 识别系统
        talker = parts[0][1:3]  # $GPGSV → GP
        sys_name = SYS_MAP.get(talker, talker)

        # 第4字段：本系统可见星总数（信息性，这里不用）
        # 从第5字段起，每4个一组：(PRN, El, Az, SNR)
        sat_data = parts[4:]

        if sys_name not in results:
            results[sys_name] = {}

        for i in range(0, len(sat_data) - 3, 4):
            try:
                prn = int(sat_data[i])
                el  = float(sat_data[i+1]) if sat_data[i+1] else None
                az  = float(sat_data[i+2]) if sat_data[i+2] else None
                snr = float(sat_data[i+3]) if sat_data[i+3] else None
            except ValueError:
                continue

            # 必须有仰角和方位角才算有效
            if el is None or az is None:
                continue

            # 去重：同系统同 PRN 只保留第一次
            if prn not in results[sys_name]:
                results[sys_name][prn] = (el, az, snr)

    return results


# ============================================
# >>>>> 4. 输出为 DOP 计算所需格式 <<<<<
# ============================================
def format_for_dop(parsed: dict, output_format='python'):
    """
    将解析结果转为 DOP 计算所需格式
    output_format: 'python' | 'json' | 'csv'
    """
    all_sats = []  # [(sys, prn, el, az, snr), ...]

    for sys_name, prn_dict in parsed.items():
        for prn, (el, az, snr) in sorted(prn_dict.items()):
            all_sats.append((sys_name, prn, el, az, snr or 0))

    if output_format == 'python':
        lines = []
        for sys, prn, el, az, snr in all_sats:
            lines.append(f"    ({el:5.0f}, {az:3.0f}),  # {sys} PRN={prn:>2}  SNR={snr:0.0f}")
        return 'sat_el_az = [\n' + '\n'.join(lines) + '\n]'

    elif output_format == 'json':
        return json.dumps(
            [{'sys': s, 'prn': p, 'el': e, 'az': a, 'snr': sn}
             for s, p, e, a, sn in all_sats],
            indent=2, ensure_ascii=False
        )

    elif output_format == 'csv':
        out = 'SYS,PRN,EL,AZ,SNR\n'
        for s, p, e, a, sn in all_sats:
            out += f'{s},{p},{e},{a},{sn}\n'
        return out


# ============================================
# >>>>> 5. 直接算 DOP（可选） <<<<<
# ============================================
def calc_dop_from_parsed(parsed: dict):
    """从解析结果直接计算 DOP"""
    G = []
    count = 0
    for sys_name, prn_dict in parsed.items():
        for prn, (el, az, snr) in prn_dict.items():
            er, ar = radians(el), radians(az)
            G.append([-sin(ar)*cos(er), cos(ar)*cos(er), sin(er), 1.0])
            count += 1

    if count < 4:
        return None, f"卫星数不足4颗（仅{count}颗），无法解算"

    G = np.array(G)
    Q = np.linalg.inv(G.T @ G)

    hdop = np.sqrt(Q[0,0] + Q[1,1])
    vdop = np.sqrt(Q[2,2])
    pdop = np.sqrt(Q[0,0] + Q[1,1] + Q[2,2])

    return {
        'count': count,
        'pdop': round(pdop, 3),
        'hdop': round(hdop, 3),
        'vdop': round(vdop, 3),
    }, None


# ============================================
# >>>>> 6. 主流程 <<<<<
# ============================================
if __name__ == '__main__':
    parsed = parse_gsv(raw_gsv_text)

    # --- 统计 ---
    print("=" * 60)
    print("📡 GSV 解析结果")
    print("=" * 60)
    total = 0
    for sys_name, prn_dict in parsed.items():
        n = len(prn_dict)
        total += n
        print(f"  {sys_name:10s}: {n:>2} 颗有效卫星（有 El+Az）")
    print(f"  {'合计':10s}: {total:>2} 颗")

    # --- 输出 DOP 计算格式 ---
    print("\n" + "=" * 60)
    print("📋 可直接粘贴到 DOP 脚本的 Python 列表")
    print("=" * 60)
    print(format_for_dop(parsed, 'python'))

    # --- 直接计算 DOP ---
    print("\n" + "=" * 60)
    print("🧮 直接计算 DOP")
    print("=" * 60)
    dop, err = calc_dop_from_parsed(parsed)
    if err:
        print(f"  ⚠️ {err}")
    else:
        print(f"  卫星数 : {dop['count']}")
        print(f"  PDOP   : {dop['pdop']}")
        print(f"  HDOP   : {dop['hdop']}")
        print(f"  VDOP   : {dop['vdop']}")

    # --- 输出 JSON ---
    # print("\n" + "=" * 60)
    # print("📦 JSON 格式")
    # print("=" * 60)
    # print(format_for_dop(parsed, 'json'))