# -*- coding: utf-8 -*-
"""
TTFF Analyzer —— NMEA 日志首次定位时间（Time To First Fix）统计工具

功能：
  1. 按配置文件为【每个文件独立指定复位标志】，识别各日志中的复位事件；
  2. 计算每次复位的 TTFF（复位时刻 -> 首次有效定位时刻）；
  3. 汇总各文件统计（复位次数 / 恢复率 / TTFF 最小·中位·均值·P95·最大）；
  4. 生成自包含的交互式 HTML 报告（内嵌 ECharts，可离线打开）；
  5. 同时输出逐次复位明细 JSON。

用法：
  python ttff_analyzer.py                          # 使用默认配置 ttff_config.json
  python ttff_analyzer.py -c my_config.json       # 指定配置文件
  python ttff_analyzer.py -o out.html -j out.json # 自定义输出文件名

TTFF 统计方法（可复核）：
  1. 复位事件：日志中出现配置的复位标志行，即视为一次复位。
  2. 复位时刻 T_reset：复位标志行之前最后一条带有效时间戳的 RMC/GGA 的时间
     （UTC hhmmss.sss，日期取 RMC 第 10 字段 DDMMYY，跨天按绝对秒处理）。
  3. 首次定位 T_fix：复位后接收机须先进入无定位状态（GGA 定位质量=0 或 RMC=V，
     以跳过复位命令写入后残留的 0.1~0.2s 旧定位），其后第一条 GGA 定位质量>0 的语句。
  4. TTFF = T_fix - T_reset。
  5. 复位后至下一条复位标志/日志结束前始终无有效定位 -> 该次记为“未恢复定位”。
"""
import os
import re
import sys
import json
import datetime
import argparse

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = "ttff_config.json"
DEFAULT_EPOCH = datetime.date(2026, 1, 1)

# 标准 NMEA 0183 语句识别：'$' + 2 字母 Talker + 语句类型(GGA/RMC)。
# 不限定具体 Talker，从而原生支持所有 GNSS 系统，例如：
#   GP=GPS   GN=组合(GPS+GNSS)   GL=GLONASS   BD/GB=北斗(BDS)
#   GA=Galileo   GQ=QZSS(准天顶)   GI=NavIC(IRNSS)
#   以及任何其它标准 2 字母 Talker。旧版本只认 $GNGGA/$GNRMC，
# 导致有效定位在 $GPGGA 等其它 Talker 时永远检测不到首定位。
# 允许行首带可选噪声前缀（如某些接收机写入 '?7' 等控制字符），
# 与复位标志的宽松子串匹配保持一致；逗号分隔字段不受前缀影响。
_NMEA_RE = re.compile(r"\$([A-Z]{2})(GGA|RMC),")

# 二进制/非打印字节清洗表：仅保留可打印 ASCII(0x20-0x7e)，其余（二进制、
# 控制字符、DEL 等）一律删除，使被乱码打断的复位标志或 NMEA 语句可以重新拼接。
_DEL_NONPRINT = {i: None for i in range(256) if not (0x20 <= i <= 0x7e)}


def _safe_filename(name):
    """把任意字符串转为安全的文件名片段（去除路径分隔符/空白等）。"""
    s = re.sub(r'[\\/:*?"<>|\s]+', "_", name or "")
    s = s.strip("._")
    return s[:80] or "file"


def _clean_line(raw):
    """原始字节行 -> 仅含可打印 ASCII 的字符串。
    用 latin-1 做 1:1 字节解码（避免 GBK/UTF-8 多字节把二进制引导字节与紧随
    的标志字符拼成一个字符而吞掉标志），再用翻译表删除所有非打印字节。
    效果：① 行内任意位置插入的二进制/控制字节被剔除，复位标志、\\$XXGGA/
    \\$XXRMC 语句即便被乱码穿插也能重新拼好；② 复位标志不在行首（前面带
    日志前缀/噪声）也能正常子串命中。"""
    return raw.decode("latin-1").translate(_DEL_NONPRINT)


def _match_sentence(line):
    """返回行内首个标准 GGA/RMC 语句的匹配对象，无则 None。
    先按子串快速过滤，绝大多数行（GSV/ZDA/$CN* 等）直接跳过，减少正则开销。"""
    if "GGA" not in line and "RMC" not in line:
        return None
    return _NMEA_RE.search(line)


def is_gga(line):
    """任意标准 Talker 的 GGA 语句（如 $GPGGA/$GNGGA/$BDGGA/$GQGGA ...）"""
    m = _match_sentence(line)
    return m is not None and m.group(2) == "GGA"


def is_rmc(line):
    """任意标准 Talker 的 RMC 语句（如 $GPRMC/$GNRMC/$BDRMC/$GIRMC ...）"""
    m = _match_sentence(line)
    return m is not None and m.group(2) == "RMC"


# ---------------------------------------------------------------------------
# 核心：TTFF 统计
# ---------------------------------------------------------------------------
def parse_hms(s):
    """hhmmss.sss -> 秒；非法返回 None"""
    try:
        s = s.strip()
        if not s or len(s) < 6:
            return None
        return int(s[0:2]) * 3600 + int(s[2:4]) * 60 + float(s[4:])
    except (ValueError, IndexError):
        return None


def parse_date_ddmmyy(s):
    """DDMMYY -> 距 2026-01-01 的天数；非法返回 None"""
    try:
        s = s.strip()
        if not s or len(s) != 6:
            return None
        dd, mm, yy = int(s[0:2]), int(s[2:4]), int(s[4:6])
        year = 2000 + yy
        return (datetime.date(year, mm, dd) - DEFAULT_EPOCH).days
    except (ValueError, IndexError):
        return None


