#!/usr/bin/env python3
"""RTK 3D Point Cloud Viewer — generates a self-contained offline HTML file.

Reads bag_*.txt or rosbag2 directories directly and renders them in 3D with
isometric projection. Supports rotate/pan/zoom and elevation comparison.
Zero external dependencies beyond Python3 (ROS2 only needed with --use-ros2).
"""

import argparse
import bisect
import json
import math
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import webbrowser
from datetime import datetime
from pathlib import Path

POS_TYPE_MAP = {
    0: "NONE", 8: "NARROW_FLOAT", 16: "SINGLE", 17: "PSRDIFF",
    18: "WAAS", 20: "PROPAGATED", 32: "L1_FLOAT", 33: "IONOFREE_FLOAT",
    34: "NARROW_FLOAT", 48: "L1_INT", 49: "WIDE_LANE", 50: "NARROW_INT",
}

PALETTE = ["#e94560", "#4ecca3", "#f0a500", "#5599dd",
           "#dd77cc", "#88cc44", "#ff8844", "#44ccdd", "#ccdd44", "#aa66ff"]

POS_COLORS = {
    "NONE": "#666",
    "SINGLE": "#e94560", "PSRDIFF": "#4da3ff", "WAAS": "#e94560", "PROPAGATED": "#e94560",
    "FIXEDPOS": "#e94560", "FIXEDHEIGHT": "#e94560", "DOPPLER_VELOCITY": "#e94560",
    "L1_FLOAT": "#f0a500", "IONOFREE_FLOAT": "#f0a500", "NARROW_FLOAT": "#f0a500",
    "L1_INT": "#4ecca3", "WIDE_LANE": "#4ecca3", "NARROW_INT": "#4ecca3",
}

RTK_TOPICS = ("/rtk_pvh", "/beitian_rtk_pvh")
PING_LATENCY_TOPIC = "/ping_latency_ms"
CDR_PAYLOAD_OFFSET = 4
MAX_ABS_ALTITUDE_M = 100_000
MAX_STD_M = 10_000
MAX_PING_LATENCY_MS = 1_000_000
PING_RTK_MATCH_LIMIT_S = 1.0


def _sanitize(obj):
    """Replace NaN/Infinity with None for JSON compliance."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def load_txt(filepath, name):
    pts = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 7:
                continue
            idx = int(parts[0])
            try:
                time_s = float(parts[1])
            except ValueError:
                time_s = idx * 0.1
            lat = float(parts[2])
            lon = float(parts[3])
            alt = float(parts[4])
            pos_label = parts[6]
            lat_std = float(parts[7]) if len(parts) > 7 else -1
            lon_std = float(parts[8]) if len(parts) > 8 else -1
            hgt_std = float(parts[9]) if len(parts) > 9 else -1
            diff_age_s = float(parts[10]) if len(parts) > 10 else None
            pts.append({"idx": idx, "lat": lat, "lon": lon, "alt": alt,
                        "pos_label": pos_label, "name": name,
                        "lat_std": lat_std, "lon_std": lon_std, "hgt_std": hgt_std,
                        "diff_age_s": diff_age_s, "time_s": time_s})
    return pts


# ---------------------------------------------------------------------------
# NMEA 支持：直接读 $GPGGA / $GPRMC 日志，度分 → 十进制度，GGA quality → 定位类型
# ---------------------------------------------------------------------------

# NMEA GGA 定位质量 → POS_COLORS 键
NMEA_QUALITY_LABEL = {
    "0": "NONE",
    "1": "SINGLE",
    "2": "PSRDIFF",
    "3": "WAAS",
    "4": "NARROW_INT",    # RTK 固定解
    "5": "NARROW_FLOAT",  # RTK 浮点解
    "6": "SINGLE",        # 推算（近似单点）
    "7": "SINGLE",
    "8": "SINGLE",
}


def _nmea_dm_to_dd(part: str, hemi: str) -> float:
    """NMEA 度分 'ddmm.mmmmm' / 'dddmm.mmmmm' + N/S/E/W → 十进制度。"""
    if not part or not hemi:
        raise ValueError("empty NMEA lat/lon")
    dot = part.find(".")
    if dot < 0:
        deg_s = part[:-2] if len(part) > 2 else "0"
        min_s = part[-2:] if len(part) > 2 else "0"
        minutes = float(min_s)
    else:
        deg_s = part[: dot - 2]
        min_s = part[dot - 2:]
        minutes = float(min_s)
    degrees = float(deg_s)
    dd = degrees + minutes / 60.0
    if hemi in ("S", "W"):
        dd = -dd
    return dd


def _nmea_time_to_seconds(hhmmss: str) -> float:
    """'hhmmss(.ss)' → 当日秒。"""
    t = hhmmss.replace(",", ".")
    parts = t.split(".")
    hms = parts[0]
    h, m, s = int(hms[0:2]), int(hms[2:4]), int(hms[4:6])
    sec = h * 3600 + m * 60 + s
    if len(parts) > 1:
        sec += float("0." + parts[1])
    return sec


def _looks_like_nmea_file(filepath) -> bool:
    """快速判断文本是否为 NMEA 日志（含 $xxGGA / $xxRMC 语句）。"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            for _ in range(200):
                line = f.readline()
                if not line:
                    break
                if "$" in line and ("GGA" in line or "RMC" in line):
                    return True
    except OSError:
        pass
    return False


def load_nmea(filepath, name, ptype="solo"):
    """解析 NMEA（$xxGGA 优先，含高程；$xxRMC 兜底）→ 与 load_txt 同结构的点列表。

    以字节流方式扫描：兼容「二进制容器内嵌 NMEA」的日志（如 RTK 接收机 .bin 文件，
    噪声字节与 $xxRMC/$xxGGA 文本混排在同一行）——只要字节流中出现 $ 语句即可识别。

    增强字段：
    - ptype: "truth" / "test" / "solo"（真值/测试/单独），供双轨迹误差对比
    - date / time_str: 来自 RMC 日期 + GGA 时间
    - confidence: RMC 第 14 个字段（索引 13，用户定制字段），范围 0~9
    - speed: RMC 第 8 个字段（索引 7，对地速度，节）→ 换算为 m/s
    - sats: 关联到点的 GSV 卫星快照（系统/PRN/仰角/方位/CN0）
    """
    # 句子 = $ + 2~3 字母系统前缀 + 3 字母语句名 + 字段（遇到 $ / 换行 即止），
    # 可带可选校验和尾巴 *XX（用于区分真实 NMEA 与二进制噪声）
    _sentence_re = re.compile(rb"\$[A-Za-z]{2,3}[A-Za-z]{3}[^*\r\n$]*(?:\*[0-9A-Fa-f]{2})?")
    # 严格校验和仅用于「二进制容器内嵌 NMEA」：可打印文本占比 <90% 判定为二进制，
    # 此时带 *XX 的语句若校验不一致即噪声丢弃；纯文本 NMEA 宽松接受（校验和缺失/错误不拒）。
    _strict_ck = False
    try:
        with open(filepath, "rb") as _f0:
            _head = _f0.read(65536)
        if _head:
            _printable = sum(1 for b in _head if 9 <= b <= 13 or 32 <= b <= 126)
            _strict_ck = (_printable / len(_head)) < 0.9
    except OSError:
        pass
    pts = []
    idx = 0
    last_sec = 0.0
    last_date = ""
    last_pt_idx = -1  # 最近产生的点（GSV 归属到它）
    pending_sats = []  # 正在聚合的 GSV 卫星（跨系统/分句累积到同一时刻）

    def _flush_gsv():
        """把累积的 GSV 卫星挂到最近的点，并清空。"""
        nonlocal pending_sats
        if pending_sats and 0 <= last_pt_idx < len(pts):
            pts[last_pt_idx]["sats"] = list(pending_sats)
        pending_sats = []

    with open(filepath, "rb") as f:
        for raw in f:
            for m in _sentence_re.finditer(raw):
                line = m.group(0).decode("ascii", "ignore")
                # 校验和验证（仅二进制容器严格模式）：带 *XX 的句子若校验不一致 → 噪声跳过
                if _strict_ck and "*" in line:
                    body, _, ck = line.partition("*")
                    ck = ck.strip()[:2]
                    if len(ck) == 2:
                        try:
                            calc = 0
                            for ch in body[1:].encode("ascii", "ignore"):
                                calc ^= ch
                            if int(ck, 16) != calc:
                                continue
                        except ValueError:
                            continue
                fields = line.split(",")
                if not fields or len(fields) < 2:
                    continue
                stmt = fields[0]
                if stmt.endswith("GGA") and len(fields) >= 10:
                    # 新时刻开始：上一时刻的 GSV 挂到最近的点
                    _flush_gsv()
                    try:
                        time_s = _nmea_time_to_seconds(fields[1])
                        lat = _nmea_dm_to_dd(fields[2], fields[3])
                        lon = _nmea_dm_to_dd(fields[4], fields[5])
                        q = fields[6]
                        alt = float(fields[9]) if fields[9] else 0.0
                        if len(fields) > 11 and fields[11]:
                            alt += float(fields[11])
                        label = NMEA_QUALITY_LABEL.get(q, "SINGLE")
                    except (ValueError, IndexError):
                        continue
                    if abs(lat) > 90 or abs(lon) > 180 or abs(alt) > MAX_ABS_ALTITUDE_M:
                        continue
                    idx += 1
                    last_sec = time_s
                    last_pt_idx = len(pts)
                    pts.append({"idx": idx, "lat": lat, "lon": lon, "alt": alt,
                                "pos_label": label, "name": name, "type": ptype,
                                "lat_std": -1, "lon_std": -1, "hgt_std": -1,
                                "diff_age_s": None, "time_s": time_s,
                                "time_str": fields[1], "date": last_date,
                                "confidence": None, "speed": None, "sats": []})
                elif stmt.endswith("RMC") and len(fields) >= 7:
                    # 新时刻开始：上一时刻的 GSV 挂到最近的点
                    _flush_gsv()
                    try:
                        if fields[2] != "A":
                            continue
                        time_s = _nmea_time_to_seconds(fields[1])
                        lat = _nmea_dm_to_dd(fields[3], fields[4])
                        lon = _nmea_dm_to_dd(fields[5], fields[6])
                        # 速度：标准 RMC 第 8 个字段（节）→ m/s
                        speed = None
                        if len(fields) > 7 and fields[7]:
                            try:
                                speed = float(fields[7]) * 0.514444
                            except ValueError:
                                speed = None
                        # 日期：ddmmyy（标准 RMC 第 10 个字段）
                        if len(fields) > 9 and len(fields[9]) == 6 and fields[9].isdigit():
                            last_date = (f"20{fields[9][4:6]}-{fields[9][2:4]}-{fields[9][0:2]}")
                    except (ValueError, IndexError):
                        continue
                    if abs(lat) > 90 or abs(lon) > 180:
                        continue
                    # 置信度：RMC 第 14 个字段（索引 13，用户定制，范围 0~9）；
                    # 该字段可能带校验和尾巴（如 "4*13"），先剥掉 * 之后的部分
                    confidence = None
                    if len(fields) > 13 and fields[13]:
                        cand = fields[13].split("*")[0]
                        try:
                            confidence = float(cand)
                        except ValueError:
                            confidence = None
                    idx += 1
                    last_sec = time_s
                    last_pt_idx = len(pts)
                    pts.append({"idx": idx, "lat": lat, "lon": lon, "alt": 0.0,
                                "pos_label": "SINGLE", "name": name, "type": ptype,
                                "lat_std": -1, "lon_std": -1, "hgt_std": -1,
                                "diff_age_s": None, "time_s": time_s,
                                "time_str": fields[1], "date": last_date,
                                "confidence": confidence, "speed": speed, "sats": []})
                elif stmt.endswith("GSV") and len(fields) >= 7:
                    # $xxGSV,总句数,句号,在视星数,prn,elev,azim,cn0[,prn,elev,azim,cn0...]
                    # 同一时刻多系统/多分句的卫星全部累积合并（下次 GGA/RMC 时挂到最近的点）
                    try:
                        sv_count = int(fields[3])
                    except ValueError:
                        continue
                    sys_name = stmt[1:3]  # GP/GL/GA/GB/QZ
                    sys_label = {"GP": "GPS", "GL": "GLO", "GA": "GAL",
                                 "GB": "BDS", "GQ": "QZSS", "GI": "IRNSS"}.get(sys_name, sys_name)
                    for k in range(4):
                        base = 4 + k * 4
                        if base + 3 >= len(fields):
                            break
                        prn = fields[base]
                        elev = fields[base + 1]
                        azim = fields[base + 2]
                        cn0 = fields[base + 3]
                        if not prn:
                            continue
                        # cn0 常带校验和尾巴（如 "35.2*00"），先剥掉
                        cn0 = cn0.split("*")[0]
                        try:
                            pending_sats.append({
                                "sys": sys_label,
                                "prn": int(prn),
                                "elev": float(elev) if elev else None,
                                "azim": float(azim) if azim else None,
                                "cn0": float(cn0) if cn0 else None,
                            })
                        except ValueError:
                            continue
    # 文件末尾：把最后累积的 GSV 挂到最近的点
    _flush_gsv()

    # 回填：GGA/RMC 同一时刻（0.1s 内）的点共享 date / confidence / speed / sats
    # （GGA 无日期/置信度/速度字段，RMC 才有；RMC 无高程，取同刻 GGA 的椭球高；
    #   GSV 也按时刻归属）
    by_time: dict[float, list] = {}
    for p in pts:
        by_time.setdefault(round(p["time_s"], 1), []).append(p)
    for group in by_time.values():
        date = next((p["date"] for p in group if p["date"]), last_date)
        conf = next((p["confidence"] for p in group if p["confidence"] is not None), None)
        spd = next((p["speed"] for p in group if p["speed"] is not None), None)
        real_alt = next((p["alt"] for p in group if p["alt"]), 0.0)
        sats = next((p["sats"] for p in group if p["sats"]), [])
        for p in group:
            if not p["date"]:
                p["date"] = date
            if p["confidence"] is None and conf is not None:
                p["confidence"] = conf
            if p["speed"] is None and spd is not None:
                p["speed"] = spd
            if p["alt"] == 0 and real_alt:
                p["alt"] = real_alt
            if not p["sats"] and sats:
                p["sats"] = sats
    for p in pts:
        if not p["date"]:
            p["date"] = last_date
    return pts


