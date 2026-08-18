"""统一 TTFF 报告：合并 NMEA 文本日志分析与 BPDEBUG 二进制捕获分析。

设计要点：
- 两套解析引擎各自保留，本模块只做「统一入口 + 统一报告」：
    * NMEA 文本日志  -> ttff_tool/ttff_analyzer（首次定位 TTFF）
    * BPDEBUG 二进制  -> ttff_acq_report_toolkit（冷启动上星/捕获速度，需 ProtocolDecoder.dll）
- 自动识别每个输入文件的格式（detect_format），分别路由到对应引擎；
- 各引擎把报告写到统一输出目录下的 ttff/ 与 bpdebug/ 子目录，
  再由 build_master_index 生成一份深色主题的入口报告（merged_ttff_report.html）：
  ⏳ TTFF 分析（必现，覆盖全部输入文件）与 📡 BPDEBUG 搜星情况（可选，仅 BPDEBUG
  文件且勾选「输出 BPDEBUG 报告」时）通过标签页切换，实现「从一个报告输出」。
"""

import re
import sys
import json
import datetime
from pathlib import Path
from typing import Callable

from app.paths import get_project_root

ROOT = get_project_root()
TTFF_TOOL = ROOT / "ttff_tool"
ACQ_TOOLKIT = ROOT / "ttff_acq_report_toolkit" / "ttff_acq_report_toolkit"

# 统一输出目录名（功能专属：output/ttff_merged）
FEATURE_KEY = "ttff_merged"

# BPDEBUG 同步头 0xC7 0xE5
_BPDEBUG_SYNC = b"\xc7\xe5"
# BPDEBUG ASCII 帧头（MSG_ASCII=0x00 承载 NMEA 文本）
_ASCII_FRAME_HDR = b"\xc7\xe5\x8f\x00"
# 行首 NMEA 语句（独立成行的纯文本，区别于嵌在二进制帧内）
_NMEA_LINE_RE = re.compile(rb"(?:^|\n)\$([A-Z]{2})(GGA|RMC),")
# 兼容旧引用（宽松：任意 Talker+语句类型）
_NMEA_RE = re.compile(rb"\$(?:GP|GN|GL|BD|GA|GB|GQ|GI)[A-Z]{3}")


def _verify_bpdebug_frame(b: bytes, idx: int) -> bool:
    """校验 idx 处的 0xC7 0xE5 是否是一个校验和有效的 BPDEBUG 真帧。"""
    if idx + 6 > len(b):
        return False
    hdr = b[idx:idx + 6]
    if hdr[0] != 0xC7 or hdr[1] != 0xE5:
        return False
    length = hdr[4] | (hdr[5] << 8)
    end = idx + 6 + length + 2
    if end > len(b):
        return False
    payload = b[idx + 6:idx + 6 + length]
    ck_a = b[idx + 6 + length]
    ck_b = b[idx + 6 + length + 1]
    a = bb = 0
    for i in range(2, 6):
        a = (a + hdr[i]) & 0xFF
        bb = (bb + a) & 0xFF
    for byte in payload:
        a = (a + byte) & 0xFF
        bb = (bb + a) & 0xFF
    return a == ck_a and bb == ck_b


def _count_valid_frames(b: bytes, sample: int = 40) -> tuple[int, int]:
    """统计前 sample 个同步头里校验通过的帧数（强 BPDEBUG 信号）。"""
    idx = b.find(_BPDEBUG_SYNC)
    valid = tried = 0
    while idx != -1 and tried < sample:
        tried += 1
        if _verify_bpdebug_frame(b, idx):
            valid += 1
        idx = b.find(_BPDEBUG_SYNC, idx + 1)
    return valid, tried


def _count_standalone_nmea(b: bytes) -> int:
    """统计独立成行（行首）的 NMEA 语句数。"""
    return len(_NMEA_LINE_RE.findall(b))


def _printable_ratio(b: bytes) -> float:
    if not b:
        return 0.0
    pr = sum(1 for c in b if 32 <= c < 127 or c in (9, 10, 13))
    return pr / len(b)