def analyze_file(path, reset_marker, default_date="040826"):
    """
    分析单个日志文件，返回 (cycles, meta)。
    cycles: 每次复位一条记录
      {reset_line, reset_time, fix_line, fix_time, ttff, status}
      status: 'ok'(已恢复) / 'no-fix'(未恢复)
    meta:   {rmc, gga, invalid_gga, lines}
    """
    cur_day = parse_date_ddmmyy(default_date)
    last_good = None        # 最近一条带有效时间戳语句的绝对秒
    last_fix_ok = False     # 最近一条 GGA 是否有效（q>0）
    last_valid_gga_time = None  # 最近一条有效 GGA 的绝对秒（TTFF 基线候选）
    pending = None          # 等待恢复的复位事件
    cycles = []
    n_rmc = n_gga = n_invalid_gga = n_lines = 0

    def close_pending(p, end):
        """关闭一条尚未最终确定的复位事件，给出正确的状态与原因。

        规则（按用户需求）：
          - 复位前有有效 GGA(_pre_valid) 且最终恢复 -> 已在循环内标记为 ok，这里仅补 end_line；
          - 复位前有有效 GGA，但复位后至下一条复位前始终未有效定位 -> 未恢复；
          - 复位前无有效 GGA -> 复位前未定位（invalid）；
              若其间出现过有效 GGA（_first_valid_found）则补充“复位后已定位”说明，
              否则叠加“未恢复”。
        """
        if p["status"] == "ok":
            p["end_line"] = end
            return
        if p["_pre_valid"]:
            p["status"] = "no-fix"
            p["reason"] = "未恢复（复位前有有效定位，但复位后至下一条复位前未再有效定位）"
        else:
            p["status"] = "invalid"
            if p.get("_first_valid_found"):
                p["reason"] = "复位前未定位（复位后已恢复定位，但复位前无有效GGA，无基线，不计入TTFF）"
            else:
                p["reason"] = "复位前未定位；未恢复（复位前后均无有效定位）"
        p["end_line"] = end
    marker_len = len(reset_marker)
    carry = ""          # 上一行清洗后尾部，用于捕捉被换行切断的复位标志
    with open(path, "rb") as f:
        for ln, raw in enumerate(f, 1):
            n_lines += 1
            sline = _clean_line(raw)
            # 复位标志检测：在“上一行尾部 + 本行”中查找，兼容标志被换行切断
            # 的情况；sline 已剔除二进制/控制字节，兼容标志被乱码穿插、或不在
            # 行首（前面带日志前缀/噪声）的情况。
            merged = carry + sline
            if reset_marker in merged:
                if pending is not None:
                    close_pending(pending, ln)
                    cycles.append(pending)
                pending = {
                    "reset_line": ln,
                    # 复位时刻基线：仅当复位前存在有效 GGA 才有意义，否则 None（复位前未定位）
                    "reset_time": last_valid_gga_time if last_fix_ok else None,
                    "_pre_valid": last_fix_ok,
                    "fix_line": None,
                    "fix_time": None,
                    "ttff": None,
                    "status": "pending",
                    "_invalid_seen": False,
                    "_first_valid_found": False,
                }
                # 标志完整落在本行 -> 本行其余内容无需再判 GGA/RMC
                if reset_marker in sline:
                    carry = sline[-(marker_len - 1):] if len(sline) >= marker_len - 1 else sline
                    continue
            carry = sline[-(marker_len - 1):] if len(sline) >= marker_len - 1 else sline
            if is_rmc(sline):
                n_rmc += 1
                parts = sline.split(",")
                t = parse_hms(parts[1]) if len(parts) > 1 else None
                if t is not None:
                    d = parts[9] if len(parts) > 9 else None
                    dd = parse_date_ddmmyy(d) if d else None
                    if dd is not None:
                        cur_day = dd
                    last_good = cur_day * 86400 + t
                status = parts[2] if len(parts) > 2 else ""
                if pending is not None and status != "A":
                    pending["_invalid_seen"] = True
                continue
            if is_gga(sline):
                n_gga += 1
                parts = sline.split(",")
                t = parse_hms(parts[1]) if len(parts) > 1 else None
                if t is not None:
                    last_good = cur_day * 86400 + t
                try:
                    q = int(float(parts[6]))
                except (ValueError, IndexError):
                    q = -1
                last_fix_ok = (q > 0)
                if q > 0:
                    last_valid_gga_time = last_good
                if pending is not None:
                    if q == 0:
                        pending["_invalid_seen"] = True
                    elif q > 0 and pending["_invalid_seen"] and not pending["_first_valid_found"]:
                        # 先经历无效阶段（跳过复位命令写入后残留的旧定位），
                        # 其后首条有效 GGA 才是真正的重新捕获。
                        pending["_first_valid_found"] = True
                        ttff = None
                        if pending["_pre_valid"] and last_good is not None and pending["reset_time"] is not None:
                            ttff = last_good - pending["reset_time"]
                            if ttff < 0:
                                ttff += 86400
                        if ttff is not None and ttff >= 0:
                            pending.update(
                                status="ok", fix_line=ln, fix_time=last_good,
                                ttff=round(ttff, 2),
                                reason="复位前有有效定位，复位后至下一条复位前已恢复首次定位")
                            cycles.append(pending)
                            pending = None
                            continue
                if q == 0:
                    n_invalid_gga += 1
                continue
    if pending is not None:
        close_pending(pending, "EOF")
        cycles.append(pending)
    for c in cycles:
        c.pop("_invalid_seen", None)
        c.pop("_first_valid_found", None)
        c.pop("_pre_valid", None)
    meta = {"rmc": n_rmc, "gga": n_gga, "invalid_gga": n_invalid_gga, "lines": n_lines}
    return cycles, meta