def _setup_ros2_path(ros2_ws):
    """Add ROS2 workspace Python site-packages to sys.path."""
    if ros2_ws is None:
        return
    ws = Path(ros2_ws)
    if not ws.is_dir():
        print(f"  WARNING: --ros2-ws not found: {ros2_ws}", file=sys.stderr)
        return
    # Find all *local/lib/python*/dist-packages under install/
    for pkg_dir in sorted((ws / "install").glob("*")):
        py_pkgs = sorted(pkg_dir.glob("local/lib/python*/dist-packages"))
        for pp in py_pkgs:
            sp = str(pp)
            if sp not in sys.path:
                sys.path.insert(0, sp)


def _cdr_skip_string(data, off):
    """Skip a CDR string: u32 len + data + align4. Returns new offset."""
    slen = struct.unpack_from("<I", data, off)[0]
    off += 4 + slen
    if off % 4:
        off += 4 - (off % 4)
    return off


def _cdr_skip_header(data, off):
    """Skip a std_msgs/Header in CDR: int32 sec + uint32 ns + string frame_id."""
    off += 8  # sec + nanosec
    off = _cdr_skip_string(data, off)
    return off


def _cdr_align(off, alignment):
    """Align an absolute offset relative to the CDR payload origin."""
    relative_off = off - CDR_PAYLOAD_OFFSET
    return off + (-relative_off % alignment)


def parse_bestnav_cdr(data, off):
    """Parse a CDR-serialized UniBestNav from *data* starting at *off*.

    The struct layout (determined empirically from bag analysis):
      bestnav_start: header(int32+uint32+string, 20 bytes incl pad to 4)
      utc_time_s (float64): 8 bytes
      p_sol_status (uint8), pos_type (uint8)
      latitude_deg (float64), longitude_deg (float64), altitude_m (float64)
      undulation (float32)
      lat_std (float32), lon_std (float32), hgt_std (float32)
      diff_age_s (float32), sol_age_s (float32)
    """
    off = _cdr_skip_header(data, off)   # header (int32 sec + uint32 ns + string)
    off = _cdr_align(off, 8)
    utc_time_s = struct.unpack_from("<d", data, off)[0]
    off += 8                            # utc_time_s (float64)
    off += 1                            # p_sol_status
    pos_type = data[off]
    off += 1                            # pos_type
    off = _cdr_align(off, 8)
    lat = struct.unpack_from("<d", data, off)[0]; off += 8
    lon = struct.unpack_from("<d", data, off)[0]; off += 8
    alt = struct.unpack_from("<d", data, off)[0]; off += 8
    off += 4                            # undulation
    lat_std = struct.unpack_from("<f", data, off)[0]; off += 4
    lon_std = struct.unpack_from("<f", data, off)[0]; off += 4
    hgt_std = struct.unpack_from("<f", data, off)[0]; off += 4
    diff_age_s = struct.unpack_from("<f", data, off)[0]; off += 4
    return {
        "lat": lat, "lon": lon, "alt": alt,
        "pos_type": pos_type,
        "lat_std": lat_std, "lon_std": lon_std, "hgt_std": hgt_std,
        "diff_age_s": diff_age_s,
        "time_s": utc_time_s,
    }, off


def _is_plausible_bestnav(point):
    """Reject fields that indicate a wrong CDR layout rather than real GNSS data."""
    finite_fields = (
        point["lat"], point["lon"], point["alt"], point["lat_std"],
        point["lon_std"], point["hgt_std"], point["diff_age_s"],
        point["time_s"],
    )
    return (
        all(math.isfinite(value) for value in finite_fields)
        and point["pos_type"] in POS_TYPE_MAP
        and -90 <= point["lat"] <= 90
        and -180 <= point["lon"] <= 180
        and abs(point["alt"]) <= MAX_ABS_ALTITUDE_M
        and all(0 <= point[key] <= MAX_STD_M
                for key in ("lat_std", "lon_std", "hgt_std"))
        and 0 <= point["diff_age_s"] <= 86_400
    )


def _parse_rtk_message_cdr(data):
    """Parse the RTK fields while supporting heading layouts with/without SV counts.

    Some older bags were recorded before ``svs_num`` and ``soln_svs_num`` were
    added to UniHeading. Trying the explicit layouts is reliable; inspecting two
    timestamp bytes as a version heuristic is not.
    """
    off = CDR_PAYLOAD_OFFSET
    off = _cdr_skip_header(data, off)       # outer header
    heading_header_end = _cdr_skip_header(data, off)

    candidates = []
    seen_bestnav_offsets = set()
    # Canonical CDR alignment comes first. The unaligned form keeps compatibility
    # with bags produced against the layout previously handled by this script.
    utc_offsets = [_cdr_align(heading_header_end, 8), heading_header_end]
    for utc_off in dict.fromkeys(utc_offsets):
        heading_off = utc_off + 8           # utc_time_s
        heading_off += 2                    # sol_status + heading_type
        heading_off = _cdr_align(heading_off, 4)
        heading_off += 20                   # five float32 heading fields

        # Current UniHeading has the two satellite counts; older definitions do not.
        for satellite_bytes in (2, 0):
            bestnav_off = _cdr_align(heading_off + satellite_bytes, 4)
            if bestnav_off in seen_bestnav_offsets:
                continue
            seen_bestnav_offsets.add(bestnav_off)
            try:
                point, _ = parse_bestnav_cdr(data, bestnav_off)
            except (struct.error, IndexError):
                continue
            if _is_plausible_bestnav(point):
                candidates.append(point)

    if not candidates:
        raise ValueError("no plausible UniBestNav layout")
    return candidates[0]


def _parse_ping_latency_cdr(data):
    """Parse a CDR-serialized ``std_msgs/msg/Float64`` latency value."""
    if len(data) < CDR_PAYLOAD_OFFSET + 8:
        raise ValueError("ping latency message is too short")
    if struct.unpack_from("<I", data, 0)[0] != 0x00000100:
        raise ValueError("unsupported ping latency CDR encoding")
    latency_ms = struct.unpack_from("<d", data, CDR_PAYLOAD_OFFSET)[0]
    if not math.isfinite(latency_ms) or not 0 <= latency_ms <= MAX_PING_LATENCY_MS:
        raise ValueError("invalid ping latency value")
    return latency_ms


def _attach_ping_latency(points, samples):
    """Attach every Ping sample to the nearest RTK frame in each RTK track.

    The rosbag recording timestamp is used because ``std_msgs/Float64`` has no
    header. A sample is copied to each RTK topic so the latency layer follows
    the existing topic filter without inventing a shared spatial trajectory.
    """
    tracks = {}
    for point in points:
        point.pop("ping_samples", None)
        tracks.setdefault(point.get("topic", ""), []).append(point)

    ordered_samples = sorted(
        (sample for sample in samples
         if math.isfinite(sample[0]) and math.isfinite(sample[1])),
        key=lambda sample: sample[0],
    )
    for track in tracks.values():
        track.sort(key=lambda point: point["time_s"])
        track_times = [point["time_s"] for point in track]
        if not track_times:
            continue
        for sample_time_s, latency_ms in ordered_samples:
            insert_at = bisect.bisect_left(track_times, sample_time_s)
            candidates = []
            if insert_at < len(track):
                candidates.append(track[insert_at])
            if insert_at > 0:
                candidates.append(track[insert_at - 1])
            nearest = min(
                candidates,
                key=lambda point: abs(point["time_s"] - sample_time_s),
            )
            if abs(nearest["time_s"] - sample_time_s) > PING_RTK_MATCH_LIMIT_S:
                continue
            nearest.setdefault("ping_samples", []).append({
                "time_s": sample_time_s,
                "latency_ms": latency_ms,
            })


_SEGMENT_INDEX_RE = re.compile(r"_(\d+)\.db3(?:\.zstd)?$")


def _metadata_relative_file_paths(metadata_path):
    """Extract rosbag segment paths from metadata.yaml without PyYAML."""
    if not metadata_path.exists():
        return None

    rel_paths = []
    in_section = False
    section_indent = 0
    for raw_line in metadata_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue

        indent = len(line) - len(line.lstrip(" "))
        if not in_section:
            if stripped == "relative_file_paths:":
                in_section = True
                section_indent = indent
            continue

        if stripped.startswith("- "):
            rel_paths.append(stripped[2:].strip())
            continue
        if indent <= section_indent:
            break

    return rel_paths or None


def _bag_segment_sort_key(path):
    match = _SEGMENT_INDEX_RE.search(path.name)
    if match:
        return (0, int(match.group(1)), path.name)
    return (1, path.name)


def _bag_storage_files(bag_dir):
    """Return bag segment files in metadata order when available."""
    bag_dir = Path(bag_dir)
    metadata_paths = _metadata_relative_file_paths(bag_dir / "metadata.yaml")
    if metadata_paths:
        ordered_files = []
        for rel_path in metadata_paths:
            candidate = bag_dir / rel_path
            if candidate.exists():
                ordered_files.append(candidate)
            else:
                print(f"  [WARN] metadata.yaml listed missing segment: {candidate}",
                      file=sys.stderr)
        if ordered_files:
            return ordered_files

    return sorted(
        [*bag_dir.glob("*.db3"), *bag_dir.glob("*.db3.zstd")],
        key=_bag_segment_sort_key,
    )


