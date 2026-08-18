# -*- coding: utf-8 -*-
"""TTFF 冷启动：BPDEBUG RawObs 上星 + ProtocolDecoder.dll PVT → HTML 报告。

按 ``#Receiver Reset:N,8000013F`` 分段（N=复位序号，13F=冷启动）。
冷启动无可靠时间，X 轴从 Reset 起按 10Hz（EOE）打点。

TrackInfo 由同目录 ``bpdebug_track_dump.exe`` + ``ProtocolDecoder.dll`` 导出：
星历有效 ``sat_state bit29``、参与解算 ``sat_state bit27``、可参与位置解 ``pvt_state bit31``；
并输出按星 spans 色带。不手写 Ext 布局。

同目录模板:
  acq_report_template.html
  acq_report.js

用法（在本目录）:
  # 预览：每文件只取前 5 次冷启动
  python ttff_chobs_acq_report.py --preview

  # 仅用已有 JSON 重新套模板
  python ttff_chobs_acq_report.py --render-only ./acq_report_preview/report_data.full.json

  # 跳过 DLL/PVT（仅 RawObs）
  python ttff_chobs_acq_report.py --skip-track --preview

  # 全量
  python ttff_chobs_acq_report.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import struct
import subprocess
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_HTML = SCRIPT_DIR / "acq_report_template.html"
TEMPLATE_JS = SCRIPT_DIR / "acq_report.js"

# ProtocolDecoder.dll 导出工具（优先本工具包 bin/）
_DEFAULT_TRACK_DUMP_CANDIDATES = [
    SCRIPT_DIR / "bin" / "bpdebug_track_dump.exe",
    SCRIPT_DIR / "bpdebug_track_dump.exe",
    SCRIPT_DIR.parent / "bpdebug_track_dump.exe",
]

# ---------------------------------------------------------------------------
# BPDEBUG 协议
# ---------------------------------------------------------------------------
SYNC = bytes([0xC7, 0xE5])
NAV_CLASS = 0x80
ASCII_CLASS = 0x8F
MSG_RAWOBS = 0x02
MSG_EOE = 0x01
MSG_ASCII = 0x00
RAWOBS_ITEM_SIZE = 44

FREQUENCY_MAP = {
    1: "B1I",
    3: "B1C",
    5: "B2a",
    6: "B2b",
    7: "B3I",
    11: "L1",
    12: "L2",
    13: "L5",
    14: "E1",
    15: "E5a",
    16: "G1",
    17: "G2",
    21: "B2I",
    22: "QZL1",
    23: "QZL2",
    24: "QZL5",
    25: "E5b",
    26: "E6",
    28: "QZL6",
    29: "SBASL1",
    30: "SBASB1A",
    31: "IRNSSL5",
}

FREQ_ORDER = [
    "L1", "L2", "L5",
    "B1I", "B1C", "B2I", "B2a", "B2b", "B3I",
    "E1", "E5a", "E5b", "E6",
    "G1", "G2",
    "QZL1", "QZL2", "QZL5", "QZL6",
    "SBASL1", "SBASB1A", "IRNSSL5",
]

RESET_RE = re.compile(rb"#Receiver Reset:(\d+),([0-9A-Fa-f]+)")
# $xxGGA,time,lat,N,lon,E,quality,...
GGA_RE = re.compile(
    rb"\$\w{2}GGA,(\d{2})(\d{2})(\d{2})(?:\.(\d+))?,([^,]*),([^,]*),([^,]*),([^,]*),(\d+)"
)

K_MILESTONES = (1, 4, 8, 12, 16, 20, 24, 32)
DEFAULT_CN0_MIN = 0.0
MAX_EPOCH_CURVE = 2500
CHUNK_SIZE = 64 * 1024 * 1024
# 时间轴：从 Reset 起按 10Hz 打点（EOE 历元；与 GGA 同频）
SAMPLE_HZ = 10.0
SAMPLE_DT = 1.0 / SAMPLE_HZ
# 兼容旧字段名
GGA_HZ = SAMPLE_HZ
GGA_DT = SAMPLE_DT


def _verify_checksum(hdr: bytes, payload: bytes, ck_a: int, ck_b: int) -> bool:
    a = b = 0
    for i in range(2, 6):
        a = (a + hdr[i]) & 0xFF
        b = (b + a) & 0xFF
    for byte in payload:
        a = (a + byte) & 0xFF
        b = (b + a) & 0xFF
    return a == ck_a and b == ck_b


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    return float(sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f))


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    mean = sum(s) / len(s)
    return {
        "n": len(s),
        "mean": round(mean, 2),
        "min": round(s[0], 2),
        "p50": round(_percentile(s, 50) or 0, 2),
        "p90": round(_percentile(s, 90) or 0, 2),
        "max": round(s[-1], 2),
    }


@dataclass
class CycleStats:
    reset_n: int
    reset_code: str
    n_epochs: int = 0
    first_acq_epochs: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    total_curve: list[int] = field(default_factory=list)
    freq_curves: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    freq_prn_curves: dict[str, list[list[int]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    time_to_k_total: dict[int, int | None] = field(default_factory=dict)
    time_to_k_freq: dict[str, dict[int, int | None]] = field(default_factory=lambda: defaultdict(dict))
    peak_total: int = 0
    peak_freq: dict[str, int] = field(default_factory=dict)
    gga_first: str | None = None
    gga_last: str | None = None
    est_epoch_hz: float | None = None
    # 首次定位（GGA quality > 0）
    fix_epoch: int | None = None
    fix_gga_time: str | None = None
    fix_quality: int | None = None
    ttff_s: float | None = None


@dataclass
class _LiveCycle:
    reset_n: int
    reset_code: str
    # 当前历元累计在视星（RawObs 可分片：continue_flag bit0=1 表示后续还有）
    latest_sats: set[tuple[str, int]] = field(default_factory=set)
    meas_offset: int = 0  # 本历元已累计的 RawObs 条目数（与 BpDebugBinaryDecoder 一致）
    first_seen: dict[tuple[str, int], int] = field(default_factory=dict)  # gga sample idx
    total_curve: list[int] = field(default_factory=list)
    freq_curves: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    freq_prn_curves: dict[str, list[list[int]]] = field(
        default_factory=lambda: defaultdict(list)
    )
    time_to_k_total: dict[int, int | None] = field(
        default_factory=lambda: {k: None for k in K_MILESTONES}
    )
    time_to_k_freq: dict[str, dict[int, int | None]] = field(default_factory=dict)
    peak_total: int = 0
    peak_freq: dict[str, int] = field(default_factory=dict)
    gga_first: str | None = None
    gga_last: str | None = None
    gga_sec_first: float | None = None
    gga_sec_last: float | None = None
    gga_count: int = 0  # 复位后见到的 GGA 数（仅用于定位时刻）
    sample_count: int = 0  # 复位后已打点的历元数（含 Reset 时刻的 t=0）
    fix_epoch: int | None = None  # 首次定位时的采样下标（相对 Reset）
    fix_gga_time: str | None = None
    fix_quality: int | None = None
    fix_gga_sec: float | None = None


def _gga_seconds(h: int, m: int, s: int, frac: str | None) -> float:
    frac_s = 0.0
    if frac:
        frac_s = int(frac.ljust(6, "0")[:6]) / 1_000_000.0
    return h * 3600 + m * 60 + s + frac_s


class FileAnalyzer:
    """流式解析单个 BPDEBUG 文件。"""

    def __init__(
        self,
        file_path: Path,
        *,
        cold_code_suffix: str = "13F",
        cn0_min: float = DEFAULT_CN0_MIN,
        max_cycles: int | None = None,
        log: Callable[[str], None] | None = None,
    ):
        self.file_path = file_path
        self.cold_code_suffix = cold_code_suffix.upper()
        self.cn0_min = cn0_min
        self.max_cycles = max_cycles
        self.log = log or (lambda m: print(m, flush=True))
        self.cycles: list[CycleStats] = []
        self.skipped_resets = 0
        self._live: _LiveCycle | None = None
        self._stop = False

    def _is_cold(self, code: str) -> bool:
        return code.upper().endswith(self.cold_code_suffix)

    def _reached_limit(self) -> bool:
        return self.max_cycles is not None and len(self.cycles) >= self.max_cycles

    def _finalize(self) -> None:
        live = self._live
        if live is None:
            return

        first_acq: dict[str, list[int]] = defaultdict(list)
        for (freq, _prn), ep in live.first_seen.items():
            first_acq[freq].append(ep)

        # TTFF：相对 Reset 的采样下标 / 10Hz
        ttff_s = (
            round(live.fix_epoch * SAMPLE_DT, 2) if live.fix_epoch is not None else None
        )

        self.cycles.append(
            CycleStats(
                reset_n=live.reset_n,
                reset_code=live.reset_code,
                n_epochs=len(live.total_curve),
                first_acq_epochs=dict(first_acq),
                total_curve=live.total_curve,
                freq_curves={k: list(v) for k, v in live.freq_curves.items()},
                freq_prn_curves={
                    k: [list(prns) for prns in v]
                    for k, v in live.freq_prn_curves.items()
                },
                time_to_k_total=dict(live.time_to_k_total),
                time_to_k_freq={f: dict(m) for f, m in live.time_to_k_freq.items()},
                peak_total=live.peak_total,
                peak_freq=dict(live.peak_freq),
                gga_first=live.gga_first,
                gga_last=live.gga_last,
                est_epoch_hz=SAMPLE_HZ,
                fix_epoch=live.fix_epoch,
                fix_gga_time=live.fix_gga_time,
                fix_quality=live.fix_quality,
                ttff_s=ttff_s,
            )
        )
        self._live = None
        if self._reached_limit():
            self._stop = True
            self.log(
                f"  {self.file_path.name}: 已达 --max-cycles={self.max_cycles}，提前结束"
            )

    def _start_cycle(self, reset_n: int, code: str) -> None:
        self._finalize()
        if self._stop:
            return
        self._live = _LiveCycle(reset_n=reset_n, reset_code=code)
        # Reset 时刻先打一个空点，保证曲线从 t=0（复位）开始
        self._sample_epoch(self._live)

    def _ensure_freq_milestones(self, live: _LiveCycle, freq: str) -> None:
        if freq not in live.time_to_k_freq:
            live.time_to_k_freq[freq] = {k: None for k in K_MILESTONES}

    def _sample_epoch(self, live: _LiveCycle) -> None:
        """从 Reset 起按 10Hz 打点（Reset 时 1 点 + 之后每个 EOE 1 点）。"""
        idx = live.sample_count
        if idx < 0 or idx >= MAX_EPOCH_CURVE:
            return
        by_freq_prns: dict[str, list[int]] = defaultdict(list)
        for freq, prn in live.latest_sats:
            by_freq_prns[freq].append(int(prn))
        for freq in by_freq_prns:
            by_freq_prns[freq].sort()
        by_freq = {f: len(prns) for f, prns in by_freq_prns.items()}
        total = len(live.latest_sats)

        live.total_curve.append(total)
        for freq, cnt in by_freq.items():
            curve = live.freq_curves[freq]
            prn_curve = live.freq_prn_curves[freq]
            while len(curve) < idx:
                curve.append(0)
                prn_curve.append([])
            curve.append(cnt)
            prn_curve.append(list(by_freq_prns[freq]))
        all_freqs = set(live.freq_curves) | set(by_freq_prns)
        for freq in all_freqs:
            curve = live.freq_curves[freq]
            prn_curve = live.freq_prn_curves[freq]
            while len(curve) < idx:
                curve.append(0)
                prn_curve.append([])
            if len(curve) == idx:
                curve.append(0)
                prn_curve.append([])

        live.peak_total = max(live.peak_total, total)
        for freq, cnt in by_freq.items():
            live.peak_freq[freq] = max(live.peak_freq.get(freq, 0), cnt)
            self._ensure_freq_milestones(live, freq)
            for k in K_MILESTONES:
                if live.time_to_k_freq[freq][k] is None and cnt >= k:
                    live.time_to_k_freq[freq][k] = idx
        for k in K_MILESTONES:
            if live.time_to_k_total[k] is None and total >= k:
                live.time_to_k_total[k] = idx

        live.sample_count += 1

    def _clear_epoch_obs(self, live: _LiveCycle) -> None:
        live.latest_sats = set()
        live.meas_offset = 0

    def _feed_rawobs(self, payload: bytes) -> None:
        """解析 RawObs；支持 continue_flag 分片累加（勿用末包覆盖整历元）。"""
        live = self._live
        if live is None:
            return
        if len(payload) < 2:
            return
        meas_cnt = payload[0]
        continue_flag = payload[1]
        more_coming = (continue_flag & 0x1) != 0
        # 与 BpDebugBinaryDecoder 一致：空包直接忽略，不清掉已累计观测
        if meas_cnt == 0:
            return
        # 新分片序列起点：清空上一历元残留
        if live.meas_offset == 0:
            live.latest_sats = set()

        base = 2
        for _ in range(meas_cnt):
            if base + RAWOBS_ITEM_SIZE > len(payload):
                break
            p = payload[base : base + RAWOBS_ITEM_SIZE]
            prn = p[1]
            sig = p[2]
            cn0 = struct.unpack_from("<H", p, 4)[0] / 100.0
            freq = FREQUENCY_MAP.get(sig)
            base += RAWOBS_ITEM_SIZE
            if not freq or cn0 < self.cn0_min:
                continue
            key = (freq, prn)
            live.latest_sats.add(key)
            if key not in live.first_seen:
                # 相对 Reset 的当前/下一采样下标
                live.first_seen[key] = max(0, live.sample_count - 1)

        if more_coming:
            live.meas_offset += meas_cnt
        else:
            live.meas_offset = 0

    def _on_gga(self, m: re.Match[bytes]) -> None:
        """GGA 只用于定位时刻/质量，不负责曲线打点（打点从 Reset+EOE）。"""
        live = self._live
        if live is None:
            return
        h, mi, s = int(m.group(1)), int(m.group(2)), int(m.group(3))
        frac = m.group(4).decode("ascii") if m.group(4) else None
        quality = int(m.group(9))
        label = f"{h:02d}:{mi:02d}:{s:02d}" + (f".{frac}" if frac else "")
        sec = _gga_seconds(h, mi, s, frac)
        live.gga_count += 1
        if live.gga_first is None:
            live.gga_first = label
            live.gga_sec_first = sec
        live.gga_last = label
        live.gga_sec_last = sec

        # 首次有效定位：记录相对 Reset 的采样下标
        if live.fix_epoch is None and quality > 0:
            live.fix_epoch = max(0, live.sample_count - 1)
            live.fix_gga_time = label
            live.fix_quality = quality
            live.fix_gga_sec = sec

    def _scan_ascii(self, data: bytes) -> None:
        if self._stop:
            return
        # 按出现顺序处理 Reset / GGA，避免把复位前的旧定位 GGA 算进新一轮
        events: list[tuple[int, str, re.Match[bytes]]] = []
        for m in RESET_RE.finditer(data):
            events.append((m.start(), "reset", m))
        for m in GGA_RE.finditer(data):
            events.append((m.start(), "gga", m))
        events.sort(key=lambda x: x[0])

        for _pos, kind, m in events:
            if kind == "reset":
                reset_n = int(m.group(1))
                code = m.group(2).decode("ascii")
                if self._is_cold(code):
                    self._start_cycle(reset_n, code)
                else:
                    self.skipped_resets += 1
                    self._finalize()
                if self._stop:
                    return
            else:
                self._on_gga(m)

    def run(self) -> list[CycleStats]:
        path = self.file_path
        size = path.stat().st_size
        limit_txt = f", max_cycles={self.max_cycles}" if self.max_cycles else ""
        self.log(f"解析 {path.name} ({size / 1e9:.2f} GB{limit_txt}) ...")
        t0 = time.time()
        leftover = b""
        processed = 0
        last_pct = -1
        pkt = rawobs = eoe = 0

        with path.open("rb") as f:
            while not self._stop:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    if leftover:
                        self._scan_ascii(leftover)
                    break
                data = leftover + chunk
                n = len(data)
                off = 0
                while off < n and not self._stop:
                    sp = data.find(SYNC, off)
                    if sp < 0:
                        if n - off > 1:
                            self._scan_ascii(data[off : n - 1])
                        leftover = data[-1:] if n else b""
                        break
                    if sp > off:
                        self._scan_ascii(data[off:sp])
                        if self._stop:
                            break
                    if n - sp < 6:
                        leftover = data[sp:]
                        break
                    hdr = data[sp : sp + 6]
                    class_id = hdr[2]
                    msg_id = hdr[3]
                    length = hdr[4] | (hdr[5] << 8)
                    need = 6 + length + 2
                    if n - sp < need:
                        leftover = data[sp:]
                        break
                    payload = data[sp + 6 : sp + 6 + length]
                    ck_a = data[sp + 6 + length]
                    ck_b = data[sp + 6 + length + 1]
                    if not _verify_checksum(hdr, payload, ck_a, ck_b):
                        off = sp + 1
                        continue
                    pkt += 1
                    if class_id == NAV_CLASS and msg_id == MSG_RAWOBS:
                        rawobs += 1
                        self._feed_rawobs(payload)
                    elif class_id == NAV_CLASS and msg_id == MSG_EOE:
                        eoe += 1
                        # 从 Reset 起每个 EOE 打一个点（10Hz）；EOE 后清空历元缓存
                        if self._live is not None:
                            self._sample_epoch(self._live)
                            self._clear_epoch_obs(self._live)
                    elif class_id == ASCII_CLASS and msg_id == MSG_ASCII and length:
                        self._scan_ascii(payload)
                    off = sp + need
                else:
                    leftover = b""

                processed += len(chunk)
                pct = int(processed * 100 / size) if size else 100
                if pct >= last_pct + 5:
                    last_pct = pct
                    self.log(f"  {path.name}: {pct}%  cycles={len(self.cycles)}")

        if not self._stop:
            self._finalize()
        elapsed = time.time() - t0
        hz_vals = [c.est_epoch_hz for c in self.cycles if c.est_epoch_hz]
        med_hz = sorted(hz_vals)[len(hz_vals) // 2] if hz_vals else None
        hz_txt = f"{med_hz:.2f}" if med_hz is not None else "n/a"
        self.log(
            f"完成 {path.name}: cycles={len(self.cycles)}, pkt={pkt}, "
            f"rawobs={rawobs}, eoe={eoe}, est_hz≈{hz_txt}, {elapsed:.1f}s"
        )
        return self.cycles


def _align_mean_curve(curves: Iterable[list[int]], max_len: int | None = None) -> dict:
    arrs = [c for c in curves if c]
    if not arrs:
        return {"x": [], "mean": [], "p50": [], "p90": []}
    n = max(len(c) for c in arrs)
    if max_len:
        n = min(n, max_len)
    mean = []
    p50 = []
    p90 = []
    for i in range(n):
        col = [c[i] for c in arrs if i < len(c)]
        if not col:
            break
        col_s = sorted(col)
        mean.append(round(sum(col) / len(col), 2))
        p50.append(round(_percentile(col_s, 50) or 0, 2))
        p90.append(round(_percentile(col_s, 90) or 0, 2))
    return {"x": list(range(len(mean))), "mean": mean, "p50": p50, "p90": p90}


def summarize_device(name: str, cycles: list[CycleStats]) -> dict:
    if not cycles:
        return {"name": name, "n_cycles": 0}

    est_hz = SAMPLE_HZ

    first_sat_by_freq: dict[str, list[float]] = defaultdict(list)
    n_sats_by_freq: dict[str, list[float]] = defaultdict(list)
    for c in cycles:
        for freq, eps in c.first_acq_epochs.items():
            if eps:
                first_sat_by_freq[freq].append(float(min(eps)))
                n_sats_by_freq[freq].append(float(len(eps)))

    freqs = [f for f in FREQ_ORDER if f in first_sat_by_freq or f in n_sats_by_freq]
    for f in sorted(set(first_sat_by_freq) | set(n_sats_by_freq)):
        if f not in freqs:
            freqs.append(f)

    freq_first_stats = {f: _stats(first_sat_by_freq[f]) for f in freqs}
    freq_count_stats = {f: _stats(n_sats_by_freq[f]) for f in freqs}

    total_mean = _align_mean_curve(c.total_curve for c in cycles)
    freq_means = {}
    for f in freqs:
        freq_means[f] = _align_mean_curve(c.freq_curves.get(f, []) for c in cycles)

    trend = {
        "reset_n": [c.reset_n for c in cycles],
        "peak_total": [c.peak_total for c in cycles],
        "n_epochs": [c.n_epochs for c in cycles],
        "time_to_k": {
            str(k): [c.time_to_k_total.get(k) for c in cycles] for k in K_MILESTONES
        },
        "first_sat_epoch": {},
        "n_sats_total": [
            sum(len(v) for v in c.first_acq_epochs.values()) for c in cycles
        ],
    }
    for f in freqs:
        trend["first_sat_epoch"][f] = [
            min(c.first_acq_epochs[f]) if c.first_acq_epochs.get(f) else None
            for c in cycles
        ]

    ttk_stats = {}
    for k in K_MILESTONES:
        vals = [
            float(c.time_to_k_total[k])
            for c in cycles
            if c.time_to_k_total.get(k) is not None
        ]
        ttk_stats[str(k)] = _stats(vals)

    # 按 Reset 号明细（曲线从 Reset 起按 10Hz 历元采样）
    details = []
    for idx, c in enumerate(cycles, start=1):
        first_by_freq = {
            f: (min(c.first_acq_epochs[f]) if c.first_acq_epochs.get(f) else None)
            for f in freqs
        }
        first_sec_by_freq = {
            f: (round(ep * SAMPLE_DT, 2) if ep is not None else None)
            for f, ep in first_by_freq.items()
        }
        ttk_ep = {str(k): c.time_to_k_total.get(k) for k in (1, 4, 8, 12, 16, 20)}
        ttk_sec = {
            k: (round(v * SAMPLE_DT, 2) if v is not None else None)
            for k, v in ttk_ep.items()
        }
        details.append(
            {
                "index": idx,
                "reset_n": c.reset_n,
                "n_epochs": c.n_epochs,
                "duration_s": round(c.n_epochs * SAMPLE_DT, 2) if c.n_epochs else 0,
                "peak_total": c.peak_total,
                "n_sats": sum(len(v) for v in c.first_acq_epochs.values()),
                "ttk": ttk_ep,
                "ttk_s": ttk_sec,
                "first_by_freq": first_by_freq,
                "first_sec_by_freq": first_sec_by_freq,
                "count_by_freq": {
                    f: len(c.first_acq_epochs.get(f, [])) for f in freqs
                },
                "est_hz": SAMPLE_HZ,
                "fix_epoch": c.fix_epoch,
                "fix_gga_time": c.fix_gga_time,
                "fix_quality": c.fix_quality,
                "ttff_s": c.ttff_s,
                "total_curve": list(c.total_curve),
                "freq_curves": {f: list(c.freq_curves.get(f, [])) for f in freqs},
                "freq_prns": {
                    f: [list(prns) for prns in c.freq_prn_curves.get(f, [])]
                    for f in freqs
                },
            }
        )

    return {
        "name": name,
        "n_cycles": len(cycles),
        "est_epoch_hz": SAMPLE_HZ,
        "freqs": freqs,
        "freq_first_stats": freq_first_stats,
        "freq_count_stats": freq_count_stats,
        "total_mean_curve": total_mean,
        "freq_mean_curves": freq_means,
        "trend": trend,
        "ttk_stats": ttk_stats,
        "details": details,
        "peak_total_stats": _stats([float(c.peak_total) for c in cycles]),
    }


def build_report(
    devices: list[dict],
    *,
    input_dir: str,
    cn0_min: float,
    preview: bool = False,
    max_cycles: int | None = None,
) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "input_dir": input_dir,
        "cn0_min": cn0_min,
        "preview": preview,
        "max_cycles": max_cycles,
        "devices": devices,
        "milestones": list(K_MILESTONES),
    }


def _safe_name(name: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)


def resolve_track_dump_exe(explicit: str | None = None) -> Path | None:
    """定位 bpdebug_track_dump.exe（链 ProtocolDecoder.dll）。"""
    if explicit:
        p = Path(explicit)
        return p if p.is_file() else None
    env = os.environ.get("BPDEBUG_TRACK_DUMP")
    if env:
        p = Path(env)
        if p.is_file():
            return p
    for p in _DEFAULT_TRACK_DUMP_CANDIDATES:
        if p.is_file():
            return p
    return None


def run_track_dump(
    log_path: Path,
    *,
    exe: Path,
    cold_suffix: str = "13F",
    max_cycles: int | None = None,
    cache_dir: Path | None = None,
    log: Callable[[str], None] | None = None,
) -> dict | None:
    """调用 DLL 工具导出 TrackInfo PVT 曲线；失败返回 None。

    - 已解析过的文件（同名 .track.json 缓存）直接复用，避免大文件重复等待数小时；
    - 未命中缓存时调用 bpdebug_track_dump.exe 解析，超时放宽到 30 分钟
      （1.2GB 级 BPDEBUG 日志解析可达 30min+，原 600s 硬超时会误杀）。
    """
    _log = log or (lambda m: print(m, flush=True))
    out_dir = cache_dir or (log_path.parent / "_track_dump_cache")
    out_dir.mkdir(parents=True, exist_ok=True)
    # v2: 含 sat_state 星历/参与解算 + 按星 spans
    tag = f"m{max_cycles}.v2" if max_cycles else "full.v2"
    out_json = out_dir / f"{log_path.stem}.{tag}.track.json"

    # —— 缓存命中：直接复用上次解析结果，秒出 ——
    if out_json.is_file():
        try:
            data = json.loads(out_json.read_text(encoding="utf-8"))
            _log(
                f"  TrackInfo(DLL) 复用缓存: {out_json.name} "
                f"(cycles={data.get('n_cycles')} track={data.get('track_frames')})"
            )
            return data
        except Exception:
            _log(f"  TrackInfo(DLL) 缓存损坏，重新解析: {out_json.name}")
            try:
                out_json.unlink()
            except OSError:
                pass

    cmd = [str(exe), str(log_path), "-o", str(out_json), "--cold-suffix", cold_suffix]
    if max_cycles is not None:
        cmd.extend(["--max-cycles", str(max_cycles)])
    _log(f"  TrackInfo(DLL): {exe.name} → {out_json.name}（大文件可能需要较长时间，请耐心等待）")
    t0 = time.time()
    try:
        # 注意：bpdebug_track_dump.exe 链接 Qt6，作为子进程时用管道捕获 stdout/stderr
        # 偶发因句柄不关闭或等待 stdin 而挂起；改为 DEVNULL 并加硬超时，从根本上避免阻塞。
        # 1.2GB 级 BPDEBUG 日志解析可达 30min+，超时给 1800s。
        proc = subprocess.run(
            cmd,
            cwd=str(exe.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=1800,
            check=False,
            # 主程序是 GUI（pythonw），不传此标志时 Windows 会给子进程新建一个
            # 控制台窗口（就是运行快结束时弹出的那个黑窗）。加 CREATE_NO_WINDOW 消除。
            creationflags=(
                subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            ),
        )
    except OSError as e:
        _log(f"  TrackInfo(DLL) 启动失败: {e}")
        return None
    except subprocess.TimeoutExpired:
        _log("  TrackInfo(DLL) 超时（>30min），跳过 PVT 曲线；下次运行若已有缓存将直接复用")
        return None
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        _log(f"  TrackInfo(DLL) 失败 code={proc.returncode}: {' | '.join(err)}")
        return None
    if not out_json.is_file():
        _log("  TrackInfo(DLL) 未生成 JSON")
        return None
    data = json.loads(out_json.read_text(encoding="utf-8"))
    _log(
        f"  TrackInfo(DLL) OK: cycles={data.get('n_cycles')} "
        f"track={data.get('track_frames')} eoe={data.get('eoe_frames')} "
        f"({time.time() - t0:.1f}s)"
    )
    return data


def attach_pvt_from_track(details: list[dict], track: dict | None) -> None:
    """按 reset_n 把 DLL 导出的 TrackInfo（可参与 / 星历 / 参与解算）并入 detail。"""
    if not track:
        return
    by_n = {int(c["reset_n"]): c for c in track.get("cycles", []) if "reset_n" in c}
    for det in details:
        tc = by_n.get(int(det["reset_n"]))
        if not tc:
            continue
        det["pvt_source"] = track.get("source", "ProtocolDecoder.dll")
        det["pvt_mask"] = track.get("pvt_mask", "0x80000000")
        det["eph_mask"] = track.get("eph_mask", "0x20000000")
        det["fix_mask"] = track.get("fix_mask", "0x08000000")
        det["pvt_total_curve"] = list(tc.get("pvt_total_curve") or [])
        det["pvt_freq_curves"] = {
            f: list(arr) for f, arr in (tc.get("pvt_freq_curves") or {}).items()
        }
        det["pvt_freq_prns"] = {
            f: [list(prns) for prns in arr]
            for f, arr in (tc.get("pvt_freq_prns") or {}).items()
        }
        freqs = list(tc.get("freqs") or [])
        if not freqs:
            freqs = sorted(det["pvt_freq_curves"].keys())
        ordered = [f for f in FREQ_ORDER if f in freqs]
        for f in freqs:
            if f not in ordered:
                ordered.append(f)
        det["pvt_freqs"] = ordered

        # 卫星级：星历有效 / 参与解算（相对 Reset 秒）
        det["eph_total_curve"] = list(tc.get("eph_total_curve") or [])
        det["fix_total_curve"] = list(tc.get("fix_total_curve") or [])
        det["eph_prns"] = [list(row) for row in (tc.get("eph_prns") or [])]
        det["fix_prns"] = [list(row) for row in (tc.get("fix_prns") or [])]
        det["sats"] = list(tc.get("sats") or [])


def _split_report_payload(report: dict) -> tuple[dict, list[tuple[str, int, dict]]]:
    """拆成轻量 meta + 按文件/Reset 的重数据块。"""
    cycle_files: list[tuple[str, int, dict]] = []
    meta_devices = []
    for dev in report.get("devices", []):
        name = dev.get("name", "device")
        slim_details = []
        for det in dev.get("details", []):
            reset_n = int(det["reset_n"])
            heavy = {
                "name": name,
                "reset_n": reset_n,
                "index": det.get("index"),
                "n_epochs": det.get("n_epochs"),
                "duration_s": det.get("duration_s"),
                "peak_total": det.get("peak_total"),
                "n_sats": det.get("n_sats"),
                "ttk": det.get("ttk"),
                "ttk_s": det.get("ttk_s"),
                "first_by_freq": det.get("first_by_freq"),
                "first_sec_by_freq": det.get("first_sec_by_freq"),
                "count_by_freq": det.get("count_by_freq"),
                "est_hz": det.get("est_hz"),
                "fix_epoch": det.get("fix_epoch"),
                "fix_gga_time": det.get("fix_gga_time"),
                "fix_quality": det.get("fix_quality"),
                "ttff_s": det.get("ttff_s"),
                "freqs": dev.get("freqs", []),
                "total_curve": det.get("total_curve", []),
                "freq_curves": det.get("freq_curves", {}),
                "freq_prns": det.get("freq_prns", {}),
                "pvt_source": det.get("pvt_source"),
                "pvt_mask": det.get("pvt_mask"),
                "eph_mask": det.get("eph_mask"),
                "fix_mask": det.get("fix_mask"),
                "pvt_total_curve": det.get("pvt_total_curve", []),
                "pvt_freq_curves": det.get("pvt_freq_curves", {}),
                "pvt_freq_prns": det.get("pvt_freq_prns", {}),
                "pvt_freqs": det.get("pvt_freqs", []),
                "eph_total_curve": det.get("eph_total_curve", []),
                "fix_total_curve": det.get("fix_total_curve", []),
                "eph_prns": det.get("eph_prns", []),
                "fix_prns": det.get("fix_prns", []),
                "sats": det.get("sats", []),
            }
            cycle_files.append((name, reset_n, heavy))
            slim_details.append(
                {
                    "index": det.get("index"),
                    "reset_n": reset_n,
                    "n_epochs": det.get("n_epochs"),
                    "duration_s": det.get("duration_s"),
                    "peak_total": det.get("peak_total"),
                    "n_sats": det.get("n_sats"),
                    "ttff_s": det.get("ttff_s"),
                    "fix_gga_time": det.get("fix_gga_time"),
                    "fix_quality": det.get("fix_quality"),
                    "data_js": f"data/{_safe_name(name)}/r{reset_n}.js",
                }
            )
        meta_devices.append(
            {
                "name": name,
                "n_cycles": dev.get("n_cycles"),
                "est_epoch_hz": dev.get("est_epoch_hz", GGA_HZ),
                "freqs": dev.get("freqs", []),
                "peak_total_stats": dev.get("peak_total_stats", {}),
                "details": slim_details,
            }
        )
    meta = {
        "generated_at": report.get("generated_at"),
        "input_dir": report.get("input_dir"),
        "cn0_min": report.get("cn0_min"),
        "preview": report.get("preview"),
        "max_cycles": report.get("max_cycles"),
        "gga_hz": SAMPLE_HZ,
        "sample_hz": SAMPLE_HZ,
        "pvt_source": report.get("pvt_source"),
        "pvt_mask": report.get("pvt_mask"),
        "pvt_meaning": report.get("pvt_meaning"),
        "eph_mask": report.get("eph_mask"),
        "eph_meaning": report.get("eph_meaning"),
        "fix_mask": report.get("fix_mask"),
        "fix_meaning": report.get("fix_meaning"),
        "devices": meta_devices,
    }
    return meta, cycle_files


def write_html(report: dict, out_dir: Path) -> Path:
    """复制模板；meta 写 report_data.js；曲线/PRN 按文件×Reset 拆分到 data/。"""
    if not TEMPLATE_HTML.is_file():
        raise FileNotFoundError(f"缺少 HTML 模板: {TEMPLATE_HTML}")
    if not TEMPLATE_JS.is_file():
        raise FileNotFoundError(f"缺少 JS 模板: {TEMPLATE_JS}")

    out_dir.mkdir(parents=True, exist_ok=True)
    meta, cycle_files = _split_report_payload(report)

    # 清理旧产物，避免残留大文件
    data_root = out_dir / "data"
    if data_root.exists():
        shutil.rmtree(data_root)
    data_root.mkdir(parents=True, exist_ok=True)
    for stale in ("report_data.json", "report_data.js", "report_meta.json", "report_data.full.json"):
        p = out_dir / stale
        if p.exists():
            p.unlink()

    for name, reset_n, heavy in cycle_files:
        ddir = data_root / _safe_name(name)
        ddir.mkdir(parents=True, exist_ok=True)
        key = f"{name}|{reset_n}"
        body = (
            "window.__CYCLE_STORE=window.__CYCLE_STORE||{};\n"
            f"window.__CYCLE_STORE[{json.dumps(key, ensure_ascii=False)}]="
            + json.dumps(heavy, ensure_ascii=False, separators=(",", ":"))
            + ";\n"
        )
        (ddir / f"r{reset_n}.js").write_text(body, encoding="utf-8")

    (out_dir / "report_data.js").write_text(
        "const REPORT = "
        + json.dumps(meta, ensure_ascii=False, separators=(",", ":"))
        + ";\n",
        encoding="utf-8",
    )
    # 完整 JSON 仅作备份（体积大）；日常页面不加载
    (out_dir / "report_data.full.json").write_text(
        json.dumps(report, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    (out_dir / "report_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    html_path = out_dir / "index.html"
    shutil.copy2(TEMPLATE_HTML, html_path)
    shutil.copy2(TEMPLATE_JS, out_dir / "acq_report.js")
    return html_path


def main(argv: list[str] | None = None) -> int:
    here = SCRIPT_DIR
    ap = argparse.ArgumentParser(description="TTFF 冷启动 CHOBS 上星速度 HTML 报告")
    ap.add_argument("--input", "-i", default=str(here), help="BPDEBUG 日志目录或单个 .log（默认=脚本所在目录）")
    ap.add_argument("--output", "-o", default=None, help="报告输出目录（默认见下）")
    ap.add_argument("--cn0-min", type=float, default=DEFAULT_CN0_MIN, help="上星 CN0 阈值 dB-Hz")
    ap.add_argument("--cold-suffix", default="13F", help="冷启动复位码后缀，默认 13F")
    ap.add_argument(
        "--max-cycles",
        type=int,
        default=None,
        help="每文件最多解析多少次完整冷启动（用于预览；需看到下一次复位才收口）",
    )
    ap.add_argument(
        "--preview",
        action="store_true",
        help="预览模式：等价于 --max-cycles 5，输出到 acq_report_preview",
    )
    ap.add_argument(
        "--render-only",
        metavar="JSON",
        default=None,
        help="不解析日志，用已有完整 JSON（建议 report_data.full.json）重新拆包并套模板",
    )
    ap.add_argument(
        "--track-dump-exe",
        default=None,
        help="bpdebug_track_dump.exe 路径（默认：本目录或 GnssStudio build）",
    )
    ap.add_argument(
        "--skip-track",
        action="store_true",
        help="跳过 ProtocolDecoder.dll TrackInfo/PVT 导出（仅 RawObs）",
    )
    args = ap.parse_args(argv)

    if args.render_only:
        json_path = Path(args.render_only)
        report = json.loads(json_path.read_text(encoding="utf-8"))
        # 若传入的是瘦 meta，尝试同目录 full
        if report.get("devices") and not report["devices"][0].get("details", [{}])[0].get("freq_curves"):
            full = json_path.parent / "report_data.full.json"
            if full.is_file():
                report = json.loads(full.read_text(encoding="utf-8"))
        out_dir = Path(args.output) if args.output else json_path.parent
        html_path = write_html(report, out_dir)
        print(f"HTML 报告: {html_path}")
        return 0

    max_cycles = args.max_cycles
    preview = bool(args.preview)
    if preview and max_cycles is None:
        max_cycles = 5

    in_path = Path(args.input)
    if in_path.is_file():
        files = [in_path]
        input_dir = str(in_path.parent)
    else:
        files = sorted(p for p in in_path.glob("*.log"))
        input_dir = str(in_path)
    if not files:
        print(f"未找到 .log: {in_path}")
        return 1

    if args.output:
        out_dir = Path(args.output)
    elif preview:
        out_dir = Path(input_dir) / "acq_report_preview"
    else:
        out_dir = Path(input_dir) / "acq_report"

    if max_cycles:
        print(f"[预览/限流] 每文件最多 {max_cycles} 次冷启动 → {out_dir}", flush=True)

    track_exe = None if args.skip_track else resolve_track_dump_exe(args.track_dump_exe)
    if args.skip_track:
        print("跳过 TrackInfo/PVT（--skip-track）", flush=True)
    elif track_exe is None:
        print(
            "未找到 bpdebug_track_dump.exe，PVT 曲线将为空。"
            "请编译 tools/bpdebug_track_dump 或指定 --track-dump-exe",
            flush=True,
        )
    else:
        print(f"TrackInfo 工具: {track_exe} (ProtocolDecoder.dll)", flush=True)

    devices = []
    for fp in files:
        ana = FileAnalyzer(
            fp,
            cold_code_suffix=args.cold_suffix,
            cn0_min=args.cn0_min,
            max_cycles=max_cycles,
        )
        cycles = ana.run()
        dev = summarize_device(fp.stem, cycles)
        if track_exe is not None:
            track = run_track_dump(
                fp,
                exe=track_exe,
                cold_suffix=args.cold_suffix,
                max_cycles=max_cycles,
                cache_dir=Path(input_dir) / "_track_dump_cache",
            )
            attach_pvt_from_track(dev.get("details") or [], track)
        devices.append(dev)

    report = build_report(
        devices,
        input_dir=input_dir,
        cn0_min=args.cn0_min,
        preview=preview or (max_cycles is not None),
        max_cycles=max_cycles,
    )
    if track_exe is not None:
        report["pvt_source"] = "ProtocolDecoder.dll"
        report["pvt_mask"] = "0x80000000"
        report["pvt_meaning"] = "可参与位置解算 (pvt_state bit31)"
        report["eph_mask"] = "0x20000000"
        report["eph_meaning"] = "星历有效 (sat_state bit29)"
        report["fix_mask"] = "0x08000000"
        report["fix_meaning"] = "参与解算 (sat_state bit27)"
    html_path = write_html(report, out_dir)
    print(f"\nHTML 报告: {html_path}")
    print(f"Meta JS: {out_dir / 'report_data.js'}")
    print(f"分片数据: {out_dir / 'data'}/<文件>/r<Reset>.js")
    print(f"完整备份: {out_dir / 'report_data.full.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