def summarize(cycles):
    """汇总一次文件的分析结果"""
    ttffs = [c["ttff"] for c in cycles if c["status"] == "ok" and c["ttff"] is not None]
    n_ok = len(ttffs)
    n_nofix = sum(1 for c in cycles if c["status"] == "no-fix")
    n_invalid = sum(1 for c in cycles if c["status"] == "invalid")
    res = {
        "n_resets": len(cycles),
        "n_ok": n_ok,
        "n_nofix": n_nofix,
        "n_invalid": n_invalid,
        "recovery_rate": round(n_ok / len(cycles) * 100, 1) if cycles else 0.0,
    }
    if ttffs:
        s = sorted(ttffs)
        n = len(s)
        res["min"] = s[0]
        res["max"] = s[-1]
        res["mean"] = round(sum(ttffs) / n, 2)
        res["median"] = s[n // 2]
        res["p95"] = s[min(n - 1, int(n * 0.95))]
    return res


def quartiles(ttffs):
    """[min, Q1, median, Q3, max]，无样本返回全 0"""
    if not ttffs:
        return [0, 0, 0, 0, 0]
    s = sorted(ttffs)
    n = len(s)
    def q(p):
        idx = (n - 1) * p
        lo = int(idx); hi = min(lo + 1, n - 1)
        return s[lo] + (s[hi] - s[lo]) * (idx - lo)
    return [round(s[0], 2), round(q(0.25), 2), round(q(0.5), 2),
            round(q(0.75), 2), round(s[-1], 2)]


# ---------------------------------------------------------------------------
# 报告生成
# ---------------------------------------------------------------------------
def wc(secs, epoch):
    """绝对秒 -> 'MM-DD HH:MM:SS'"""
    if secs is None:
        return "-"
    d = epoch + datetime.timedelta(seconds=int(secs))
    hh = int((secs % 86400) // 3600)
    mm = int((secs % 3600) // 60)
    ss = secs % 60
    return f"{d.month:02d}-{d.day:02d} {hh:02d}:{mm:02d}:{ss:05.2f}"


def build_payload(files, epoch):
    """构造报告所需的 JS 数据"""
    payload = []
    for fd in files:
        cycles_js = []
        for i, c in enumerate(fd["cycles"], 1):
            cycles_js.append({
                "i": i,
                "reset_line": c.get("reset_line"),
                "reset_time": wc(c.get("reset_time"), epoch),
                "fix_line": c.get("fix_line"),
                "fix_time": wc(c.get("fix_time"), epoch),
                "ttff": c.get("ttff"),
                "status": c.get("status"),
                "reason": c.get("reason", ""),
            })
        payload.append({
            "short": fd["name"],
            "file": fd["file"],
            "marker": fd["marker"],
            "note": fd.get("note", ""),
            "n_resets": fd["summary"]["n_resets"],
            "n_ok": fd["summary"]["n_ok"],
            "n_nofix": fd["summary"]["n_nofix"],
            "n_invalid": fd["summary"]["n_invalid"],
            "rate": fd["summary"]["recovery_rate"],
            "min": fd["summary"].get("min"), "median": fd["summary"].get("median"),
            "mean": fd["summary"].get("mean"), "max": fd["summary"].get("max"),
            "p95": fd["summary"].get("p95"),
            "box": quartiles([c["ttff"] for c in fd["cycles"] if c.get("ttff") is not None]),
            "ttffs": [c["ttff"] for c in fd["cycles"] if c.get("ttff") is not None],
            "cycles": cycles_js,
        })
    return payload


def esc_html(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_report_html(payload, json_out_name, echarts_js, gen_time,
                      echarts_cdn_url=None):
    """
    生成自包含 HTML 报告。echarts_js: 内嵌库源码；若给出 echarts_cdn_url 则用外链。
    """
    n_files = len(payload)
    n_resets = sum(p["n_resets"] for p in payload)
    n_ok_all = sum(p["n_ok"] for p in payload)
    n_nofix_all = sum(p["n_nofix"] for p in payload)
    ok_names = [p["short"] for p in payload if p["n_ok"] > 0]
    nofix_names = [p["short"] for p in payload if p["n_ok"] == 0]
    # 图2 默认选中的文件：有有效 TTFF 的前 2 个
    seq_default = ok_names[:2]

    # 方法说明（动态生成）
    if ok_names:
        ok_line = ("✅ 已核实：<b>" + "、".join(esc_html(n) for n in ok_names) +
                   "</b> 复位后能恢复定位，测得有效 TTFF 样本 <b>" + str(n_ok_all) +
                   "</b> 个；")
    else:
        ok_line = "⚠️ 所有文件的复位后均未出现有效定位，无 TTFF 样本；"
    if nofix_names:
        ok_line += ("其余 <b>" + "、".join(esc_html(n) for n in nofix_names) +
                    "</b> 在首次复位后未再输出有效定位（GGA 定位质量恒为 0），TTFF 无法测得。")
    else:
        ok_line += "全部复位均恢复定位。"

    method_note = ("每个文件的复位标志在配置文件（ttff_config.json）的 "
                   "<code>files[].reset_marker</code> 字段中<b>独立配置</b>，"
                   "本报告按各文件配置的标志识别复位事件。")

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NMEA 日志 TTFF 统计报告</title>
<script>__ECHARTS__</script>
<style>
  :root{
    --bg:#0f1520; --panel:#161e2c; --panel2:#1b2434; --border:#263349;
    --text:#dce4f0; --muted:#8ba0bd; --accent:#4da3ff; --accent2:#2dd4a7;
    --warn:#f5a623; --danger:#ff6b6b; --ok:#2dd4a7;
  }
  *{box-sizing:border-box; margin:0; padding:0;}
  body{background:var(--bg); color:var(--text); font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif; padding:28px 20px 60px;}
  .wrap{max-width:none; width:100%; margin:0 auto;}
  h1{font-size:24px; font-weight:600; letter-spacing:.5px;}
  h1 .sub{display:block; font-size:13px; color:var(--muted); font-weight:400; margin-top:6px;}
  .meta{color:var(--muted); font-size:12.5px; margin-top:10px;}
  .card{background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:18px 20px; margin-top:18px;}
  .card h2{font-size:16px; font-weight:600; margin-bottom:6px; display:flex; align-items:center; gap:8px;}
  .card h2 .tag{font-size:11px; color:var(--accent); border:1px solid var(--accent); border-radius:4px; padding:1px 6px; font-weight:400;}
  .card h3{font-size:13.5px; font-weight:600; color:var(--accent); margin:14px 0 6px;}
  .ctitle{display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap;}
  .zoom-btn{background:var(--panel2); border:1px solid var(--border); color:var(--muted); font-size:12px; padding:3px 11px; border-radius:6px; cursor:pointer; transition:.15s; white-space:nowrap;}
  .zoom-btn:hover{color:var(--accent); border-color:var(--accent);}
  #modal{display:none; position:fixed; inset:0; z-index:999; background:rgba(4,8,16,.82); align-items:center; justify-content:center; padding:20px;}
  .modal-box{width:94vw; max-width:1500px; height:90vh; background:var(--panel); border:1px solid var(--border); border-radius:14px; padding:14px 18px; display:flex; flex-direction:column; box-shadow:0 20px 60px rgba(0,0,0,.55);}
  .modal-head{display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; font-size:15px; font-weight:600; color:var(--text);}
  #modalClose{background:var(--panel2); border:1px solid var(--border); color:var(--muted); padding:5px 14px; border-radius:6px; cursor:pointer; font-size:12.5px; transition:.15s;}
  #modalClose:hover{color:var(--danger); border-color:var(--danger);}
  .modal-chart{flex:1; width:100%; min-height:0;}
  p.lead{color:var(--muted); font-size:13px; line-height:1.7;}
  ol.steps{list-style:none; counter-reset:s; margin-top:8px;}
  ol.steps li{counter-increment:s; position:relative; padding-left:34px; margin:9px 0; font-size:13.5px; line-height:1.65; color:#c9d6e8;}
  ol.steps li::before{content:counter(s); position:absolute; left:0; top:1px; width:22px; height:22px; background:var(--panel2); border:1px solid var(--accent); color:var(--accent); border-radius:50%; text-align:center; line-height:20px; font-size:12px; font-weight:600;}
  code{background:var(--panel2); border:1px solid var(--border); padding:1px 6px; border-radius:4px; font-family:Consolas,monospace; font-size:12.5px; color:#9ecbff;}
  .badge{display:inline-block; padding:2px 9px; border-radius:10px; font-size:11.5px; font-weight:600;}
  .b-ok{background:rgba(45,212,167,.14); color:var(--ok);}
  .b-warn{background:rgba(245,166,35,.14); color:var(--warn);}
  .b-fail{background:rgba(255,107,107,.14); color:var(--danger);}
  table{width:100%; border-collapse:collapse; font-size:13px; margin-top:10px;}
  th,td{padding:8px 10px; text-align:center; border-bottom:1px solid var(--border);}
  th{color:var(--muted); font-weight:600; font-size:12px; background:var(--panel2);}
  td.l, th.l{text-align:left;}
  tr:hover td{background:rgba(77,163,255,.05);}
  .num{font-variant-numeric:tabular-nums;}
  .chart{width:100%; height:380px;}
  .chart.tall{height:430px;}
  .charts-row{display:flex; gap:14px; margin-top:14px;}
  .charts-row .card{flex:1; margin-top:0;}
  .tabs{display:flex; gap:6px; flex-wrap:wrap; margin-top:10px;}
  .tab{padding:6px 14px; border-radius:8px; border:1px solid var(--border); background:var(--panel2); color:var(--muted); cursor:pointer; font-size:12.5px; transition:.15s;}
  .tab:hover{color:var(--text); border-color:var(--accent);}
  .tab.active{background:rgba(77,163,255,.16); color:var(--accent); border-color:var(--accent);}
  .legend-note{color:var(--muted); font-size:12px; margin-top:8px;}
  .method-warn{border-left:3px solid var(--warn); background:rgba(245,166,35,.07); padding:10px 14px; border-radius:0 8px 8px 0; font-size:13px; line-height:1.7; margin-top:12px;}
  .method-ok{border-left:3px solid var(--ok); background:rgba(45,212,167,.07); padding:10px 14px; border-radius:0 8px 8px 0; font-size:13px; line-height:1.7; margin-top:8px;}
  .grid2{display:grid; grid-template-columns:1fr 1fr; gap:14px;}
  @media(max-width:900px){.grid2{grid-template-columns:1fr;}.charts-row{flex-direction:column;}}
  .stat-cards{display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-top:14px;}
  @media(max-width:900px){.stat-cards{grid-template-columns:repeat(2,1fr);}}
  .stat{background:var(--panel2); border:1px solid var(--border); border-radius:10px; padding:14px 16px;}
  .stat .v{font-size:22px; font-weight:700; color:var(--accent); margin-top:4px; font-variant-numeric:tabular-nums;}
  .stat .k{font-size:12px; color:var(--muted);}
  .stat.ok .v{color:var(--ok);} .stat.fail .v{color:var(--danger);}
  .foot{margin-top:26px; color:var(--muted); font-size:12px; text-align:center; line-height:1.8;}
  .scroll{max-height:420px; overflow:auto;}
  .seq-legend{font-size:12px; color:var(--muted); margin-top:6px;}
  .ctrl-row{display:flex; align-items:center; gap:10px; margin-top:10px; flex-wrap:wrap;}
  .ctrl-label{font-size:12.5px; color:var(--muted);}
  .filename{font-family:Consolas,monospace; font-size:12px; color:var(--muted);}
</style>
</head>
<body>
<div class="wrap">
  <h1>NMEA 日志 TTFF 统计报告
    <span class="sub">多文件冷启动 / 复位 首次定位时间 (Time To First Fix) 统计与分析</span>
  </h1>
  <div class="meta">共分析 <b>__N_FILES__</b> 个日志文件 ｜ 识别复位事件 <b><span id="totalResets">0</span></b> 次 ｜ 生成时间：__GEN_TIME__ ｜ 工具：ttff_tool/ttff_analyzer.py</div>

  <!-- ============ METHOD ============ -->
  <div class="card">
    <h2>一、TTFF 统计方法说明 <span class="tag">请先核对</span></h2>
    <p class="lead">TTFF（Time To First Fix，首次定位时间）= 从复位/冷启动时刻到接收机恢复首次有效定位的时间间隔。本报告按如下规则统计：</p>
    <ol class="steps">
      <li><b>复位事件识别</b>：以日志中的复位标志行作为一次复位的起点。__METHOD_NOTE__ 识别过程对<b>行内二进制/控制字节、复位标志不在行首、以及标志被换行切断</b>等情况均做了容错（逐行剔除非打印字节后重新拼接匹配，并跨行拼接标志）。</li>
      <li><b>复位时刻 T<sub>reset</sub>（TTFF 基线）</b>：复位标志本身不含时间戳，取<b>复位标志行之前最后一条有效 GGA（定位质量&gt;0）的时间</b>作为 TTFF 基线（UTC hhmmss.sss，日期取自 RMC 第 10 字段 DDMMYY）。<b>仅当复位前存在有效 GGA 时，本次复位才具备有效基线</b>；若复位前无有效 GGA，则本次标记为“复位前未定位”，TTFF 不参与统计。复位命令通常紧随接收机最后一条有效输出写入日志，误差不超过一个输出周期（0.1~0.2 s）。</li>
      <li><b>首次定位 T<sub>fix</sub></b>：复位后接收机必须<b>先进入无定位状态</b>（出现 GGA 定位质量 = 0 或 RMC 状态 = V 的语句），之后出现的<b>第一条 GGA 定位质量 &gt; 0 的语句</b>视为首次定位。此步骤可跳过复位命令写入后、接收机真正重启前短暂残留的有效定位（约 0.1~0.2 s），避免把复位前的旧定位误算为首次定位。</li>
      <li><b>TTFF 计算</b>：TTFF = T<sub>fix</sub> − T<sub>reset</sub>，跨午夜（日期切换）时按绝对时间自动处理。</li>
      <li><b>无法测得 / 不计入的情况</b>：① 若复位标志行之前没有有效 GGA（<b>复位前未定位</b>），本次无论复位后是否定位均不计入 TTFF（无基线可算）；② 若复位前有有效 GGA，但复位后至下一条复位标志出现前（或日志结束前）始终未出现有效定位，则记为<b>"未恢复定位"</b>。以上两类均不参与 TTFF 统计。</li>
      <li><b>有效定位判据</b>：GGA 定位质量字段（第 7 字段）&gt; 0，即接收机报告了可用定位（含 2D/3D）；未使用 RMC 状态字段作为主判据。</li>
    </ol>
    <div class="method-ok">__OK_LINE__</div>
    <div class="method-warn">⚠️ 说明：若复位命令写入后接收机先输出 0.1~0.2 s 的<b>残留有效定位</b>再进入无效状态，本统计已按第 3 条规则跳过；若您希望把"复位命令写入时刻"严格定义为复位时刻，TTFF 结果会有 ±0.2 s 以内的偏差。</div>
  </div>

  <!-- ============ SUMMARY ============ -->
  <div class="card">
    <h2>二、各文件统计结果总览</h2>
    <table>
      <thead>
        <tr>
          <th class="l">文件</th><th>复位标志</th><th>复位次数</th><th>恢复次数</th><th>恢复率</th>
          <th>TTFF 最小值</th><th>中位数</th><th>平均值</th><th>P95</th><th>最大值</th><th>结论</th>
        </tr>
      </thead>
      <tbody id="summaryBody"></tbody>
    </table>
    <div class="legend-note">TTFF 单位为秒；"未恢复定位"文件的 TTFF 统计无有效样本，显示为 —。</div>
    <div class="stat-cards" id="statCards"></div>
  </div>

  <!-- ============ CHARTS ============ -->
  <div class="card">
    <h2>三、交互图表</h2>
    <div class="method-ok" style="margin-top:4px">💡 <b>交互操作提示</b>：① 鼠标<b>滚轮</b>——在 <b>Y 轴</b>上滚动只缩放 Y 轴，在 <b>X 轴或绘图区</b>滚动只缩放 X 轴；② 放大后<b>按住鼠标左键拖拽</b>可平移视图（X、Y 均可，视图跟随鼠标移动）；图 2/图 3 底部还有<b>缩放滑条</b>；③ 右上角<b>工具箱</b>：框选区域局部放大、一键还原、保存为图片。</div>

    <h3 class="ctitle">图 1 · 各文件 TTFF 分布（箱线图，含最小/四分位/中位/最大，仅含有效样本的文件）<button class="zoom-btn" data-idx="0">⛶ 放大</button></h3>
    <div id="boxChart" class="chart"></div>

    <h3 class="ctitle">图 2 · 逐次复位的 TTFF 序列（横轴为复位序号，可筛选文件、可切换绘图类型）<button class="zoom-btn" data-idx="1">⛶ 放大</button></h3>
    <div class="tabs" id="seqTabs"></div>
    <div class="ctrl-row">
      <span class="ctrl-label">绘图类型：</span>
      <div class="tabs" id="seqModeTabs">
        <div class="tab active" data-mode="scatter">散点图</div>
        <div class="tab" data-mode="line-point">点线图</div>
        <div class="tab" data-mode="line">折线图</div>
      </div>
    </div>
    <div id="seqChart" class="chart tall"></div>
    <div class="seq-legend">每次复位一个点（散点图/点线图）；无点位表示该文件复位后未恢复定位。悬停可查看复位时刻与首次定位时刻。</div>

    <h3 class="ctitle">图 3 · 各文件 TTFF 直方图（切换文件查看分布形态）<button class="zoom-btn" data-idx="2">⛶ 放大</button></h3>
    <div class="tabs" id="histTabs"></div>
    <div id="histChart" class="chart"></div>

    <h3 class="ctitle">图 4 · 恢复率与平均 TTFF 对比
      <span style="display:flex;gap:8px"><button class="zoom-btn" data-idx="3">⛶ 恢复率</button><button class="zoom-btn" data-idx="4">⛶ 平均 TTFF</button></span>
    </h3>
    <div class="charts-row">
      <div class="card"><div id="rateChart" class="chart" style="height:340px;"></div></div>
      <div class="card"><div id="meanChart" class="chart" style="height:340px;"></div></div>
    </div>
  </div>

  <!-- ============ DETAIL ============ -->
  <div class="card">
    <h2>四、逐次复位明细 <span class="tag">可切换文件</span></h2>
    <div class="tabs" id="detailTabs"></div>
    <div class="scroll">
      <table>
        <thead><tr><th>序号</th><th>复位标志行号</th><th>复位时刻 T<sub>reset</sub></th><th>首次定位行号</th><th>首次定位时刻 T<sub>fix</sub></th><th>TTFF (s)</th><th>状态</th><th>说明</th></tr></thead>
        <tbody id="detailBody"></tbody>
      </table>
    </div>
  </div>

  <div class="foot">报告由 ttff_tool/ttff_analyzer.py 自动统计生成 ｜ 方法详见第一节 ｜ 合并明细：__JSON_NAME__（每次复位的复位时刻、首次定位时刻、TTFF）；同时每个文件另输出独立明细 JSON：ttff_XX_文件名.json</div>
</div>

<!-- 图表放大浮层 -->
<div id="modal">
  <div class="modal-box">
    <div class="modal-head">
      <span id="modalTitle"></span>
      <button id="modalClose">✕ 关闭（Esc）</button>
    </div>
    <div id="modalChart" class="modal-chart"></div>
  </div>
</div>

<script>
const DATA = __DATA__;
const SEQ_DEFAULT = __SEQ_DEFAULT__;
const shortList = DATA.map(d => d.short);

// ---------- helpers ----------
const $ = id => document.getElementById(id);
function initChart(id){ return echarts.init($(id), null, {renderer:'canvas'}); }
function fmtT(v){ return v == null ? "—" : v.toFixed(2); }
function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
// 公共：工具箱（框选缩放 / 还原 / 保存图片）+ 交互缩放（滚轮/拖拽双轴 + 底部滑条）
const TOOLBOX = {
  right:8, top:2, itemSize:13, itemGap:8,
  iconStyle:{borderColor:'#8ba0bd'},
  emphasis:{iconStyle:{borderColor:'#4da3ff'}},
  feature:{
    dataZoom:{yAxisIndex:0, title:{zoom:'框选缩放', back:'还原缩放'}, iconStyle:{borderColor:'#8ba0bd'}},
    restore:{title:'还原'},
    saveAsImage:{title:'保存图片', name:'ttff_chart', pixelRatio:2}
  }
};
const DZ_INSIDE = [
  // x/y inside：滚轮缩放与拖拽平移均由自定义 axisWheelZoom / axisPan 接管
  {type:'inside', xAxisIndex:0, filterMode:'none', zoomOnMouseWheel:false, moveOnMouseWheel:false, moveOnMouseMove:false},
  {type:'inside', yAxisIndex:0, filterMode:'none', zoomOnMouseWheel:false, moveOnMouseWheel:false, moveOnMouseMove:false}
];
const DZ_SLIDER = {type:'slider', xAxisIndex:0, height:14, bottom:6, filterMode:'none',
  borderColor:'#263349', backgroundColor:'rgba(27,36,52,.85)', fillerColor:'rgba(77,163,255,.18)',
  handleStyle:{color:'#4da3ff'}, textStyle:{color:'#8ba0bd'}};

// 自定义滚轮缩放：鼠标在 Y 轴区域 -> 只缩放 Y；在 X 轴或绘图区 -> 只缩放 X
// dzIdxX / dzIdxY 为 option.dataZoom 数组中 x / y 实例的索引
function enableAxisWheelZoom(chart, dzIdxX, dzIdxY){
  const zr = chart.getZr();
  zr.off('mousewheel');
  zr.on('mousewheel', e=>{
    const grid = chart.getModel().getComponent('grid').coordinateSystem.getRect();
    const x = e.offsetX, y = e.offsetY;
    const up = e.deltaY ? e.deltaY < 0 : e.wheelDelta > 0;   // 向上滚 = 放大
    const factor = up ? 0.82 : 1.22;
    const onYAxis = x < grid.x && y >= grid.y - 24 && y <= grid.y + grid.height + 24;
    const dzIdx = onYAxis ? dzIdxY : dzIdxX;
    const model = chart.getModel().getComponent('dataZoom', dzIdx);
    if(!model) return;
    const pr = model.getPercentRange();           // [start%, end%]
    const center = (pr[0] + pr[1]) / 2;
    let ns = center - (center - pr[0]) * factor;
    let ne = center + (pr[1] - center) * factor;
    ns = Math.max(0, ns); ne = Math.min(100, ne);
    if(ne - ns < 0.5) return;                     // 最小窗口保护
    chart.dispatchAction({type:'dataZoom', dataZoomIndex: dzIdx, start: ns, end: ne});
  });
}

// 自定义拖拽平移：按住鼠标左键在图中拖动，视图跟随鼠标移动（X、Y 均可平移，类似地图）
function enableAxisPan(chart, dzIdxX, dzIdxY){
  const zr = chart.getZr();
  let dragging = false, lastX = 0, lastY = 0;
  zr.off('mousedown');
  zr.off('mousemove');
  zr.off('mouseup');
  zr.off('globalout');
  zr.on('mousedown', e=>{
    dragging = true; lastX = e.offsetX; lastY = e.offsetY;
    chart.dispatchAction({type:'hideTip'});
  });
  zr.on('mousemove', e=>{
    if(!dragging) return;
    const dx = e.offsetX - lastX, dy = e.offsetY - lastY;
    if(dx === 0 && dy === 0) return;
    lastX = e.offsetX; lastY = e.offsetY;
    const grid = chart.getModel().getComponent('grid').coordinateSystem.getRect();
    const pan = (dzIdx, shiftPct)=>{
      const model = chart.getModel().getComponent('dataZoom', dzIdx);
      if(!model) return;
      const pr = model.getPercentRange();
      const span = pr[1] - pr[0];
      if(span <= 0) return;
      let ns = pr[0] + shiftPct * span;
      let ne = pr[1] + shiftPct * span;
      if(ns < 0){ ne -= ns; ns = 0; }              // 越界钳制
      if(ne > 100){ ns -= (ne - 100); ne = 100; }
      chart.dispatchAction({type:'dataZoom', dataZoomIndex: dzIdx, start: ns, end: ne});
    };
    // X：向右拖 -> 视图显示更小的序号；Y：向下拖（屏幕坐标 dy>0）-> 视图显示更大的 TTFF
    pan(dzIdxX, -dx / grid.width);
    pan(dzIdxY,  dy / grid.height);
  });
  zr.on('mouseup', ()=>{ dragging = false; });
  zr.on('globalout', ()=>{ dragging = false; });
}

// total resets
$('totalResets').textContent = DATA.reduce((a,d)=>a+d.n_resets,0);

// ---------- summary table ----------
const statusBadge = d => {
  if(d.n_ok === d.n_resets) return '<span class="badge b-ok">全部恢复</span>';
  if(d.n_ok === 0 && d.n_invalid === d.n_resets) return '<span class="badge b-warn">复位前均未定位</span>';
  if(d.n_ok === 0) return '<span class="badge b-fail">未恢复定位</span>';
  return '<span class="badge b-warn">部分恢复</span>';
};
$('summaryBody').innerHTML = DATA.map(d => `
  <tr>
    <td class="l"><span class="filename">${esc(d.file)}</span>${d.note?`<br/><span style="color:var(--muted);font-size:11px">${esc(d.note)}</span>`:''}</td>
    <td><code>${esc(d.marker)}</code></td>
    <td class="num">${d.n_resets}</td>
    <td class="num">${d.n_ok}</td>
    <td class="num">${d.rate}%</td>
    <td class="num">${fmtT(d.min)}</td>
    <td class="num">${fmtT(d.median)}</td>
    <td class="num">${fmtT(d.mean)}</td>
    <td class="num">${fmtT(d.p95)}</td>
    <td class="num">${fmtT(d.max)}</td>
    <td>${statusBadge(d)}</td>
  </tr>`).join('');

// stat cards
const okFiles = DATA.filter(d=>d.n_ok>0);
const allTtff = okFiles.flatMap(d=>d.ttffs);
$('statCards').innerHTML = `
  <div class="stat"><div class="k">可测 TTFF 文件数</div><div class="v">${okFiles.length}<span style="font-size:13px;color:var(--muted)">/${DATA.length}</span></div></div>
  <div class="stat ok"><div class="k">有效 TTFF 样本数</div><div class="v">${allTtff.length}</div></div>
  <div class="stat"><div class="k">全部样本中位 TTFF</div><div class="v">${allTtff.length? (allTtff.slice().sort((a,b)=>a-b)[Math.floor(allTtff.length/2)]).toFixed(1):'—'} s</div></div>
  <div class="stat fail"><div class="k">无效/未恢复复位次数</div><div class="v">${DATA.reduce((a,d)=>a+d.n_nofix+d.n_invalid,0)}</div></div>`;

// ---------- chart 1: box (only files with measurable TTFF) ----------
const okIdx = DATA.map((d,i)=>d.n_ok>0 ? i : -1).filter(i=>i>=0);
const boxFiles = okIdx.map(i=>DATA[i]);
const boxChart = initChart('boxChart');
boxChart.setOption({
  backgroundColor:'transparent',
  toolbox:TOOLBOX,
  tooltip:{trigger:'item', axisPointer:{type:'shadow'}},
  grid:{left:60,right:30,top:30,bottom:70},
  xAxis:{type:'category', data:boxFiles.map(d=>d.short), axisLabel:{color:'#8ba0bd', interval:0, rotate:22, fontSize:11}},
  yAxis:{type:'value', name:'TTFF (s)', nameTextStyle:{color:'#8ba0bd'}, axisLabel:{color:'#8ba0bd'}, splitLine:{lineStyle:{color:'#263349'}}},
  series:[{
    name:'TTFF 分布', type:'boxplot', data:boxFiles.map(d=>d.box),
    itemStyle:{color:'rgba(77,163,255,.35)', borderColor:'#4da3ff', borderWidth:1.5},
    tooltip:{formatter:p=>{ const d=boxFiles[p.dataIndex]; return d.short+'<br/>Min: '+d.min.toFixed(1)+'s<br/>Q1: '+d.box[1].toFixed(1)+'s<br/>中位: '+d.box[2].toFixed(1)+'s<br/>Q3: '+d.box[3].toFixed(1)+'s<br/>Max: '+d.max.toFixed(1)+'s'; }},
  }],
});

// ---------- tabs helper ----------
function makeTabs(tabEl, activeFn){
  tabEl.innerHTML = shortList.map(s=>`<div class="tab">${s}</div>`).join('');
  [...tabEl.children].forEach((el,i)=>{
    el.onclick = ()=>{ [...tabEl.children].forEach(x=>x.classList.remove('active')); el.classList.add('active'); activeFn(i); };
  });
}

// ---------- chart 2: sequence ----------
const seqChart = initChart('seqChart');
const seqColors = ['#4da3ff','#2dd4a7','#f5a623','#ff6b6b','#b48cff','#ff9ff3','#55efc4'];
let seqMode = 'scatter';   // 'scatter' | 'line-point' | 'line'
// multi-select tabs for sequence chart: click toggles
$('seqTabs').innerHTML = shortList.map(s=>`<div class="tab ${SEQ_DEFAULT.indexOf(s)>=0?'active':''}">${s}</div>`).join('');
Array.from($('seqTabs').children).forEach(el=>{
  el.onclick = ()=>{
    el.classList.toggle('active');
    renderSeq();
  };
});
// plot-type switch
Array.from($('seqModeTabs').children).forEach(el=>{
  el.onclick = ()=>{
    Array.from($('seqModeTabs').children).forEach(x=>x.classList.remove('active'));
    el.classList.add('active');
    seqMode = el.dataset.mode;
    renderSeq();
  };
});
function seriesCfg(name, k, pts){
  const d = DATA[shortList.indexOf(name)];
  const ttp = p=>{ const c = d.cycles[p.value[0]-1]; return `${name}<br/>第 ${c.i} 次复位<br/>T_reset: ${c.reset_time}<br/>T_fix: ${c.fix_time}<br/>TTFF: ${c.ttff}s`; };
  const base = { name, data:pts, color:seqColors[k%seqColors.length], tooltip:{formatter:ttp} };
  if(seqMode === 'line-point'){
    return Object.assign(base, {type:'line', showSymbol:true, symbolSize:5, lineStyle:{width:1.5}, connectNulls:true});
  }
  if(seqMode === 'line'){
    return Object.assign(base, {type:'line', showSymbol:false, lineStyle:{width:1.5}, connectNulls:true});
  }
  return Object.assign(base, {type:'scatter', symbolSize:7});
}
function renderSeq(){
  const active = Array.from($('seqTabs').children).filter(x=>x.classList.contains('active')).map(x=>x.textContent);
  const series = active.map((name, k)=>{
    const d = DATA[shortList.indexOf(name)];
    const pts = d.cycles.filter(c=>c.ttff!=null).map(c=>[c.i, c.ttff]);
    return seriesCfg(name, k, pts);
  });
  seqChart.setOption({
    backgroundColor:'transparent',
    toolbox:TOOLBOX,
    tooltip:{trigger:'item'},
    legend:{textStyle:{color:'#8ba0bd'}, top:0},
    dataZoom:[...DZ_INSIDE, DZ_SLIDER],
    grid:{left:60,right:30,top:34,bottom:60},
    xAxis:{type:'value', name:'复位序号', nameTextStyle:{color:'#8ba0bd'}, axisLabel:{color:'#8ba0bd'}, splitLine:{lineStyle:{color:'#263349'}}},
    yAxis:{type:'value', name:'TTFF (s)', nameTextStyle:{color:'#8ba0bd'}, axisLabel:{color:'#8ba0bd'}, splitLine:{lineStyle:{color:'#263349'}}},
    series
  }, true);
}
renderSeq();
// 图 2 自定义交互：dataZoom 数组 [x inside(0), y inside(1), x slider(2)]
enableAxisWheelZoom(seqChart, 0, 1);
enableAxisPan(seqChart, 0, 1);

// ---------- chart 3: histogram ----------
const histChart = initChart('histChart');
let histIdx = 0;
function renderHist(){
  const d = DATA[histIdx];
  const ttffs = d.ttffs;
  const opt = {
    backgroundColor:'transparent',
    toolbox:TOOLBOX,
    tooltip:{trigger:'axis'},
    dataZoom:[...DZ_INSIDE, DZ_SLIDER],
    grid:{left:60,right:30,top:30,bottom:58},
    xAxis:{type:'category', name:'TTFF 区间 (s)', nameTextStyle:{color:'#8ba0bd'}, data:[], axisLabel:{color:'#8ba0bd', fontSize:11}},
    yAxis:{type:'value', name:'复位次数', nameTextStyle:{color:'#8ba0bd'}, axisLabel:{color:'#8ba0bd'}, splitLine:{lineStyle:{color:'#263349'}}},
    series:[{type:'bar', data:[], itemStyle:{color:'rgba(77,163,255,.75)', borderRadius:[4,4,0,0]}}],
  };
  if(!ttffs.length){
    opt.graphic = {type:'text', left:'center', top:'middle', style:{text:'该文件无有效 TTFF 样本（复位后未恢复定位）', fill:'#8ba0bd', fontSize:14}};
    histChart.setOption(opt, true);
    return;
  }
  const bins = {};
  ttffs.forEach(t=>{ const b = Math.floor(t/5)*5; bins[b] = (bins[b]||0)+1; });
  const xs = Object.keys(bins).sort((a,b)=>+a-+b).map(Number);
  opt.xAxis.data = xs.map(b=>b+'-'+(+b+5));
  opt.series[0].data = xs.map(b=>bins[b]);
  opt.series[0].label = {show:true, position:'top', fontSize:10, color:'#8ba0bd'};
  opt.tooltip.formatter = ps=>{ const b=ps[0].axisValue; return `${b}s<br/>${ps[0].value} 次复位`; };
  histChart.setOption(opt, true);
}
makeTabs($('histTabs'), idx=>{ histIdx = idx; renderHist(); });
renderHist();
// 图 3 自定义交互：dataZoom 数组 [x inside(0), y inside(1), x slider(2)]
enableAxisWheelZoom(histChart, 0, 1);
enableAxisPan(histChart, 0, 1);

// ---------- chart 4: recovery rate & mean ----------
const rateChart = initChart('rateChart');
rateChart.setOption({
  backgroundColor:'transparent',
  toolbox:TOOLBOX,
  tooltip:{trigger:'axis', formatter:ps=>ps.map(p=>`${p.name}: ${p.value}%`).join('<br/>')},
  grid:{left:55,right:20,top:34,bottom:70},
  xAxis:{type:'category', data:shortList, axisLabel:{color:'#8ba0bd', interval:0, rotate:24, fontSize:10.5}},
  yAxis:{type:'value', name:'恢复率 (%)', min:0, max:100, nameTextStyle:{color:'#8ba0bd'}, axisLabel:{color:'#8ba0bd'}, splitLine:{lineStyle:{color:'#263349'}}},
  series:[{
    type:'bar', data:DATA.map(d=>d.rate),
    itemStyle:{color:p=>p.value>=90?'rgba(45,212,167,.8)':(p.value>0?'rgba(245,166,35,.8)':'rgba(255,107,107,.75)'), borderRadius:[4,4,0,0]},
    label:{show:true, position:'top', fontSize:10.5, color:'#8ba0bd', formatter:p=>p.value+'%'},
  }]
});
const meanChart = initChart('meanChart');
const okNames = DATA.filter(d=>d.n_ok>0).map(d=>d.short);
meanChart.setOption({
  backgroundColor:'transparent',
  toolbox:TOOLBOX,
  tooltip:{trigger:'axis', formatter:ps=>ps.map(p=>`${p.name}: ${p.value.toFixed(2)} s`).join('<br/>')},
  grid:{left:55,right:60,top:34,bottom:70},
  xAxis:{type:'category', data:okNames, axisLabel:{color:'#8ba0bd', interval:0, rotate:24, fontSize:10.5}},
  yAxis:{type:'value', name:'平均 TTFF (s)', nameTextStyle:{color:'#8ba0bd'}, axisLabel:{color:'#8ba0bd'}, splitLine:{lineStyle:{color:'#263349'}}},
  series:[{
    type:'bar', data:DATA.filter(d=>d.n_ok>0).map(d=>d.mean),
    itemStyle:{color:'rgba(77,163,255,.8)', borderRadius:[4,4,0,0]},
    label:{show:true, position:'top', fontSize:10.5, color:'#8ba0bd', formatter:p=>p.value.toFixed(1)},
    markLine:{
      silent:true,
      symbol:['none','arrow'],
      data:[{type:'average'}],
      lineStyle:{color:'#f5a623', width:1.5},
      label:{
        position:'start',
        formatter:p=>'AVG '+p.value.toFixed(1),
        color:'#f5a623',
        fontSize:10.5,
        backgroundColor:'rgba(27,36,52,.85)',
        padding:[2,6],
        borderRadius:3,
        distance:6
      }
    },
  }]
});

// ---------- detail table ----------
let detailIdx = 0;
function renderDetail(){
  const d = DATA[detailIdx];
  $('detailBody').innerHTML = d.cycles.map(c=>{
    let st, badgeCls;
    if(c.status==='ok'){ st='已恢复'; badgeCls='b-ok'; }
    else if(c.status==='invalid'){ st='复位前未定位'; badgeCls='b-warn'; }
    else { st='未恢复'; badgeCls='b-fail'; }
    const stHtml = `<span class="badge ${badgeCls}">${st}</span>`;
    const reason = c.reason ? esc(c.reason) : '—';
    return `<tr>
      <td class="num">${c.i}</td>
      <td class="num">${c.reset_line}</td>
      <td class="num">${c.reset_time}</td>
      <td class="num">${c.fix_line ?? '—'}</td>
      <td class="num">${c.fix_time ?? '—'}</td>
      <td class="num">${c.ttff!=null? c.ttff.toFixed(2) : '—'}</td>
      <td>${stHtml}</td>
      <td class="l" style="color:var(--muted);font-size:11.5px">${reason}</td>
    </tr>`;
  }).join('');
}
makeTabs($('detailTabs'), idx=>{ detailIdx = idx; renderDetail(); });
renderDetail();

// ---------- 图表放大（modal） ----------
const modal = $('modal'), modalChartEl = $('modalChart'), modalTitle = $('modalTitle');
let modalChart = null;
function openZoom(chart, title, custom){
  modalTitle.textContent = title;
  modal.style.display = 'flex';
  document.body.style.overflow = 'hidden';
  setTimeout(()=>{                       // 等 modal 布局完成后再初始化，避免尺寸为 0
    if(modalChart){ modalChart.dispose(); modalChart = null; }
    modalChart = echarts.init(modalChartEl, null, {renderer:'canvas'});
    modalChart.setOption(chart.getOption());   // 继承当前图表全部配置与视图
    if(custom){ enableAxisWheelZoom(modalChart, 0, 1); enableAxisPan(modalChart, 0, 1); }
    modalChart.resize();
  }, 80);
}
function closeZoom(){
  if(modalChart){ modalChart.dispose(); modalChart = null; }
  modal.style.display = 'none';
  document.body.style.overflow = '';
}
$('modalClose').onclick = closeZoom;
document.addEventListener('keydown', e=>{ if(e.key === 'Escape') closeZoom(); });

// 放大按钮映射（顺序对应 data-idx）
const ZOOM_MAP = [
  {chart:boxChart,  title:'图 1 · 各文件 TTFF 分布（箱线图）', custom:false},
  {chart:seqChart,  title:'图 2 · 逐次复位 TTFF 序列（可筛选文件/切换绘图类型）', custom:true},
  {chart:histChart, title:'图 3 · 各文件 TTFF 直方图', custom:true},
  {chart:rateChart, title:'图 4 · 恢复率对比', custom:false},
  {chart:meanChart, title:'图 4 · 平均 TTFF 对比', custom:false},
];
document.querySelectorAll('.zoom-btn').forEach(btn=>{
  btn.onclick = ()=>{
    const z = ZOOM_MAP[+btn.dataset.idx];
    openZoom(z.chart, z.title, z.custom);
  };
});

// responsive
window.addEventListener('resize', ()=>{
  boxChart.resize(); seqChart.resize(); histChart.resize(); rateChart.resize(); meanChart.resize();
  if(modalChart) modalChart.resize();
});
</script>
</body>
</html>
"""
    # ECharts：内嵌或外链
    if echarts_cdn_url:
        echarts_tag = '<script src="%s"></script>' % echarts_cdn_url
    else:
        echarts_tag = "<script>" + echarts_js + "</script>"
    html = (html
            .replace("__N_FILES__", str(n_files))
            .replace("__GEN_TIME__", gen_time)
            .replace("__METHOD_NOTE__", method_note)
            .replace("__OK_LINE__", ok_line)
            .replace("__JSON_NAME__", esc_html(json_out_name))
            .replace("__SEQ_DEFAULT__", json.dumps(seq_default, ensure_ascii=False))
            .replace("<script>__ECHARTS__</script>", echarts_tag)
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False)))
    return html


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def load_config(cfg_path):
    with open(cfg_path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    files_cfg = cfg.get("files", [])
    if not files_cfg:
        print("[错误] 配置文件中没有 files 列表。")
        sys.exit(1)
    settings = cfg.get("settings", {})
    return files_cfg, settings


def main():
    ap = argparse.ArgumentParser(
        description="NMEA 日志 TTFF 统计工具（按各文件配置的复位标志识别并统计）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("-c", "--config", default=DEFAULT_CONFIG, help="配置文件路径")
    ap.add_argument("-o", "--output-html", default=None, help="输出 HTML 报告路径（默认取配置 settings.output_html）")
    ap.add_argument("-j", "--output-json", default=None, help="输出明细 JSON 路径（默认取配置 settings.output_json）")
    ap.add_argument("--log-dir", default=None, help="日志所在目录（默认取配置 settings.log_dir，缺省为当前目录）")
    ap.add_argument("-e", "--echarts", default=None, help="ECharts 库文件路径（默认与脚本同目录 echarts.min.js）")
    ap.add_argument("--no-embed", action="store_true", help="不内嵌 ECharts（改用 CDN 外链，需要联网）")
    args = ap.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    # 配置文件：优先 cwd，其次脚本同目录
    cfg_path = args.config
    if not os.path.exists(cfg_path):
        alt = os.path.join(script_dir, os.path.basename(cfg_path))
        if os.path.exists(alt):
            cfg_path = alt
    files_cfg, settings = load_config(cfg_path)

    log_dir = args.log_dir or settings.get("log_dir", ".")
    out_html = args.output_html or settings.get("output_html", "TTFF统计报告.html")
    out_json = args.output_json or settings.get("output_json", "ttff_results.json")
    default_date = str(settings.get("default_date", "040826"))
    epoch = DEFAULT_EPOCH

    # 校验文件
    results = {}
    files = []
    for fc in files_cfg:
        fname = fc.get("file")
        if not fname:
            print(f"[警告] 跳过无 file 字段的配置项：{fc}")
            continue
        path = fname if os.path.isabs(fname) else os.path.join(log_dir, fname)
        if not os.path.exists(path):
            print(f"[警告] 文件不存在，跳过：{path}")
            continue
        marker = fc.get("reset_marker")
        if not marker:
            print(f"[警告] 未配置 reset_marker，跳过：{fname}")
            continue
        cycles, meta = analyze_file(path, marker, default_date)
        summary = summarize(cycles)
        files.append({
            "file": fname,
            "name": fc.get("name") or os.path.splitext(os.path.basename(fname))[0],
            "marker": marker,
            "note": fc.get("note", ""),
            "cycles": cycles,
            "meta": meta,
            "summary": summary,
        })
        print(f"[OK] {fname}: 复位 {summary['n_resets']} 次, "
              f"恢复 {summary['n_ok']} 次 ({summary['recovery_rate']}%), "
              f"TTFF {summary.get('median', '—')}s(中位)")
        if summary["n_ok"] == 0:
            print(f"     -> 该文件复位后未恢复定位，无有效 TTFF 样本")

    if not files:
        print("[错误] 没有可分析的文件。请检查配置文件与日志路径。")
        sys.exit(1)

    # 输出明细 JSON（合并 + 逐文件）
    out = {}
    for fd in files:
        cycles_save = [{k: v for k, v in c.items()} for c in fd["cycles"]]
        for c in cycles_save:
            c.pop("_invalid_seen", None)
        out[fd["file"]] = {"summary": fd["summary"], "cycles": cycles_save}
    # 确保输出目录存在（CLI 自定义输出路径时目录可能尚未创建）
    os.makedirs(os.path.dirname(os.path.abspath(out_json)) or ".", exist_ok=True)
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[OK] 明细 JSON(合并) -> {out_json}")

    # 逐文件明细 JSON：每个被分析的文件单独输出一个，便于核对单个日志
    out_dir = os.path.dirname(os.path.abspath(out_json))
    used = set()
    for idx, fd in enumerate(files, 1):
        base = _safe_filename(fd["name"] or os.path.splitext(os.path.basename(fd["file"]))[0])
        per_name = f"ttff_{idx:02d}_{base}.json"
        while per_name in used or os.path.exists(os.path.join(out_dir, per_name)):
            per_name = f"ttff_{idx:02d}_{base}_{len(used)}.json"
        used.add(per_name)
        per_path = os.path.join(out_dir, per_name)
        with open(per_path, "w", encoding="utf-8") as f:
            json.dump({
                "file": fd["file"], "name": fd["name"], "marker": fd["marker"],
                "summary": fd["summary"], "cycles": out[fd["file"]]["cycles"],
            }, f, ensure_ascii=False, indent=1)
        print(f"[OK] 明细 JSON(单文件) -> {per_path}")

    # 生成 HTML 报告
    echarts_cdn_url = None
    if args.no_embed:
        echarts_cdn_url = "https://cdn.bootcdn.net/ajax/libs/echarts/5.5.1/echarts.min.js"
        echarts_js = ""
    else:
        echarts_path = args.echarts or os.path.join(script_dir, "echarts.min.js")
        if not os.path.exists(echarts_path):
            print(f"[错误] 未找到 ECharts 库：{echarts_path}。"
                  f"请下载 echarts.min.js 放到工具目录，或使用 --no-embed（外链 CDN）。")
            sys.exit(1)
        with open(echarts_path, "r", encoding="utf-8") as f:
            echarts_js = f.read()

    payload = build_payload(files, epoch)
    gen_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    html = build_report_html(payload, os.path.basename(out_json), echarts_js, gen_time,
                             echarts_cdn_url=echarts_cdn_url)
    os.makedirs(os.path.dirname(os.path.abspath(out_html)) or ".", exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"[OK] HTML 报告 -> {out_html} ({os.path.getsize(out_html)/1024:.0f} KB)")

    n_ok_all = sum(fd["summary"]["n_ok"] for fd in files)
    n_nofix_all = sum(fd["summary"]["n_nofix"] for fd in files)
    print(f"\n汇总：{len(files)} 个文件，复位 {sum(fd['summary']['n_resets'] for fd in files)} 次，"
          f"恢复 {n_ok_all} 次，未恢复 {n_nofix_all} 次")


if __name__ == "__main__":
    main()