def _materialize_db3(db_file, temp_dir):
    """Return a readable .db3 file, decompressing .db3.zstd into *temp_dir* if needed."""
    db_file = Path(db_file)
    if db_file.suffix != ".zstd":
        return db_file

    out_path = Path(temp_dir) / db_file.stem
    if out_path.exists():
        return out_path

    try:
        import zstandard as zstd
    except ImportError:
        zstd = None

    if zstd is not None:
        with open(db_file, "rb") as src, open(out_path, "wb") as dst:
            zstd.ZstdDecompressor().copy_stream(src, dst)
        return out_path

    zstd_bin = shutil.which("zstd")
    if zstd_bin is None:
        raise RuntimeError(
            "compressed rosbag segments require python 'zstandard' or the 'zstd' CLI"
        )

    with open(out_path, "wb") as dst:
        try:
            subprocess.run(
                [zstd_bin, "-d", "-c", str(db_file)],
                check=True,
                stdout=dst,
                stderr=subprocess.PIPE,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(exc.stderr.strip() or "zstd decompression failed") from exc
    return out_path


def load_bag_pure(bag_dir, name):
    """Extract UniRtkPvh from rosbag2 using only Python stdlib (sqlite3 + struct).
    Zero external dependencies — works without ROS2 installed.
    """
    import sqlite3

    pts = []
    ping_samples = []
    with tempfile.TemporaryDirectory(prefix="rtk_3d_viewer_") as temp_dir:
        for source_file in _bag_storage_files(bag_dir):
            try:
                db_file = _materialize_db3(source_file, temp_dir)
            except RuntimeError as exc:
                print(f"  [WARN] Skipping unreadable bag segment {source_file}: {exc}",
                      file=sys.stderr)
                continue

            try:
                conn = sqlite3.connect(str(db_file))
                topic_rows = conn.execute(
                    "SELECT id, name, type FROM topics WHERE name IN (?, ?, ?)",
                    (*RTK_TOPICS, PING_LATENCY_TOPIC),
                ).fetchall()
                if not topic_rows:
                    conn.close()
                    continue
                topic_info = {
                    topic_id: (topic_name, topic_type)
                    for topic_id, topic_name, topic_type in topic_rows
                }
                placeholders = ",".join("?" for _ in topic_info)
                rows = conn.execute(
                    f"SELECT topic_id, timestamp, data FROM messages "
                    f"WHERE topic_id IN ({placeholders}) ORDER BY id",
                    tuple(topic_info),
                ).fetchall()
                conn.close()
            except sqlite3.Error as e:
                print(f"  [WARN] Skipping corrupted sqlite3 file {source_file}: {e}",
                      file=sys.stderr)
                continue

            rejected_messages = 0
            rejected_ping_messages = 0
            for topic_id, timestamp_ns, data in rows:
                topic_name, topic_type = topic_info[topic_id]
                if topic_name == PING_LATENCY_TOPIC:
                    if topic_type != "std_msgs/msg/Float64":
                        rejected_ping_messages += 1
                        continue
                    try:
                        latency_ms = _parse_ping_latency_cdr(data)
                    except (ValueError, struct.error):
                        rejected_ping_messages += 1
                        continue
                    ping_samples.append((timestamp_ns / 1_000_000_000, latency_ms))
                    continue
                if len(data) < 4 or struct.unpack_from("<I", data, 0)[0] != 0x00000100:
                    continue
                try:
                    bn = _parse_rtk_message_cdr(data)
                except (ValueError, struct.error, IndexError):
                    rejected_messages += 1
                    continue
                bn["idx"] = len(pts)
                bn["name"] = name
                bn["topic"] = topic_name
                bn["time_s"] = timestamp_ns / 1_000_000_000
                bn["pos_label"] = POS_TYPE_MAP.get(bn["pos_type"], "UNK")
                pts.append(bn)

            if rejected_messages:
                print(
                    f"  [WARN] Rejected {rejected_messages} RTK messages with an "
                    f"unknown or invalid CDR layout in {source_file}",
                    file=sys.stderr,
                )
            if rejected_ping_messages:
                print(
                    f"  [WARN] Rejected {rejected_ping_messages} invalid Ping "
                    f"latency messages in {source_file}",
                    file=sys.stderr,
                )

    _attach_ping_latency(pts, ping_samples)
    return _sanitize(pts)


def load_bag(bag_dir, name):
    """Extract UniRtkPvh messages from a rosbag2 directory. Requires ROS2 env."""
    if any(path.suffix == ".zstd" for path in _bag_storage_files(bag_dir)):
        print("  [INFO] Compressed rosbag2 segments detected; using pure-Python reader.",
              file=sys.stderr)
        return load_bag_pure(bag_dir, name)

    try:
        from rosbag2_py import SequentialReader, StorageOptions, ConverterOptions
        from rclpy.serialization import deserialize_message
        from rosidl_runtime_py.utilities import get_message
    except ImportError as e:
        print(f"  ERROR: ROS2 not available — {e}", file=sys.stderr)
        print("  Source ROS2 and workspace setup.bash first, e.g.:", file=sys.stderr)
        print("    source /opt/ros/humble/setup.bash", file=sys.stderr)
        print("    source <workspace>/install/setup.bash", file=sys.stderr)
        print("  Or use --ros2-ws /path/to/workspace to auto-load", file=sys.stderr)
        sys.exit(1)

    storage_options = StorageOptions(uri=str(bag_dir), storage_id="sqlite3")
    converter_options = ConverterOptions(
        input_serialization_format="cdr", output_serialization_format="cdr"
    )
    reader = SequentialReader()
    reader.open(storage_options, converter_options)
    rtk_msg_type = get_message("robots_dog_msgs/msg/UniRtkPvh")
    ping_msg_type = get_message("std_msgs/msg/Float64")

    pts = []
    ping_samples = []
    while reader.has_next():
        topic, data, timestamp_ns = reader.read_next()
        if topic == PING_LATENCY_TOPIC:
            try:
                msg = deserialize_message(data, ping_msg_type)
                latency_ms = float(msg.data)
                if not math.isfinite(latency_ms) or not 0 <= latency_ms <= MAX_PING_LATENCY_MS:
                    raise ValueError("invalid Ping latency value")
            except Exception as exc:
                print(f"  [WARN] Failed to deserialize message on topic {topic}: {exc}",
                      file=sys.stderr)
                continue
            ping_samples.append((timestamp_ns / 1_000_000_000, latency_ms))
            continue
        if topic not in RTK_TOPICS:
            continue
        try:
            msg = deserialize_message(data, rtk_msg_type)
        except Exception as exc:
            print(f"  [WARN] Failed to deserialize message on topic {topic}: {exc}",
                  file=sys.stderr)
            continue
        bn = msg.bestnav
        pos_type = getattr(bn, "pos_type", 0)
        pts.append({
            "idx": len(pts),
            "lat": float(bn.latitude_deg),
            "lon": float(bn.longitude_deg),
            "alt": float(bn.altitude_m),
            "pos_label": POS_TYPE_MAP.get(pos_type, "UNK"),
            "name": name,
            "topic": topic,
            "lat_std": float(getattr(bn, "lat_std", -1) or 0),
            "lon_std": float(getattr(bn, "lon_std", -1) or 0),
            "hgt_std": float(getattr(bn, "hgt_std", -1) or 0),
            "diff_age_s": float(getattr(bn, "diff_age_s", 0) or 0),
            "time_s": timestamp_ns / 1_000_000_000,
        })
    _attach_ping_latency(pts, ping_samples)
    return _sanitize(pts)


def is_bag_dir(path):
    """Check if path is a rosbag2 directory."""
    p = Path(path)
    return p.is_dir() and (p / "metadata.yaml").exists()


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RTK 3D Point Viewer</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
     background:#111;color:#eee;height:100vh;display:flex;flex-direction:column;overflow:hidden}
#bar{display:flex;flex-wrap:wrap;align-items:center;gap:5px 12px;padding:5px 12px;
      background:#1a1a2e;border-bottom:1px solid #333;flex-shrink:0}
#bar button{background:#222;color:#e94560;border:1px solid #e94560;border-radius:4px;
    padding:4px 12px;cursor:pointer;font-size:12px}
#bar button:hover{background:#e94560;color:#fff}
#bar .grp{display:inline-flex;align-items:center;gap:4px;white-space:nowrap}
#bar .sep{color:#4a5a7a;margin:0 2px;user-select:none}
#bar select{background:#222;color:#eee;border:1px solid #444;border-radius:4px;padding:3px 7px;font-size:12px}
#bar label{font-size:11px;color:#aaa}
#bar input[type=range]{width:110px;accent-color:#b86cff;cursor:pointer}
#bar input[type=number]{background:#222;color:#eee;border:1px solid #444;border-radius:4px;
    padding:3px 6px;font-size:12px;width:56px}
#bar input[type=checkbox]{accent-color:#00d4ff;margin:0;cursor:pointer}
.std-threshold-value{min-width:34px;font-size:11px;color:#ccc}
#view{flex:1;position:relative;background:#0a0a14;cursor:grab;overflow:hidden;
    display:flex;flex-direction:row}
#view:active{cursor:grabbing}
#cv-wrap{flex:1;position:relative;overflow:hidden;min-width:0}
#cv-wrap canvas{display:block;position:absolute;top:0;left:0}
#diff-bar{position:absolute;top:8px;left:50%;transform:translateX(-50%);z-index:20;
    background:rgba(22,22,44,0.92);border:1px solid #555;border-radius:6px;
    padding:6px 14px;font-size:12px;display:none;white-space:nowrap}
#diff-bar.on{display:block}
.diff-h{color:#f0a500;font-weight:bold}
#leg{position:absolute;bottom:6px;left:6px;z-index:20;
    background:rgba(22,22,44,0.8);border:1px solid #333;border-radius:5px;
    padding:5px 8px;font-size:10px}
.lr{display:flex;align-items:center;gap:5px;margin:2px 0}
.lq{width:10px;height:10px;border-radius:2px;flex-shrink:0}
#tip{position:absolute;z-index:25;pointer-events:none;
    background:rgba(0,0,0,0.88);color:#eee;padding:3px 8px;border-radius:3px;
    font-size:11px;display:none;white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;
    line-height:1.35;max-width:min(360px,calc(100% - 12px))}
#info{position:absolute;top:6px;right:6px;z-index:20;
    background:rgba(22,22,44,0.92);border:1px solid #333;border-radius:6px;
    padding:8px 10px;width:280px;font-size:11px;display:none}
#info.on{display:block}
#info pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word}
#stats{position:absolute;top:6px;left:6px;z-index:20;
    background:rgba(22,22,44,0.92);border:1px solid #444;border-radius:6px;
    padding:7px 10px;min-width:250px;font-size:11px;line-height:1.55}
#stats .bad{color:#ff5964;font-weight:bold}
/* 误差分析面板：右侧停靠栏（不遮挡地图，地图自动让位，图表完整显示） */
#err-panel{width:min(340px,34vw);flex-shrink:0;display:none;flex-direction:column;
    background:rgba(16,18,34,0.97);border-left:1px solid #444;padding:6px 8px;overflow:hidden}
#err-panel.on{display:flex}
#err-head{display:flex;align-items:center;gap:6px;padding:2px 2px 6px;
    color:#ddd;font-size:11px}
#err-head button{margin-left:auto;background:#222;color:#aaa;border:1px solid #444;
    border-radius:3px;cursor:pointer;line-height:1}
#err-head select{background:#222;color:#eee;border:1px solid #444;border-radius:4px;
    padding:2px 5px;font-size:11px}
#err-hint{font-size:10px;color:#8ab4ff;padding:0 2px 4px}
#err-cv{width:100%;flex:1;min-height:240px;background:#0a0a14;border:1px solid #333;
    border-radius:4px;cursor:crosshair}
#err-stats-txt{color:#aaa;font-size:10px;margin-top:5px;line-height:1.6;
    max-height:72px;overflow:auto}