def _classify_chunk(chunk: bytes) -> str | None:
    """对一段字节做格式判定，返回 'nmea' / 'bpdebug' / None（无法判定）。

    关键发现（实测）：同一份 GNSS 日志常常**同时**含 NMEA 文本与 BPDEBUG 二进制
    帧（如 LG 模块既输出 NMEA 文本行，也输出 0xC7 0xE5 二进制帧）。因此不能简单
    按“出现 NMEA 即 NMEA / 出现同步头即 BPDEBUG”一刀切。

    判定逻辑：
    - 校验和有效的 BPDEBUG 真帧是强信号：纯 NMEA 噪声字节几乎不可能凑出校验
      通过的帧（随机数据实测 0/60），只有真正的二进制帧结构才会通过；
    - 独立成行的 NMEA 语句是文本日志信号；
    - 两者都出现时（双格式），按**文本密度**（可打印字节比例）决定主导格式：
      文本密集 → NMEA（如 17_0805.log，可打印≈0.69），二进制密集 → BPDEBUG
      （如 LG690P_tongxian_1.log，可打印≈0.42）。
    """
    if not chunk:
        return None
    vframes, _ = _count_valid_frames(chunk)
    n_nmea = _count_standalone_nmea(chunk)
    has_frames = vframes >= 3          # 真帧密集
    has_nmea = n_nmea >= 3            # 文本语句充足
    if has_frames and not has_nmea:
        return "bpdebug"
    if has_nmea and not has_frames:
        return "nmea"
    if has_frames and has_nmea:
        return "bpdebug" if _printable_ratio(chunk) < 0.5 else "nmea"
    if has_frames:
        return "bpdebug"
    if has_nmea:
        return "nmea"
    return None


def detect_format(path: str, probe: int = 4 << 20) -> str:
    """识别单个日志的格式。返回 'bpdebug' / 'nmea' / 'unknown'。

    先扫前 4MB；若仍无法判定（如开头是长 ASCII 头或噪声段）再扩扫 8MB。
    """
    p = Path(path)
    if not p.is_file():
        return "unknown"
    try:
        with p.open("rb") as f:
            head = f.read(probe)
    except OSError:
        return "unknown"
    res = _classify_chunk(head)
    if res:
        return res
    try:
        with p.open("rb") as f:
            chunk = f.read(8 << 20)
    except OSError:
        return "unknown"
    res = _classify_chunk(chunk)
    if res:
        return res
    return "unknown"


# ---------------------------------------------------------------------------
# NMEA 板块：复用 ttff_tool/ttff_analyzer（经 ttff_runner 写报告）
# ---------------------------------------------------------------------------

def _run_ttff(ttff_specs: list[dict], settings: dict, out_ttff: Path,
              log_callback: Callable[[str], None]) -> str | None:
    """调用 ttff_runner 生成 TTFF 报告（覆盖 NMEA 与 BPDEBUG 两类文件），
    返回 HTML 路径（无数据返回 None）。"""
    if not ttff_specs:
        return None
    sys.path.insert(0, str(TTFF_TOOL))
    from app.core import ttff_runner

    ttff_settings = {
        "output_html": settings.get("nmea_output_html") or "TTFF统计报告.html",
        "output_json": settings.get("nmea_output_json") or "ttff_results.json",
        "default_date": settings.get("default_date") or "040826",
    }
    out_ttff.mkdir(parents=True, exist_ok=True)
    try:
        html = ttff_runner.run_config(
            ttff_specs, ttff_settings,
            output_dir=str(out_ttff), log_callback=log_callback,
        )
        return html
    except Exception as exc:  # 单板块失败不应拖垮整份报告
        log_callback(f"[警告] TTFF 报告生成失败：{exc}")
        return None


# ---------------------------------------------------------------------------
# BPDEBUG 板块：复用 ttff_acq_report_toolkit（写 report 到 out_bpdebug）
# ---------------------------------------------------------------------------

