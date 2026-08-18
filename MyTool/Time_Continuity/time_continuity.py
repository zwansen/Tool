
import argparse
from datetime import datetime, timedelta

def parse_nmea_time(time_str, date_str=None):
    """
    Parses NMEA time string (HHMMSS.sss) and optionally date string (DDMMYY)
    into a datetime object.
    """
    if date_str:
        # NMEA date format is DDMMYY
        # We assume the year is in the 21st century (20YY)
        year = int(date_str[4:6]) + 2000
        month = int(date_str[2:4])
        day = int(date_str[0:2])
    else:
        # If no date string, use a dummy date (e.g., Jan 1, 2000)
        # This is primarily for internal calculation if only time is available
        # and date needs to be inferred or is not critical for relative time.
        # For actual date, RMC is needed.
        year, month, day = 2000, 1, 1

    try:
        # Split seconds to handle milliseconds
        if '.' in time_str:
            time_parts = time_str.split('.')
            seconds = int(time_parts[0][-2:])
            milliseconds = int(time_parts[1][:3]) # Take first 3 digits for milliseconds
            time_str_no_ms = time_parts[0]
        else:
            seconds = int(time_str[-2:])
            milliseconds = 0
            time_str_no_ms = time_str

        hour = int(time_str_no_ms[0:2])
        minute = int(time_str_no_ms[2:4])

        dt_object = datetime(year, month, day, hour, minute, seconds, milliseconds * 1000)
        return dt_object
    except ValueError:
        return None

def parse_pvt_result_time(datetime_str):
    """
    Parses PVTResult datetime string (YYYYMMDD-HHMMSS.sss) into a datetime object.
    """
    try:
        # Example: 20260331-043919.800
        date_part, time_part = datetime_str.split('-')

        year = int(date_part[0:4])
        month = int(date_part[4:6])
        day = int(date_part[6:8])

        # Split seconds to handle milliseconds
        if '.' in time_part:
            time_parts = time_part.split('.')
            seconds = int(time_parts[0][-2:])
            milliseconds = int(time_parts[1][:3]) # Take first 3 digits for milliseconds
            time_part_no_ms = time_parts[0]
        else:
            seconds = int(time_part[-2:])
            milliseconds = 0
            time_part_no_ms = time_part

        hour = int(time_part_no_ms[0:2])
        minute = int(time_part_no_ms[2:4])

        dt_object = datetime(year, month, day, hour, minute, seconds, milliseconds * 1000)
        return dt_object
    except (ValueError, IndexError):
        return None

def time_to_seconds(time_obj):
    """将datetime.time或datetime.datetime转换为当天秒数（含小数）"""
    return time_obj.hour * 3600 + time_obj.minute * 60 + time_obj.second + time_obj.microsecond / 1e6

def find_matching_rmc_date(gga_line, gga_time, rmc_records, max_line_distance=1000, time_tolerance=2.0):
    """
    根据GGA时间和行号，在RMC记录中查找匹配的日期。
    支持RMC在GGA前面或后面（在行号距离范围内）。

    Args:
        gga_line: GGA所在行号
        gga_time: datetime对象或time对象
        rmc_records: [(line_num, datetime_obj), ...]
        max_line_distance: 最大允许行号差
        time_tolerance: 时间容差（秒）

    Returns:
        匹配的date对象，或None
    """
    gga_seconds = time_to_seconds(gga_time)
    best_match_date = None
    best_time_diff = float('inf')

    for rmc_line, rmc_dt in rmc_records:
        line_diff = abs(rmc_line - gga_line)
        if line_diff > max_line_distance:
            continue

        rmc_seconds = time_to_seconds(rmc_dt)
        # 计算时间差，考虑跨午夜情况
        diff = abs(gga_seconds - rmc_seconds)
        diff = min(diff, 86400 - diff)  # 一天86400秒

        if diff <= time_tolerance and diff < best_time_diff:
            best_time_diff = diff
            best_match_date = rmc_dt.date()

    return best_match_date