#err-stats-txt .es{display:flex;gap:12px;padding:1px 0;white-space:nowrap}
#err-stats-txt .es .el{color:#8ab4ff;min-width:56px}
#err-stats-txt .es b{color:#e6eaf2;font-weight:600}
.conf-ok{color:#00e676;font-weight:bold}
.conf-bad{color:#ff6b6b;font-weight:bold}
#info table{border-collapse:collapse;width:100%;margin-top:2px}
#info td,#info th{padding:1px 4px;font-size:10px;text-align:left;border-bottom:1px solid #2a2a3a}
#info th{color:#8ab4ff;font-weight:600}
#info .sys-tag{color:#b86cff;font-weight:bold}
#hint{position:absolute;left:8px;bottom:6px;z-index:19;font-size:10px;color:#556;
    background:rgba(10,10,20,0.6);padding:3px 8px;border-radius:4px;pointer-events:none}
</style>
</head>
<body>

<div id="bar">
  <span class="grp"><button id="btn-reset">&#8630; 复位</button></span>
  <span class="grp"><button id="btn-fit">&#8596; 适配</button></span>
  <span class="sep">│</span>
  <span class="grp"><label>高程</label><select id="z-exag">
    <option value="1" selected>1x</option><option value="3">3x</option>
    <option value="5">5x</option><option value="10">10x</option><option value="20">20x</option>
  </select></span>
  <span class="grp"><label>点大小</label><select id="pt-size">
    <option value="2">2px</option><option value="3" selected>3px</option><option value="4">4px</option><option value="5">5px</option>
  </select></span>
  <span class="grp"><label>视角</label><select id="view-ang">
    <option value="45" selected>45°</option><option value="30">30°</option><option value="60">60°</option><option value="90">俯视</option>
  </select></span>
  <span class="grp"><label>厚度</label><select id="band-thickness" title="轨迹画成立体带：上缘+下缘曲线（像圆柱上下两面的周曲线），中间填充成面">
    <option value="0">关</option>
    <option value="0.2">0.2m</option>
    <option value="0.5" selected>0.5m</option>
    <option value="1">1m</option>
    <option value="2">2m</option>
  </select></span>
  <span class="sep">│</span>
  <span class="grp"><label>着色</label><select id="color-mode">
    <option value="pos" selected>定位状态</option>
    <option value="conf">置信度</option>
    <option value="err">误差超差</option>
  </select></span>
  <span class="grp"><label>状态过滤</label><select id="pos-filter">
    <option value="all" selected>全部</option>
    <option value="fixed">仅固定解</option>
    <option value="float">仅浮点解</option>
    <option value="psrdiff">仅伪距差分</option>
    <option value="single">仅单点解</option>
    <option value="none">仅无解</option>
  </select></span>
  <span class="sep">│</span>
  <span class="grp"><label>置信度阈值</label>
    <input id="conf-threshold" type="range" min="0" max="9" step="1" value="5"
           aria-label="置信度阈值">
    <span id="conf-threshold-value" class="std-threshold-value">5</span>
  </span>
  <span class="sep">│</span>
  <span class="grp"><input type="checkbox" id="err-hor-en" checked><label title="参与误差判定">水平</label>
    <input id="err-hor-threshold" type="number" min="0" step="0.01" value="0.5"
           aria-label="水平误差阈值"></span>
  <span class="grp"><input type="checkbox" id="err-ver-en" checked><label title="参与误差判定">高程</label>
    <input id="err-ver-threshold" type="number" min="0" step="0.01" value="0.5"
           aria-label="高程误差阈值"></span>
  <span class="grp"><input type="checkbox" id="err-spd-en" checked><label title="参与误差判定">速度</label>
    <input id="err-spd-threshold" type="number" min="0" step="0.01" value="1.0"
           aria-label="速度误差阈值"></span>
  <span class="grp"><label>单位</label><select id="err-unit">
    <option value="m" selected>m</option>
    <option value="cm">cm</option>
  </select></span>
  <span class="grp"><button id="btn-err">&#128200; 误差</button></span>
  <span id="pick-info" style="font-size:11px;color:#888"></span>
</div>

<div id="view">
  <div id="cv-wrap">
  <canvas id="cv"></canvas>
  <div id="tip"></div>
  <div id="diff-bar">
  &#128207; <b>选点对比</b> &nbsp;
  Δlat: <span class="diff-h" id="dv-lat">--</span> &nbsp;
  Δlon: <span class="diff-h" id="dv-lon">--</span> &nbsp;
  Δalt: <span class="diff-h" id="dv-alt">--</span> &nbsp;
  水平距: <span class="diff-h" id="dv-hor">--</span> &nbsp;
  3D距: <span class="diff-h" id="dv-3d">--</span>
  </div>
  <div id="leg"></div>
  <div id="stats"></div>
  <div id="info"><pre id="info-txt" style="color:#ccc;font-size:11px;margin:0"></pre></div>
  <div id="hint">左拖=旋转 · 右键/中键拖=平移 · 滚轮=光标处缩放 · 双击=放大 · Shift+双击=缩小</div>
  </div>
  <div id="err-panel">
    <div id="err-head">误差分析（测试 vs 真值）
      <button id="err-close" title="关闭">×</button>
    </div>
    <div id="err-hint" class="err-hint">点击图中任意点 → 地图同步选中该点（橙色圈）</div>
    <canvas id="err-cv"></canvas>
    <div id="err-stats-txt"></div>
  </div>
</div>

<script>
// === State ===
const EMBEDDED_POINTS=__RTK_POINTS_JSON__;
let pts=[], cv, ctx, w, h;
let rotY=-0.7, rotX=0.785, scale=15, px=0, py=0; // rotX=45°
let defRotY, defRotX, defScale, defPx, defPy;
let zExag=1, ptR=3;
let bandThickness=0.5;               // 轨迹立体带厚度(m)，0=普通细线
let dragging=false, dx0=0, dy0=0, dpx=0, dpy=0, rightDrag=false;
let pickA=null, pickB=null; // {idx, x, y, z, alt}
let hoverIdx=-1;
let altMin=Infinity;
// 着色模式 / 置信度阈值 / 误差对比
let colorMode='pos';
let confThresh=5;                    // 置信度阈值 0~9
let errHorThresh=0.5;                // 水平误差阈值(m)；0=不检查该维
let errVerThresh=0.5;                // 高程误差阈值(m)；0=不检查该维
let errSpdThresh=1.0;                // 速度误差阈值(m/s)；0=不检查该维
let errHorEn=true;                   // 水平误差是否参与判定（勾选控制）
let errVerEn=true;                   // 高程误差是否参与判定
let errSpdEn=true;                   // 速度误差是否参与判定
let errUnit='m';                     // 误差阈值输入单位：'m' / 'cm'（内部仍以 m 存储）
let posFilter='all';                 // 定位状态过滤：all/fixed/float/psrdiff/single/none
let errData=null;                    // {t, hor, ver, spd, idx} 误差曲线 + 对应点索引
let errT0=0;
let errSelIdx=-1;                    // 误差图上点击选中的点（地图橙色圈高亮）
let hiddenSets=new Set(); // names of hidden datasets
let highlightSet=null;     // 图例悬停高亮的数据集（其余变暗）
// 数据集线型（按数据集序号轮换，重合轨迹也能区分）：实线/虚线/点线/点划线/密点线
const DASHES=[[],[6,4],[2,3],[8,3,2,3],[1,2]];
let rafPending=false;                // 拖动/滚轮时合并重绘，保证流畅
// 定位状态过滤分组（仅对测试/单独数据生效，真值参考点始终显示）
const POS_FILTER_GROUPS={
  fixed:['L1_INT','WIDE_LANE','NARROW_INT'],
  float:['L1_FLOAT','IONOFREE_FLOAT','NARROW_FLOAT'],
  psrdiff:['PSRDIFF'],
  single:['SINGLE','WAAS','PROPAGATED','FIXEDPOS','FIXEDHEIGHT','DOPPLER_VELOCITY'],
  none:['NONE']
};
function matchesPosFilter(p){
  if(posFilter==='all') return true;
  return (POS_FILTER_GROUPS[posFilter]||[]).indexOf(p.pos_label)!==-1;
}
const PALETTE=["#e94560","#4ecca3","#f0a500","#5599dd","#dd77cc","#88cc44","#ff8844","#44ccdd","#ccdd44","#aa66ff"];
// 定位状态 → 颜色（顶层常量，供 applyColorMode/buildLegend 使用）
const PTCL={'NONE':'#666','SINGLE':'#e94560','PSRDIFF':'#4da3ff','WAAS':'#e94560','PROPAGATED':'#e94560','FIXEDPOS':'#e94560','FIXEDHEIGHT':'#e94560','DOPPLER_VELOCITY':'#e94560','L1_FLOAT':'#f0a500','IONOFREE_FLOAT':'#f0a500','NARROW_FLOAT':'#f0a500','L1_INT':'#4ecca3','WIDE_LANE':'#4ecca3','NARROW_INT':'#4ecca3'};
// 误差超差着色：颜色图注（buildLegend 中展示）
const ERR_COLOR_OK='#00e676';        // 正常（未超差）
const ERR_COLOR_HOR='#f0a500';       // 水平误差超差
const ERR_COLOR_VER='#00d9ff';       // 高程误差超差
const ERR_COLOR_SPD='#b86cff';       // 速度误差超差
const ERR_COLOR_MULTI='#ff3355';     // 多个误差同时超差
const ERR_COLOR_UNMATCHED='#666666'; // 未匹配到真值
const ERR_COLOR_TRUTH='#9aa7c7';     // 真值参考点

function errThreshLabel(v, unit){
  if(v<=0) return '关闭';
  if(unit==='cm') return (v*100).toFixed(0)+'cm';
  return v.toFixed(2)+'m';
}

function pointVisible(p){
  if(hiddenSets.has(p.name)) return false;
  if(p.type==='truth') return true;   // 真值参考点始终显示
  return matchesPosFilter(p);
}

// === Projection ===
function proj(wx,wy,wz){
  let rx=wx*Math.cos(rotY)-wz*Math.sin(rotY);
  let rz=wx*Math.sin(rotY)+wz*Math.cos(rotY);
  return {
    sx: rx*scale + px + w/2,
    sy: (-wy*Math.cos(rotX) + rz*Math.sin(rotX))*scale + py + h/2
  };
}

function buildWorld(){
  altMin=Infinity;
  for(let p of pts){
    if(p.alt<altMin) altMin=p.alt;
  }
  // Center lat/lon on first point
  let lat0=pts[0].lat, lon0=pts[0].lon;
  for(let p of pts){
    p.x=(p.lon-lon0)*111320*Math.cos(lat0*Math.PI/180);
    p.y=(p.alt-altMin)*zExag;
    p.z=-(p.lat-lat0)*111320;
  }
}

// 自动适配：按数据包围盒缩放并居中（静态毫米簇也能铺满视图）
function autoFit(){
  if(!pts.length||!w||!h) return;
  let xa=Infinity,xb=-Infinity,za=Infinity,zb=-Infinity,ya=Infinity,yb=-Infinity;
  for(let p of pts){
    if(p.x<xa) xa=p.x; if(p.x>xb) xb=p.x;
    if(p.z<za) za=p.z; if(p.z>zb) zb=p.z;
    if(p.y<ya) ya=p.y; if(p.y>yb) yb=p.y;
  }
  let diag=Math.sqrt((xb-xa)*(xb-xa)+(zb-za)*(zb-za)+Math.pow((yb-ya)*0.6,2));
  if(!(diag>0) || !Number.isFinite(diag)) diag=1;
  scale=Math.min(w,h)*0.75/Math.max(diag,0.02);
  scale=Math.max(0.005,Math.min(scale,20000));
  let cx=(xa+xb)/2, cz=(za+zb)/2;
  let cp=proj(cx,0,cz);
  px=-cp.sx+w/2; py=-cp.sy+h/2;
}

function visibleTrackGroups(){
  return (window._trackGroups||[]).filter(g=>!hiddenSets.has(g.name));
}

function updateStats(){
  let el=document.getElementById('stats');
  if(!el) return;
  let total=pts.filter(pointVisible).length;
  let nTruth=pts.filter(p=>p.type==='truth').length;
  let nTest=pts.filter(p=>p.type==='test'&&pointVisible(p)).length;
  let s='<b>数据统计</b><br>显示点数: '+total;
  if(nTruth||nTest) s+='<br>真值: '+nTruth+' &nbsp;|&nbsp; 测试: '+nTest;
  if(errData){
    let nMatch=errData.t.length;
    let nBad=pts.filter(p=>p.type==='test' && pointVisible(p) && p._errMatched && p._errBad).length;
    s+='<br>误差匹配: '+nMatch+' 点 &nbsp;|&nbsp; 超差: <span class="'+(nBad?'bad':'')+'">'+nBad+'</span>';
  }
  if(colorMode==='conf' && !pts.some(p=>p.confidence!==null&&p.confidence!==undefined)){
    s+='<br><span class="bad">数据无置信度字段（RMC 第14字段非数字），点按定位状态着色</span>';
  }
  el.innerHTML=s;
}

// rAF 节流：拖动/滚轮/悬停等高频事件合并到每帧只重绘一次，保证操作流畅
function scheduleDraw(){
  if(rafPending) return;
  rafPending=true;
  requestAnimationFrame(function(){ rafPending=false; draw(); });
}

// === Drawing ===
function draw(){
  if(!ctx||!w||!h) return;
  ctx.clearRect(0,0,w,h);
  ctx.fillStyle='#0a0a14'; ctx.fillRect(0,0,w,h);
  if(!pts.length) return;
  updateStats();

  // grid: 1m fine + 10m bold with labels
  let xs=pts.map(p=>p.x), zs=pts.map(p=>p.z);
  let xMin=Math.min(...xs)-5, xMax=Math.max(...xs)+5;
  let zMin=Math.min(...zs)-5, zMax=Math.max(...zs)+5;
  // Keep grid work bounded for large tracks or malformed input. At normal robot
  // scales this remains a 1m/10m grid; larger extents use coarser round steps.
  let maxSpan=Math.max(xMax-xMin,zMax-zMin);
  let fineStep=1;
  if(Number.isFinite(maxSpan) && maxSpan>200){
    fineStep=Math.pow(10,Math.ceil(Math.log10(maxSpan/200)));
  }
  let boldStep=fineStep*10;
  // fine grid
  ctx.strokeStyle='#121e2a'; ctx.lineWidth=0.3;
  ctx.beginPath();
  for(let x=Math.floor(xMin/fineStep)*fineStep; x<=xMax; x+=fineStep){
    let a=proj(x,0,zMin), b=proj(x,0,zMax);
    ctx.moveTo(a.sx,a.sy); ctx.lineTo(b.sx,b.sy);
  }
  for(let z=Math.floor(zMin/fineStep)*fineStep; z<=zMax; z+=fineStep){
    let a=proj(xMin,0,z), b=proj(xMax,0,z);
    ctx.moveTo(a.sx,a.sy); ctx.lineTo(b.sx,b.sy);
  }
  ctx.stroke();
  // bold grid + labels
  ctx.strokeStyle='#1e3040'; ctx.lineWidth=0.8;
  ctx.beginPath();
  ctx.fillStyle='#335'; ctx.font='9px monospace';
  for(let x=Math.floor(xMin/boldStep)*boldStep; x<=xMax; x+=boldStep){
    let a=proj(x,0,zMin), b=proj(x,0,zMax);
    ctx.moveTo(a.sx,a.sy); ctx.lineTo(b.sx,b.sy);
    ctx.fillText(x.toFixed(0),a.sx+2,a.sy-2);
  }
  for(let z=Math.floor(zMin/boldStep)*boldStep; z<=zMax; z+=boldStep){
    let a=proj(xMin,0,z), b=proj(xMax,0,z);
    ctx.moveTo(a.sx,a.sy); ctx.lineTo(b.sx,b.sy);
    ctx.fillText(z.toFixed(0),a.sx+2,a.sy-2);
  }
  ctx.stroke();

  // vertical drops (subset, skip hidden)
  ctx.strokeStyle='rgba(255,255,255,0.04)'; ctx.lineWidth=0.5;
  let vstep=Math.max(3, Math.floor(pts.length/200));
  ctx.beginPath();
  for(let i=0;i<pts.length;i+=vstep){
    let p=pts[i]; if(!pointVisible(p)) continue;
    let top=proj(p.x,p.y,p.z), bot=proj(p.x,0,p.z);
    ctx.moveTo(top.sx,top.sy); ctx.lineTo(bot.sx,bot.sy);
  }
  ctx.stroke();

  // trajectory：厚度>0 时画成立体带（上缘+下缘周曲线，中间填充成面，像圆柱上下两面的周曲线）；
  // 厚度=0 时退回普通细线。数据集线型按序号轮换（重合轨迹也能区分）；悬停高亮时其余变暗。
  let groups=window._trackGroups||[];
  let lineStep=Math.max(1,Math.ceil(pts.length/20000));
  let bandH=bandThickness*zExag;
  let dashMap={};
  (window._groups||[]).forEach(function(g,i){ dashMap[g.name]=DASHES[i%DASHES.length]; });
  function dimAlpha(g,base){ return (highlightSet && g.name!==highlightSet)?base*0.15:base; }
  if(bandH>0){
    for(let g of groups){
      if(hiddenSets.has(g.name)) continue;
      if(g.indices.length<2) continue;
      let arr=g.indices;
      let dash=dashMap[g.name]||[];
      // 带体填充（低透明度）
      ctx.fillStyle=g.color; ctx.globalAlpha=dimAlpha(g,0.20); ctx.beginPath();
      for(let j=0;j<arr.length;j+=lineStep){
        let p=pts[arr[j]], s=proj(p.x,p.y,p.z);
        if(j===0) ctx.moveTo(s.sx,s.sy); else ctx.lineTo(s.sx,s.sy);
      }
      for(let j=arr.length-1;j>=0;j-=lineStep){
        let p=pts[arr[j]], s=proj(p.x,p.y+bandH,p.z);
        ctx.lineTo(s.sx,s.sy);
      }
      ctx.closePath(); ctx.fill(); ctx.globalAlpha=1;
      // 下缘曲线 + 上缘曲线（圆柱上下两面的“周曲线”），按数据集线型
      for(let k=0;k<2;k++){
        let yy=k*bandH;
        ctx.strokeStyle=g.color; ctx.globalAlpha=dimAlpha(g,0.8); ctx.lineWidth=1.6;
        ctx.setLineDash(dash);
        ctx.beginPath();
        for(let j=0;j<arr.length;j+=lineStep){
          let p=pts[arr[j]], s=proj(p.x,p.y+yy,p.z);
          if(j===0) ctx.moveTo(s.sx,s.sy); else ctx.lineTo(s.sx,s.sy);
        }
        ctx.stroke();
      }
      ctx.setLineDash([]); ctx.globalAlpha=1;
      // 竖直骨架线（像圆柱侧面母线，间隔绘制）
      ctx.strokeStyle=g.color; ctx.globalAlpha=dimAlpha(g,0.28); ctx.lineWidth=1;
      ctx.beginPath();
      let rung=Math.max(1,Math.floor(arr.length/100));
      for(let j=0;j<arr.length;j+=rung){
        let p=pts[arr[j]];
        let a=proj(p.x,p.y,p.z), b=proj(p.x,p.y+bandH,p.z);
        ctx.moveTo(a.sx,a.sy); ctx.lineTo(b.sx,b.sy);
      }
      ctx.stroke(); ctx.globalAlpha=1;
    }
  } else {
    for(let g of groups){
      if(hiddenSets.has(g.name)) continue;
      if(g.indices.length<2) continue;
      let dash=dashMap[g.name]||[];
      ctx.lineWidth=1; ctx.strokeStyle=g.color; ctx.globalAlpha=dimAlpha(g,0.6);
      ctx.setLineDash(dash);
      ctx.beginPath();
      let p0=pts[g.indices[0]], s0=proj(p0.x,p0.y,p0.z);
      ctx.moveTo(s0.sx,s0.sy);
      for(let j=lineStep;j<g.indices.length;j+=lineStep){
        let p=pts[g.indices[j]], s=proj(p.x,p.y,p.z);
        ctx.lineTo(s.sx,s.sy);
      }
      ctx.stroke(); ctx.setLineDash([]); ctx.globalAlpha=1;
    }
  }

  // points（超大点数抽稀；悬停高亮时其余数据集变暗）
  let pstep=Math.max(1,Math.ceil(pts.length/30000));
  let dimOn=highlightSet!==null;
  for(let i=0;i<pts.length;i+=pstep){
    let p=pts[i]; if(!pointVisible(p)) continue;
    let sc=proj(p.x,p.y,p.z);
    if(sc.sx<-20||sc.sx>w+20||sc.sy<-20||sc.sy>h+20) continue;
    ctx.fillStyle=p.color;
    if(dimOn && p.name!==highlightSet) ctx.globalAlpha=0.15;
    ctx.beginPath(); ctx.arc(sc.sx,sc.sy,ptR,0,Math.PI*2); ctx.fill();
    ctx.globalAlpha=1;
  }

  // pick highlights
  if(pickA){
    let sc=proj(pickA.x,pickA.y,pickA.z);
    ctx.strokeStyle='#fff'; ctx.lineWidth=2;
    ctx.beginPath(); ctx.arc(sc.sx,sc.sy,ptR+4,0,Math.PI*2); ctx.stroke();
    ctx.fillStyle='#fff'; ctx.beginPath(); ctx.arc(sc.sx,sc.sy,2,0,Math.PI*2); ctx.fill();
    // label
    ctx.fillStyle='#fff'; ctx.font='10px monospace';
    ctx.fillText('A', sc.sx+8, sc.sy-8);
  }
  if(pickB){
    let sc=proj(pickB.x,pickB.y,pickB.z);
    ctx.strokeStyle='#ff0'; ctx.lineWidth=2;
    ctx.beginPath(); ctx.arc(sc.sx,sc.sy,ptR+4,0,Math.PI*2); ctx.stroke();
    ctx.fillStyle='#ff0'; ctx.beginPath(); ctx.arc(sc.sx,sc.sy,2,0,Math.PI*2); ctx.fill();
    ctx.fillStyle='#ff0'; ctx.font='10px monospace';
    ctx.fillText('B', sc.sx+8, sc.sy-8);
  }

  // pick line
  if(pickA && pickB){
    let a=proj(pickA.x,pickA.y,pickA.z), b=proj(pickB.x,pickB.y,pickB.z);
    ctx.strokeStyle='rgba(255,255,0,0.5)'; ctx.lineWidth=1; ctx.setLineDash([4,3]);
    ctx.beginPath(); ctx.moveTo(a.sx,a.sy); ctx.lineTo(b.sx,b.sy); ctx.stroke();
    ctx.setLineDash([]);
  }

  // hover
  if(hoverIdx>=0 && hoverIdx<pts.length){
    let p=pts[hoverIdx];
    let sc=proj(p.x,p.y,p.z);
    ctx.strokeStyle='rgba(255,255,255,0.4)'; ctx.lineWidth=1.5;
    ctx.beginPath(); ctx.arc(sc.sx,sc.sy,ptR+3,0,Math.PI*2); ctx.stroke();
  }
  // 误差图点击选中的点：橙色圈高亮
  if(errSelIdx>=0 && errSelIdx<pts.length){
    let p=pts[errSelIdx];
    if(pointVisible(p)){
      let sc=proj(p.x,p.y,p.z);
      ctx.strokeStyle='#ffb454'; ctx.lineWidth=2.5;
      ctx.beginPath(); ctx.arc(sc.sx,sc.sy,ptR+6,0,Math.PI*2); ctx.stroke();
    }
  }
}

// === Hit test ===
function hit(sx,sy,th){
  th=th||8;
  let best=-1, bd=Infinity;
  for(let i=0;i<pts.length;i++){
    if(!pointVisible(pts[i])) continue;
    let sc=proj(pts[i].x,pts[i].y,pts[i].z);
    let dx=sx-sc.sx, dy=sy-sc.sy, d2=dx*dx+dy*dy;
    if(d2<th*th && d2<bd){ best=i; bd=d2; }
  }
  return best>=0?best:-1;
}

function buildLegend(){
  if(!window._groups) return;
  // 数据集段：点击=显隐 · 悬停=高亮（其余变暗）· 双击=只看该数据集
  let leg='<div style="color:#aaa;margin-bottom:4px">— 数据集 (点击显隐 · 悬停高亮 · 双击只看) —</div>';
  for(let i=0;i<window._groups.length;i++){
    let g=window._groups[i];
    let vis=!hiddenSets.has(g.name);
    let dash=DASHES[i%DASHES.length];
    let dashTxt=dash.length?('　'+['实线','虚线','点线','点划线','密点线'][i%DASHES.length]):'';
    leg+='<div class="lr" style="cursor:pointer" '
      +'onclick="event.stopPropagation();if(hiddenSets.has(\''+g.name+'\'))hiddenSets.delete(\''+g.name+'\');else hiddenSets.add(\''+g.name+'\');buildLegend();draw();" '
      +'onmouseover="highlightSet=\''+g.name+'\';draw();" '
      +'onmouseout="highlightSet=null;draw();" '
      +'ondblclick="event.stopPropagation();hiddenSets=new Set(window._groups.map(function(x){return x.name;}));hiddenSets.delete(\''+g.name+'\');buildLegend();draw();">'
      +'<span class="lq" style="background:'+(vis?g.color:'#333')+'"></span>'
      +(vis?'&#9673; ':'&#9678; ')+g.name+' ('+g.indices.length+'pts)'+dashTxt+'</div>';
  }
  if(hiddenSets.size){
    leg+='<div class="lr" style="cursor:pointer;color:#8ab4ff" onclick="event.stopPropagation();hiddenSets.clear();buildLegend();draw();">显示全部</div>';
  }
  const posFilterLabel={'all':'全部','fixed':'仅固定解','float':'仅浮点解','psrdiff':'仅伪距差分','single':'仅单点解','none':'仅无解'};
  leg+='<div class="lr" style="color:#8ab4ff">状态过滤：'+(posFilterLabel[posFilter]||'全部')+'</div>';
  if(bandThickness>0){
    leg+='<div class="lr" style="color:#8ab4ff">轨迹立体带：厚 '+bandThickness+'m（上缘+下缘周曲线）</div>';
  }
  // 定位状态：始终标注（单点定位/浮点解/RTK固定解等）
  leg+='<div style="color:#aaa;margin-top:5px;margin-bottom:2px">— 定位状态 —</div>';
  leg+='<div class="lr"><span class="lq" style="background:#4ecca3"></span> RTK固定解 (L1_INT/WIDE/NARROW_INT)</div>';
  leg+='<div class="lr"><span class="lq" style="background:#f0a500"></span> 浮点解 (L1_FLOAT/NARROW_FLOAT)</div>';
  leg+='<div class="lr"><span class="lq" style="background:#4da3ff"></span> 伪距差分 (PSRDIFF)</div>';
  leg+='<div class="lr"><span class="lq" style="background:#e94560"></span> 单点定位 (SINGLE/WAAS等)</div>';
  leg+='<div class="lr"><span class="lq" style="background:#666"></span> 无解/其他 (NONE)</div>';
  if(colorMode==='conf'){
    leg+='<div style="color:#aaa;margin-top:5px;margin-bottom:2px">— 置信度着色 (0~9) —</div>';
    leg+='<div class="lr"><span class="lq" style="background:#00e676"></span> 置信度 ≥ '+confThresh+'</div>';
    leg+='<div class="lr"><span class="lq" style="background:#ff6b6b"></span> 置信度 &lt; '+confThresh+'</div>';
    leg+='<div class="lr" style="color:#8ab4ff">无置信度数据（非 RMC 第14字段）的点按定位状态着色</div>';
  } else if(colorMode==='err'){
    leg+='<div style="color:#aaa;margin-top:5px;margin-bottom:2px">— 测试点误差着色 (vs 真值) —</div>';
    if(!pts.some(p=>p.type==='truth')){
      leg+='<div class="lr" style="color:#ffb454">未提供真值文件（--truth），误差不可计算，点按定位状态着色</div>';
    } else {
    // 参与判定的组合摘要：勾选 ✓ 且阈值>0 才参与
    let parts=[];
    if(errHorEn&&errHorThresh>0) parts.push('水平');
    if(errVerEn&&errVerThresh>0) parts.push('高程');
    if(errSpdEn&&errSpdThresh>0) parts.push('速度');
    leg+='<div class="lr" style="color:#8ab4ff">参与判定：'+(parts.length?parts.join('+')+'（任一超差即超差）':'无（不判定超差）')+'</div>';
    leg+='<div class="lr"><span class="lq" style="background:'+ERR_COLOR_TRUTH+'"></span> 真值轨迹参考点</div>';
    leg+='<div class="lr"><span class="lq" style="background:'+ERR_COLOR_OK+'"></span> 正常（未超差）</div>';
    leg+='<div class="lr"><span class="lq" style="background:'+ERR_COLOR_HOR+'"></span> 水平误差 &gt; '+errThreshLabel(errHorThresh,errUnit)+(errHorEn&&errHorThresh>0?'':'（未参与）')+'</div>';
    leg+='<div class="lr"><span class="lq" style="background:'+ERR_COLOR_VER+'"></span> 高程误差 &gt; '+errThreshLabel(errVerThresh,errUnit)+(errVerEn&&errVerThresh>0?'':'（未参与）')+'</div>';
    leg+='<div class="lr"><span class="lq" style="background:'+ERR_COLOR_SPD+'"></span> 速度误差 &gt; '+errThreshLabel(errSpdThresh,errUnit)+(errSpdEn&&errSpdThresh>0?'（'+(errUnit==='cm'?'cm/s':'m/s')+'）':'（未参与）')+'</div>';
    leg+='<div class="lr"><span class="lq" style="background:'+ERR_COLOR_MULTI+'"></span> 多项误差同时超差</div>';
    leg+='<div class="lr"><span class="lq" style="background:'+ERR_COLOR_UNMATCHED+'"></span> 未匹配到真值</div>';
    }
  }
  document.getElementById('leg').innerHTML=leg;
}

// === Elevation diff ===
function updateDiff(){
  let el=document.getElementById('diff-bar');
  function $(id){return document.getElementById(id);}
  if(pickA && pickB){
    el.classList.add('on');
    let dLat=Math.abs(pickA.lat-pickB.lat)*111320*100; // cm
    let latMid=(pickA.lat+pickB.lat)/2*Math.PI/180;
    let dLon=Math.abs(pickA.lon-pickB.lon)*111320*Math.cos(latMid)*100; // cm
    let dAlt=Math.abs(pickA.alt-pickB.alt)*100; // cm
    let horDist=Math.sqrt(dLat*dLat+dLon*dLon);
    let dist3d=Math.sqrt(horDist*horDist+dAlt*dAlt);
    $('dv-lat').textContent=dLat.toFixed(1)+'cm';
    $('dv-lon').textContent=dLon.toFixed(1)+'cm';
    $('dv-alt').textContent=dAlt.toFixed(1)+'cm';
    $('dv-hor').textContent=horDist.toFixed(1)+'cm';
    $('dv-3d').textContent=dist3d.toFixed(1)+'cm';
  } else if(pickA){
    el.classList.add('on');
    $('dv-lat').textContent='--'; $('dv-lon').textContent='--';
    $('dv-alt').textContent='--'; $('dv-hor').textContent='--';
    $('dv-3d').textContent='选第2点...';
  } else {
    el.classList.remove('on');
  }
}

function showPointInfo(idx){
  if(idx<0||idx>=pts.length) return;
  let p=pts[idx];
  let info='#'+p.idx+'  '+p.name+(p.type==='truth'?'  <span class="conf-ok">[真值]</span>':(p.type==='test'?'  <span class="conf-bad">[测试]</span>':''))+'\n'
    +'lat: '+p.lat.toFixed(9)+'\nlon: '+p.lon.toFixed(9)+'\nalt: '+p.alt.toFixed(4)+' m\nsol: '+p.pos_label;
  if(p.date) info+='\n日期: '+p.date;
  if(p.time_str) info+='  时间: '+p.time_str;
  if(p.confidence!==null && p.confidence!==undefined)
    info+='\n置信度: '+p.confidence+(p.confidence>=confThresh?'  <span class="conf-ok">✓</span>':'  <span class="conf-bad">✗</span>');
  if(p.speed!==null && p.speed!==undefined)
    info+='\n速度: '+p.speed.toFixed(2)+' m/s';
  if(p.type==='test' && p._errMatched){
    info+='\n— 与真值对比 —';
    info+='\n水平误差: '+(p._errHor*100).toFixed(1)+'cm'+(p._errHorBad?'  <span class="conf-bad">✗ 超阈值</span>':'  <span class="conf-ok">✓</span>');
    info+='\n高程误差: '+(p._errVer*100).toFixed(1)+'cm'+(p._errVerBad?'  <span class="conf-bad">✗ 超阈值</span>':'  <span class="conf-ok">✓</span>');
    if(p._errSpd!==null&&p._errSpd!==undefined){
      info+='\n速度误差: '+p._errSpd.toFixed(2)+' m/s'+(p._errSpdBad?'  <span class="conf-bad">✗ 超阈值</span>':'  <span class="conf-ok">✓</span>');
    }
  }
  // 卫星快照（GSV）：系统分组显示 PRN/仰角/方位/CN0
  let sats=(p.sats||[]);
  if(sats.length){
    info+='\n\n卫星 ('+sats.length+'):';
    let bySys={};
    for(let s of sats){ (bySys[s.sys]=bySys[s.sys]||[]).push(s); }
    let html='<table><tr><th>系统</th><th>PRN</th><th>仰角°</th><th>方位°</th><th>CN0</th></tr>';
    for(let sys of Object.keys(bySys)){
      for(let s of bySys[sys]){
        html+='<tr><td class="sys-tag">'+sys+'</td><td>'+s.prn+'</td><td>'
          +(s.elev!==null&&s.elev!==undefined?s.elev.toFixed(0):'--')+'</td><td>'
          +(s.azim!==null&&s.azim!==undefined?s.azim.toFixed(0):'--')+'</td><td>'
          +(s.cn0!==null&&s.cn0!==undefined?s.cn0.toFixed(1):'--')+'</td></tr>';
      }
    }
    html+='</table>';
    info+='\n<table-placeholder>'+html+'</table-placeholder>';
  }
  document.getElementById('info-txt').innerHTML=info
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/&lt;span class="conf-ok"&gt;✓&lt;\/span&gt;/g,'<span class="conf-ok">✓</span>')
    .replace(/&lt;span class="conf-bad"&gt;✗&lt;\/span&gt;/g,'<span class="conf-bad">✗</span>')
    .replace(/&lt;table-placeholder&gt;([\s\S]*?)&lt;\/table-placeholder&gt;/g,function(_,h){return h;});
  document.getElementById('info').classList.add('on');
}