def _run_bpdebug(bpdebug_files: list[str], settings: dict, out_bpdebug: Path,
                 log_callback: Callable[[str], None]) -> str | None:
    """调用 BPDEBUG 工具包生成捕获分析板块，返回 index.html 路径（无数据返回 None）。"""
    if not bpdebug_files:
        return None
    if not ACQ_TOOLKIT.is_dir():
        log_callback("[警告] 未找到 ttff_acq_report_toolkit，跳过 BPDEBUG 板块")
        return None
    sys.path.insert(0, str(ACQ_TOOLKIT))
    try:
        from ttff_chobs_acq_report import (
            FileAnalyzer, summarize_device, build_report, write_html,
            run_track_dump, attach_pvt_from_track, resolve_track_dump_exe,
        )
    except Exception as exc:
        log_callback(f"[警告] 导入 BPDEBUG 工具包失败：{exc}")
        return None

    cold_suffix = str(settings.get("cold_suffix") or "13F").upper()
    cn0_min = float(settings.get("cn0_min", 0.0))
    max_cycles = settings.get("max_cycles")
    skip_track = bool(settings.get("skip_track"))

    track_exe = None if skip_track else resolve_track_dump_exe()
    if skip_track:
        log_callback("跳过 TrackInfo/PVT（skip_track）")
    elif track_exe is None:
        log_callback("未找到 bpdebug_track_dump.exe，PVT 曲线将为空（仅 RawObs）")

    devices = []
    for fp in bpdebug_files:
        fp_path = Path(fp)
        log_callback(f"分析(BPDEBUG): {fp_path.name}")
        try:
            ana = FileAnalyzer(
                fp_path, cold_code_suffix=cold_suffix, cn0_min=cn0_min,
                max_cycles=max_cycles, log=log_callback,
            )
            cycles = ana.run()
            dev = summarize_device(fp_path.stem, cycles)
            devices.append(dev)
            log_callback(f"  -> 冷启动循环 {len(cycles)} 次")
        except Exception as exc:
            log_callback(f"[警告] BPDEBUG 解析失败 {fp_path.name}：{exc}")
            continue

        # TrackInfo/PVT 经 ProtocolDecoder.dll 解析，独立于帧解析：
        # 即使 DLL/子进程失败或超时，冷启动板块（RawObs + 循环统计）仍保留。
        if track_exe is not None:
            try:
                track = run_track_dump(
                    fp_path, exe=track_exe, cold_suffix=cold_suffix,
                    max_cycles=max_cycles,
                    cache_dir=fp_path.parent / "_track_dump_cache",
                    log=log_callback,
                )
                attach_pvt_from_track(dev.get("details") or [], track)
            except Exception as exc:
                log_callback(f"[警告] TrackInfo/PVT 解析失败 {fp_path.name}：{exc}")

    if not devices:
        return None

    parent_dir = str(Path(bpdebug_files[0]).parent)
    report = build_report(
        devices, input_dir=parent_dir, cn0_min=cn0_min,
        preview=bool(max_cycles), max_cycles=max_cycles,
    )
    if track_exe is not None:
        report["pvt_source"] = "ProtocolDecoder.dll"
        report["pvt_mask"] = "0x80000000"
        report["pvt_meaning"] = "可参与位置解算 (pvt_state bit31)"
        report["eph_mask"] = "0x20000000"
        report["eph_meaning"] = "星历有效 (sat_state bit29)"
        report["fix_mask"] = "0x08000000"
        report["fix_meaning"] = "参与解算 (sat_state bit27)"

    out_bpdebug.mkdir(parents=True, exist_ok=True)
    try:
        html_path = write_html(report, out_bpdebug)
        return str(html_path)
    except Exception as exc:
        log_callback(f"[警告] BPDEBUG 报告写出失败：{exc}")
        return None


# ---------------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------------

