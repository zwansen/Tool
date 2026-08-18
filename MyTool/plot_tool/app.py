import streamlit as st
import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")  # 使用非交互式后端，避免关闭/渲染时卡顿
import matplotlib.pyplot as plt
try:
    plt.style.use("seaborn-v0_8-whitegrid")
except Exception:
    pass
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import re
import io
from io import StringIO, BytesIO
from typing import Callable, Optional
from pathlib import Path

st.set_page_config(page_title="交互式绘图工具", page_icon="📊", layout="wide")

st.title("📊 交互式绘图工具")
st.markdown("上传数据文件，选择列并绘制图表。支持 CSV、Excel、TXT、LOG 等格式，支持多文件对比。")


# ==================== 工具函数 ====================

@st.cache_data(show_spinner=False, ttl=1800)
def read_table_file(file_bytes, filename):
    """读取表格文件（csv / excel / tsv）"""
    fname = filename.lower()
    try:
        if fname.endswith(".csv"):
            df = pd.read_csv(BytesIO(file_bytes))
        elif fname.endswith(".tsv"):
            df = pd.read_csv(BytesIO(file_bytes), sep="\t")
        elif fname.endswith((".xlsx", ".xls")):
            df = pd.read_excel(BytesIO(file_bytes))
        else:
            return None
        if df is not None and not df.empty and "__原始顺序__" not in df.columns:
            df["__原始顺序__"] = range(1, len(df) + 1)
        return df
    except Exception as e:
        st.error(f"读取文件 {filename} 失败: {e}")
        return None


def read_text_file(
    file_bytes,
    delimiter,
    has_header,
    skip_rows=0,
    carry_nmea_time=False,
    prefix_filters=None,
    split_by_prefix=False,
    use_default_headers=False,
    default_header_prefix="col",
    delimiter_mode="single",
    multi_delimiters=", ",
    regex_delimiter=r"[,\s]+",
    nmea_utc_direction="backward",
    nmea_utc_cross_day=True,
    nmea_utc_representation="offset",
    filename="file",
    progress_callback=None,
):
    """读取文本文件（txt / log），按指定分隔符解析。

    - carry_nmea_time=True 时，从 $xxGGA/$xxRMC 继承 UTC。
    - prefix_filters 为非空列表时，只保留以其中任一前缀开头的数据行。
    - split_by_prefix=True 且多个前缀时，返回 dict[str, DataFrame]。
    - use_default_headers=True 时，使用 {filename}_{prefix}_{n} 作为列名。
    - delimiter_mode 支持 single / multi / regex 分隔符。
    - progress_callback 接收 0-100 的整数进度。
    """
    if not file_bytes:
        st.error("文件内容为空")
        return None

    total_bytes = len(file_bytes)
    total_lines = _estimate_line_count(file_bytes)

    sep = delimiter
    if delimiter == "\\t":
        sep = "\t"
    elif delimiter == "\\s+":
        sep = r"\s+"

    filters = [p.strip() for p in prefix_filters if isinstance(p, str) and p.strip()] if prefix_filters else []
    use_prefix_filter = bool(filters)
    split_mode = split_by_prefix and len(filters) > 1
    unique_prefix = _make_safe_name(filename)

    # 快速路径：单分隔符、不继承 UTC、不拆分前缀，流式过滤后交给 pandas 解析
    if delimiter_mode == "single" and not carry_nmea_time and not split_mode:
        read_cb = _make_phase_progress_callback(progress_callback, 0, 80)
        build_cb = _make_phase_progress_callback(progress_callback, 80, 100)
        prefix_pattern = re.compile("|".join(re.escape(p) for p in filters)) if use_prefix_filter else None

        stream = _open_text_stream(file_bytes)
        kept_lines = []
        header_raw = None
        try:
            for i, raw_line in enumerate(stream):
                if i < skip_rows:
                    continue
                line = raw_line.rstrip("\r\n")
                # 首行作为表头（仅在需要时）
                if has_header and not use_default_headers and header_raw is None:
                    header_raw = line
                    continue
                # 前缀过滤
                if use_prefix_filter and not prefix_pattern.match(line.strip()):
                    continue
                kept_lines.append(line)
                if i % 200 == 0:
                    if read_cb and total_bytes > 0:
                        read_cb(int(stream.buffer.tell() / total_bytes * 100))
            if read_cb:
                read_cb(100)
        finally:
            stream.close()

        if not kept_lines:
            st.error("没有可解析的数据行")
            return None

        lines_to_parse = ([header_raw] if header_raw else []) + kept_lines
        text_buffer = StringIO("\n".join(lines_to_parse))
        try:
            if header_raw:
                df = pd.read_csv(text_buffer, sep=sep, dtype=str, keep_default_na=False)
            else:
                df = pd.read_csv(text_buffer, sep=sep, header=None, dtype=str, keep_default_na=False)
        except Exception as e:
            st.error(f"解析失败: {e}")
            return None

        if use_default_headers or not has_header:
            new_cols = [f"{unique_prefix}_{default_header_prefix}_{i + 1}" for i in range(len(df.columns))]
            df.columns = new_cols
        df = _add_original_order_to_dataframe(df)
        if build_cb:
            build_cb(100)
        return df

    # 通用路径（含 UTC 继承 / 多分隔符 / 拆分前缀）：分阶段汇报进度
    read_cb = _make_phase_progress_callback(progress_callback, 0, 25)
    utc_cb = _make_phase_progress_callback(progress_callback, 25, 45)
    filter_cb = _make_phase_progress_callback(progress_callback, 45, 65)
    build_cb = _make_phase_progress_callback(progress_callback, 65, 85)
    addutc_cb = _make_phase_progress_callback(progress_callback, 85, 100)

    stream = _open_text_stream(file_bytes)
    try:
        lines = []
        anchor_events = []
        for i, raw_line in enumerate(stream):
            if i < skip_rows:
                continue
            line = raw_line.rstrip("\r\n")
            lines.append(line)
            if carry_nmea_time and line.strip().startswith("$"):
                stripped = line.strip()
                stripped_no_checksum = stripped.split("*")[0]
                parts = stripped_no_checksum.split(",")
                if parts and len(parts[0]) > 1:
                    stype = parts[0][1:].upper()
                    if stype.endswith(("GGA", "RMC")):
                        raw_utc = parts[1] if len(parts) > 1 else ""
                        anchor_events.append({
                            "idx": i - skip_rows,
                            "seconds": _nmea_utc_to_seconds(raw_utc),
                            "is_invalid": _is_invalid_nmea_utc(raw_utc),
                            "fix_status": _get_nmea_fix_status(stype, parts),
                        })
            if i % 200 == 0:
                if read_cb and total_bytes > 0:
                    read_cb(int(stream.buffer.tell() / total_bytes * 100))
        if read_cb:
            read_cb(100)
    finally:
        stream.close()

    if not lines:
        st.error("文件内容为空")
        return None

    # 构建 UTC 查找表以及原始 UTC 无效标记
    utc_lookup = {}
    raw_invalid_lookup = {}
    if carry_nmea_time:
        utc_events = [(e["idx"], e["seconds"]) for e in anchor_events if e["seconds"] is not None]
        utc_lookup = _build_utc_lookup(
            lines, nmea_utc_direction, nmea_utc_cross_day, nmea_utc_representation,
            progress_callback=utc_cb, events=utc_events,
        )
        raw_invalid_lookup = _build_raw_invalid_lookup(
            lines, nmea_utc_direction, nmea_utc_cross_day, events=anchor_events,
        )
        anchor_indices = set()
        if isinstance(raw_invalid_lookup, tuple):
            anchor_indices = raw_invalid_lookup[1]
            raw_invalid_lookup = raw_invalid_lookup[0]
    elif utc_cb:
        utc_cb(100)

    # 提取文件自带表头（若使用），并在收集数据行时跳过它
    header_raw = None
    data_start_idx = 0
    if has_header and not use_default_headers and lines:
        header_raw = lines[0]
        data_start_idx = 1

    # 收集数据行
    all_data_infos = []
    prefix_pattern = re.compile("|".join(re.escape(p) for p in filters)) if use_prefix_filter else None
    total_data_indices = max(1, len(lines) - data_start_idx)
    for offset, i in enumerate(range(data_start_idx, len(lines))):
        raw_line = lines[i]
        line = raw_line.strip()
        if carry_nmea_time and _is_nmea_time_sentence(line):
            continue
        matched_prefix = None
        if use_prefix_filter:
            m = prefix_pattern.match(line)
            if not m:
                continue
            matched_prefix = m.group(0)
        all_data_infos.append((i, raw_line, matched_prefix))
        if offset % 200 == 0:
            if filter_cb:
                filter_cb(int(offset / total_data_indices * 100))
    if filter_cb:
        filter_cb(100)

    if split_mode:
        result = {}
        for p in filters:
            subset = [info for info in all_data_infos if info[2] == p]
            if not subset:
                continue
            if build_cb:
                build_cb(0)
            df_p = _build_dataframe(
                subset, sep, delimiter_mode, multi_delimiters, regex_delimiter,
                has_header, use_default_headers, default_header_prefix, unique_prefix,
                header_raw=header_raw,
            )
            if df_p is not None:
                if carry_nmea_time:
                    if addutc_cb:
                        addutc_cb(0)
                    df_p = _add_utc_to_dataframe(df_p, [info[0] for info in subset], utc_lookup)
                    df_p = _add_raw_invalid_to_dataframe(df_p, [info[0] for info in subset], raw_invalid_lookup, anchor_indices)
                    if addutc_cb:
                        addutc_cb(100)
                df_p = _add_original_order_to_dataframe(df_p)
                result[p] = df_p
        if progress_callback:
            progress_callback(100)
        return result if result else None

    if build_cb:
        build_cb(0)
    df = _build_dataframe(
        all_data_infos, sep, delimiter_mode, multi_delimiters, regex_delimiter,
        has_header, use_default_headers, default_header_prefix, unique_prefix,
        header_raw=header_raw,
    )
    if build_cb:
        build_cb(100)
    if df is not None:
        if carry_nmea_time:
            if addutc_cb:
                addutc_cb(0)
            df = _add_utc_to_dataframe(df, [info[0] for info in all_data_infos], utc_lookup)
            df = _add_raw_invalid_to_dataframe(df, [info[0] for info in all_data_infos], raw_invalid_lookup, anchor_indices)
            if addutc_cb:
                addutc_cb(100)
        df = _add_original_order_to_dataframe(df)
    elif addutc_cb:
        addutc_cb(100)
    if progress_callback:
        progress_callback(100)
    return df


# ---------- NMEA/语句型日志解析 ----------

def _decode_bytes(file_bytes):
    """解码文件字节，优先 UTF-8，失败回退 GBK。"""
    try:
        return file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return file_bytes.decode("gbk", errors="replace")


def _open_text_stream(file_bytes):
    """根据字节内容检测编码，返回可逐行读取的 TextIOWrapper。"""
    encoding = "utf-8"
    try:
        file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "gbk"
    return io.TextIOWrapper(io.BytesIO(file_bytes), encoding=encoding, errors="replace")


def _estimate_line_count(file_bytes):
    """快速估算行数（用于进度条）。"""
    if not file_bytes:
        return 0
    return file_bytes.count(b"\n") + (0 if file_bytes.endswith(b"\n") else 1)


def _make_progress_callback(progress_bar, total_steps=100):
    """构造节流后的进度回调，避免每行都刷新 UI。"""
    if progress_bar is None:
        return None
    last_reported = [-1]

    def callback(percent):
        pct = max(0, min(100, int(percent)))
        if pct != last_reported[0]:
            last_reported[0] = pct
            try:
                progress_bar.progress(pct, text=f"解析进度 {pct}%")
            except Exception:
                progress_bar.progress(pct)

    return callback


def _make_phase_progress_callback(callback, start, end):
    """把 0-100 的局部进度映射到全局 [start, end] 区间。

    用于在单个函数内部把不同阶段的进度合并到同一条进度条。
    """
    if callback is None:
        return None
    last_reported = [-1]

    def report(local_percent):
        pct = start + int(local_percent / 100.0 * (end - start))
        pct = max(0, min(100, pct))
        if pct != last_reported[0]:
            last_reported[0] = pct
            callback(pct)

    return report


def _nmea_latlon_to_decimal(value, hemisphere):
    """把 NMEA 经纬度格式转换为十进制度。"""
    if not value or not hemisphere:
        return None
    try:
        dot_idx = value.find(".")
        if dot_idx == -1:
            return None
        integer_part = value[:dot_idx]
        decimal_part = value[dot_idx:]
        if len(integer_part) <= 4:  # 纬度: ddmm.mmmm
            deg_len = 2
        else:  # 经度: dddmm.mmmm
            deg_len = 3
        degrees = int(integer_part[:deg_len])
        minutes = float(integer_part[deg_len:] + decimal_part)
        dec = degrees + minutes / 60.0
        if hemisphere in ("S", "W"):
            dec = -dec
        return dec
    except (ValueError, IndexError):
        return None


def _format_nmea_utc(raw_utc):
    """把 HHMMSS[.SS] 格式化为 HH:MM:SS。"""
    if not raw_utc:
        return None
    try:
        # 去掉小数部分
        integer_utc = raw_utc.split(".")[0]
        if len(integer_utc) != 6:
            return raw_utc
        hours = integer_utc[:2]
        minutes = integer_utc[2:4]
        seconds = integer_utc[4:6]
        return f"{hours}:{minutes}:{seconds}"
    except (ValueError, IndexError):
        return raw_utc