def split_line_into_sentences(line):
    """
    将一行中混合的多个NMEA/PVT语句拆分开，支持前缀污染（如[DEBUG] $GPRMC）。
    返回该行的所有有效语句列表（已去除前后缀污染）。
    """
    known_prefixes = ('$GNRMC', '$GPRMC', '$GNGGA', '$GPGGA', '$PVTResult', '$PVTMeas')

    # 找到所有已知语句的起始位置
    positions = []
    for prefix in known_prefixes:
        start = 0
        while True:
            idx = line.find(prefix, start)
            if idx == -1:
                break
            positions.append(idx)
            start = idx + 1

    if not positions:
        return []

    positions.sort()

    sentences = []
    for i, pos in enumerate(positions):
        if i + 1 < len(positions):
            sentence = line[pos:positions[i + 1]].strip()
        else:
            sentence = line[pos:].strip()
        if sentence:
            # 截断尾部污染：只取到 *xx（校验和）为止，去掉后面的垃圾数据
            star_idx = sentence.find('*')
            if star_idx != -1 and star_idx + 2 < len(sentence):
                sentence = sentence[:star_idx + 3]
            sentences.append(sentence)

    return sentences

def analyze_gps_log(input_file, output_file, freq=10):
    # 根据频率计算阈值：标称间隔 = 1/freq，允许 ±10% 偏差
    # 1Hz→1.1s / 2Hz→0.55s / 5Hz→0.22s / 10Hz→0.11s / 20Hz→0.055s
    nominal = 1.0 / freq
    time_threshold = timedelta(seconds=nominal * 1.1)
    pvt_meas_min = nominal * 0.9
    pvt_meas_max = nominal * 1.1

    # ========== 第一遍：收集所有RMC ==========
    rmc_records = []  # [(line_num, datetime_obj), ...]
    with open(input_file, 'r', encoding='gbk', errors='ignore') as infile:
        for line_num, line in enumerate(infile, 1):
            sentences = split_line_into_sentences(line)
            for sentence in sentences:
                parts = sentence.split(',')
                if len(parts) > 0:
                    sentence_type = parts[0]
                    if sentence_type.startswith('$GNRMC') or sentence_type.startswith('$GPRMC'):
                        if len(parts) >= 10:
                            time_str = parts[1]
                            date_str = parts[9]
                            if time_str and date_str and len(date_str) >= 6:
                                rmc_time = parse_nmea_time(time_str, date_str)
                                if rmc_time:
                                    rmc_records.append((line_num, rmc_time))

    last_rmc_time = None
    last_gga_time = None
    current_date = None # Stores the date from the most recent RMC message

    last_pvt_result_tag = None
    last_pvt_result_time = None

    last_pvt_meas_tag = None
    last_pvt_meas_time = None

    rmc_gaps = []
    gga_gaps = []
    gga_position_gaps = []  # 时间正常但经纬度字段为空
    pvt_result_tag_gaps = []
    pvt_result_time_gaps = []
    pvt_meas_tag_gaps = []
    pvt_meas_time_gaps = []

    total_lines = 0

    with open(input_file, 'r', encoding='gbk', errors='ignore') as infile, open(output_file, 'w', encoding='utf-8') as outfile:
        outfile.write(f"Analyzing GPS log file: {input_file}\n")
        outfile.write(f"Outputting results to: {output_file}\n\n")

        for line_num, line in enumerate(infile, 1):
            total_lines = line_num
            sentences = split_line_into_sentences(line)

            for sentence in sentences:
                parts = sentence.split(',')

                if len(parts) > 0:
                    sentence_type = parts[0]

                    if sentence_type.startswith('$GNRMC') or sentence_type.startswith('$GPRMC'):
                        if len(parts) >= 10:
                            time_str = parts[1]
                            date_str = parts[9]

                            if time_str and date_str and len(date_str) >= 6:
                                current_rmc_time = parse_nmea_time(time_str, date_str)
                                if current_rmc_time:
                                    # 日期合理性校验：与当前已知日期比较，或检查年份范围
                                    date_valid = True
                                    if current_date:
                                        year_diff = abs(current_rmc_time.year - current_date.year)
                                        if year_diff > 5:
                                            date_valid = False
                                            rmc_gaps.append(f"RMC date anomaly at line {line_num}: Year {current_rmc_time.year} deviates too far from current date {current_date}, raw='{sentence}'")
                                    else:
                                        # 没有参考日期时，检查年份是否在合理范围
                                        if current_rmc_time.year < 2000 or current_rmc_time.year > 2035:
                                            date_valid = False
                                            rmc_gaps.append(f"RMC date anomaly at line {line_num}: Year {current_rmc_time.year} out of reasonable range (2000-2035), raw='{sentence}'")

                                    if date_valid:
                                        current_date = current_rmc_time.date() # Update current date from RMC

                                        if last_rmc_time:
                                            time_diff = current_rmc_time - last_rmc_time
                                            if time_diff > time_threshold:
                                                rmc_gaps.append(f"RMC time gap detected at line {line_num}: Expected ~{time_threshold.total_seconds():.1f}s, but found {time_diff.total_seconds():.3f}s between {last_rmc_time.strftime('%Y-%m-%d %H:%M:%S.%f')} and {current_rmc_time.strftime('%Y-%m-%d %H:%M:%S.%f')}")
                                        last_rmc_time = current_rmc_time
                                else:
                                    rmc_gaps.append(f"RMC data quality gap at line {line_num}: Could not parse time/date from '{sentence}'")
                            else:
                                rmc_gaps.append(f"RMC data quality gap at line {line_num}: Time or date field missing/malformed in '{sentence}'")
                        else:
                            rmc_gaps.append(f"RMC data quality gap at line {line_num}: Message malformed or missing fields in '{sentence}'")

                    elif sentence_type.startswith('$GNGGA') or sentence_type.startswith('$GPGGA'):
                        if len(parts) >= 2:
                            time_str = parts[1]
                            if time_str:
                                # 获取GGA定位质量
                                fix_quality = parts[6] if len(parts) > 6 and parts[6] != '' else '0'

                                current_gga_time = parse_nmea_time(time_str)
                                if current_gga_time:
                                    gga_date_source = None

                                    # 尝试1：顺序逻辑（使用current_date）
                                    if current_date:
                                        temp_gga_time = current_gga_time.replace(
                                            year=current_date.year,
                                            month=current_date.month,
                                            day=current_date.day
                                        )

                                        # 验证时间连续性，如果异常（倒流或跳变>1s）则尝试反向匹配RMC
                                        if last_gga_time:
                                            time_diff = temp_gga_time - last_gga_time
                                            if time_diff < timedelta(seconds=0) or time_diff > timedelta(seconds=1):
                                                matched_date = find_matching_rmc_date(line_num, current_gga_time, rmc_records)
                                                if matched_date:
                                                    current_gga_time = current_gga_time.replace(
                                                        year=matched_date.year,
                                                        month=matched_date.month,
                                                        day=matched_date.day
                                                    )
                                                    current_date = matched_date  # 更新current_date
                                                    gga_date_source = "rmc_matched"
                                                else:
                                                    current_gga_time = temp_gga_time
                                                    gga_date_source = "sequential"
                                            else:
                                                current_gga_time = temp_gga_time
                                                gga_date_source = "sequential"
                                        else:
                                            # 没有last_gga_time，无法验证，直接使用current_date
                                            current_gga_time = temp_gga_time
                                            gga_date_source = "sequential"
                                    else:
                                        # 没有current_date，尝试反向匹配RMC（支持RMC在GGA后面）
                                        matched_date = find_matching_rmc_date(line_num, current_gga_time, rmc_records)
                                        if matched_date:
                                            current_gga_time = current_gga_time.replace(
                                                year=matched_date.year,
                                                month=matched_date.month,
                                                day=matched_date.day
                                            )
                                            current_date = matched_date  # 更新current_date供后续GGA使用
                                            gga_date_source = "rmc_matched"
                                        else:
                                            gga_date_source = "no_date"

                                    # 检测经纬度字段是否为空（时间正常但位置数据缺失）
                                    lat_field = parts[2] if len(parts) > 2 else ''
                                    lat_ns = parts[3] if len(parts) > 3 else ''
                                    lon_field = parts[4] if len(parts) > 4 else ''
                                    lon_ew = parts[5] if len(parts) > 5 else ''
                                    if not lat_field or not lon_field:
                                        gga_position_gaps.append(f"GGA position data missing at line {line_num}: Time is present but latitude/longitude fields are empty. Raw='{sentence}'")

                                    # 进行时间连续性检测
                                    if gga_date_source != "no_date":
                                        if last_gga_time:
                                            time_diff = current_gga_time - last_gga_time
                                            if time_diff > time_threshold:
                                                # 在消息中添加定位状态信息
                                                if fix_quality == '0':
                                                    status_info = "[No fix]"
                                                else:
                                                    status_info = f"[Fix: {fix_quality}]"

                                                gga_gaps.append(f"GGA gap detected at line {line_num}: Expected ~{time_threshold.total_seconds():.1f}s, but found {time_diff.total_seconds():.3f}s between {last_gga_time.strftime('%Y-%m-%d %H:%M:%S.%f')} and {current_gga_time.strftime('%Y-%m-%d %H:%M:%S.%f')} {status_info}")
                                        last_gga_time = current_gga_time
                                    else:
                                        gga_gaps.append(f"GGA data quality gap at line {line_num}: Message found before RMC provided date. Cannot fully timestamp: '{sentence}'")
                                else:
                                    gga_gaps.append(f"GGA data quality gap at line {line_num}: Could not parse time from '{sentence}'")
                            else:
                                gga_gaps.append(f"GGA data quality gap at line {line_num}: Time field missing in '{sentence}'")
                        else:
                            gga_gaps.append(f"GGA data quality gap at line {line_num}: Message malformed or missing fields in '{sentence}'")


                    elif sentence_type.startswith('$PVTResult'):
                        if len(parts) >= 3:
                            try:
                                current_tag = int(parts[1])
                                datetime_str = parts[2]
                                current_pvt_result_time = parse_pvt_result_time(datetime_str)

                                if current_pvt_result_time:
                                    # Check tag continuity
                                    if last_pvt_result_tag is not None:
                                        if current_tag != (last_pvt_result_tag + 1):
                                            pvt_result_tag_gaps.append(f"PVTResult tag gap detected at line {line_num}: Expected tag {last_pvt_result_tag + 1}, but found {current_tag}")
                                    last_pvt_result_tag = current_tag

                                    # Check time continuity
                                    if last_pvt_result_time:
                                        time_diff = current_pvt_result_time - last_pvt_result_time
                                        if time_diff > time_threshold:
                                            pvt_result_time_gaps.append(f"PVTResult time gap detected at line {line_num}: Expected ~{time_threshold.total_seconds():.1f}s, but found {time_diff.total_seconds():.3f}s between {last_pvt_result_time.strftime('%H:%M:%S.%f')} and {current_pvt_result_time.strftime('%H:%M:%S.%f')}")
                                    last_pvt_result_time = current_pvt_result_time
                                else:
                                    outfile.write(f"Warning: Could not parse PVTResult datetime at line {line_num}: {sentence}\n")
                            except ValueError:
                                outfile.write(f"Warning: Could not parse PVTResult tag or datetime field at line {line_num}: {sentence}\n")
                        else:
                            outfile.write(f"Warning: PVTResult message malformed or missing fields at line {line_num}: {sentence}\n")

                    elif sentence_type.startswith('$PVTMeas'):
                        if len(parts) >= 5:
                            try:
                                current_tag = int(parts[1])
                                current_gps_rcv_time = float(parts[4])

                                # Check tag continuity
                                if last_pvt_meas_tag is not None:
                                    if current_tag != (last_pvt_meas_tag + 1):
                                        pvt_meas_tag_gaps.append(f"PVTMeas tag gap detected at line {line_num}: Expected tag {last_pvt_meas_tag + 1}, but found {current_tag}")
                                last_pvt_meas_tag = current_tag

                                # Check time continuity
                                if last_pvt_meas_time is not None:
                                    time_diff = current_gps_rcv_time - last_pvt_meas_time
                                    if not (pvt_meas_min <= time_diff <= pvt_meas_max):
                                        pvt_meas_time_gaps.append(f"PVTMeas time gap detected at line {line_num}: Expected ~{time_threshold.total_seconds():.1f}s, but found {time_diff:.3f}s between {last_pvt_meas_time:.3f} and {current_gps_rcv_time:.3f}")
                                last_pvt_meas_time = current_gps_rcv_time

                            except ValueError:
                                outfile.write(f"Warning: Could not parse PVTMeas tag or gps_rcv_time field at line {line_num}: {sentence}\n")
                        else:
                            outfile.write(f"Warning: PVTMeas message malformed or missing fields at line {line_num}: {sentence}\n")

        outfile.write("\n--- RMC Analysis ---\n")
        if rmc_gaps:
            for gap in rmc_gaps:
                outfile.write(gap + "\n")
        else:
            outfile.write("No significant RMC time gaps detected.\n")

        outfile.write("\n--- GGA Analysis ---\n")
        if gga_gaps:
            for gap in gga_gaps:
                outfile.write(gap + "\n")
        else:
            outfile.write("No significant GGA time gaps detected.\n")

        outfile.write("\n--- GGA Position Data Missing (Time present but lat/lon empty) ---\n")
        if gga_position_gaps:
            for gap in gga_position_gaps:
                outfile.write(gap + "\n")
        else:
            outfile.write("No GGA position data missing detected.\n")

        outfile.write("\n--- PVTResult Analysis (Tag Continuity) ---\n")
        if pvt_result_tag_gaps:
            for gap in pvt_result_tag_gaps:
                outfile.write(gap + "\n")
        else:
            outfile.write("No significant PVTResult tag gaps detected.\n")

        outfile.write("\n--- PVTResult Analysis (Time Continuity) ---\n")
        if pvt_result_time_gaps:
            for gap in pvt_result_time_gaps:
                outfile.write(gap + "\n")
        else:
            outfile.write("No significant PVTResult time gaps detected.\n")

        outfile.write("\n--- PVTMeas Analysis (Tag Continuity) ---\n")
        if pvt_meas_tag_gaps:
            for gap in pvt_meas_tag_gaps:
                outfile.write(gap + "\n")
        else:
            outfile.write("No significant PVTMeas tag gaps detected.\n")

        outfile.write("\n--- PVTMeas Analysis (Time Continuity) ---\n")
        if pvt_meas_time_gaps:
            for gap in pvt_meas_time_gaps:
                outfile.write(gap + "\n")
        else:
            outfile.write("No significant PVTMeas time gaps detected.\n")

    print(f"Analysis complete. Results written to {output_file}")

    return {
        "freq": freq,
        "time_threshold_s": time_threshold.total_seconds(),
        "total_lines": total_lines,
        "sections": {
            "RMC": rmc_gaps,
            "GGA": gga_gaps,
            "GGA定位缺失": gga_position_gaps,
            "PVTResult": pvt_result_tag_gaps + pvt_result_time_gaps,
            "PVTMeas": pvt_meas_tag_gaps + pvt_meas_time_gaps,
        },
    }

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Analyze GPS log file for GGA and RMC time continuity.")
    parser.add_argument("input_file", help="Path to the input GPS log file.")
    parser.add_argument("output_file", help="Path to the output file for analysis results.")
    parser.add_argument("--freq", type=int, choices=[1, 2, 5, 10, 20], default=10,
                        help="Data output frequency in Hz. Supported: 1, 2, 5, 10, 20 (default: 10).")
    args = parser.parse_args()

    analyze_gps_log(args.input_file, args.output_file, freq=args.freq)