def run_unified(files: list[dict], settings: dict, output_dir: str = "",
                log_callback: Callable[[str], None] = print) -> str:
    """统一运行：按格式分流两套引擎，生成「分板块并列」的合并报告。

    files:    [{"file","reset_marker","name","note"}, ...]
              reset_marker 仅对 NMEA 生效；BPDEBUG 用 cold_suffix 检测冷启动。
    settings: {"default_date","nmea_output_html","nmea_output_json",
               "cold_suffix","cn0_min","max_cycles","skip_track",
               "include_bpdebug_report"}
    output_dir: 统一输出目录（缺省取 output/ttff_merged/）
    """
    if not files:
        raise ValueError("未提供任何输入文件")

    # 解析输出目录
    if output_dir:
        out_dir = Path(output_dir)
    else:
        from app.output_dirs import make_run_dir
        out_dir = make_run_dir(FEATURE_KEY)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_ttff = out_dir / "ttff"
    out_bpdebug = out_dir / "bpdebug"

    include_bpdebug_report = bool(settings.get("include_bpdebug_report", True))

    log_callback(f"统一输出目录: {out_dir}")

    # 分流：所有文件都做 TTFF（NMEA 文本 / BPDEBUG 二进制均可直接解析 TTFF，
    # 已验证 BPDEBUG 内嵌的 NMEA 语句可被 ttff_analyzer 直接识别）；
    # 仅 BPDEBUG 文件额外按需生成「搜星情况」报告。
    ttff_specs, bpdebug_files, skipped = [], [], []
    for spec in files:
        fp = spec.get("file", "").strip()
        if not fp or not Path(fp).exists():
            log_callback(f"[跳过] 文件不存在: {fp}")
            continue
        fmt = detect_format(fp)
        if fmt == "bpdebug":
            bpdebug_files.append(fp)
        elif fmt == "unknown":
            skipped.append(fp)
            log_callback(f"[提示] 无法识别格式，仍按通用方式尝试 TTFF: {fp}")
        # TTFF：两种格式都纳入（BPDEBUG 同样可算 TTFF）
        ttff_specs.append({
            "file": fp,
            "reset_marker": spec.get("reset_marker") or "$RESET",
            "name": spec.get("name") or Path(fp).stem,
            "note": (spec.get("note", "") or "")
                     + ("" if fmt == "nmea" else " [BPDEBUG]"),
        })
    log_callback(
        f"TTFF 分析: {len(ttff_specs)} 个文件（其中 BPDEBUG {len(bpdebug_files)} 个）；"
        f"BPDEBUG 搜星报告: {'是' if (include_bpdebug_report and bpdebug_files) else '否'}"
    )

    # TTFF 报告（必生成，覆盖所有文件）
    ttff_html = _run_ttff(ttff_specs, settings, out_ttff, log_callback)
    # BPDEBUG 搜星情况报告（可选）
    bpdebug_html = None
    if include_bpdebug_report and bpdebug_files:
        bpdebug_html = _run_bpdebug(bpdebug_files, settings, out_bpdebug, log_callback)

    # 写合并入口（深色主题，TTFF 必现，BPDEBUG 可选）
    master = _build_master_index(out_dir, ttff_html, bpdebug_html,
                                 ttff_count=len(ttff_specs),
                                 bpdebug_count=len(bpdebug_files))
    log_callback(f"完成 -> 合并报告: {master}")
    return str(master)


# ---------------------------------------------------------------------------
# 合并入口 HTML（分板块并列，iframe 标签页）
# ---------------------------------------------------------------------------