// 着色模式：定位状态 / 置信度 / 误差超差
function applyColorMode(){
  for(let i=0;i<pts.length;i++){
    let p=pts[i];
    if(colorMode==='conf' && p.confidence!==null && p.confidence!==undefined){
      p.color = p.confidence>=confThresh ? '#00e676' : '#ff6b6b';
    } else if(colorMode==='err'){
      if(p.type==='truth'){
        p.color=ERR_COLOR_TRUTH;
      } else if(p.type==='test'){
        if(!p._errMatched){
          p.color=ERR_COLOR_UNMATCHED;
        } else {
          // 逐项超差判断：参与 = 勾选参与(✓) 且 阈值>0
          let horP=errHorEn && errHorThresh>0;
          let verP=errVerEn && errVerThresh>0;
          let spdP=errSpdEn && errSpdThresh>0 && p._errSpd!==null && p._errSpd!==undefined;
          p._errHorBad=horP && p._errHor>errHorThresh;
          p._errVerBad=verP && p._errVer>errVerThresh;
          p._errSpdBad=spdP && p._errSpd>errSpdThresh;
          let nBad=(p._errHorBad?1:0)+(p._errVerBad?1:0)+(p._errSpdBad?1:0);
          // 任一参与项超差即判超差（颜色直接表达满足的条件：黄/青/紫=单项，红=多项）
          p._errBad = nBad>0;
          if(p._errBad){
            if(nBad>=2) p.color=ERR_COLOR_MULTI;
            else if(p._errHorBad) p.color=ERR_COLOR_HOR;
            else if(p._errVerBad) p.color=ERR_COLOR_VER;
            else p.color=ERR_COLOR_SPD;
          } else {
            p.color=ERR_COLOR_OK;
          }
        }
      } else {
        p.color=PTCL[p.pos_label]||'#666';  // 无真值场景（solo）
      }
    } else {
      p.color=PTCL[p.pos_label]||'#666';
    }
  }
  draw();
}