def _looks_like_raw_nmea_utc(series):
    """判断一列是否全部为原始 NMEA UTC 格式（如 083253.000 / 83253.0）。"""
    vals = series.dropna()
    if len(vals) == 0:
        return False
    for v in vals:
        s = str(v).strip()
        if not re.match(r"^\d{5,6}(\.\d+)?$", s):
            return False
        try:
            n = int(float(s))
            hh = n // 10000
            mm = (n // 100) % 100
            ss = n % 100
            if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
                return False
        except Exception:
            return False
    return True


def _format_nmea_utc_raw(value):
    """将数值化的原始 NMEA UTC 还原为 HHMMSS.SSS 字符串，保留前导零。"""
    if pd.isna(value):
        return value
    try:
        f = float(value)
        hhmmss = int(f)
        frac = f - hhmmss
        ms = int(round(frac * 1000))
        return f"{hhmmss:06d}.{ms:03d}"
    except Exception:
        return value


def _extract_nmea_utc(line):
    """从单条 NMEA 语句中提取 UTC 时间（支持 $xxGGA 和 $xxRMC）。"""
    line = line.strip()
    if not line.startswith("$"):
        return None
    line = line.split("*")[0]
    parts = line.split(",")
    if not parts or len(parts[0]) <= 1:
        return None
    stype = parts[0][1:].upper()
    if stype.endswith(("GGA", "RMC")) and len(parts) > 1 and parts[1]:
        return _format_nmea_utc(parts[1])
    return None


def _make_safe_name(name):
    """把文件名中的非法字符替换为下划线，限制长度，用于生成默认列名前缀。"""
    stem = Path(name).stem
    safe = re.sub(r"[^\w]", "_", stem).strip("_")
    return (safe[:30] or "file")


def _make_unique_file_prefixes(filenames):
    """为多个文件名生成互不重复的短前缀。"""
    prefixes = {}
    used = set()
    for fname in filenames:
        base = _make_safe_name(fname)
        candidate = base
        counter = 2
        while candidate in used:
            candidate = f"{base}_{counter}"
            counter += 1
        prefixes[fname] = candidate
        used.add(candidate)
    return prefixes


def _is_nmea_time_sentence(line):
    """判断一行是否为 $xxGGA 或 $xxRMC 时间语句。"""
    line = line.strip()
    if not line.startswith("$"):
        return False
    line = line.split("*")[0]
    parts = line.split(",")
    if not parts or len(parts[0]) <= 1:
        return False
    stype = parts[0][1:].upper()
    return stype.endswith(("GGA", "RMC"))


def _nmea_utc_to_seconds(raw_utc):
    """把原始 NMEA UTC 字段（HHMMSS[.SSS]）转为自午夜起的秒数（含毫秒）。

    000000.000 / 000000 被视为无效 UTC，返回 None。
    """
    if _is_invalid_nmea_utc(raw_utc):
        return None
    try:
        s = re.sub(r"[^\d.]", "", str(raw_utc).strip())
        if not s:
            return None
        parts_utc = s.split(".")
        int_part = parts_utc[0]
        if len(int_part) > 6:
            return None
        int_part = int_part.zfill(6)
        hh = int(int_part[:2])
        mm = int(int_part[2:4])
        ss = int(int_part[4:6])
        frac = parts_utc[1] if len(parts_utc) > 1 else ""
        frac = (frac + "000")[:3]
        ms = int(frac)
        if not (0 <= hh < 24 and 0 <= mm < 60 and 0 <= ss < 60):
            return None
        return hh * 3600 + mm * 60 + ss + ms / 1000.0
    except Exception:
        return None


def _extract_nmea_utc_seconds(line):
    """从 $xxGGA/$xxRMC 语句中提取 UTC 并返回自午夜起的秒数（含毫秒）。

    注意：000000.000 / 000000 被视为无效 UTC，返回 None。
    """
    line = line.strip()
    if not line.startswith("$"):
        return None
    line = line.split("*")[0]
    parts = line.split(",")
    if not parts or len(parts[0]) <= 1:
        return None
    stype = parts[0][1:].upper()
    if not (stype.endswith("GGA") or stype.endswith("RMC")):
        return None
    if len(parts) <= 1 or not parts[1]:
        return None
    return _nmea_utc_to_seconds(parts[1])


def _is_invalid_nmea_utc(raw_utc):
    """判断原始 NMEA UTC 字段是否为无效值（空、000000、000000.000 等）。"""
    if raw_utc is None:
        return True
    s = re.sub(r"[^\d.]", "", str(raw_utc).strip())
    if not s:
        return True
    parts = s.split(".")
    int_part = parts[0].zfill(6)
    if int_part != "000000":
        return False
    frac = parts[1] if len(parts) > 1 else ""
    frac = (frac + "000")[:3]
    return int(frac) == 0


def _get_nmea_fix_status(stype, parts):
    """根据语句类型和字段判断定位状态。

    返回：
        "fix"      - 定位有效
        "no_fix"   - 不定位/无效
        None       - 无法判断（非 GGA/RMC 或字段不足）
    """
    if not stype.endswith(("GGA", "RMC")) or len(parts) < 3:
        return None
    if stype.endswith("GGA"):
        # GGA field 6 (0-based index 6): 0=invalid, 1=GPS, 2=DGPS, ...
        if len(parts) > 6:
            quality = parts[6].strip()
            if quality == "0":
                return "no_fix"
            elif quality and quality.isdigit() and int(quality) > 0:
                return "fix"
    elif stype.endswith("RMC"):
        # RMC field 2 (0-based index 2): A=valid, V=invalid
        if len(parts) > 2:
            status = parts[2].strip().upper()
            if status == "A":
                return "fix"
            elif status == "V":
                return "no_fix"
    return None


def _format_hhmmss(seconds):
    """把秒数格式化为 HH:MM:SS（不含天偏移）。"""
    seconds = seconds % 86400
    total_ms = int(round(seconds * 1000))
    hh = total_ms // 3600000
    mm = (total_ms // 60000) % 60
    ss_ms = total_ms % 60000
    ss = ss_ms // 1000
    ms = ss_ms % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"


def _fill_invalid_utc_seconds(seconds_list, is_invalid_list):
    """对无效 UTC 段做线性插值填充。

    参数：
        seconds_list: 原始秒数列表（无效位置可能为 None 或 0）
        is_invalid_list: 与 seconds_list 等长的布尔列表，True 表示该位置无效

    返回：
        filled_seconds: 填充后的秒数列表
        filled_flags: 是否被填充的布尔列表
    """
    n = len(seconds_list)
    filled = list(seconds_list)
    filled_flags = [False] * n

    i = 0
    while i < n:
        if not is_invalid_list[i]:
            i += 1
            continue
        # 找到连续无效段 [start, end]
        start = i
        while i < n and is_invalid_list[i]:
            i += 1
        end = i - 1

        # 只有前后都有有效值时才填充
        prev_valid = None
        next_valid = None
        for j in range(start - 1, -1, -1):
            if not is_invalid_list[j] and filled[j] is not None:
                prev_valid = (j, filled[j])
                break
        for j in range(end + 1, n):
            if not is_invalid_list[j] and filled[j] is not None:
                next_valid = (j, filled[j])
                break

        if prev_valid is None or next_valid is None:
            continue

        total_gap = next_valid[1] - prev_valid[1]
        num_invalid = end - start + 1
        num_steps = num_invalid + 1  # 从 prev 到 next 之间的间隔数
        if num_steps <= 1:
            continue
        step = total_gap / num_steps
        for k, idx in enumerate(range(start, end + 1)):
            filled[idx] = prev_valid[1] + step * (k + 1)
            filled_flags[idx] = True

    return filled, filled_flags


def _normalize_utc_events(events):
    """根据 UTC 时间序列检测跨天，给每个事件累加天数偏移（秒数）。"""
    if not events:
        return []
    result = []
    day_offset = 0
    prev_seconds = events[0][1]
    for idx, seconds in events:
        if seconds < prev_seconds - 3600:
            day_offset += 86400
        result.append((idx, seconds + day_offset))
        prev_seconds = seconds
    return result


def _find_utc_for_line(line_idx, normalized_events, direction):
    """根据方向（backward/forward/nearest）为指定行查找 UTC 秒数。"""
    if not normalized_events:
        return None
    before = None
    after = None
    for e_idx, e_seconds in normalized_events:
        if e_idx == line_idx:
            return e_seconds
        elif e_idx < line_idx:
            before = (e_idx, e_seconds)
        elif e_idx > line_idx and after is None:
            after = (e_idx, e_seconds)

    if direction == "backward":
        return before[1] if before else (after[1] if after else None)
    elif direction == "forward":
        return after[1] if after else (before[1] if before else None)
    else:  # nearest
        if before is None:
            return after[1] if after else None
        if after is None:
            return before[1]
        d_before = line_idx - before[0]
        d_after = after[0] - line_idx
        if d_before <= d_after:
            return before[1]
        return after[1]


def _format_utc_seconds(seconds, representation):
    """把含跨天偏移的 UTC 秒数格式化为字符串。"""
    if representation == "seconds":
        return seconds
    days = int(seconds // 86400)
    day_ms = int(round((seconds % 86400) * 1000))
    hh = day_ms // 3600000
    mm = (day_ms // 60000) % 60
    ss_ms = day_ms % 60000
    ss = ss_ms // 1000
    ms = ss_ms % 1000
    offset = f"(+{days * 86400})" if days > 0 else ""
    return f"{hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}{offset}"


def _build_utc_lookup(lines, direction, cross_day, representation, progress_callback=None, events=None):
    """为每一行构建 UTC 查找表（仅数据行会实际使用）。

    如果传入 events，则跳过从 lines 中扫描事件的过程（用于减少一次全量遍历）。
    """
    total = len(lines)
    if events is None:
        raw_events = []
        for idx, line in enumerate(lines):
            if line.strip().startswith("$"):
                seconds = _extract_nmea_utc_seconds(line)
                if seconds is not None:
                    raw_events.append((idx, seconds))
            if progress_callback and total and idx % 200 == 0:
                progress_callback(int(idx / total * 30))
    else:
        raw_events = events
        if progress_callback:
            progress_callback(30)

    normalized = _normalize_utc_events(raw_events) if cross_day else raw_events

    if progress_callback:
        progress_callback(50)

    lookup = {}
    for idx, line in enumerate(lines):
        utc = _find_utc_for_line(idx, normalized, direction)
        if utc is not None:
            lookup[idx] = _format_utc_seconds(utc, representation)
        if progress_callback and total and idx % 200 == 0:
            progress_callback(int(50 + idx / total * 50))
    if progress_callback:
        progress_callback(100)
    return lookup


def _find_utc_source_for_line(line_idx, normalized_events, direction):
    """根据方向查找为指定行提供 UTC 的锚点原始行索引。"""
    if not normalized_events:
        return None
    before = None
    after = None
    exact = None
    for e_idx, _ in normalized_events:
        if e_idx == line_idx:
            exact = e_idx
        elif e_idx < line_idx:
            before = e_idx
        elif e_idx > line_idx and after is None:
            after = e_idx

    if exact is not None:
        return exact

    if direction == "backward":
        return before if before is not None else after
    elif direction == "forward":
        return after if after is not None else before
    else:  # nearest
        if before is None:
            return after
        if after is None:
            return before
        d_before = line_idx - before
        d_after = after - line_idx
        return before if d_before <= d_after else after


def _build_raw_invalid_lookup(lines, direction, cross_day, events=None, progress_callback=None):
    """为每一行构建原始 UTC 是否无效（如 000000.000）的查找表。

    对 GGA/RMC 锚点行使用自身原始 UTC 是否无效；对非锚点行使用其 UTC 来源锚点的标记，
    以便按原始顺序绘图时也能高亮继承自无效 UTC 的数据点。
    events 为列表，每个元素是 {"idx", "seconds", "is_invalid", "fix_status"}。
    返回 (lookup, anchor_indices)。
    """
    total = len(lines)
    if events is None:
        anchor_infos = []
        for idx, line in enumerate(lines):
            line = line.strip()
            if not line.startswith("$"):
                continue
            line = line.split("*")[0]
            parts = line.split(",")
            if not parts or len(parts[0]) <= 1:
                continue
            stype = parts[0][1:].upper()
            if not stype.endswith(("GGA", "RMC")):
                continue
            raw_utc = parts[1] if len(parts) > 1 else ""
            anchor_infos.append({
                "idx": idx,
                "seconds": _nmea_utc_to_seconds(raw_utc),
                "is_invalid": _is_invalid_nmea_utc(raw_utc),
                "fix_status": _get_nmea_fix_status(stype, parts),
            })
            if progress_callback and total and idx % 200 == 0:
                progress_callback(int(idx / total * 30))
    else:
        anchor_infos = events
        if progress_callback:
            progress_callback(30)

    if not anchor_infos:
        if progress_callback:
            progress_callback(100)
        return {}, set()

    raw_events = [(i, info["seconds"]) for i, info in enumerate(anchor_infos) if info["seconds"] is not None]
    normalized_pos = _normalize_utc_events(raw_events) if cross_day else raw_events
    for pos, sec in normalized_pos:
        anchor_infos[pos]["seconds"] = sec

    seconds_list = [info["seconds"] for info in anchor_infos]
    is_invalid_list = [info["is_invalid"] for info in anchor_infos]
    filled_seconds, _ = _fill_invalid_utc_seconds(seconds_list, is_invalid_list)

    anchor_raw_invalid_by_idx = {info["idx"]: info["is_invalid"] for info in anchor_infos}
    normalized_events_with_idx = [(info["idx"], filled_seconds[pos]) for pos, info in enumerate(anchor_infos)]
    anchor_indices = {info["idx"] for info in anchor_infos}

    lookup = {}
    for idx in range(len(lines)):
        source_idx = _find_utc_source_for_line(idx, normalized_events_with_idx, direction)
        # 继承 UTC 的数据行同样标记来源锚点的原始 UTC 是否无效，用于原始顺序绘图高亮
        if source_idx is not None:
            lookup[idx] = anchor_raw_invalid_by_idx.get(source_idx, False)
        else:
            lookup[idx] = False
        if progress_callback and total and idx % 200 == 0:
            progress_callback(int(60 + idx / total * 40))

    if progress_callback:
        progress_callback(100)
    return lookup, anchor_indices


def _add_raw_invalid_to_dataframe(df, data_original_indices, raw_invalid_lookup, anchor_indices=None):
    """为 DataFrame 附加 utc_raw_invalid 布尔列以及 __utc_anchor_row__ 标记列。"""
    if df is None or df.empty or not raw_invalid_lookup:
        return df
    values = [raw_invalid_lookup.get(idx, False) for idx in data_original_indices]
    col = "utc_raw_invalid"
    if col in df.columns:
        col = "nmea_utc_raw_invalid"
    df[col] = values
    if anchor_indices is not None:
        df["__utc_anchor_row__"] = [idx in anchor_indices for idx in data_original_indices]
    return df


def _add_original_order_to_dataframe(df):
    """为 DataFrame 附加 __原始顺序__ 列（从 1 开始的文件内行号）。"""
    if df is None or df.empty:
        return df
    col = "__原始顺序__"
    if col in df.columns:
        return df
    df[col] = range(1, len(df) + 1)
    return df


# ---------- 原始 UTC 无效点标红 ----------

_HIGHLIGHT_RAW_INVALID_COL = "_highlight_raw_invalid_utc"
_HIGHLIGHT_RAW_INVALID_INFO = {
    "raw_invalid": {"label": "原始 UTC 无效", "color": "#EF4444"},
}


def _add_raw_invalid_highlight_column(df, raw_invalid_col="utc_raw_invalid"):
    """为原始 UTC 无效点生成高亮列。原始 UTC 无效点标红，其余点标记为"正常"。"""
    if df is None or raw_invalid_col not in df.columns:
        return df
    df = df.copy()
    label = _HIGHLIGHT_RAW_INVALID_INFO["raw_invalid"]["label"]
    df[_HIGHLIGHT_RAW_INVALID_COL] = df[raw_invalid_col].map({True: label, False: "正常"}).fillna("正常")
    return df



def _split_line_multi_delim(line, delimiter_str, delimiter_mode):
    """用多个分隔符或正则拆分一行文本，过滤空字段。"""
    line = str(line)
    try:
        if delimiter_mode == "regex":
            if not delimiter_str:
                delimiter_str = r"[,\s]+"
            parts = re.split(delimiter_str, line)
        else:
            if not delimiter_str:
                delimiter_str = " \t"
            pattern = "|".join(re.escape(c) for c in delimiter_str)
            parts = re.split(pattern, line)
    except re.error as e:
        st.error(f"分隔符正则错误: {e}")
        return []
    return [p.strip() for p in parts if p.strip()]


def _build_dataframe(all_data_infos, sep, delimiter_mode, multi_delimiters, regex_delimiter,
                     has_header, use_default_headers, default_header_prefix, unique_prefix,
                     header_raw=None):
    """根据行信息构建 DataFrame，支持单分隔符与多分隔符模式。"""
    if not all_data_infos:
        st.error("没有可解析的数据行")
        return None

    data_infos = all_data_infos

    if not data_infos:
        st.error("没有可解析的数据行")
        return None

    if delimiter_mode == "single":
        lines_to_parse = ([header_raw] if header_raw else []) + [info[1] for info in data_infos]
        text_buffer = StringIO("\n".join(lines_to_parse))
        try:
            if header_raw:
                df = pd.read_csv(text_buffer, sep=sep)
            else:
                # NMEA 混合语句字段数不一致，先估算最大列数，避免 pandas 因字段数不同报错
                try:
                    if sep and (sep.startswith("\\") or "|" in sep or "[" in sep):
                        max_cols = max(len(re.split(sep, line)) for line in lines_to_parse)
                    else:
                        max_cols = max(len(line.split(sep)) for line in lines_to_parse)
                except Exception:
                    max_cols = None
                names = [f"col_{i}" for i in range(max_cols)] if max_cols else None
                df = pd.read_csv(text_buffer, sep=sep, header=None, names=names, keep_default_na=False, on_bad_lines="skip")
        except Exception as e:
            st.error(f"解析失败: {e}")
            return None
    else:
        delimiter_str = regex_delimiter if delimiter_mode == "regex" else multi_delimiters
        split_rows = [_split_line_multi_delim(info[1], delimiter_str, delimiter_mode) for info in data_infos]
        max_cols = max(len(r) for r in split_rows)
        padded = [r + [""] * (max_cols - len(r)) for r in split_rows]
        if header_raw:
            header = _split_line_multi_delim(header_raw, delimiter_str, delimiter_mode)
            header += [f"{unique_prefix}_{default_header_prefix}_{i + 1}" for i in range(max_cols - len(header))]
        else:
            header = [f"{unique_prefix}_{default_header_prefix}_{i + 1}" for i in range(max_cols)]
        df = pd.DataFrame(padded, columns=header)

    # 应用默认列名
    if use_default_headers or not has_header:
        new_cols = [f"{unique_prefix}_{default_header_prefix}_{i + 1}" for i in range(len(df.columns))]
        df.columns = new_cols

    return df


def _add_utc_to_dataframe(df, data_original_indices, utc_lookup):
    """为 DataFrame 附加 UTC 列。"""
    if df is None or df.empty:
        return df
    utc_values = [utc_lookup.get(idx) for idx in data_original_indices]
    utc_col = "utc_time"
    if utc_col in df.columns:
        utc_col = "nmea_utc_time"
    df[utc_col] = utc_values
    return df


# 已知含经纬度的语句类型及其字段位置（0-based）
_LATLON_SCHEMA = {
    "GGA": {"lat": 2, "lat_h": 3, "lon": 4, "lon_h": 5},
    "GLL": {"lat": 1, "lat_h": 2, "lon": 3, "lon_h": 4},
    "RMC": {"lat": 3, "lat_h": 4, "lon": 5, "lon_h": 6},
}


def detect_sentence_types(file_bytes, progress_callback=None):
    """扫描文件中出现过的所有 $ 语句类型，返回排序后的列表。"""
    total_lines = _estimate_line_count(file_bytes)
    types = set()
    stream = _open_text_stream(file_bytes)
    try:
        for i, raw_line in enumerate(stream):
            line = raw_line.strip()
            if not line.startswith("$"):
                continue
            line = line.split("*")[0]
            parts = line.split(",")
            if parts and len(parts[0]) > 1:
                stype = parts[0][1:].upper()
                if stype:
                    types.add(stype)
            if progress_callback and total_lines and i % 5000 == 0:
                progress_callback(min(100, int(i / total_lines * 100)))
    finally:
        stream.close()
    if progress_callback:
        progress_callback(100)
    return sorted(types)


def read_sentence_log(file_bytes, selected_types, carry_time=True, convert_latlon=True, progress_callback=None):
    """
    解析 NMEA-like 语句日志。

    参数:
        selected_types: 要提取的语句类型列表，如 ["GNGGA", "GNVTG"]
        carry_time: 是否从最近的前一条 GGA 语句继承 UTC 时间到无时间语句
        convert_latlon: 是否把 GGA/GLL/RMC 中的经纬度转换为十进制度

    返回的 DataFrame 中：
        - 每个语句的字段会展开为 {stype}_field_{i} 列
        - 存在 GGA/RMC 时间时，所有行都会带一个统一的 "utc_time" 列
        - GGA 行额外保留 {stype}_utc_time 列，非 GGA/RMC 行可选继承 {stype}_GGA_utc_time 等字段
    """
    if not selected_types:
        st.error("未选择任何语句类型")
        return None

    selected_set = set(t.upper() for t in selected_types)
    total_bytes = len(file_bytes)

    parse_cb = _make_phase_progress_callback(progress_callback, 0, 60)
    df_cb = _make_phase_progress_callback(progress_callback, 60, 100)

    last_gga_fields = {}  # 最近一条 GGA 的完整字段，供跨语句继承
    last_utc_time = None  # 最近一条 GGA 或 RMC 的 UTC 时间
    last_utc_raw_invalid = False  # 最近一条 GGA/RMC 的原始 UTC 是否无效
    rows = []
    row_infos = []

    stream = _open_text_stream(file_bytes)
    try:
        for i, raw_line in enumerate(stream):
            line = raw_line.strip()
            if not line.startswith("$"):
                continue
            # 去掉校验和
            line = line.split("*")[0]
            parts = line.split(",")
            if not parts or len(parts[0]) <= 1:
                continue
            stype = parts[0][1:].upper()  # 去掉 '$' 并统一大写

            # 记录最近一条 GGA 的完整字段，同时更新最近 UTC 时间
            if stype.endswith("GGA"):
                last_gga_fields = {f"GGA_field_{i}": v for i, v in enumerate(parts)}
                if len(parts) > 1 and parts[1]:
                    last_utc_time = _format_nmea_utc(parts[1])
                    last_gga_fields["GGA_utc_time"] = last_utc_time
                    last_utc_raw_invalid = _is_invalid_nmea_utc(parts[1])

            # RMC 语句同样提供 UTC 时间
            if stype.endswith("RMC"):
                if len(parts) > 1 and parts[1]:
                    last_utc_time = _format_nmea_utc(parts[1])
                    last_utc_raw_invalid = _is_invalid_nmea_utc(parts[1])

            if stype not in selected_set:
                continue

            row = {f"{stype}_field_{i}": v for i, v in enumerate(parts)}
            row["__原始顺序__"] = len(rows) + 1

            if last_utc_time is not None:
                row["utc_time"] = last_utc_time

            # GGA 行也保留格式化后的 per-type utc_time 列，兼容旧用法
            if stype.endswith("GGA") and "GGA_utc_time" in last_gga_fields:
                row[f"{stype}_utc_time"] = last_gga_fields["GGA_utc_time"]

            # 对非 GGA/RMC 语句继承最近 GGA 的所有字段，实现跨语句选坐标
            if carry_time and last_gga_fields and not stype.endswith(("GGA", "RMC")):
                for gga_key, gga_val in last_gga_fields.items():
                    row[f"{stype}_{gga_key}"] = gga_val

            # 标记原始 UTC 是否无效（用于按原始顺序绘图时标红 000000.000）
            if stype.endswith(("GGA", "RMC")):
                row["utc_raw_invalid"] = last_utc_raw_invalid
                row["__utc_anchor_row__"] = True
            else:
                # 继承最近 GGA/RMC 的原始 UTC 无效标记，使数据点也能按来源锚点高亮
                row["utc_raw_invalid"] = last_utc_raw_invalid if carry_time else False
                row["__utc_anchor_row__"] = False

            # 经纬度转换
            if convert_latlon:
                base_type = None
                if stype.endswith("GGA"):
                    base_type = "GGA"
                elif stype.endswith("GLL"):
                    base_type = "GLL"
                elif stype.endswith("RMC"):
                    base_type = "RMC"

                if base_type and base_type in _LATLON_SCHEMA:
                    schema = _LATLON_SCHEMA[base_type]
                    lat = _nmea_latlon_to_decimal(
                        parts[schema["lat"]] if len(parts) > schema["lat"] else "",
                        parts[schema["lat_h"]] if len(parts) > schema["lat_h"] else "",
                    )
                    lon = _nmea_latlon_to_decimal(
                        parts[schema["lon"]] if len(parts) > schema["lon"] else "",
                        parts[schema["lon_h"]] if len(parts) > schema["lon_h"] else "",
                    )
                    if lat is not None:
                        row[f"{stype}_latitude_dec"] = lat
                    if lon is not None:
                        row[f"{stype}_longitude_dec"] = lon

            rows.append(row)

            if i % 200 == 0 and parse_cb and total_bytes > 0:
                parse_cb(int(stream.buffer.tell() / total_bytes * 100))
    finally:
        stream.close()

    if parse_cb:
        parse_cb(100)

    if not rows:
        st.error("未找到选中的语句类型，请检查文件内容或语句类型选择")
        if df_cb:
            df_cb(100)
        return None

    if df_cb:
        df_cb(0)
    df = pd.DataFrame(rows)
    if df_cb:
        df_cb(100)
    if progress_callback:
        progress_callback(100)
    return df


def _build_plotly_figure(plot_df, x_col, y_cols, chart_type, chart_title, x_label, y_label, color_by_file, multi_mode, bins, color_col=None):
    """使用 Plotly 构建单个图表。"""
    is_highlight_color = color_col == _HIGHLIGHT_RAW_INVALID_COL
    use_color = bool(color_col) and color_col in plot_df.columns and (len(y_cols) == 1 or is_highlight_color)
    color_label = color_col if color_col else "状态"

    # 横轴若选了内置 "__行号__"，转换为基于行号的整数坐标（0,1,2,…）
    if x_col == "__行号__":
        plot_df = plot_df.copy()
        plot_df["__行号__"] = range(len(plot_df))
        x_col = "__行号__"
    is_row_idx = (x_col == "__行号__")

    # 原始 UTC 无效高亮使用固定红色，正常点为蓝色
    color_discrete_map = None
    if use_color and is_highlight_color:
        color_discrete_map = {_HIGHLIGHT_RAW_INVALID_INFO["raw_invalid"]["label"]: _HIGHLIGHT_RAW_INVALID_INFO["raw_invalid"]["color"]}
        color_discrete_map.setdefault("正常", "#3B82F6")

    # 高亮多 Y 列：把数据 melt 后用线型/符号区分系列，颜色区分高亮状态
    if is_highlight_color and len(y_cols) > 1 and chart_type not in ["直方图 (Histogram)", "箱线图 (Box)"]:
        melted = plot_df.melt(id_vars=[x_col, color_col, "__来源文件__"], value_vars=y_cols,
                              var_name="系列", value_name="数值")
        if chart_type == "折线图 (Line)":
            fig = px.line(melted, x=x_col, y="数值", color=color_col, line_dash="系列",
                          title=chart_title, labels={x_col: x_label, "数值": y_label, color_col: color_label, "系列": "Y列"},
                          color_discrete_map=color_discrete_map)
        elif chart_type == "点线图 (Line+Marker)":
            fig = px.line(melted, x=x_col, y="数值", color=color_col, line_dash="系列",
                          title=chart_title, labels={x_col: x_label, "数值": y_label, color_col: color_label, "系列": "Y列"},
                          color_discrete_map=color_discrete_map)
            fig.update_traces(mode="lines+markers", marker=dict(size=8, line=dict(width=1, color="white")))
            fig.update_layout(hovermode="closest")
        elif chart_type == "散点图 (Scatter)":
            fig = px.scatter(melted, x=x_col, y="数值", color=color_col, symbol="系列",
                             title=chart_title, labels={x_col: x_label, "数值": y_label, color_col: color_label, "系列": "Y列"},
                             color_discrete_map=color_discrete_map, opacity=0.7)
        elif chart_type == "柱状图 (Bar)":
            fig = px.bar(melted, x=x_col, y="数值", color=color_col, pattern_shape="系列",
                         title=chart_title, labels={x_col: x_label, "数值": y_label, color_col: color_label, "系列": "Y列"},
                         color_discrete_map=color_discrete_map, barmode="group")
        elif chart_type == "面积图 (Area)":
            fig = px.area(melted, x=x_col, y="数值", color=color_col, line_dash="系列",
                          title=chart_title, labels={x_col: x_label, "数值": y_label, color_col: color_label, "系列": "Y列"},
                          color_discrete_map=color_discrete_map)
        else:
            fig = None
        if fig is not None:
            fig.update_layout(showlegend=False)
            return fig

    if chart_type == "折线图 (Line)":
        if use_color:
            fig = px.line(plot_df, x=x_col, y=y_cols[0], color=color_col,
                         title=chart_title, labels={x_col: x_label, y_cols[0]: y_label, color_col: color_label},
                         color_discrete_map=color_discrete_map)
        elif multi_mode and color_by_file and len(y_cols) == 1:
            fig = px.line(plot_df, x=x_col, y=y_cols[0], color="__来源文件__",
                         title=chart_title, labels={x_col: x_label, y_cols[0]: y_label, "__来源文件__": "来源"})
        else:
            fig = px.line(plot_df, x=x_col, y=y_cols, title=chart_title, labels={x_col: x_label})
            fig.update_yaxes(title_text=y_label)

    elif chart_type == "点线图 (Line+Marker)":
        if use_color:
            fig = px.line(plot_df, x=x_col, y=y_cols[0], color=color_col,
                         title=chart_title, labels={x_col: x_label, y_cols[0]: y_label, color_col: color_label},
                         color_discrete_map=color_discrete_map)
        elif multi_mode and color_by_file and len(y_cols) == 1:
            fig = px.line(plot_df, x=x_col, y=y_cols[0], color="__来源文件__",
                         title=chart_title, labels={x_col: x_label, y_cols[0]: y_label, "__来源文件__": "来源"})
        else:
            fig = px.line(plot_df, x=x_col, y=y_cols, title=chart_title, labels={x_col: x_label})
            fig.update_yaxes(title_text=y_label)
        fig.update_traces(mode="lines+markers", marker=dict(size=8, line=dict(width=1, color="white")))
        fig.update_layout(hovermode="closest")

    elif chart_type == "散点图 (Scatter)":
        if use_color:
            fig = px.scatter(plot_df, x=x_col, y=y_cols[0], color=color_col,
                            title=chart_title,
                            labels={x_col: x_label, y_cols[0]: y_label, color_col: color_label},
                            color_discrete_map=color_discrete_map, opacity=0.7)
        elif multi_mode and color_by_file and len(y_cols) == 1:
            fig = px.scatter(plot_df, x=x_col, y=y_cols[0], color="__来源文件__",
                            title=chart_title,
                            labels={x_col: x_label, y_cols[0]: y_label, "__来源文件__": "来源"},
                            opacity=0.7)
        elif len(y_cols) == 1:
            fig = px.scatter(plot_df, x=x_col, y=y_cols[0], title=chart_title,
                            labels={x_col: x_label, y_cols[0]: y_label}, opacity=0.7)
        else:
            fig = px.scatter(plot_df, x=x_col, y=y_cols, title=chart_title,
                            labels={x_col: x_label}, opacity=0.7)
            fig.update_yaxes(title_text=y_label)

    elif chart_type == "柱状图 (Bar)":
        if use_color:
            fig = px.bar(plot_df, x=x_col, y=y_cols[0], color=color_col,
                        title=chart_title, labels={x_col: x_label, y_cols[0]: y_label, color_col: color_label},
                        color_discrete_map=color_discrete_map, barmode="group")
        elif multi_mode and color_by_file and len(y_cols) == 1:
            fig = px.bar(plot_df, x=x_col, y=y_cols[0], color="__来源文件__",
                        title=chart_title, labels={x_col: x_label, y_cols[0]: y_label, "__来源文件__": "来源"},
                        barmode="group")
        else:
            fig = px.bar(plot_df, x=x_col, y=y_cols, title=chart_title, labels={x_col: x_label})
            fig.update_yaxes(title_text=y_label)

    elif chart_type == "面积图 (Area)":
        if use_color:
            fig = px.area(plot_df, x=x_col, y=y_cols[0], color=color_col,
                         title=chart_title, labels={x_col: x_label, y_cols[0]: y_label, color_col: color_label},
                         color_discrete_map=color_discrete_map)
        elif multi_mode and color_by_file and len(y_cols) == 1:
            fig = px.area(plot_df, x=x_col, y=y_cols[0], color="__来源文件__",
                         title=chart_title, labels={x_col: x_label, y_cols[0]: y_label, "__来源文件__": "来源"})
        else:
            fig = px.area(plot_df, x=x_col, y=y_cols, title=chart_title, labels={x_col: x_label})
            fig.update_yaxes(title_text=y_label)

    elif chart_type == "箱线图 (Box)":
        if len(y_cols) == 1:
            color_arg = color_col if use_color else ("__来源文件__" if multi_mode else None)
            fig = px.box(plot_df, x=x_col, y=y_cols[0], color=color_arg,
                        title=chart_title,
                        labels={x_col: x_label, y_cols[0]: y_label,
                                color_col: color_label, "__来源文件__": "来源"})
        else:
            melted = plot_df.melt(id_vars=[x_col, "__来源文件__"], value_vars=y_cols,
                                  var_name="系列", value_name="数值")
            fig = px.box(melted, x=x_col, y="数值", color="系列",
                        title=chart_title,
                        labels={x_col: x_label, "数值": y_label})

    elif chart_type == "直方图 (Histogram)":
        fig = go.Figure()
        for yc in y_cols:
            fig.add_trace(go.Histogram(x=plot_df[yc], name=str(yc), nbinsx=bins))
        fig.update_layout(title=chart_title, xaxis_title=x_label, yaxis_title=y_label, barmode="overlay")

    elif chart_type == "多Y轴独立子图 (Multi-Y Subplots)":
        # 每个 Y 列独立子图（垂直堆叠），各自独立 Y 轴；X 轴共享
        n = len(y_cols)
        if n < 2:
            fig = go.Figure()
            fig.add_annotation(text="多Y轴子图模式需要至少 2 个 Y 列", showarrow=False)
        else:
            fig = make_subplots(rows=n, cols=1, shared_xaxes=True, vertical_spacing=0.03,
                                subplot_titles=[str(yc) for yc in y_cols])
            markers = ["o","s","^","v","D","P","X","*"]
            for i, yc in enumerate(y_cols, start=1):
                col = plot_df[yc]
                valid = col.notna()
                fig.add_trace(go.Scatter(
                    x=plot_df[x_col], y=col, mode="lines+markers",
                    name=f"{yc}", marker=dict(symbol=markers[(i-1)%len(markers)], size=6),
                    line=dict(width=1.2), connectgaps=False,
                ), row=i, col=1)
                fig.update_yaxes(title_text=str(yc), row=i, col=1)
            fig.update_xaxes(title_text=x_label, row=n, col=1)
            fig.update_layout(title=chart_title, height=180*n + 80,
                              hoverlabel=dict(bgcolor="white", font_size=13),
                              legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                              showlegend=True)

    # 通用配置：悬停显示详细信息
    if chart_type != "直方图 (Histogram)":
        fig.update_layout(
            hoverlabel=dict(bgcolor="white", font_size=13),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        )
        for trace in fig.data:
            trace.update(
                hovertemplate=f"<b>{trace.name}</b><br>"
                             f"{x_label or x_col}: %{{x}}<br>"
                             f"{y_label or 'Y'}: %{{y}}<extra></extra>"
            )
        # 若 X 轴是字符串类型的 UTC 相关列，强制使用分类轴，避免 Plotly 按字符串排序导致回跳/插值后顺序混乱
        if x_col != "__原始顺序__":
            is_utc_name = str(x_col).lower() in ("utc_time", "nmea_utc_time")
            is_numeric = pd.api.types.is_numeric_dtype(plot_df[x_col])
            if _looks_like_raw_nmea_utc(plot_df[x_col]) or (is_utc_name and not is_numeric):
                fig.update_xaxes(type="category")

    # 高亮模式下隐藏图例，靠数据点颜色区分状态
    is_highlight_color = color_col == _HIGHLIGHT_RAW_INVALID_COL
    if is_highlight_color:
        fig.update_layout(showlegend=False)
    return fig


def _build_matplotlib_figure(plot_df, x_col, y_cols, chart_type, chart_title, x_label, y_label, color_by_file, multi_mode, bins, color_col=None):
    """使用 Matplotlib 构建单个图表。"""
    fig, ax = plt.subplots(figsize=(10, 5))
    use_color = bool(color_col) and color_col in plot_df.columns and len(y_cols) == 1

    if use_color and chart_type not in ["箱线图 (Box)", "直方图 (Histogram)"]:
        groups = list(plot_df.groupby(color_col))
        if chart_type in ["折线图 (Line)", "点线图 (Line+Marker)"]:
            marker_style = "o" if chart_type == "点线图 (Line+Marker)" else ""
            ms = 5 if chart_type == "点线图 (Line+Marker)" else 0
            for name, group in groups:
                ax.plot(group[x_col], group[y_cols[0]], label=str(name), marker=marker_style, markersize=ms, linewidth=1.5)
        elif chart_type == "散点图 (Scatter)":
            for name, group in groups:
                ax.scatter(group[x_col], group[y_cols[0]], label=str(name), alpha=0.6, s=40)
        elif chart_type == "柱状图 (Bar)":
            unique_x = sorted(plot_df[x_col].unique())
            x_map = {x: i for i, x in enumerate(unique_x)}
            width = 0.8 / len(groups)
            for gidx, (name, group) in enumerate(groups):
                offset = (gidx - len(groups) / 2 + 0.5) * width
                ax.bar([x_map[v] + offset for v in group[x_col]], group[y_cols[0]], width=width, label=str(name))
            ax.set_xticks(range(len(unique_x)))
            ax.set_xticklabels(unique_x, rotation=45, ha="right")
        elif chart_type == "面积图 (Area)":
            for name, group in groups:
                ax.fill_between(group[x_col], group[y_cols[0]], alpha=0.4, label=str(name))
                ax.plot(group[x_col], group[y_cols[0]], linewidth=1)
        if chart_type != "柱状图 (Bar)":
            ax.legend(title=color_col)

    elif multi_mode and color_by_file and len(y_cols) == 1:
        for file_name, group in plot_df.groupby("__来源文件__"):
            ax.plot(group[x_col], group[y_cols[0]], label=file_name, marker="o", markersize=5, linewidth=1.5)
        ax.legend(title="来源")
    elif chart_type in ["折线图 (Line)", "点线图 (Line+Marker)"]:
        marker_style = "o" if chart_type == "点线图 (Line+Marker)" else ""
        ms = 5 if chart_type == "点线图 (Line+Marker)" else 0
        for yc in y_cols:
            ax.plot(plot_df[x_col], plot_df[yc], label=str(yc), marker=marker_style, markersize=ms, linewidth=1.5)
        ax.legend()
    elif chart_type == "散点图 (Scatter)":
        for yc in y_cols:
            ax.scatter(plot_df[x_col], plot_df[yc], label=str(yc), alpha=0.6, s=40)
        ax.legend()
    elif chart_type == "柱状图 (Bar)":
        x_pos = range(len(plot_df))
        width = 0.8 / len(y_cols)
        for i, yc in enumerate(y_cols):
            offset = (i - len(y_cols)/2 + 0.5) * width
            ax.bar([p + offset for p in x_pos], plot_df[yc], width=width, label=str(yc))
        ax.set_xticks(x_pos)
        ax.set_xticklabels(plot_df[x_col], rotation=45, ha="right")
        ax.legend()
    elif chart_type == "面积图 (Area)":
        for yc in y_cols:
            ax.fill_between(plot_df[x_col], plot_df[yc], alpha=0.4, label=str(yc))
            ax.plot(plot_df[x_col], plot_df[yc], linewidth=1)
        ax.legend()
    elif chart_type == "箱线图 (Box)":
        ax.boxplot([plot_df[yc].dropna() for yc in y_cols], labels=[str(yc) for yc in y_cols])
    elif chart_type == "直方图 (Histogram)":
        for yc in y_cols:
            ax.hist(plot_df[yc].dropna(), bins=bins, alpha=0.5, label=str(yc))
        ax.legend()

    ax.set_title(chart_title)
    if chart_type != "柱状图 (Bar)":
        ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    if chart_type in ["箱线图 (Box)", "直方图 (Histogram)"]:
        ax.legend()

    # 高亮模式下隐藏图例（靠刻度颜色本身指示）
    if color_col == _HIGHLIGHT_RAW_INVALID_COL and ax.get_legend() is not None:
        ax.get_legend().set_visible(False)

    plt.tight_layout()

    return fig


def _build_combined_plotly_figure(file_plot_data, chart_type, chart_title, x_label, y_label, bins, color_col=None):
    """将多个文件（各自可能有不同的 X/Y 列）的曲线合并到一张 Plotly 图。"""
    fig = go.Figure()
    multi_files = len(file_plot_data) > 1

    highlight_color_map = None
    is_highlight_color = color_col == _HIGHLIGHT_RAW_INVALID_COL
    if is_highlight_color:
        highlight_color_map = {_HIGHLIGHT_RAW_INVALID_INFO["raw_invalid"]["label"]: _HIGHLIGHT_RAW_INVALID_INFO["raw_invalid"]["color"]}

    def _add_trace(group_df, trace_name, fx, yc, trace_color=None):
        marker_dict = dict(size=8, line=dict(width=1, color="white"))
        if trace_color:
            marker_dict["color"] = trace_color
        if chart_type == "折线图 (Line)":
            line_dict = dict(color=trace_color) if trace_color else None
            fig.add_trace(go.Scatter(x=group_df[fx], y=group_df[yc], mode="lines", name=trace_name, line=line_dict))
        elif chart_type == "点线图 (Line+Marker)":
            line_dict = dict(color=trace_color) if trace_color else None
            fig.add_trace(
                go.Scatter(
                    x=group_df[fx],
                    y=group_df[yc],
                    mode="lines+markers",
                    name=trace_name,
                    marker=marker_dict,
                    line=line_dict,
                )
            )
        elif chart_type == "散点图 (Scatter)":
            fig.add_trace(go.Scatter(x=group_df[fx], y=group_df[yc], mode="markers", name=trace_name, opacity=0.7, marker=marker_dict))
        elif chart_type == "柱状图 (Bar)":
            bar_kwargs = {}
            if trace_color:
                bar_kwargs["marker_color"] = trace_color
            fig.add_trace(go.Bar(x=group_df[fx], y=group_df[yc], name=trace_name, **bar_kwargs))
        elif chart_type == "面积图 (Area)":
            line_dict = dict(color=trace_color) if trace_color else None
            fig.add_trace(go.Scatter(x=group_df[fx], y=group_df[yc], mode="lines", name=trace_name, fill="tozeroy", line=line_dict))
        elif chart_type == "箱线图 (Box)":
            fig.add_trace(go.Box(x=group_df[fx], y=group_df[yc], name=trace_name))
        elif chart_type == "直方图 (Histogram)":
            fig.add_trace(go.Histogram(x=group_df[yc], name=trace_name, nbinsx=bins))

    for fname, fx, fys, file_df in file_plot_data:
        for yc in fys:
            base_name = f"{fname} - {yc}" if multi_files else str(yc)
            if color_col and color_col in file_df.columns:
                for group_name, group_df in file_df.groupby(color_col):
                    trace_color = highlight_color_map.get(str(group_name)) if highlight_color_map else None
                    _add_trace(group_df, f"{base_name} - {group_name}", fx, yc, trace_color=trace_color)
            else:
                _add_trace(file_df, base_name, fx, yc)

    if chart_type == "柱状图 (Bar)":
        fig.update_layout(barmode="group")
    elif chart_type == "直方图 (Histogram)":
        fig.update_layout(barmode="overlay")

    # 若 X 轴是字符串类型的 UTC 相关列，强制使用分类轴
    fx = file_plot_data[0][1] if file_plot_data else None
    if fx:
        fx_series = file_plot_data[0][3][fx]
        is_utc_name = str(fx).lower() in ("utc_time", "nmea_utc_time")
        is_numeric = pd.api.types.is_numeric_dtype(fx_series)
        if _looks_like_raw_nmea_utc(fx_series) or (is_utc_name and not is_numeric):
            fig.update_xaxes(type="category")

    fig.update_layout(
        title=chart_title,
        xaxis_title=x_label,
        yaxis_title=y_label,
        hoverlabel=dict(bgcolor="white", font_size=13),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    # 高亮模式下隐藏图例，靠数据点颜色区分状态
    if is_highlight_color:
        fig.update_layout(showlegend=False)
    return fig


def _build_combined_matplotlib_figure(file_plot_data, chart_type, chart_title, x_label, y_label, bins, color_col=None):
    """将多个文件（各自可能有不同的 X/Y 列）的曲线合并到一张 Matplotlib 图。"""
    fig, ax = plt.subplots(figsize=(10, 5))
    multi_files = len(file_plot_data) > 1
    box_data = []
    box_labels = []

    def _plot_group(group_df, label, fx, yc):
        if chart_type in ["折线图 (Line)", "点线图 (Line+Marker)"]:
            marker = "o" if chart_type == "点线图 (Line+Marker)" else ""
            ms = 5 if chart_type == "点线图 (Line+Marker)" else 0
            ax.plot(group_df[fx], group_df[yc], label=label, marker=marker, markersize=ms, linewidth=1.5)
        elif chart_type == "散点图 (Scatter)":
            ax.scatter(group_df[fx], group_df[yc], label=label, alpha=0.6, s=40)
        elif chart_type == "柱状图 (Bar)":
            ax.bar(group_df[fx], group_df[yc], label=label, alpha=0.7)
        elif chart_type == "面积图 (Area)":
            ax.fill_between(group_df[fx], group_df[yc], alpha=0.4, label=label)
            ax.plot(group_df[fx], group_df[yc], linewidth=1)
        elif chart_type == "箱线图 (Box)":
            box_data.append(group_df[yc].dropna())
            box_labels.append(label)
        elif chart_type == "直方图 (Histogram)":
            ax.hist(group_df[yc].dropna(), bins=bins, alpha=0.5, label=label)

    for fname, fx, fys, file_df in file_plot_data:
        for yc in fys:
            label = f"{fname} - {yc}" if multi_files else str(yc)
            if color_col and color_col in file_df.columns:
                for group_name, group_df in file_df.groupby(color_col):
                    _plot_group(group_df, f"{label} - {group_name}", fx, yc)
            else:
                _plot_group(file_df, label, fx, yc)

    if chart_type == "箱线图 (Box)" and box_data:
        ax.boxplot(box_data, labels=box_labels)

    ax.set_title(chart_title)
    ax.set_xlabel(x_label)
    ax.set_ylabel(y_label)
    if chart_type not in ["箱线图 (Box)"]:
        ax.legend()

    # 高亮模式下隐藏图例（靠刻度颜色本身指示）
    if color_col == _HIGHLIGHT_RAW_INVALID_COL and ax.get_legend() is not None:
        ax.get_legend().set_visible(False)

    plt.tight_layout()

    return fig


def _render_plotly(fig):
    """统一渲染 Plotly 图表：启用滚轮缩放、设置主题与交互。"""
    fig.update_layout(
        template="plotly_white",
        hovermode="x unified",
        dragmode="zoom",
        font=dict(size=12),
        margin=dict(l=60, r=40, t=80, b=60),
    )
    st.plotly_chart(
        fig,
        width="stretch",
        config={
            "scrollZoom": True,
            "displayModeBar": True,
            "displaylogo": False,
        },
    )


def _format_original_order_axis(fig, df, use_plotly=True, max_ticks=50):
    """当 X 轴为 __原始顺序__ 时，把刻度标签和 hover 替换为对应的 utc_time。

    max_ticks 限制显示的刻度数量，避免数据量过大时 Plotly 渲染卡死。
    """
    if df is None or "__原始顺序__" not in df.columns or "utc_time" not in df.columns:
        return
    order_to_utc = {}
    for k, v in zip(df["__原始顺序__"], df["utc_time"]):
        try:
            order_to_utc[int(k)] = str(v)
        except Exception:
            continue
    if not order_to_utc:
        return

    # 限制刻度数量，均匀采样，保证首尾一定有标签
    order_vals = sorted(order_to_utc.keys())
    n = len(order_vals)
    if n > max_ticks:
        step = max(1, n // max_ticks)
        selected = order_vals[::step]
        if order_vals[-1] not in selected:
            selected.append(order_vals[-1])
        order_vals = selected
    tick_labels = [order_to_utc[k] for k in order_vals]

    if use_plotly:
        fig.update_xaxes(
            tickmode="array",
            tickvals=order_vals,
            ticktext=tick_labels,
            title_text="UTC 时间（按原始顺序排列）",
        )
        for trace in fig.data:
            try:
                xs = list(trace.x)
                labels = [order_to_utc.get(int(x), str(x)) for x in xs]
                trace.customdata = [[label] for label in labels]
                trace.hovertemplate = (
                    f"<b>{trace.name}</b><br>"
                    "UTC: %{customdata[0]}<br>"
                    "顺序: %{x}<br>"
                    "Y: %{y}<extra></extra>"
                )
            except Exception:
                pass
    else:
        ax = fig.axes[0]
        ax.set_xticks(order_vals)
        ax.set_xticklabels(tick_labels, rotation=45, ha="right")
        ax.set_xlabel("UTC 时间（按原始顺序排列）")


def _ensure_numeric_y(df, y_cols):
    """把指定的 Y 列转换为数值类型，避免字符串被 Plotly/Matplotlib 当作分类轴导致 Y 轴方向异常。"""
    if df is None or df.empty:
        return df
    for yc in y_cols:
        if yc in df.columns and not pd.api.types.is_numeric_dtype(df[yc]):
            df[yc] = pd.to_numeric(df[yc], errors="coerce")
    return df


def _mask_invalid_utc_y(df, y_cols, invalid_col="utc_raw_invalid"):
    """把原始 UTC 无效的锚点行（GGA/RMC 自身）的 Y 值置为 NaN，避免在错误 UTC 时刻绘制。

    继承 UTC 的数据行不置空，以便按原始顺序绘图时仍能看到这些点并被高亮。
    """
    if df is None or df.empty or invalid_col not in df.columns:
        return df
    df = df.copy()
    mask = df[invalid_col].fillna(False).astype(bool)
    if not mask.any():
        return df
    # 仅对锚点行自身置空；没有锚点标记时兼容旧数据（全部不处理，避免误伤继承行）
    if "__utc_anchor_row__" in df.columns:
        mask = mask & df["__utc_anchor_row__"].fillna(False).astype(bool)
        if not mask.any():
            return df
    for yc in y_cols:
        if yc in df.columns:
            df.loc[mask, yc] = np.nan
    return df


def _render_plotly_figure(fig, df, x_col, plot_by_original_order, chart_type):
    if plot_by_original_order and x_col == "__原始顺序__" and chart_type not in ["直方图 (Histogram)", "箱线图 (Box)"]:
        _format_original_order_axis(fig, df, use_plotly=True)
    _render_plotly(fig)


def _show_matplotlib_figure(fig, df, x_col, plot_by_original_order, chart_type):
    if plot_by_original_order and x_col == "__原始顺序__" and chart_type not in ["直方图 (Histogram)", "箱线图 (Box)"]:
        _format_original_order_axis(fig, df, use_plotly=False)
    st.pyplot(fig)


# ==================== 侧边栏：文件上传与解析 ====================

st.sidebar.header("📁 文件上传")

uploaded_files = st.sidebar.file_uploader(
    "选择文件（可多选，用于对比）",
    type=["csv", "xlsx", "xls", "txt", "log", "tsv"],
    accept_multiple_files=True,
)

# session state 初始化
if "parsed_files" not in st.session_state:
    st.session_state.parsed_files = {}  # {filename: df}
if "last_uploaded_names" not in st.session_state:
    st.session_state.last_uploaded_names = []
if "file_configs" not in st.session_state:
    st.session_state.file_configs = {}  # {filename: {delimiter, has_header, skip_rows}}
if "column_transforms" not in st.session_state:
    st.session_state.column_transforms = []  # [{source_col, transform_type, new_col}]
if "use_per_file_config" not in st.session_state:
    st.session_state.use_per_file_config = False
if "per_file_plot_config" not in st.session_state:
    st.session_state.per_file_plot_config = {}  # {filename: {x_col, y_cols}}

# 检测上传文件列表变化
current_names = [f.name for f in uploaded_files] if uploaded_files else []
if current_names != st.session_state.last_uploaded_names:
    st.session_state.last_uploaded_names = current_names
    st.session_state.parsed_files = {}
    st.session_state.column_transforms = []  # 文件变化时清除列转换
    st.session_state.per_file_plot_config = {}  # 文件变化时清除每文件绘图配置
    # 保留已有文件的配置，删除不再上传文件的配置
    new_configs = {name: st.session_state.file_configs.get(name, {}) for name in current_names}
    # 重新上传同名文件时，清除语句类型缓存以便重新检测
    for cfg in new_configs.values():
        cfg.pop("detected_types", None)
    st.session_state.file_configs = new_configs

# 判断是否为多文件/多数据集模式
multi_mode = (len(uploaded_files) > 1 if uploaded_files else False) or len(st.session_state.parsed_files) > 1

# 文件解析
if uploaded_files:
    # 分类文件
    table_files = []
    text_files = []
    for f in uploaded_files:
        if f.name.lower().endswith((".csv", ".xlsx", ".xls", ".tsv")):
            table_files.append(f)
        elif f.name.lower().endswith((".txt", ".log")):
            text_files.append(f)

    # 表格文件自动解析
    for f in table_files:
        if f.name not in st.session_state.parsed_files:
            df_tmp = read_table_file(f.getvalue(), f.name)
            if df_tmp is not None:
                st.session_state.parsed_files[f.name] = df_tmp

    # 文本文件：逐个配置
    if text_files:
        st.sidebar.subheader("⚙️ 文本解析设置（每个文件独立配置）")
        st.sidebar.caption("不同文件可使用不同分隔符或语句解析")

        delimiter_options = {
            "逗号 ,": ",",
            "制表符 \\t": "\\t",
            "分号 ;": ";",
            "空格": " ",
            "多个空格 \\s+": "\\s+",
            "竖线 |": "|",
            "自定义": "custom",
        }

        for i, f in enumerate(text_files):
            fname = f.name
            # 初始化配置
            if fname not in st.session_state.file_configs or not st.session_state.file_configs[fname]:
                st.session_state.file_configs[fname] = {
                    "parse_mode": "generic",
                    "delimiter": ",",
                    "has_header": True,
                    "skip_rows": 0,
                    "carry_nmea_time": False,
                    "use_prefix_filter": False,
                    "prefix_filters": [],
                    "split_by_prefix": False,
                    "selected_types": [],
                    "carry_time": True,
                    "convert_latlon": True,
                    "use_default_headers": False,
                    "default_header_prefix": "col",
                    "delimiter_mode": "single",
                    "multi_delimiters": ", ",
                    "regex_delimiter": r"[,\s]+",
                    "nmea_utc_direction": "backward",
                    "nmea_utc_cross_day": True,
                    "nmea_utc_representation": "offset",
                }

            cfg = st.session_state.file_configs[fname]

            with st.sidebar.expander(f"📄 {fname}"):
                parse_mode = st.radio(
                    "解析模式",
                    ["generic", "sentence"],
                    format_func=lambda x: "通用文本解析" if x == "generic" else "语句解析 (NMEA-like)",
                    index=0 if cfg.get("parse_mode", "generic") == "generic" else 1,
                    key=f"parse_mode_{i}_{fname}",
                )
                cfg["parse_mode"] = parse_mode

                if parse_mode == "generic":
                    # 表头设置
                    header_mode = st.radio(
                        "表头设置",
                        ["file_header", "default_header"],
                        format_func=lambda x: "使用文件自带表头" if x == "file_header" else "使用默认列名",
                        index=0 if not cfg.get("use_default_headers", False) else 1,
                        key=f"header_mode_{i}_{fname}",
                    )
                    cfg["use_default_headers"] = (header_mode == "default_header")
                    if cfg["use_default_headers"]:
                        cfg["default_header_prefix"] = st.text_input(
                            "默认列名前缀",
                            value=cfg.get("default_header_prefix", "col"),
                            key=f"default_header_prefix_{i}_{fname}",
                        )

                    # 分隔符设置
                    delimiter_mode = st.radio(
                        "分隔符模式",
                        ["single", "multi", "regex"],
                        format_func=lambda x: {
                            "single": "单个分隔符",
                            "multi": "多个分隔符",
                            "regex": "正则表达式",
                        }[x],
                        index=["single", "multi", "regex"].index(cfg.get("delimiter_mode", "single")),
                        key=f"delimiter_mode_{i}_{fname}",
                    )
                    cfg["delimiter_mode"] = delimiter_mode

                    if delimiter_mode == "single":
                        selected_delim = st.selectbox(
                            "分隔符",
                            list(delimiter_options.keys()),
                            index=list(delimiter_options.values()).index(cfg["delimiter"]) if cfg["delimiter"] in delimiter_options.values() else 0,
                            key=f"delim_{i}_{fname}",
                        )
                        delim_val = delimiter_options[selected_delim]
                        if selected_delim == "自定义":
                            delim_val = st.text_input("自定义分隔符", value=cfg.get("delimiter", ","), key=f"custom_delim_{i}_{fname}")
                        cfg["delimiter"] = delim_val
                    elif delimiter_mode == "multi":
                        cfg["multi_delimiters"] = st.text_input(
                            "多个分隔符（每个字符都会被当作分隔符）",
                            value=cfg.get("multi_delimiters", ", "),
                            key=f"multi_delim_{i}_{fname}",
                            help="例如输入 ', ' 表示按逗号或空格拆分",
                        )
                    else:  # regex
                        cfg["regex_delimiter"] = st.text_input(
                            "正则分隔符",
                            value=cfg.get("regex_delimiter", r"[,\s]+"),
                            key=f"regex_delim_{i}_{fname}",
                            help="例如 [，,\\s]+ 表示按逗号或空白拆分",
                        )

                    cfg["has_header"] = st.checkbox("第一行为表头", value=cfg.get("has_header", True), key=f"has_header_{i}_{fname}", disabled=cfg["use_default_headers"])
                    cfg["skip_rows"] = st.number_input("跳过前 N 行", min_value=0, value=cfg.get("skip_rows", 0), step=1, key=f"skip_rows_{i}_{fname}")
                    cfg["carry_nmea_time"] = st.checkbox(
                        "从附近的 $xxGGA/$xxRMC 语句继承 UTC 时间",
                        value=cfg.get("carry_nmea_time", False),
                        key=f"carry_nmea_time_{i}_{fname}",
                        help="在通用分隔符模式下，若数据行本身没有时间字段，可自动附加最近一条 GGA/RMC 语句的 UTC 时间作为一列",
                    )

                    if cfg["carry_nmea_time"]:
                        direction_options = {
                            "backward": "从上方最近 GGA/RMC 继承",
                            "forward": "从下方最近 GGA/RMC 继承",
                            "nearest": "从双向最近 GGA/RMC 继承",
                        }
                        cfg["nmea_utc_direction"] = st.selectbox(
                            "UTC 继承方向",
                            list(direction_options.keys()),
                            format_func=lambda x: direction_options[x],
                            index=list(direction_options.keys()).index(cfg.get("nmea_utc_direction", "backward")),
                            key=f"nmea_utc_direction_{i}_{fname}",
                        )
                        cfg["nmea_utc_cross_day"] = st.checkbox(
                            "检测跨天 UTC",
                            value=cfg.get("nmea_utc_cross_day", True),
                            key=f"nmea_utc_cross_day_{i}_{fname}",
                        )
                        if cfg["nmea_utc_cross_day"]:
                            representation_options = {
                                "offset": "HH:MM:SS(+offset)",
                                "seconds": "累计秒数",
                            }
                            cfg["nmea_utc_representation"] = st.radio(
                                "跨天 UTC 输出格式",
                                list(representation_options.keys()),
                                format_func=lambda x: representation_options[x],
                                index=list(representation_options.keys()).index(cfg.get("nmea_utc_representation", "offset")),
                                key=f"nmea_utc_representation_{i}_{fname}",
                            )
                    cfg["use_prefix_filter"] = st.checkbox(
                        "启用自定义格式过滤",
                        value=cfg.get("use_prefix_filter", False),
                        key=f"use_prefix_filter_{i}_{fname}",
                        help="只保留以指定前缀开头的数据行，表头行始终保留",
                    )
                    if cfg["use_prefix_filter"]:
                        prefix_text = st.text_area(
                            "自定义前缀（每行一个）",
                            value="\n".join(cfg.get("prefix_filters", [])),
                            key=f"prefix_filters_text_{i}_{fname}",
                            help="例如：[corr]\\n[corr] UTC_GAL",
                        )
                        cfg["prefix_filters"] = [p.strip() for p in prefix_text.splitlines() if p.strip()]

                        # 只有存在多个有效前缀时才允许拆分
                        valid_prefixes = [p for p in cfg["prefix_filters"] if p]
                        cfg["split_by_prefix"] = st.checkbox(
                            "按前缀拆分为多个数据集（可分别绘图）",
                            value=cfg.get("split_by_prefix", False) and len(valid_prefixes) > 1,
                            key=f"split_by_prefix_{i}_{fname}",
                            disabled=(len(valid_prefixes) <= 1),
                            help="每个前缀生成一个独立数据集，像多文件一样分别或合并绘图",
                        )
                    else:
                        cfg["prefix_filters"] = []
                        cfg["split_by_prefix"] = False

                else:  # sentence mode
                    # 检测语句类型
                    detected_types = cfg.get("detected_types")
                    if detected_types is None:
                        detected_types = detect_sentence_types(f.getvalue())
                        cfg["detected_types"] = detected_types

                    if detected_types:
                        selected_types = st.multiselect(
                            "选择要提取的语句类型",
                            detected_types,
                            default=cfg.get("selected_types", detected_types[:1]),
                            key=f"selected_types_{i}_{fname}",
                        )
                        cfg["selected_types"] = selected_types
                    else:
                        st.warning("未检测到 $ 开头的语句，请检查文件格式")
                        cfg["selected_types"] = []

                    cfg["carry_time"] = st.checkbox(
                        "从 GGA/RMC 语句携带时间到无时间语句",
                        value=cfg.get("carry_time", True),
                        key=f"carry_time_{i}_{fname}",
                    )
                    cfg["convert_latlon"] = st.checkbox(
                        "转换经纬度为十进制度",
                        value=cfg.get("convert_latlon", True),
                        key=f"convert_latlon_{i}_{fname}",
                    )
        if st.sidebar.button("🔍 解析所有文本文件", key="parse_btn"):
            parse_progress = st.sidebar.progress(0, text="准备解析...")
            with st.spinner("正在解析文本文件，请稍候..."):
                text_fnames = [f.name for f in text_files]
                unique_prefixes = _make_unique_file_prefixes(text_fnames)
                for f in text_files:
                    fname = f.name
                    cfg = st.session_state.file_configs.get(fname, {})
                    if not cfg:
                        continue
                    parse_progress.progress(0, text=f"正在解析 {fname}...")
                    progress_cb = _make_progress_callback(parse_progress)
                    if cfg.get("parse_mode", "generic") == "sentence":
                        selected_types = cfg.get("selected_types", [])
                        if selected_types:
                            df_tmp = read_sentence_log(
                                f.getvalue(),
                                selected_types,
                                carry_time=cfg.get("carry_time", True),
                                convert_latlon=cfg.get("convert_latlon", True),
                                progress_callback=progress_cb,
                            )
                            if df_tmp is not None:
                                st.session_state.parsed_files[fname] = df_tmp
                    else:
                        result = read_text_file(
                            f.getvalue(),
                            cfg["delimiter"],
                            cfg["has_header"],
                            cfg["skip_rows"],
                            carry_nmea_time=cfg.get("carry_nmea_time", False),
                            prefix_filters=cfg.get("prefix_filters", []),
                            split_by_prefix=cfg.get("split_by_prefix", False),
                            use_default_headers=cfg.get("use_default_headers", False),
                            default_header_prefix=cfg.get("default_header_prefix", "col"),
                            delimiter_mode=cfg.get("delimiter_mode", "single"),
                            multi_delimiters=cfg.get("multi_delimiters", ", "),
                            regex_delimiter=cfg.get("regex_delimiter", r"[,\s]+"),
                            nmea_utc_direction=cfg.get("nmea_utc_direction", "backward"),
                            nmea_utc_cross_day=cfg.get("nmea_utc_cross_day", True),
                            nmea_utc_representation=cfg.get("nmea_utc_representation", "offset"),
                            filename=unique_prefixes.get(fname, fname),
                            progress_callback=progress_cb,
                        )
                        if isinstance(result, dict):
                            for prefix, df_p in result.items():
                                display_name = f"{fname} [{prefix}]"
                                st.session_state.parsed_files[display_name] = df_p
                        elif result is not None:
                            st.session_state.parsed_files[fname] = result
                parse_progress.progress(100, text="解析完成")

    # 显示已解析文件列表
    if st.session_state.parsed_files:
        st.sidebar.success(f"✅ 已解析 {len(st.session_state.parsed_files)} 个文件")
        with st.sidebar.expander("📋 已加载文件详情"):
            for name, df_tmp in st.session_state.parsed_files.items():
                st.caption(f"• **{name}**: {df_tmp.shape[0]} 行 × {df_tmp.shape[1]} 列")
    else:
        if text_files:
            st.sidebar.info('👆 请配置分隔符后点击 "解析所有文本文件"')


# ==================== 主界面：数据处理与绘图 ====================

parsed_files = st.session_state.parsed_files

if not parsed_files:
    st.info("👈 请在左侧上传文件开始绘图")

    st.markdown("""
    ### 支持的文件格式
    - **CSV** (`.csv`) — 逗号分隔
    - **Excel** (`.xlsx`, `.xls`) — 表格文件
    - **TSV** (`.tsv`) — 制表符分隔
    - **TXT / LOG** (`.txt`, `.log`) — 通用文本文件，可自定义分隔符
    - **NMEA-like LOG** (`.log`) — `$` 开头的语句日志，可选择语句类型并提取字段，支持从 GGA 携带时间

    ### 使用步骤
    1. **上传文件**：支持单文件或多文件（多文件用于对比）
    2. **解析文件**：表格文件自动解析；TXT/LOG 可配置通用分隔符或语句解析模式后点击解析
    3. **选择坐标轴**：选择 X 轴和 Y 轴列
    4. **配置图表**：选择图表类型（支持点线图）、设置标题标签、X轴范围对齐
    5. **导出结果**：支持下载数据和图表
    """)

    # 示例图表
    st.subheader("🎨 示例图表（点线图 + 多文件时间不对齐对比）")
    sample_df = pd.DataFrame({
        "时间": ["02:00", "02:10", "02:20", "02:30", "02:40", "02:50", "03:00",
                "02:15", "02:25", "02:35", "02:45"],
        "温度": [22, 23, 24, 25, 26, 25, 24, 21, 22, 23, 22],
        "来源": ["文件A（完整）"] * 7 + ["文件B（部分）"] * 4,
    })
    fig = px.line(sample_df, x="时间", y="温度", color="来源",
                  title="示例: 时间范围不同的多文件对比",
                  labels={"时间": "时间", "温度": "温度 (°C)"})
    fig.update_traces(mode="lines+markers", marker=dict(size=10))
    fig.update_layout(hovermode="closest")
    _render_plotly(fig)
    st.caption("💡 提示：即使两个文件的时间范围不同（如文件B只有部分时间段），也能绘制在同一图上对比。缺失时间段自然留空。")

else:
    # ---------- 合并数据 ----------
    all_dfs = []
    for name, df_tmp in parsed_files.items():
        df_copy = df_tmp.copy()
        df_copy["__来源文件__"] = name
        all_dfs.append(df_copy)

    if len(all_dfs) == 1:
        merged_df = all_dfs[0]
    else:
        all_columns = set()
        for d in all_dfs:
            all_columns.update(d.columns)
        all_columns = list(all_columns)

        aligned_dfs = []
        for d in all_dfs:
            for col in all_columns:
                if col not in d.columns:
                    d[col] = pd.NA
            aligned_dfs.append(d[all_columns])

        merged_df = pd.concat(aligned_dfs, ignore_index=True)

    # ---------- 数据预览 ----------
    total_rows = sum(df_tmp.shape[0] for df_tmp in parsed_files.values())
    total_cols = len([c for c in merged_df.columns if c != "__来源文件__"])

    if multi_mode:
        st.success(f"多文件模式: {len(parsed_files)} 个文件，共 {total_rows} 行 × {total_cols} 列")
    else:
        st.success(f"单文件模式: {total_rows} 行 × {total_cols} 列")

    with st.expander("🔍 合并数据预览 (前 20 行)", expanded=False):
        st.dataframe(merged_df.head(20))
        st.caption("💡 `__来源文件__` 列标识每行数据来自哪个文件")

    with st.expander("📁 各文件数据预览", expanded=False):
        file_preview_cols = st.columns(min(len(parsed_files), 3))
        for idx, (name, df_tmp) in enumerate(parsed_files.items()):
            with file_preview_cols[idx % 3]:
                st.markdown(f"**📄 {name}**")
                st.dataframe(df_tmp.head(10))
                st.caption(f"{df_tmp.shape[0]} 行 × {df_tmp.shape[1]} 列")

    with st.expander("📋 列信息", expanded=False):
        display_df = merged_df.drop(columns=["__来源文件__"], errors="ignore")
        col_info = pd.DataFrame({
            "列名": display_df.columns,
            "数据类型": [str(dt) for dt in display_df.dtypes.values],
            "非空数量": display_df.count().values,
            "空值数量": display_df.isnull().sum().values,
        })
        st.dataframe(col_info)

    with st.expander("🔗 文件-列对应关系", expanded=False):
        for name, df_tmp in parsed_files.items():
            file_cols = [c for c in df_tmp.columns if c != "__来源文件__"]
            st.markdown(f"**📄 {name}** 拥有的列:")
            st.text(", ".join(file_cols))
            st.caption(f"共 {len(file_cols)} 列")

    # ---------- 列格式转换 ----------
    st.divider()
    with st.expander("🔧 列格式转换 / 统一列名（把不同文件的同含义列合成一列）", expanded=False):
        st.markdown("""
        当不同文件的同一含义列格式或列名不一致时（如 GGA 文件有 `GNGGA_utc_time`，PQTMPVT 文件有 `PQTMPVT_field_2`），
        可以在这里转换格式，并把它们映射到同一个输出列名，这样就能作为同一个 X/Y 轴使用。

        示例：
        - 文件 1：源列 `GNGGA_utc_time` → 输出列 `utc_time`
        - 文件 4：源列 `PQTMPVT_field_2` → 转换方式选「NMEA UTC 时间格式化」→ 输出列填 `utc_time`
        - 绘图时 X 轴直接选 `utc_time` 即可同时显示 4 个文件的数据。
        """)

        transform_options = {
            "NMEA UTC 时间格式化 (HHMMSS[.SSS] → HH:MM:SS)": "nmea_utc",
            "重命名 / 统一列名（多文件同含义列合并）": "rename",
            "转为数值": "to_numeric",
            "去除前后空格": "strip",
        }

        all_source_cols = [c for c in merged_df.columns if c != "__来源文件__"]

        col_t1, col_t2 = st.columns(2)
        with col_t1:
            source_col = st.selectbox("选择源列", [""] + all_source_cols, key="transform_source_col")
        with col_t2:
            transform_type_label = st.selectbox("选择转换方式", list(transform_options.keys()), key="transform_type")
            transform_type = transform_options[transform_type_label]

        # 自动生成默认输出列名
        default_suffix_map = {
            "nmea_utc": "utc",
            "rename": "unified",
            "to_numeric": "num",
            "strip": "strip",
        }
        default_new_col = f"{source_col}_{default_suffix_map.get(transform_type, 'fmt')}" if source_col else ""
        if transform_type == "rename" and source_col:
            default_new_col = source_col

        new_col_input = st.text_input(
            "输出列名（可自定义，多个文件可填相同列名以合并）",
            value=default_new_col,
            key="transform_new_col",
        )

        add_transform = st.button("➕ 添加转换", key="add_transform")

        if add_transform and source_col and new_col_input:
            new_col = new_col_input.strip()
            st.session_state.column_transforms.append({
                "source_col": source_col,
                "transform_type": transform_type,
                "new_col": new_col,
            })

        # 显示并管理已添加的转换
        if st.session_state.column_transforms:
            st.markdown("---")
            st.subheader("已添加的转换")
            for idx, t in enumerate(st.session_state.column_transforms):
                c1, c2 = st.columns([6, 1])
                with c1:
                    st.caption(f"**{t['new_col']}** ← {t['source_col']} ({t['transform_type']})")
                with c2:
                    if st.button("🗑️ 删除", key=f"del_transform_{idx}"):
                        st.session_state.column_transforms.pop(idx)
                        st.rerun()

    # 应用列转换到 merged_df
    for t in st.session_state.column_transforms:
        src = t["source_col"]
        dst = t["new_col"]
        ttype = t["transform_type"]
        if src not in merged_df.columns:
            continue
        try:
            if ttype == "nmea_utc":
                transformed = merged_df[src].astype(str).apply(_format_nmea_utc)
            elif ttype == "to_numeric":
                transformed = pd.to_numeric(merged_df[src], errors="coerce")
            elif ttype == "strip":
                transformed = merged_df[src].astype(str).str.strip()
            elif ttype == "rename":
                transformed = merged_df[src]
            else:
                continue

            if dst in merged_df.columns:
                # 如果目标列已存在，用非空值合并（支持多文件映射到同一列）
                merged_df[dst] = merged_df[dst].combine_first(transformed)
            else:
                merged_df[dst] = transformed
        except Exception as e:
            st.error(f"转换列 {src} → {dst} 失败: {e}")

    # ---------- 绘图设置 ----------
    st.divider()
    st.header("📈 绘图设置")

    # 多文件图表布局选择放在更显眼的位置
    if multi_mode:
        chart_layout = st.radio(
            "📊 多文件图表布局",
            ["merged", "per_file"],
            format_func=lambda x: "合并到一张图（便于对比）" if x == "merged" else "每个文件单独绘图",
            index=0,
            key="chart_layout",
            horizontal=True,
        )
    else:
        chart_layout = "merged"

    # 按文件单独配置坐标轴
    if multi_mode:
        use_per_file_config = st.checkbox(
            "按文件单独配置坐标轴（每个文件可选不同的 X/Y 列）",
            value=st.session_state.use_per_file_config,
            key="use_per_file_config_checkbox",
        )
        st.session_state.use_per_file_config = use_per_file_config
    else:
        st.session_state.use_per_file_config = False
        use_per_file_config = False

    numeric_cols = merged_df.select_dtypes(include=["number"]).columns.tolist()
    numeric_cols = [c for c in numeric_cols if c not in ("__来源文件__", "__原始顺序__", "__utc_anchor_row__")]
    all_cols = ["__行号__"] + [c for c in merged_df.columns if c != "__来源文件__"]

    # 每文件绘图配置 UI
    per_file_configs = {}
    if use_per_file_config:
        with st.expander("⚙️ 每文件坐标轴配置", expanded=True):
            st.markdown("为每个文件选择对应的 X 轴和 Y 轴列。合并到一张图时，各文件会按各自的配置生成曲线。")
            for fname in parsed_files.keys():
                cfg = st.session_state.per_file_plot_config.get(fname, {})
                # 从 merged_df 中过滤出该文件实际有数据的列（含转换后的统一列）
                file_df_for_cols = merged_df[merged_df["__来源文件__"] == fname]
                file_cols = [
                    c for c in file_df_for_cols.columns
                    if c != "__来源文件__" and file_df_for_cols[c].notna().any()
                ]
                file_numeric = [c for c in file_cols if c in numeric_cols]

                st.markdown(f"**📄 {fname}**")
                c1, c2 = st.columns(2)
                with c1:
                    preferred_x = "utc_time" if "utc_time" in file_cols else (file_cols[0] if file_cols else None)
                    default_x = cfg.get("x_col", preferred_x)
                    default_x = default_x if default_x in file_cols else preferred_x
                    key_x = f"per_file_x_{fname}"
                    if key_x not in st.session_state:
                        st.session_state[key_x] = default_x
                    elif st.session_state[key_x] not in file_cols and file_cols:
                        st.session_state[key_x] = preferred_x
                    file_x = st.selectbox(
                        f"X 轴",
                        file_cols,
                        key=key_x,
                    )
                with c2:
                    default_y = cfg.get("y_cols", [file_numeric[0]] if file_numeric else [file_cols[0]] if file_cols else [])
                    default_y = [c for c in default_y if c in file_cols]
                    if not default_y and file_cols:
                        default_y = [file_cols[0]]
                    key_y = f"per_file_y_{fname}"
                    if key_y not in st.session_state:
                        st.session_state[key_y] = default_y
                    elif not all(v in file_cols for v in st.session_state[key_y]) and file_cols:
                        kept = [v for v in st.session_state[key_y] if v in file_cols]
                        st.session_state[key_y] = kept if kept else [file_cols[0]]
                    file_y = st.multiselect(
                        f"Y 轴（可多选）",
                        file_cols,
                        key=key_y,
                    )
                per_file_configs[fname] = {"x_col": file_x, "y_cols": file_y}
            st.session_state.per_file_plot_config = per_file_configs

    col1, col2, col3 = st.columns(3)

    with col1:
        st.subheader("坐标轴选择")

        # 按原始顺序绘图：用行号作横坐标，刻度显示 UTC，并把无效 UTC 点标红
        plot_by_original_order = st.checkbox(
            "按原始文件顺序绘制 X 轴（无效 UTC 标红）",
            value=st.session_state.get("plot_by_original_order", False),
            key="plot_by_original_order",
            help="X 轴按文件内原始顺序排列，横坐标刻度显示对应 UTC 时间；原始 UTC 无效的点用红色绘制。默认关闭，建议直接选“行号 (0,1,2,...)”作横坐标。",
        )

        if use_per_file_config:
            st.info("已启用「按文件单独配置坐标轴」，请在上方展开区为每个文件选择 Y 轴。")
            # 使用第一个文件的配置作为全局回退（用于标签等）
            first_file = list(parsed_files.keys())[0]
            x_col = per_file_configs[first_file]["x_col"] if per_file_configs else None
            y_cols = []
            if plot_by_original_order:
                x_col = "__原始顺序__"
        else:
            if plot_by_original_order:
                x_col = "__原始顺序__"
                st.info("X 轴已固定为原始文件顺序，取消上方勾选可自定义 X 轴。")
            else:
                # 默认第 0 项即"__行号__"，未选时按表的行号从 0 递增
                default_x_index = 0
                x_col = st.selectbox("选择 X 轴（横坐标），默认 = 行号 (0,1,2,...)", all_cols, index=default_x_index, key="x_col")

            default_y = [numeric_cols[0]] if numeric_cols else [all_cols[0] if all_cols else None]
            y_cols = st.multiselect(
                "选择 Y 轴（纵坐标，可多选）",
                all_cols,
                default=[c for c in default_y if c in all_cols],
                key="y_cols",
            )

            if not y_cols:
                st.warning("请至少选择一列作为 Y 轴")

        if use_per_file_config and plot_by_original_order and chart_layout == "merged":
            # 按文件单独配置 + 原始顺序：各文件顺序编号会重叠，合并到一张图无法正确显示 utc_time 刻度
            chart_layout = "per_file"
            st.info("按文件单独配置且使用原始顺序作为 X 轴时，已自动切换为“每个文件单独绘图”布局。")

        # 颜色列
        if plot_by_original_order and "utc_raw_invalid" in merged_df.columns:
            color_col = _HIGHLIGHT_RAW_INVALID_COL
            st.info("原始 UTC 无效（000000.000）点用红色显示。")
        else:
            color_options = ["(无)"] + all_cols
            if "color_col" not in st.session_state or st.session_state["color_col"] not in color_options:
                st.session_state["color_col"] = "(无)"
            color_col = st.selectbox(
                "颜色列（按分类着色）",
                color_options,
                key="color_col",
                help="选择一列按类别着色",
            )
            if color_col == "(无)":
                color_col = None

    with col2:
        st.subheader("图表类型")
        chart_type = st.selectbox(
            "选择图表类型",
            [
                "折线图 (Line)",
                "点线图 (Line+Marker)",
                "散点图 (Scatter)",
                "柱状图 (Bar)",
                "面积图 (Area)",
                "箱线图 (Box)",
                "直方图 (Histogram)",
                "多Y轴独立子图 (Multi-Y Subplots)",
            ],
            key="chart_type",
        )

        use_plotly = st.checkbox("使用 Plotly 交互式图表", value=True, key="use_plotly")

    with col3:
        st.subheader("图表配置")
        chart_title = st.text_input("图表标题", value="数据可视化", key="chart_title")
        x_label = st.text_input("X 轴标签", value=str(x_col) if x_col else "", key="x_label")
        if x_col == "__原始顺序__" and x_label in ("__原始顺序__", ""):
            x_label = "原始顺序"
        y_label = st.text_input("Y 轴标签", value=", ".join(str(c) for c in y_cols) if y_cols else "", key="y_label")

        bins = 30  # 默认值，仅在直方图中使用
        if chart_type == "直方图 (Histogram)":
            bins = st.number_input("分箱数", min_value=5, max_value=200, value=30, step=5, key="bins")

        # 多文件模式选项
        if multi_mode and chart_type not in ["直方图 (Histogram)", "箱线图 (Box)"]:
            color_by_file = st.checkbox("按文件分颜色", value=True, key="color_by_file")
        else:
            color_by_file = False

    # ---------- 数据过滤 & X轴范围对齐 ----------
    st.divider()
    with st.expander("🔧 高级: 数据过滤 & X轴范围对齐", expanded=False):
        # 选择要参与绘图的数据源
        if multi_mode:
            available_files = list(parsed_files.keys())
            selected_files = st.multiselect("选择参与绘制的文件", available_files, default=available_files, key="selected_files")
            if selected_files:
                merged_df = merged_df[merged_df["__来源文件__"].isin(selected_files)]

        # X 轴范围对齐（仅多文件模式）
        if multi_mode:
            st.markdown("---")
            st.subheader("📏 X 轴范围对齐")
            x_align_mode = st.radio(
                "范围模式",
                ["显示全部范围", "仅显示交集范围（所有文件共有的X值）", "自定义范围"],
                index=0,
                key="x_align_mode",
            )

            if x_align_mode == "仅显示交集范围（所有文件共有的X值）":
                # 计算所有文件X列的交集
                x_vals_per_file = {}
                for fname, df_tmp in parsed_files.items():
                    if fname in (selected_files if selected_files else available_files):
                        if x_col in df_tmp.columns:
                            x_vals_per_file[fname] = set(df_tmp[x_col].dropna().astype(str))
                if x_vals_per_file:
                    common_x = set.intersection(*x_vals_per_file.values())
                    if common_x:
                        merged_df = merged_df[merged_df[x_col].astype(str).isin(common_x)]
                        st.info(f"交集范围: {len(common_x)} 个共有 X 值")
                    else:
                        st.warning('两个文件没有完全相同的 X 值，交集为空。建议改用 "显示全部范围" 或 "自定义范围"')

            elif x_align_mode == "自定义范围":
                # 尝试判断X列是否为数值/时间类型
                x_series = merged_df[x_col].dropna()
                try:
                    # 尝试转为数值
                    x_numeric = pd.to_numeric(x_series, errors="coerce")
                    if x_numeric.notna().all():
                        min_val = float(x_numeric.min())
                        max_val = float(x_numeric.max())
                        range_val = st.slider("X 轴范围", min_val, max_val, (min_val, max_val), key="x_custom_range")
                        merged_df = merged_df[pd.to_numeric(merged_df[x_col], errors="coerce").between(range_val[0], range_val[1])]
                    else:
                        # 尝试转为时间
                        x_time = pd.to_datetime(x_series, errors="coerce")
                        if x_time.notna().all():
                            min_t = x_time.min()
                            max_t = x_time.max()
                            start_t = st.datetime_input("开始时间", value=min_t, key="x_start_t")
                            end_t = st.datetime_input("结束时间", value=max_t, key="x_end_t")
                            merged_df = merged_df[pd.to_datetime(merged_df[x_col], errors="coerce").between(start_t, end_t)]
                        else:
                            # 字符串类型：提供多选
                            unique_x = sorted(x_series.unique())
                            selected_x = st.multiselect("选择要显示的 X 值", unique_x, default=unique_x, key="x_custom_str")
                            if selected_x:
                                merged_df = merged_df[merged_df[x_col].isin(selected_x)]
                except Exception:
                    # 兜底：字符串多选
                    unique_x = sorted(x_series.unique())
                    selected_x = st.multiselect("选择要显示的 X 值", unique_x, default=unique_x, key="x_custom_str2")
                    if selected_x:
                        merged_df = merged_df[merged_df[x_col].isin(selected_x)]

        # 通用列过滤
        st.markdown("---")
        st.subheader("🔍 列值过滤")
        filter_col = st.selectbox("选择过滤列", ["无"] + all_cols, key="filter_col")
        if filter_col != "无":
            unique_vals = merged_df[filter_col].dropna().unique()
            if len(unique_vals) <= 50:
                selected_vals = st.multiselect("选择保留的值", unique_vals, default=unique_vals[: min(5, len(unique_vals))], key="filter_vals")
                if selected_vals:
                    merged_df = merged_df[merged_df[filter_col].isin(selected_vals)]
            else:
                if pd.api.types.is_numeric_dtype(merged_df[filter_col]):
                    min_val = float(merged_df[filter_col].min())
                    max_val = float(merged_df[filter_col].max())
                    range_val = st.slider("范围", min_val, max_val, (min_val, max_val), key="filter_range")
                    merged_df = merged_df[(merged_df[filter_col] >= range_val[0]) & (merged_df[filter_col] <= range_val[1])]
                else:
                    filter_text = st.text_input("包含文本", key="filter_text")
                    if filter_text:
                        merged_df = merged_df[merged_df[filter_col].astype(str).str.contains(filter_text, na=False)]

    # ---------- 绘图 ----------
    st.divider()
    st.header("📉 图表")

    figures = []  # [(label, figure), ...]
    plot_df = None
    file_plot_data = []

    if use_per_file_config and per_file_configs:
        # 按文件单独配置模式：每个文件使用自己的 X/Y
        skipped_files = []
        for fname, cfg in per_file_configs.items():
            fx = cfg.get("x_col")
            fys = [c for c in cfg.get("y_cols", []) if c]
            if not fx or not fys:
                skipped_files.append((fname, "未配置 X 轴或 Y 轴"))
                continue
            file_df = merged_df[merged_df["__来源文件__"] == fname].copy()
            if file_df.empty:
                skipped_files.append((fname, "该文件在合并数据中没有行（可能被高级过滤排除）"))
                continue
            if plot_by_original_order:
                if "__原始顺序__" in file_df.columns:
                    fx = "__原始顺序__"
                if "utc_raw_invalid" in file_df.columns:
                    file_df = _add_raw_invalid_highlight_column(file_df)

            needed_cols = [c for c in ([fx] + fys + ["__来源文件__"] + ([color_col] if color_col else [])) if c in file_df.columns]
            if "utc_time" in file_df.columns and "utc_time" not in needed_cols:
                needed_cols.append("utc_time")
            if "utc_raw_invalid" in file_df.columns and "utc_raw_invalid" not in needed_cols:
                needed_cols.append("utc_raw_invalid")
            if "__utc_anchor_row__" in file_df.columns and "__utc_anchor_row__" not in needed_cols:
                needed_cols.append("__utc_anchor_row__")
            if fx not in needed_cols:
                skipped_files.append((fname, f"X 轴列 {fx} 不存在于该文件数据中"))
                continue
            file_df = file_df[needed_cols].dropna(subset=[fx])
            file_df = _ensure_numeric_y(file_df, fys)
            file_df = _mask_invalid_utc_y(file_df, fys)
            if file_df.empty:
                skipped_files.append((fname, f"X 轴列 {fx} 在该文件中全部为空"))
                continue
            if fx != "__原始顺序__" and _looks_like_raw_nmea_utc(file_df[fx]):
                file_df[fx] = file_df[fx].apply(_format_nmea_utc_raw).astype("string")
            file_plot_data.append((fname, fx, fys, file_df))

        if skipped_files:
            for fname, reason in skipped_files:
                st.warning(f"📄 {fname} 未绘制：{reason}")

        if not file_plot_data:
            st.warning("没有可用的文件绘图配置，请为文件选择 X/Y 轴")
        else:
            try:
                if chart_layout == "per_file":
                    for fname, fx, fys, file_df in file_plot_data:
                        file_title = f"{chart_title} - {fname}"
                        st.subheader(f"📄 {fname}")
                        is_original = fx == "__原始顺序__"
                        if use_plotly:
                            fig = _build_plotly_figure(file_df, fx, fys, chart_type, file_title, x_label, y_label, False, False, bins, color_col=color_col)
                            _render_plotly_figure(fig, file_df, fx, is_original, chart_type)
                        else:
                            fig = _build_matplotlib_figure(file_df, fx, fys, chart_type, file_title, x_label, y_label, False, False, bins, color_col=color_col)
                            _show_matplotlib_figure(fig, file_df, fx, is_original, chart_type)
                        figures.append((fname, fig))
                        st.caption("💡 鼠标**悬停**在数据点上即可查看横纵坐标值；点击图例可隐藏/显示某条线；拖拽可框选缩放")
                        st.divider()
                else:
                    if use_plotly:
                        fig = _build_combined_plotly_figure(file_plot_data, chart_type, chart_title, x_label, y_label, bins, color_col=color_col)
                        _render_plotly_figure(fig, None, None, plot_by_original_order, chart_type)
                        st.caption("💡 鼠标**悬停**在数据点上即可查看横纵坐标值；点击图例可隐藏/显示某条线；拖拽可框选缩放")
                    else:
                        fig = _build_combined_matplotlib_figure(file_plot_data, chart_type, chart_title, x_label, y_label, bins, color_col=color_col)
                        _show_matplotlib_figure(fig, None, None, plot_by_original_order, chart_type)
                    figures.append(("merged", fig))
            except Exception as e:
                st.error(f"绘图失败: {e}")
                import traceback
                st.code(traceback.format_exc())
    else:
        # 全局配置模式
        if y_cols and x_col:
            # 高亮列 _highlight_raw_invalid_utc 在 _add_raw_invalid_highlight_column 之后才存在，
            # 不要提前从 merged_df 中选取，否则会 KeyError。
            color_cols_for_selection = [color_col] if color_col and color_col != _HIGHLIGHT_RAW_INVALID_COL else []
            cols_to_plot = list(dict.fromkeys([x_col] + y_cols + ["__来源文件__"] + color_cols_for_selection))
            if "utc_time" in merged_df.columns and "utc_time" not in cols_to_plot:
                cols_to_plot.append("utc_time")
            if "utc_raw_invalid" in merged_df.columns and "utc_raw_invalid" not in cols_to_plot:
                cols_to_plot.append("utc_raw_invalid")
            if "__utc_anchor_row__" in merged_df.columns and "__utc_anchor_row__" not in cols_to_plot:
                cols_to_plot.append("__utc_anchor_row__")
            plot_df = merged_df[cols_to_plot].copy()
            if color_col == _HIGHLIGHT_RAW_INVALID_COL and "utc_raw_invalid" in plot_df.columns:
                plot_df = _add_raw_invalid_highlight_column(plot_df)
            # 只按 X 轴删除空值，避免多 Y 列来自不同文件时全部被 drop
            plot_df = plot_df.dropna(subset=[x_col])
            # 若 X 轴是原始 NMEA UTC（如 083253.000），保留字符串刻度，避免 Plotly 显示为 83.253k
            if x_col != "__原始顺序__" and _looks_like_raw_nmea_utc(plot_df[x_col]):
                plot_df[x_col] = plot_df[x_col].apply(_format_nmea_utc_raw).astype("string")

            # 确保 Y 轴为数值类型，避免字符串被当成分类轴导致上下颠倒
            plot_df = _ensure_numeric_y(plot_df, y_cols)
            # 原始 UTC 无效（000000.000）对应的点置空，不在错误 UTC 时刻绘制
            plot_df = _mask_invalid_utc_y(plot_df, y_cols)

            if plot_df.empty:
                st.warning("过滤后数据为空，无法绘图")
            else:
                try:
                    if chart_layout == "per_file" and multi_mode:
                        file_names = sorted(plot_df["__来源文件__"].unique())
                        for file_name in file_names:
                            file_df = plot_df[plot_df["__来源文件__"] == file_name].copy()
                            if file_df.empty:
                                continue
                            # 每文件单独检查并格式化 UTC，避免全局检测失败导致单文件出现 K 单位
                            if _looks_like_raw_nmea_utc(file_df[x_col]):
                                file_df[x_col] = file_df[x_col].apply(_format_nmea_utc_raw).astype("string")
                            file_df = _ensure_numeric_y(file_df, y_cols)
                            file_df = _mask_invalid_utc_y(file_df, y_cols)
                            file_title = f"{chart_title} - {file_name}"
                            st.subheader(f"📄 {file_name}")
                            if use_plotly:
                                fig = _build_plotly_figure(file_df, x_col, y_cols, chart_type, file_title, x_label, y_label, False, False, bins, color_col=color_col)
                                _render_plotly_figure(fig, file_df, x_col, plot_by_original_order, chart_type)
                            else:
                                fig = _build_matplotlib_figure(file_df, x_col, y_cols, chart_type, file_title, x_label, y_label, False, False, bins, color_col=color_col)
                                _show_matplotlib_figure(fig, file_df, x_col, plot_by_original_order, chart_type)
                            figures.append((file_name, fig))
                            st.caption("💡 鼠标**悬停**在数据点上即可查看横纵坐标值；点击图例可隐藏/显示某条线；拖拽可框选缩放")
                            st.divider()
                    else:
                        if use_plotly:
                            fig = _build_plotly_figure(plot_df, x_col, y_cols, chart_type, chart_title, x_label, y_label, color_by_file, multi_mode, bins, color_col=color_col)
                            _render_plotly_figure(fig, plot_df, x_col, plot_by_original_order, chart_type)
                            st.caption("💡 鼠标**悬停**在数据点上即可查看横纵坐标值；点击图例可隐藏/显示某条线；拖拽可框选缩放")
                        else:
                            fig = _build_matplotlib_figure(plot_df, x_col, y_cols, chart_type, chart_title, x_label, y_label, color_by_file, multi_mode, bins, color_col=color_col)
                            _show_matplotlib_figure(fig, plot_df, x_col, plot_by_original_order, chart_type)
                        figures.append(("merged", fig))
                except Exception as e:
                    st.error(f"绘图失败: {e}")
                    import traceback
                    st.code(traceback.format_exc())
        else:
            if not y_cols:
                st.info("请在上方选择 Y 轴列以开始绘图")

    # ---------- 数据导出 ----------
    if figures:
        st.divider()
        st.header("💾 导出")
        col_a, col_b = st.columns(2)

        if use_per_file_config and file_plot_data:
            export_df = pd.concat([fd[3] for fd in file_plot_data], ignore_index=True)
        elif plot_df is not None:
            export_df = plot_df.copy()
        else:
            export_df = pd.DataFrame()
        export_df = export_df.drop(columns=["__来源文件__", _HIGHLIGHT_RAW_INVALID_COL, "__utc_anchor_row__"], errors="ignore")

        with col_a:
            csv = export_df.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                label="下载当前数据为 CSV",
                data=csv,
                file_name="plot_data.csv",
                mime="text/csv",
            )

        with col_b:
            if not use_plotly:
                if len(figures) == 1:
                    buf = BytesIO()
                    figures[0][1].savefig(buf, format="png", dpi=150, bbox_inches="tight")
                    buf.seek(0)
                    st.download_button(
                        label="下载图表为 PNG",
                        data=buf,
                        file_name="chart.png",
                        mime="image/png",
                    )
                else:
                    st.info("分文件模式下请使用各图表右上角的保存功能")
            else:
                st.info("Plotly 图表可点击右上角相机图标保存为 PNG/SVG")
