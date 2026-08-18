"""生成自包含的 HTML 报告（内联 CSS + 内联 SVG，离线可用）。

供「时间连续性检测」与「重复行检测」的结果预览使用：
- 时间连续性：概览卡片 + SVG 时间轴（直观定位异常时间点）+ 异常明细表。
- 重复行检测：概览卡片 + 重复分组表（行号 + 内容预览）。
渲染由 base_page.show_result_preview_html 载入 QWebEngineView。
"""

import html
from typing import Any


def esc(s: Any) -> str:
    return html.escape(str(s), quote=True)


# ---------- 通用样式 ----------
BASE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; padding: 18px 22px; background: #F7F8FA; color: #1F2937;
       font-family: -apple-system, "Segoe UI", "Microsoft YaHei", Roboto, Helvetica, Arial, sans-serif;
       font-size: 13px; line-height: 1.5; }
h1 { font-size: 18px; margin: 0 0 2px; color: #111827; }
h2 { font-size: 14px; margin: 20px 0 10px; color: #374151;
     border-left: 3px solid #4F46E5; padding-left: 8px; }
.sub { color: #6B7280; font-size: 12px; margin-bottom: 14px; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 6px; }
.card { background: #fff; border: 1px solid #E5E7EB; border-radius: 10px;
        padding: 10px 14px; min-width: 120px; box-shadow: 0 1px 2px rgba(16,24,40,.04); }
.card .n { font-size: 20px; font-weight: 700; color: #111827; }
.card .l { font-size: 11px; color: #6B7280; margin-top: 2px; }
.card.bad .n { color: #EF4444; }
.card.warn .n { color: #F59E0B; }
.card.ok .n { color: #22C55E; }
table { width: 100%; border-collapse: collapse; background: #fff;
        border: 1px solid #E5E7EB; border-radius: 10px; overflow: hidden; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #F1F3F5;
         font-size: 12px; vertical-align: top; }
th { background: #F9FAFB; color: #374151; font-weight: 600; }
tr:last-child td { border-bottom: none; }
tr.sev-time td:first-child { box-shadow: inset 3px 0 0 #EF4444; }
tr.sev-quality td:first-child { box-shadow: inset 3px 0 0 #F59E0B; }
tr.sev-exact td:first-child { box-shadow: inset 3px 0 0 #4F46E5; }
tr.sev-similar td:first-child { box-shadow: inset 3px 0 0 #0EA5E9; }
.mono { font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
        font-size: 11.5px; white-space: pre-wrap; word-break: break-all; color: #374151; }
.muted { color: #9CA3AF; }
.tag { display: inline-block; padding: 1px 7px; border-radius: 999px; font-size: 11px; }
.tag.time { background: #FEE2E2; color: #B91C1C; }
.tag.quality { background: #FEF3C7; color: #B45309; }
.tag.exact { background: #EEF2FF; color: #4338CA; }
.tag.similar { background: #E0F2FE; color: #0369A1; }
.timeline-wrap { background: #fff; border: 1px solid #E5E7EB; border-radius: 10px;
                 padding: 12px 14px; }
.legend { font-size: 11px; color: #6B7280; margin-top: 6px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px;
            margin: 0 4px 0 12px; vertical-align: middle; }
.empty { color: #22C55E; font-weight: 600; }
"""

TIMELINE_SVG = """
<svg viewBox="0 0 1000 90" width="100%" height="90" preserveAspectRatio="none"
     style="display:block;margin-top:4px">
  <rect x="10" y="42" width="980" height="6" rx="3" fill="#E5E7EB"></rect>
  <text x="10" y="74" font-size="11" fill="#9CA3AF">行 1</text>
  <text x="990" y="74" font-size="11" fill="#9CA3AF" text-anchor="end">行 {total}</text>
  {ticks}
</svg>
"""


def _timeline_ticks(anomalies: list[dict], total: int) -> str:
    if total <= 0:
        return ""
    ticks = []
    for a in anomalies:
        line = a.get("line") or 0
        x = 10 + (line / total) * 980
        color = "#EF4444" if a.get("severity") == "time" else "#F59E0B"
        title = esc(
            f"行 {line} | {a.get('atype','')} | 时间 {a.get('time') or '-'} | 偏差 {a.get('delta') or '-'}"
        )
        ticks.append(
            f'<line x1="{x:.1f}" y1="22" x2="{x:.1f}" y2="62" stroke="{color}" '
            f'stroke-width="2.5"><title>{title}</title></line>'
        )
    return "\n  ".join(ticks)


# ---------- 时间连续性 ----------
def build_time_continuity_html(
    meta: dict,
    summary: dict,
    anomalies: list[dict],
    total_lines: int,
) -> str:
    freq = meta.get("freq")
    th = meta.get("time_threshold_s")
    th_txt = f"{th:.3f}s" if isinstance(th, (int, float)) else str(th)

    cards = []
    cards.append(("<总历元数>", total_lines, ""))
    cards.append(("<RMC 时间异常>", summary.get("RMC", 0), "bad" if summary.get("RMC") else "ok"))
    cards.append(("<GGA 时间异常>", summary.get("GGA", 0), "bad" if summary.get("GGA") else "ok"))
    cards.append(("<GGA 定位缺失>", summary.get("GGA定位缺失", 0), "warn" if summary.get("GGA定位缺失") else "ok"))
    cards.append(("<PVTResult 异常>", summary.get("PVTResult", 0), "warn" if summary.get("PVTResult") else "ok"))
    cards.append(("<PVTMeas 异常>", summary.get("PVTMeas", 0), "warn" if summary.get("PVTMeas") else "ok"))
    cards_html = "".join(
        f'<div class="card {cls}"><div class="n">{n}</div><div class="l">{esc(lbl)}</div></div>'
        for lbl, n, cls in cards
    )

    if anomalies:
        rows = []
        sev_label = {"time": "时间跳变", "quality": "数据质量"}
        for a in sorted(anomalies, key=lambda x: x.get("line") or 0):
            sev = a.get("severity", "quality")
            tag = "time" if sev == "time" else "quality"
            rows.append(
                f'<tr class="sev-{tag}"><td><span class="tag {tag}">{esc(sev_label.get(sev, sev))}</span></td>'
                f'<td class="mono">{esc(a.get("line") or "-")}</td>'
                f'<td class="mono">{esc(a.get("time") or "-")}</td>'
                f'<td class="mono">{esc(a.get("delta") or "-")}</td>'
                f'<td class="mono">{esc(a.get("detail") or "")}</td></tr>'
            )
        table_html = (
            "<table><thead><tr><th>类型</th><th>行号</th><th>时间</th>"
            "<th>偏差</th><th>详情</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
    else:
        table_html = '<p class="empty">✓ 未检测到时间不连续 / 异常。</p>'

    timeline = TIMELINE_SVG.format(total=total_lines, ticks=_timeline_ticks(anomalies, total_lines))

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{BASE_CSS}</style></head><body>
<h1>时间连续性检测报告</h1>
<div class="sub">文件：{esc(meta.get('input_name',''))} ｜ 频率：{esc(freq)} Hz ｜ 连续阈值：{esc(th_txt)} ｜ 输出：{esc(meta.get('output_name',''))}</div>
<div class="cards">{cards_html}</div>
<h2>时间轴概览（红=时间跳变，橙=数据质量异常；鼠标悬停查看详情）</h2>
<div class="timeline-wrap">{timeline}
  <div class="legend"><i style="background:#EF4444"></i>时间跳变<i style="background:#F59E0B"></i>数据质量异常</div>
</div>
<h2>异常明细（按行号排序）</h2>
{table_html}
</body></html>"""


# ---------- 重复行检测 ----------
def build_duplicate_html(meta: dict, result: dict) -> str:
    total = result.get("total_lines", 0)
    unique = result.get("unique_lines", 0)
    sim = result.get("similarity")
    exact_groups = result.get("exact_groups", []) or []
    similar_groups = result.get("similar_groups", []) or []

    exact_dup = sum(len(g["lines"]) for g in exact_groups) - len(exact_groups) if exact_groups else 0
    similar_dup = sum(len(g["lines"]) for g in similar_groups) - len(similar_groups) if similar_groups else 0
    total_dup = exact_dup + similar_dup
    rate = (total_dup / total * 100) if total else 0.0

    cards = []
    cards.append(("<总行数>", total, ""))
    cards.append(("<去重后行数>", unique, ""))
    cards.append(("<完全重复组>", len(exact_groups), "bad" if exact_groups else "ok"))
    if sim is not None:
        cards.append(("<相似重复组>", len(similar_groups), "warn" if similar_groups else "ok"))
    cards.append(("<总计重复率>", f"{rate:.2f}%", "bad" if total_dup else "ok"))
    cards_html = "".join(
        f'<div class="card {cls}"><div class="n">{esc(n)}</div><div class="l">{esc(lbl)}</div></div>'
        for lbl, n, cls in cards
    )

    rows = []
    for g in exact_groups:
        lines = g.get("lines", [])
        content = g.get("content", "")
        if len(content) > 200:
            content = content[:200] + " …"
        rows.append(
            f'<tr class="sev-exact"><td><span class="tag exact">完全重复</span></td>'
            f'<td class="mono">{len(lines)}</td>'
            f'<td class="mono">{esc(lines)}</td>'
            f'<td class="mono">{esc(content)}</td></tr>'
        )
    for g in similar_groups:
        lines = g.get("lines", [])
        rep = g.get("rep", "")
        if len(rep) > 200:
            rep = rep[:200] + " …"
        rows.append(
            f'<tr class="sev-similar"><td><span class="tag similar">相似重复</span></td>'
            f'<td class="mono">{len(lines)}</td>'
            f'<td class="mono">{esc(lines)}</td>'
            f'<td class="mono">{esc(rep)}</td></tr>'
        )

    if rows:
        table_html = (
            "<table><thead><tr><th>类型</th><th>重复次数</th><th>行号</th><th>内容预览</th></tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )
    else:
        table_html = '<p class="empty">✓ 未发现重复行。</p>'

    sim_txt = f"{sim*100:.0f}%" if isinstance(sim, (int, float)) else "未启用（仅完全重复）"

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>{BASE_CSS}</style></head><body>
<h1>重复行检测报告</h1>
<div class="sub">文件：{esc(meta.get('input_name',''))} ｜ 相似度阈值：{esc(sim_txt)} ｜ 输出：{esc(meta.get('output_name',''))}</div>
<div class="cards">{cards_html}</div>
<h2>重复分组明细</h2>
{table_html}
</body></html>"""