// 误差对比：测试点 vs 真值（按时间最近邻），算水平/高程/速度误差
function computeErrors(){
  errData=null;
  for(let p of pts){ if(p.type==='test'){ p._errMatched=false; p._errHor=null; p._errVer=null; p._errSpd=null; } }
  let truth=pts.filter(p=>p.type==='truth');
  let test=pts.filter(p=>p.type==='test');
  if(!truth.length||!test.length) return;
  let tSorted=truth.slice().sort((a,b)=>a.time_s-b.time_s);
  let tTimes=tSorted.map(p=>p.time_s);
  // 二分查找最近真值点
  function nearest(sec){
    let lo=0,hi=tTimes.length-1;
    if(sec<=tTimes[0]) return tSorted[0];
    if(sec>=tTimes[hi]) return tSorted[hi];
    while(hi-lo>1){ let mid=(lo+hi)>>1; if(tTimes[mid]<=sec) lo=mid; else hi=mid; }
    let a=tSorted[lo], b=tSorted[hi];
    return (sec-tTimes[lo])<=(tTimes[hi]-sec)?a:b;
  }
  function meterDist(la1,lo1,la2,lo2){
    let dLat=(la2-la1)*111320, dLon=(lo2-lo1)*111320*Math.cos(la1*Math.PI/180);
    return Math.sqrt(dLat*dLat+dLon*dLon);
  }
  let arr=[];
  for(let k=0;k<pts.length;k++){
    let p=pts[k];
    if(p.type!=='test') continue;
    if(p.time_s===undefined||p.time_s===null) continue;
    let t=nearest(p.time_s);
    let h=Math.abs(t.time_s-p.time_s);
    if(h>2.0) continue; // 时间差 >2s 的不算（可能不是同一轨迹段）
    let hor=meterDist(t.lat,t.lon,p.lat,p.lon);
    let ver=Math.abs((t.alt||0)-(p.alt||0));
    // 速度误差：双方都有速度才计算
    let spd=null;
    if(p.speed!==null&&p.speed!==undefined&&t.speed!==null&&t.speed!==undefined){
      spd=Math.abs(p.speed-t.speed);
    }
    // 逐点挂载（供误差着色/详情用）
    p._errMatched=true; p._errHor=hor; p._errVer=ver; p._errSpd=spd;
    arr.push({t:p.time_s, hor:hor, ver:ver, spd:spd, idx:k});
  }
  if(!arr.length) return;
  arr.sort((a,b)=>a.t-b.t);
  errData={t:arr.map(x=>x.t), hor:arr.map(x=>x.hor), ver:arr.map(x=>x.ver),
           spd:arr.map(x=>x.spd), idx:arr.map(x=>x.idx)};
  errT0=errData.t[0];
  drawErrPanel();
}