def _build_master_index(out_dir: Path, ttff_html: str | None,
                        bpdebug_html: str | None, ttff_count: int,
                        bpdebug_count: int) -> Path:
    gen_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    def _rel(html_abs: str) -> str:
        if not html_abs:
            return ""
        try:
            return str(Path(html_abs).relative_to(out_dir).as_posix())
        except ValueError:
            return ""

    ttff_rel = _rel(ttff_html) if ttff_html else ""
    bpdebug_rel = _rel(bpdebug_html) if bpdebug_html else ""

    def _panel(tab_id: str, label: str, icon: str, rel: str, count: int, desc: str) -> str:
        if rel:
            body = f'<iframe class="frame" src="{rel}"></iframe>'
        else:
            body = (f'<div class="empty">本批次未包含该类型数据'
                    f'（{label}），或生成失败。</div>')
        open_link = (f'<a class="openlink" href="{rel}" target="_blank">'
                     f'↗ 在新标签页打开</a>' if rel else '')
        return f'''
        <div class="panel" id="{tab_id}">
          <div class="panel-head">{icon} {label}
            <span class="cnt">（{count} 个文件）</span>{open_link}</div>
          <div class="pdesc">{desc}</div>
          {body}
        </div>'''

    ttff_panel = _panel("tab-ttff", "TTFF 分析", "⏳", ttff_rel, ttff_count,
                        "首次定位时间（Time To First Fix）：覆盖 NMEA 与 BPDEBUG 全部输入文件。")
    bp_panel = _panel("tab-bpdebug", "BPDEBUG 搜星情况", "📡", bpdebug_rel, bpdebug_count,
                      "冷启动上星 / 星历有效 / 参与解算（含 ProtocolDecoder.dll 的 TrackInfo/PVT）。")

    has_ttff = bool(ttff_rel)
    has_bp = bool(bpdebug_rel)
    first = "tab-ttff" if has_ttff else ("tab-bpdebug" if has_bp else "tab-ttff")

    tabs = []
    if has_ttff:
        tabs.append(
            f'<div class="tab {"active" if first=="tab-ttff" else ""}" '
            f'id="btn-tab-ttff" onclick="show(\'tab-ttff\')">'
            f'⏳ TTFF 分析（{ttff_count} 个文件）</div>')
    if has_bp:
        tabs.append(
            f'<div class="tab {"active" if first=="tab-bpdebug" else ""}" '
            f'id="btn-tab-bpdebug" onclick="show(\'tab-bpdebug\')">'
            f'📡 BPDEBUG 搜星情况（{bpdebug_count} 个文件）</div>')
    tabs_html = "\n    ".join(tabs)
    panels_html = (ttff_panel if has_ttff else "") + (bp_panel if has_bp else "")

    html = f'''<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TTFF 综合分析报告</title>
<style>
  :root {{
    --bg:#0f1520; --panel:#161e2c; --panel2:#1b2434; --border:#263349;
    --text:#dce4f0; --muted:#8ba0bd; --accent:#4da3ff;
  }}
  * {{ box-sizing:border-box; }}
  html, body {{ height:100%; }}
  body {{ margin:0; background:var(--bg); color:var(--text);
          font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif; }}
  .top {{ background:var(--panel); border-bottom:1px solid var(--border); padding:10px 20px; }}
  .top h1 {{ margin:0; font-size:17px; }}
  .top .sub {{ color:var(--muted); font-size:11.5px; margin-top:2px; }}
  .tabs {{ display:flex; gap:6px; padding:8px 20px 0; flex-wrap:wrap; }}
  .tab {{ padding:5px 12px; border:1px solid var(--border); border-radius:7px 7px 0 0;
          cursor:pointer; background:var(--panel2); color:var(--muted); font-size:13px; }}
  .tab.active {{ background:var(--bg); color:var(--accent); font-weight:600;
                 border-bottom:2px solid var(--accent); }}
  .panel {{ display:none; padding:0 16px 12px; height:calc(100vh - 108px); }}
  .panel.active {{ display:flex; flex-direction:column; }}
  .panel-head {{ font-size:13.5px; font-weight:600; margin:8px 0 2px; color:var(--text); }}
  .panel-head .cnt {{ color:var(--muted); font-weight:400; font-size:11.5px; }}
  .openlink {{ float:right; color:var(--muted); font-size:11.5px; text-decoration:none;
               font-weight:400; margin-left:12px; }}
  .openlink:hover {{ color:var(--accent); }}
  .pdesc {{ color:var(--muted); font-size:11.5px; margin:0 0 6px; }}
  .frame {{ flex:1; width:100%; min-height:0; border:1px solid var(--border); border-radius:8px;
            background:var(--panel); }}
  .empty {{ padding:40px; text-align:center; color:var(--muted); background:var(--panel);
            border:1px dashed var(--border); border-radius:8px; }}
</style></head>
<body>
  <div class="top">
    <h1>TTFF 综合分析报告</h1>
    <div class="sub">首次定位时间（TTFF）+ BPDEBUG 搜星情况 ｜ 生成于 {gen_time}</div>
  </div>
  <div class="tabs">
    {tabs_html}
  </div>
  {panels_html}
  <script>
    function show(id) {{
      document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
      document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
      var el=document.getElementById(id); if(el) el.classList.add('active');
      var b=document.getElementById('btn-'+id); if(b) b.classList.add('active');
    }}
    show('{first}');
  </script>
</body></html>'''
    master_path = out_dir / "merged_ttff_report.html"
    master_path.write_text(html, encoding="utf-8")
    return master_path