// 误差面板：上中下三轨子图（水平/高程/速度），共享时间轴；保存几何供点击映射
function drawErrPanel(){
  let panel=document.getElementById('err-panel');
  if(!errData){ panel.classList.remove('on'); return; }
  panel.classList.add('on');
  let cvs=document.getElementById('err-cv'), ctx2=cvs.getContext('2d');
  let W=cvs.clientWidth||300, H=cvs.clientHeight||260;
  cvs.width=W; cvs.height=H;
  ctx2.clearRect(0,0,W,H);
  let n=errData.t.length;
  let tmin=errData.t[0], tmax=errData.t[n-1];
  let span=(tmax-tmin)||1;
  let padL=34, padR=8, padT=12, padB=14, gap=6;
  let rH=(H-padT-padB-gap*2)/3;
  let regions=[
    {label:'水平误差', color:ERR_COLOR_HOR, data:errData.hor, th:errHorThresh, en:errHorEn, unit:'m'},
    {label:'高程误差', color:ERR_COLOR_VER, data:errData.ver, th:errVerThresh, en:errVerEn, unit:'m'},
    {label:'速度误差', color:ERR_COLOR_SPD, data:errData.spd, th:errSpdThresh, en:errSpdEn, unit:'m/s'},
  ];
  ctx2.font='9px monospace';
  regions.forEach(function(rg,ri){
    let y0=padT+ri*(rH+gap);
    let vals=rg.data.filter(function(v){ return v!==null&&v!==undefined; });
    let maxV=vals.length?Math.max(0.001,...vals):0.001;
    if(rg.th>0) maxV=Math.max(maxV,rg.th);
    maxV*=1.15;
    // 区域标题（含阈值）
    ctx2.fillStyle=rg.color;
    let title=rg.label+(rg.en&&rg.th>0?(' > '+errThreshLabel(rg.th,errUnit)):(rg.en?'（未设阈值）':'（未参与）'));
    ctx2.fillText(title, padL, y0+9);
    // 阈值虚线
    if(rg.en&&rg.th>0){
      let ty=y0+rH-(rg.th/maxV)*rH;
      ctx2.strokeStyle=rg.color; ctx2.globalAlpha=0.45; ctx2.lineWidth=1; ctx2.setLineDash([3,3]);
      ctx2.beginPath(); ctx2.moveTo(padL,ty); ctx2.lineTo(W-padR,ty); ctx2.stroke();
      ctx2.setLineDash([]); ctx2.globalAlpha=1;
    }
    // 曲线（分段：null 断开）
    ctx2.strokeStyle=rg.color; ctx2.lineWidth=1.5; ctx2.beginPath();
    let started=false;
    for(let i=0;i<n;i++){
      if(rg.data[i]===null||rg.data[i]===undefined){ started=false; continue; }
      let x=padL+(errData.t[i]-tmin)/span*(W-padL-padR);
      let y=y0+rH-(rg.data[i]/maxV)*rH;
      if(!started){ ctx2.moveTo(x,y); started=true; } else ctx2.lineTo(x,y);
    }
    ctx2.stroke();
    // 超阈值点：红色圆点标出（仅参与且超阈值的点）
    if(rg.en&&rg.th>0){
      ctx2.fillStyle='#ff3355';
      for(let i=0;i<n;i++){
        if(rg.data[i]===null||rg.data[i]===undefined) continue;
        if(rg.data[i]<=rg.th) continue;
        let x=padL+(errData.t[i]-tmin)/span*(W-padL-padR);
        let y=y0+rH-(rg.data[i]/maxV)*rH;
        ctx2.beginPath(); ctx2.arc(x,y,2,0,Math.PI*2); ctx2.fill();
      }
    }
    // y 轴刻度
    ctx2.fillStyle='#777';
    ctx2.fillText('0', 3, y0+rH+3);
    ctx2.fillText(maxV.toFixed(1), 3, y0+8);
    // 区域分隔线
    if(ri>0){
      ctx2.strokeStyle='#2a2a3a'; ctx2.lineWidth=1;
      ctx2.beginPath(); ctx2.moveTo(padL-6,y0-3); ctx2.lineTo(W-padR,y0-3); ctx2.stroke();
    }
  });
  // 共享时间轴刻度（底部）
  ctx2.fillStyle='#888';
  for(let k=0;k<=4;k++){
    let t=tmin+(tmax-tmin)*k/4;
    let x=padL+k/4*(W-padL-padR);
    ctx2.fillText(t.toFixed(0)+'s', x-10, H-3);
  }
  // 保存几何供点击映射（点击图中点 → 地图选中该点）
  cvs._errGeom={n:n, tmin:tmin, span:span, padL:padL, padR:padR, padT:padT, rH:rH, gap:gap, W:W, H:H};
  updateErrStats();
}

function updateErrStats(){
  if(!errData) return;
  let n=errData.t.length;
  let spdVals=errData.spd.filter(function(v){ return v!==null&&v!==undefined; });
  function line(label, arr, unit){
    let vals=arr.filter(function(v){ return v!==null&&v!==undefined; });
    if(!vals.length) return '';
    let mean=vals.reduce(function(a,b){return a+b;},0)/vals.length;
    let sorted=vals.slice().sort(function(a,b){return a-b;});
    let p95=sorted[Math.floor(vals.length*0.95)];
    let mx=sorted[vals.length-1];
    return '<div class="es"><span class="el">'+label+'</span>'
      +'<span>均值 <b>'+mean.toFixed(2)+'</b> '+unit+'</span>'
      +'<span>95% <b>'+p95.toFixed(2)+'</b> '+unit+'</span>'
      +'<span>最大 <b>'+mx.toFixed(2)+'</b> '+unit+'</span></div>';
  }
  document.getElementById('err-stats-txt').innerHTML=
    line('水平误差', errData.hor, 'm')+
    line('高程误差', errData.ver, 'm')+
    line('速度误差', errData.spd, 'm/s');
}

// === Events ===
function setupEvents(){
  cv.addEventListener('mousedown',function(e){
    // 左键=选点/旋转；右键或中键=平移
    rightDrag=(e.button===1||e.button===2);
    if(e.button===1) e.preventDefault();   // 阻止中键自动滚动
    if(e.button===0){
      let h=hit(e.offsetX,e.offsetY);
      if(h!==-1){
        if(e.shiftKey || (pickA && !pickB)){
          // shift+click always sets B; or auto-alternate: if A exists and B doesn't, next click sets B
          pickB={idx:h, x:pts[h].x, y:pts[h].y, z:pts[h].z, alt:pts[h].alt, lat:pts[h].lat, lon:pts[h].lon};
          showPointInfo(h);
        } else {
          pickA={idx:h, x:pts[h].x, y:pts[h].y, z:pts[h].z, alt:pts[h].alt, lat:pts[h].lat, lon:pts[h].lon};
          pickB=null;
          showPointInfo(h);
        }
        updateDiff();
        draw();
        return;
      }
    }
    dragging=true; dx0=e.clientX; dy0=e.clientY; dpx=px; dpy=py;
  });

  window.addEventListener('mousemove',function(e){
    if(dragging){
      if(rightDrag){
        px=dpx+(e.clientX-dx0); py=dpy+(e.clientY-dy0);
      } else {
        rotY+=-(e.clientX-dx0)*0.005; rotX+=(e.clientY-dy0)*0.005;
      }
      dx0=e.clientX; dy0=e.clientY; dpx=px; dpy=py;
      scheduleDraw(); return;
    }
    let r=cv.getBoundingClientRect();
    let sx=e.clientX-r.left, sy=e.clientY-r.top;
    hoverIdx=hit(sx,sy,8);
    scheduleDraw();
    let t=document.getElementById('tip');
    if(hoverIdx!==-1){
      let p=pts[hoverIdx];
      t.style.display='block';
      let confTxt=(p.confidence!==null&&p.confidence!==undefined)?' conf:'+p.confidence:'';
      let spdTxt=(p.speed!==null&&p.speed!==undefined)?' spd:'+p.speed.toFixed(2)+'m/s':'';
      t.textContent='#'+p.idx+'\n'+p.name+'\nalt:'+p.alt.toFixed(3)+'m '+p.pos_label+confTxt+spdTxt;
      placeTip(t,sx,sy);
    } else {
      t.style.display='none';
    }
  });

  window.addEventListener('mouseup',function(e){ dragging=false; });
  cv.addEventListener('contextmenu',function(e){ e.preventDefault(); });

  // 光标处缩放（滚轮挂在 #view 上，避开覆盖层也能缩放；指数平滑；支持行/页 deltaMode）
  function zoomAt(sx,sy,factor){
    let ns=Math.max(0.005, Math.min(20000, scale*factor));
    let ratio=ns/scale;
    px=sx-ratio*(sx-px); py=sy-ratio*(sy-py);
    scale=ns; scheduleDraw();
  }
  cv.parentElement.addEventListener('wheel',function(e){
    e.preventDefault();
    let r=cv.getBoundingClientRect();
    let mx=e.clientX-r.left, my=e.clientY-r.top;
    let delta = e.deltaMode===1 ? e.deltaY*16 : (e.deltaMode===2 ? e.deltaY*120 : e.deltaY);
    zoomAt(mx,my,Math.exp(-delta*0.0016));
  },{passive:false});

  // 双击放大（Shift+双击缩小），都以光标为锚点
  cv.addEventListener('dblclick',function(e){
    let r=cv.getBoundingClientRect();
    let mx=e.clientX-r.left, my=e.clientY-r.top;
    zoomAt(mx,my, e.shiftKey?0.6:1.6);
  });

  window.addEventListener('resize',function(){
    let v=document.getElementById('view');
    w=v.clientWidth; h=v.clientHeight;
    cv.width=w; cv.height=h;
    draw();
    if(document.getElementById('err-panel').classList.contains('on')) drawErrPanel();
  });

  document.addEventListener('keydown',function(e){
    if(e.key==='Escape'){ pickA=null; pickB=null; updateDiff(); draw(); }
  });
}

function placeTip(t,sx,sy){
  let margin=6, left=sx+14, top=sy-24;
  if(left+t.offsetWidth>w-margin) left=sx-t.offsetWidth-14;
  left=Math.max(margin,Math.min(left,w-t.offsetWidth-margin));
  if(top+t.offsetHeight>h-margin) top=h-t.offsetHeight-margin;
  top=Math.max(margin,top);
  t.style.left=left+'px'; t.style.top=top+'px';
}

// === Controls ===
function onCtrl(id, ev, fn){
  document.getElementById(id).addEventListener(ev, fn);
}

function initControls(){
  onCtrl('btn-reset','click',function(){
    rotY=defRotY; rotX=defRotX; scale=defScale; px=defPx; py=defPy;
    pickA=null; pickB=null; updateDiff(); draw();
  });
  onCtrl('btn-fit','click',function(){
    autoFit();
    pickA=null; pickB=null; updateDiff(); draw();
  });
  onCtrl('z-exag','change',function(){
    zExag=parseFloat(this.value);
    buildWorld(); pickA=null; pickB=null; updateDiff(); draw();
  });
  onCtrl('pt-size','change',function(){
    ptR=parseInt(this.value); draw();
  });
  onCtrl('view-ang','change',function(){
    let a=parseFloat(this.value)*Math.PI/180;
    rotX=a; rotY=-0.7; draw();
  });
  // 轨迹立体带厚度：上缘+下缘曲线 + 填充面（像圆柱上下两面的周曲线）
  onCtrl('band-thickness','change',function(){
    bandThickness=parseFloat(this.value);
    draw();
  });
  // 着色模式：仅切换着色方式（滑条始终可见）
  onCtrl('color-mode','change',function(){
    colorMode=this.value;
    buildLegend(); applyColorMode();
  });
  // 定位状态过滤
  onCtrl('pos-filter','change',function(){
    posFilter=this.value;
    pickA=null; pickB=null; hoverIdx=-1; updateDiff(); buildLegend(); draw();
  });
  // 置信度阈值（0~9）：拖动即生效，并自动切到置信度着色
  onCtrl('conf-threshold','input',function(){
    confThresh=parseFloat(this.value);
    document.getElementById('conf-threshold-value').textContent=confThresh;
    if(colorMode!=='conf'){
      colorMode='conf';
      document.getElementById('color-mode').value='conf';
    }
    buildLegend(); applyColorMode();
  });
  // 误差阈值：输入框（m/cm 单位，内部统一存 m）
  function readErrInput(v){
    let x=parseFloat(v);
    if(!Number.isFinite(x)||x<0) x=0;
    return errUnit==='cm'?x/100:x;   // 转成 m
  }
  function writeErrInput(){
    if(errUnit==='cm'){
      document.getElementById('err-hor-threshold').value=+(errHorThresh*100).toFixed(2);
      document.getElementById('err-ver-threshold').value=+(errVerThresh*100).toFixed(2);
      document.getElementById('err-spd-threshold').value=+(errSpdThresh*100).toFixed(2);
    } else {
      document.getElementById('err-hor-threshold').value=errHorThresh;
      document.getElementById('err-ver-threshold').value=errVerThresh;
      document.getElementById('err-spd-threshold').value=errSpdThresh;
    }
  }
  onCtrl('err-hor-threshold','input',function(){
    errHorThresh=readErrInput(this.value);
    if(colorMode!=='err'){ colorMode='err'; document.getElementById('color-mode').value='err'; }
    buildLegend(); applyColorMode(); drawErrPanel();
  });
  onCtrl('err-ver-threshold','input',function(){
    errVerThresh=readErrInput(this.value);
    if(colorMode!=='err'){ colorMode='err'; document.getElementById('color-mode').value='err'; }
    buildLegend(); applyColorMode(); drawErrPanel();
  });
  onCtrl('err-spd-threshold','input',function(){
    errSpdThresh=readErrInput(this.value);
    if(colorMode!=='err'){ colorMode='err'; document.getElementById('color-mode').value='err'; }
    buildLegend(); applyColorMode(); drawErrPanel();
  });
  onCtrl('err-unit','change',function(){
    errUnit=this.value;
    writeErrInput();
    buildLegend(); applyColorMode(); drawErrPanel();
  });
  // 参与判定勾选框：任意组合（单独/两两/多个/全部），切换即生效
  function bindErrEn(id, setter){
    onCtrl(id,'change',function(){
      setter(this.checked);
      if(colorMode!=='err'){ colorMode='err'; document.getElementById('color-mode').value='err'; }
      buildLegend(); applyColorMode(); drawErrPanel();
    });
  }
  bindErrEn('err-hor-en',function(v){ errHorEn=v; });
  bindErrEn('err-ver-en',function(v){ errVerEn=v; });
  bindErrEn('err-spd-en',function(v){ errSpdEn=v; });
  // 误差面板开关（右侧停靠，不遮挡地图）
  onCtrl('btn-err','click',function(){
    let panel=document.getElementById('err-panel');
    if(panel.classList.contains('on')){
      panel.classList.remove('on');
    } else {
      panel.classList.add('on');
      drawErrPanel();
    }
    draw();
  });
  onCtrl('err-close','click',function(){
    document.getElementById('err-panel').classList.remove('on');
    draw();
  });
  // 点击误差图 → 地图同步选中该点（橙色圈高亮 + 详情窗）
  onCtrl('err-cv','click',function(e){
    if(!errData||!this._errGeom) return;
    let r=this.getBoundingClientRect();
    let x=e.clientX-r.left, y=e.clientY-r.top;
    let g=this._errGeom;
    if(x<g.padL||x>g.W-g.padR) return;
    let ri=Math.floor((y-g.padT)/(g.rH+g.gap));
    if(ri<0||ri>=3) return;
    let frac=(x-g.padL)/(g.W-g.padL-g.padR);
    let t=g.tmin+frac*g.span;
    let i=0, bd=Infinity;
    for(let k=0;k<g.n;k++){
      let d=Math.abs(errData.t[k]-t);
      if(d<bd){ bd=d; i=k; }
    }
    if(i>=0&&i<errData.idx.length){
      errSelIdx=errData.idx[i];
      showPointInfo(errSelIdx);
      draw();
    }
  });
}

// === Load ===
window.addEventListener('load',async function(){
  pts=EMBEDDED_POINTS;
  if(!pts.length) return;

  let invalidPoints=pts.filter(p=>
    !Number.isFinite(p.lat) || !Number.isFinite(p.lon) || !Number.isFinite(p.alt) ||
    p.lat < -90 || p.lat > 90 || p.lon < -180 || p.lon > 180 ||
    Math.abs(p.alt) > 100000
  );
  if(invalidPoints.length){
    document.getElementById('stats').innerHTML=
      '<b class="bad">数据解析失败</b><br>检测到 '+invalidPoints.length+
      ' 个异常定位点，请修正 CDR 消息布局后从原始 rosbag 重新生成。';
    return;
  }

  buildWorld();
  computeErrors();
  // 有真值数据时默认进入误差着色模式（误差条件默认勾选，打开即可看到分色）
  if(errData && colorMode==='pos'){
    colorMode='err';
    document.getElementById('color-mode').value='err';
  }
  applyColorMode();
  errSelIdx=-1;

  // color by pos_type, grouped by dataset name for legend（PTCL 为顶层常量）
  let uniq={}, ci=0;
  for(let p of pts){
    p.color=PTCL[p.pos_label]||'#666';
    if(!(p.name in uniq)){ uniq[p.name]={color:PALETTE[ci++%PALETTE.length],count:0}; }
    uniq[p.name].count++;
  }

  // group by dataset for lines
  window._groups=[]; // datasets used by the existing legend
  let grpMap={};
  for(let i=0;i<pts.length;i++){
    let n=pts[i].name;
    if(!grpMap[n]){ grpMap[n]={name:n, color:uniq[n].color, indices:[]}; window._groups.push(grpMap[n]); }
    grpMap[n].indices.push(i);
  }
  // Keep trajectories from different topics separate, even when they came from
  // the same bag directory.
  window._trackGroups=[];
  let trackMap={};
  for(let i=0;i<pts.length;i++){
    let p=pts[i], key=p.name+'\u0000'+(p.topic||'');
    if(!trackMap[key]){
      trackMap[key]={name:p.name, topic:p.topic||'', color:uniq[p.name].color, indices:[]};
      window._trackGroups.push(trackMap[key]);
    }
    trackMap[key].indices.push(i);
  }
  buildLegend();

  // canvas
  cv=document.getElementById('cv'); ctx=cv.getContext('2d');
  let v=document.getElementById('view');
  w=v.clientWidth; h=v.clientHeight;
  cv.width=w; cv.height=h;

  // 自动适配：静态毫米簇也能放大看到轨迹
  autoFit();
  defRotY=rotY; defRotX=rotX; defScale=scale; defPx=px; defPy=py;

  setupEvents();
  initControls();
  draw();
});
</script>
</body>
</html>"""


def build_offline_html(points):
    """Return a self-contained HTML document with safely embedded point data."""
    points_json = json.dumps(
        _sanitize(points),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    # Prevent dataset names or other strings from terminating the script block.
    points_json = (
        points_json
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )
    return INDEX_HTML.replace("__RTK_POINTS_JSON__", points_json, 1)


def default_output_path(dataset_names):
    """Return a descriptive, timestamped HTML path beside this script."""
    output_dir = Path(__file__).resolve().parent
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if len(dataset_names) == 1:
        safe_name = re.sub(r"[^\w.-]+", "_", dataset_names[0]).strip("._") or "rtk"
        filename_base = f"{safe_name}_{timestamp}"
    else:
        filename_base = f"rtk_{timestamp}"
    candidate = output_dir / f"{filename_base}.html"
    sequence = 2
    while candidate.exists():
        candidate = output_dir / f"{filename_base}_{sequence}.html"
        sequence += 1
    return candidate


def main():
    parser = argparse.ArgumentParser(description="RTK 3D Point Cloud Viewer")
    parser.add_argument("inputs", nargs="+",
                        help="bag_*.txt files or rosbag2 directories (auto-detected)")
    parser.add_argument(
        "--output",
        metavar="PATH",
        help="output HTML path (default: timestamped dataset name for one input, "
             "or rtk_YYYYMMDD_HHMMSS.html for multiple inputs)",
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="generate the offline HTML without opening it in a browser",
    )
    parser.add_argument("--ros2-ws", metavar="PATH",
                        help="ROS2 workspace path for .bag loading via ROS2 (optional; "
                             "by default a pure-Python parser is used which needs no ROS2)")
    parser.add_argument("--use-ros2", action="store_true",
                        help="Force using ROS2 reader instead of pure-Python CDR parser")
    parser.add_argument("--truth", metavar="PATH",
                        help="真值 NMEA 文件：与测试数据同图对比，计算水平/高程/速度误差"
                             "（按时间最近邻匹配），支持误差阈值超差着色")
    args = parser.parse_args()

    _setup_ros2_path(args.ros2_ws)

    all_pts = []
    dataset_names = []
    loader_flags = []

    def _load_one(fp, name, ptype):
        """加载单个输入，返回 (points, 加载方式说明)。"""
        p = Path(fp)
        if p.is_dir() or is_bag_dir(p):
            if is_bag_dir(p):
                if args.use_ros2:
                    pts_ = load_bag(p, name)
                    tag = "bag(ros2)"
                else:
                    pts_ = load_bag_pure(p, name)
                    tag = "bag"
                for pt in pts_:
                    pt.setdefault("type", ptype)
                return pts_, tag
            return [], "dir"
        if not p.is_file():
            return [], "missing"
        if _looks_like_nmea_file(p):
            return load_nmea(p, name, ptype=ptype), "NMEA"
        return load_txt(p, name), "txt"

    # 真值文件（若有）先加载，标记 type=truth
    truth_pts = []
    if args.truth:
        tp = Path(args.truth)
        truth_pts, tag = _load_one(tp, tp.stem, "truth")
        if truth_pts:
            dataset_names.append(tp.stem + "(真值)")
            all_pts.extend(truth_pts)
            loader_flags.append(f"[真值] {tp.name}: {len(truth_pts)} pts ({tag})")
        else:
            print(f"  SKIP truth: {args.truth} (无有效数据)", file=sys.stderr)

    for i, fp in enumerate(args.inputs):
        p = Path(fp)
        name = p.name if is_bag_dir(p) else p.stem
        ptype = "test" if args.truth else "solo"
        pts_, tag = _load_one(fp, name, ptype)
        if tag == "missing":
            print(f"  SKIP: {fp} (not found)", file=sys.stderr)
            continue
        if tag == "dir":
            print(f"  SKIP: {fp} (未识别的目录)", file=sys.stderr)
            continue
        print(f"  [{i}] {name}: {len(pts_)} pts ({tag})")
        dataset_names.append(name)
        all_pts.extend(pts_)
        loader_flags.append(f"[{'测试' if ptype=='test' else '数据'}] {name}: {len(pts_)} pts ({tag})")

    if not all_pts:
        print("No valid data.", file=sys.stderr)
        sys.exit(1)

    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        output_path = default_output_path(dataset_names)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(build_offline_html(all_pts), encoding="utf-8")

    print(f"\n  Total: {len(all_pts)} points")
    for line in loader_flags:
        print(f"    {line}")
    print(f"  Offline HTML: {output_path}")
    print("  左键=选点/详情 | 左键交替选A/B点 | Shift+左键=强制B点 | 左拖=旋转 | 右拖=平移 | 滚轮=缩放\n")

    if not args.no_open:
        if not webbrowser.open(output_path.as_uri(), new=2):
            print("  WARNING: Could not open the browser automatically.", file=sys.stderr)


if __name__ == "__main__":
    main()
