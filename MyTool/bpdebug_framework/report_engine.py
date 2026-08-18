# -*- coding: utf-8 -*-
"""报告生成引擎：配置 → 解析 → 聚合 → HTML 报告。

流程：
  1. load_config 规范化配置（文件/字段/图表声明）
  2. 对每个文件用 BPDebugFrame 解析，得到每历元记录（含全部字段）
  3. 按图表类型聚合数据（line/bar/pie/scatter/table/nofix_timeline）
  4. 渲染自包含 HTML（内嵌 ECharts + 数据 JS），输出到指定路径

对外主入口：generate_report(config, output_dir=None, log_callback=None) -> HTML 路径
"""
import datetime
import json
import os
import shutil
from pathlib import Path

from app.paths import get_project_root
from bpdebug_framework.bpdebug_framework import BPDebugFrame, FieldSpec, parse_bpdebug
from bpdebug_framework.report_config import load_config

ECHARTS_SRC = get_project_root() / "ttff_tool" / "echarts.min.js"
FILE_COLORS = ["#1f77b4", "#2ca02c", "#ff7f0e", "#9467bd", "#d62728", "#17becf",
               "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22"]


def _esc(s):
    return str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")


def _fmt_ms(ms):
    dt = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.timezone.utc)
    return dt.strftime("%m-%d %H:%M:%S")


def _parse_one_file(path, fields_cfg, log):
    """解析单个文件，返回 (rows, stats)。rows 中 utc_time 为 epoch ms。"""
    log(f"[解析] {Path(path).name} ...")
    specs = [
        FieldSpec(f["stmt"], f["index"], f["name"], f["parser"], take=f["take"])
        for f in fields_cfg
    ]
    frame = BPDebugFrame(specs, log_callback=log)
    rows = frame.parse_file(path)
    # 转 epoch ms 便于 JS 使用
    for r in rows:
        if r.get("utc_time") is not None:
            r["_t"] = int(r["utc_time"].timestamp() * 1000)
        else:
            r["_t"] = None
    log(f"       历元 {frame.stats['total_epochs']}, 不定位 {frame.stats['nofix_epochs']}")
    return rows, frame.stats


def _series_for_chart(rows, field):
    """提取字段的时间序列（跳过 None 和 None 时间）。返回 [[t, v], ...]"""
    out = []
    for r in rows:
        if r["_t"] is None:
            continue
        v = r.get(field)
        if v is None:
            continue
        out.append([r["_t"], v])
    return out


# ---------------------------------------------------------------------------
# 图表数据聚合
# ---------------------------------------------------------------------------

def _agg_line(files_data, chart):
    """line: 多文件 × 多字段，每字段一个系列（值随时间变化）。"""
    series = []
    field_list = chart.get("fields") or []
    for fi, fd in enumerate(files_data):
        for fld in field_list:
            pts = _series_for_chart(fd["rows"], fld)
            series.append({
                "name": f"{fd['name']} · {fld}",
                "color": fd["color"],
                "points": pts,
            })
    return {"series": series, "y_name": chart.get("y_name") or (field_list[0] if field_list else "")}


def _agg_bar(files_data, chart):
    """bar: 字段值的分布/占比（按整数值分桶或按文本值分组）。"""
    field = (chart.get("fields") or [None])[0]
    out = []
    for fd in files_data:
        counter = {}
        for r in fd["rows"]:
            v = r.get(field)
            if v is None:
                continue
            counter[str(v)] = counter.get(str(v), 0) + 1
        items = sorted(counter.items(), key=lambda kv: kv[0])
        out.append({
            "name": fd["name"],
            "color": fd["color"],
            "categories": [k for k, _ in items],
            "counts": [v for _, v in items],
        })
    return {"groups": out, "field": field}


def _agg_pie(files_data, chart):
    """pie: 字段值占比（如 is_nofix 0/1、定位质量 0/1/2）。"""
    field = (chart.get("fields") or [None])[0]
    out = []
    for fd in files_data:
        counter = {}
        total = 0
        for r in fd["rows"]:
            v = r.get(field)
            if v is None:
                continue
            counter[str(v)] = counter.get(str(v), 0) + 1
            total += 1
        items = [{"name": k, "value": v} for k, v in sorted(counter.items(), key=lambda kv: kv[0])]
        out.append({"name": fd["name"], "color": fd["color"], "items": items, "total": total})
    return {"groups": out, "field": field}


def _agg_scatter(files_data, chart):
    """scatter: X 字段 vs Y 字段。"""
    xf = chart.get("x_field")
    yf = chart.get("y_field")
    out = []
    for fd in files_data:
        pts = []
        for r in fd["rows"]:
            xv = r.get(xf)
            yv = r.get(yf)
            if xv is None or yv is None or r["_t"] is None:
                continue
            pts.append([xv, yv])
        out.append({"name": fd["name"], "color": fd["color"], "points": pts})
    return {"series": out, "x_name": xf, "y_name": yf}


def _agg_table(files_data, chart):
    """table: 各文件各字段的统计（min/max/mean/std/中位数/计数/不定位数）。"""
    field_list = chart.get("fields") or []
    rows_out = []
    for fd in files_data:
        row = {"name": fd["name"]}
        for fld in field_list:
            vals = [r[fld] for r in fd["rows"] if r.get(fld) is not None and isinstance(r[fld], (int, float))]
            if vals:
                n = len(vals)
                s = sorted(vals)
                row[f"{fld}_n"] = n
                row[f"{fld}_min"] = round(min(vals), 4)
                row[f"{fld}_max"] = round(max(vals), 4)
                row[f"{fld}_mean"] = round(sum(vals) / n, 4)
                row[f"{fld}_median"] = round(s[n // 2], 4)
                row[f"{fld}_std"] = round((sum((x - sum(vals) / n) ** 2 for x in vals) / n) ** 0.5, 4)
            else:
                row[f"{fld}_n"] = 0
                for suf in ("min", "max", "mean", "median", "std"):
                    row[f"{fld}_{suf}"] = None
        row["_nofix"] = sum(1 for r in fd["rows"] if r["is_nofix"] == 1)
        row["_total"] = len(fd["rows"])
        rows_out.append(row)
    return {"rows": rows_out, "fields": field_list}


def _agg_nofix_timeline(files_data, chart):
    """nofix_timeline: 横轴时间，纵轴 0/1 标记不定位（is_nofix 为框架内置字段）。"""
    out = []
    for fd in files_data:
        pts = []
        for r in fd["rows"]:
            if r["_t"] is None:
                continue
            pts.append([r["_t"], 1 if r["is_nofix"] == 1 else 0])
        out.append({"name": fd["name"], "color": fd["color"], "points": pts})
    return {"series": out}


_AGGREGATORS = {
    "line": _agg_line,
    "bar": _agg_bar,
    "pie": _agg_pie,
    "scatter": _agg_scatter,
    "table": _agg_table,
    "nofix_timeline": _agg_nofix_timeline,
}


# ---------------------------------------------------------------------------
# 渲染 HTML
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{TITLE}</title>
<script src="echarts.min.js"></script>
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ font-family:"Microsoft YaHei",Arial,sans-serif; background:#f5f6fa; color:#2c3e50; padding:20px; }}
  .header {{
    background:linear-gradient(135deg,#1a2a6c,#b21f1f,#fdbb2d); border-radius:12px; color:#fff;
    padding:22px 28px; margin-bottom:16px; box-shadow:0 4px 15px rgba(0,0,0,.15);
  }}
  .header h1 {{ font-size:21px; }}
  .header p {{ font-size:13px; opacity:.9; margin-top:6px; }}
  .controls {{ display:flex; align-items:center; gap:10px; margin-bottom:14px; flex-wrap:wrap; }}
  .file-check {{
    display:inline-flex; align-items:center; gap:6px; padding:6px 13px; border:2px solid #ccc;
    border-radius:20px; background:#fff; font-size:13px; font-weight:600; cursor:pointer;
  }}
  .file-check input {{ margin:0; cursor:pointer; }}
  .info {{ font-size:13px; color:#555; background:#eef1f7; padding:7px 13px; border-radius:8px; }}
  .chart-card {{ background:#fff; border-radius:12px; box-shadow:0 2px 10px rgba(0,0,0,.07); padding:10px; margin-bottom:16px; }}
  .chart-title {{ font-size:14px; font-weight:700; padding:6px 10px 2px; color:#333; }}
  .chart-body {{ width:100%; }}
  .tip {{ font-size:12px; color:#999; margin:4px 12px 10px; }}
  .swatch {{ display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:4px; vertical-align:middle; }}
  table.stats {{ border-collapse:collapse; width:100%; font-size:13px; margin:8px 0; }}
  table.stats th, table.stats td {{ border:1px solid #ddd; padding:6px 9px; text-align:center; }}
  table.stats th {{ background:#f0f3f8; }}
</style>
</head>
<body>
<div class="header">
  <h1>📡 {TITLE}</h1>
  <p>BPDEBUG 数据分析报告 ｜ 共 {NFILES} 个文件 ｜ {TIME_RANGE} ｜ {TEMP_DESC}</p>
</div>
<div class="controls" id="fileControls">
  <span class="info" id="fileInfo">加载中...</span>
</div>
{NOFIX_TOGGLE}
{CHARTS_HTML}
<div class="tip">💡 滚轮缩放 X 轴；Ctrl+滚轮缩放 Y 轴；拖动平移；悬停查看数值；图例点击隐藏/显示曲线。</div>
<script>
var ACTIVE = {ACTIVE_JS};
var TEMP_AVAILABLE = {TEMP_AVAILABLE};
var FILES = [];
ACTIVE.forEach(function(a,i){{ FILES.push({{id:i+1, key:a.key, label:a.label, note:a.note, color:a.color, dataJs:'data_'+a.key+'.js'}}); }});
var dataStore = {{}}, tempData = null, selected = {{}}, loadPending = 0, showNofix = {SHOW_NOFIX};
{TEMP_LOAD_JS}
(function(){{
  var ctrl = document.getElementById('fileControls');
  FILES.forEach(function(f){{
    selected[f.id] = true;
    var lbl = document.createElement('label');
    lbl.className = 'file-check';
    lbl.style.borderColor = f.color;
    var cb = document.createElement('input');
    cb.type = 'checkbox'; cb.checked = true;
    cb.onchange = function(){{ toggleFile(f.id); }};
    lbl.appendChild(cb);
    var sw = document.createElement('span');
    sw.className = 'swatch'; sw.style.background = f.color;
    lbl.appendChild(sw);
    lbl.appendChild(document.createTextNode(f.label + (f.note ? '(' + f.note + ')' : '')));
    ctrl.insertBefore(lbl, ctrl.firstChild);
  }});
}})();
function fmtTime(ms){{ var d=new Date(ms); function p(n){{return n<10?'0'+n:''+n;}} return p(d.getUTCHours())+':'+p(d.getUTCMinutes())+':'+p(d.getUTCSeconds()); }}
function fmtFull(ms){{ var d=new Date(ms); function p(n){{return n<10?'0'+n:''+n;}} return p(d.getUTCMonth()+1)+'-'+p(d.getUTCDate())+' '+p(d.getUTCHours())+':'+p(d.getUTCMinutes())+':'+p(d.getUTCSeconds()); }}
function findNearest(ts, vals, x) {{
  if(!ts||!ts.length) return null;
  if(x<=ts[0]) return vals[0];
  if(x>=ts[ts.length-1]) return vals[vals.length-1];
  var lo=0, hi=ts.length-1;
  while(lo<hi-1){{ var mid=(lo+hi)>>1; if(ts[mid]<=x) lo=mid; else hi=mid; }}
  return (x-ts[lo]<=ts[hi]-x)?vals[lo]:vals[hi];
}}
var CHARTS = {CHARTS_JS};
function _allIdx(n){{ var a=[]; for(var i=0;i<n;i++) a.push(i); return a; }}
function _yZoom(i){{ return {{type:'inside', yAxisIndex:[i], start:0, end:100, zoomOnMouseWheel:true, moveOnMouseMove:true, modifierKey:'ctrl', zoomLock:false}}; }}
function buildOption(activeFiles, chart) {{
  var c = chart;
  var legend = [], series = [];
  var xAxis, yAxis, grids, dataZoom, tooltip;
  var nFiles = activeFiles.length;

  if (c.type === 'line' || c.type === 'nofix_timeline') {{
    // 每文件一条线（line 还支持多字段 => 每条线一个 grid 更清晰；这里多字段共用 Y 轴）
    grids = [{{ left:60, right:50, top:'8%', height:'72%' }}];
    xAxis = [{{ type:'time', gridIndex:0, axisLabel:{{formatter:fmtTime}} }}];
    yAxis = [{{ type:'value', gridIndex:0, name:c.y_name||'', scale:true }}];
    activeFiles.forEach(function(af){{
      var d = af.data;
      if (c.type === 'nofix_timeline') {{
        series.push({{ name:af.file.label+(af.file.note?'('+af.file.note+')':''),
          type:'line', showSymbol:false, data:d.nofix||[], step:'end',
          lineStyle:{{width:1.1,color:af.file.color}}, itemStyle:{{color:af.file.color}},
          large:true, progressive:5000 }});
      }} else {{
        c.fields.forEach(function(fld){{
          var pts = d.series[fld] || [];
          series.push({{ name:af.file.label+(af.file.note?'('+af.file.note+')':'')+' · '+fld,
            type:'line', showSymbol:false, data:pts, sampling:'lttb',
            lineStyle:{{width:1.1,color:af.file.color}}, itemStyle:{{color:af.file.color}},
            large:true, progressive:5000 }});
        }});
      }}
    }});
    legend = [{{ top:'0%', type:'scroll' }}];
    dataZoom = [{{type:'inside', xAxisIndex:[0], zoomOnMouseWheel:true, moveOnMouseMove:true}},
                {{type:'slider', xAxisIndex:[0], bottom:0, height:20, formatter:fmtTime}}];
    tooltip = {{ trigger:'axis', axisPointer:{{type:'cross'}},
      formatter:function(params){{ if(!params||!params.length) return ''; var x=params[0].axisValue;
        var lines=['<b>'+fmtFull(x)+' UTC</b>'];
        params.forEach(function(p){{ lines.push('<span style=\"display:inline-block;width:10px;height:10px;background:'+p.color+';border-radius:50%;margin-right:6px;\"></span>'+p.seriesName+': <b>'+Number(p.value[1]).toFixed(3)+'</b>'); }});
        return lines.join('<br/>'); }} }};
  }} else if (c.type === 'bar') {{
    // 多文件分组柱状图：X=分类, 每文件一组
    var cats = [];
    activeFiles.forEach(function(af){{ (af.data.groups||[]).forEach(function(g){{ g.categories.forEach(function(k){{ if(cats.indexOf(k)===-1) cats.push(k); }}); }}); }});
    cats.sort();
    grids = [{{ left:60, right:50, top:'8%', height:'72%' }}];
    xAxis = [{{ type:'category', gridIndex:0, data:cats }}];
    yAxis = [{{ type:'value', gridIndex:0, name:'次数' }}];
    activeFiles.forEach(function(af){{
      var g = (af.data.groups||[])[0];
      var data = cats.map(function(k){{ var i = g.categories.indexOf(k); return i>=0 ? g.counts[i] : 0; }});
      series.push({{ name:af.file.label+(af.file.note?'('+af.file.note+')':''), type:'bar', data:data,
        itemStyle:{{color:af.file.color}} }});
    }});
    legend = [{{ top:'0%' }}];
    dataZoom = [{{type:'inside', xAxisIndex:[0]}}];
    tooltip = {{ trigger:'axis' }};
  }} else if (c.type === 'pie') {{
    // 多文件饼图：并排多个
    grids = [];
    xAxis = []; yAxis = []; dataZoom = [];
    var w = 100 / Math.min(nFiles, 4);
    activeFiles.forEach(function(af, i){{
      var left = 4 + i * w;
      grids.push({{ left:left+'%', width:(w-6)+'%', top:'12%', height:'68%' }});
      var g = (af.data.groups||[])[0];
      series.push({{ name:af.file.label, type:'pie', radius:'65%', center:[left+w/2+'%', '46%'],
        data:g.items || [], label:{{formatter:'{{b}}: {{d}}%'}} }});
    }});
    legend = [{{ bottom:'0%', type:'scroll' }}];
    tooltip = {{ trigger:'item' }};
  }} else if (c.type === 'scatter') {{
    grids = [{{ left:60, right:50, top:'8%', height:'72%' }}];
    xAxis = [{{ type:'value', gridIndex:0, name:c.x_name||'X' }}];
    yAxis = [{{ type:'value', gridIndex:0, name:c.y_name||'Y' }}];
    activeFiles.forEach(function(af){{
      series.push({{ name:af.file.label+(af.file.note?'('+af.file.note+')':''), type:'scatter',
        data:af.data.series0||[], symbolSize:3,
        itemStyle:{{color:af.file.color, opacity:.6}} }});
    }});
    legend = [{{ top:'0%' }}];
    dataZoom = [{{type:'inside', xAxisIndex:[0], yAxisIndex:[0]}}];
    tooltip = {{ trigger:'item',
      formatter:function(p){{ return p.seriesName+'<br/>'+c.x_name+': '+p.value[0]+'<br/>'+c.y_name+': '+p.value[1]; }} }};
  }} else {{
    // table 不渲染 echarts，由后端生成 HTML 表格
    return null;
  }}

  return {{ backgroundColor:'#fff', animation:false, legend:legend, grid:grids, xAxis:xAxis,
    yAxis:yAxis, series:series, dataZoom:dataZoom, tooltip:tooltip }};
}}
function renderChart(container, activeFiles, chart) {{
  if (chart.type === 'table') {{
    // 后端已生成静态 HTML 表格
    return;
  }}
  var myChart = echarts.init(container);
  var opt = buildOption(activeFiles, chart);
  if (opt) myChart.setOption(opt);
  window._charts = window._charts || [];
  window._charts.push(myChart);
}}
function refresh() {{
  var active = [];
  FILES.forEach(function(f){{ if (selected[f.id] && dataStore[f.id]) active.push({{file:f, data:dataStore[f.id]}}); }});
  if (!active.length) {{ document.getElementById('fileInfo').textContent='请至少勾选一个文件'; return; }}
  // 销毁旧图
  (window._charts||[]).forEach(function(c){{ try{{c.dispose();}}catch(e){{}} }});
  window._charts = [];
  var containers = document.querySelectorAll('.chart-body');
  CHARTS.forEach(function(chart, i){{
    if (i >= containers.length) return;
    var box = containers[i];
    // 表格类: 内容已静态渲染，无需刷新（多文件切换时表格不联动）
    if (chart.type === 'table') return;
    box.innerHTML = '';
    var el = document.createElement('div');
    el.style.width = '100%';
    el.style.height = chart.height + 'px';
    box.appendChild(el);
    renderChart(el, active, chart);
  }});
  var first = active[0].data;
  document.getElementById('fileInfo').textContent =
    '已显示 ' + active.length + ' 个文件 ｜ 时间范围: ' + fmtFull(first.range[0]) + ' ~ ' + fmtFull(first.range[1]) + ' UTC';
}}
function loadFile(f) {{
  if (dataStore[f.id]) {{ refresh(); return; }}
  var s = document.createElement('script');
  s.src = f.dataJs + '?t=' + Date.now();
  loadPending++;
  s.onload = function(){{ dataStore[f.id] = window.DATA; loadPending--; if (loadPending<=0) refresh(); }};
  s.onerror = function(){{ loadPending--; }};
  document.body.appendChild(s);
}}
function toggleFile(id) {{ selected[id]=!selected[id]; refresh(); }}
function toggleNofix(checked) {{ showNofix=checked; refresh(); }}
window.addEventListener('resize', function(){{ (window._charts||[]).forEach(function(c){{ c.resize(); }}); }});
FILES.forEach(function(f){{ loadFile(f); }});
</script>
</body>
</html>
"""


def _chart_html(cfg, chart, index):
    """生成单个图表的 HTML 卡片（含标题 + 容器 div；表格类直接渲染静态表）。"""
    if chart["type"] == "table":
        return _render_table_chart(chart)
    height = cfg.get("chart_height", 420)
    return (
        '<div class="chart-card">'
        f'<div class="chart-title">{_esc(chart.get("title") or chart["type"])}</div>'
        f'<div class="chart-body" id="cb{index}"></div>'
        '</div>'
    )


def _render_table_chart(chart):
    """渲染统计表（静态 HTML，不依赖 ECharts）。"""
    fields = chart.get("fields") or []
    rows_data = chart.get("_rows") or []
    if not rows_data:
        return '<div class="chart-card"><div class="chart-title">无数据</div></div>'
    headers = ["文件", "历元总数", "不定位"] + [f"{f}\n(min/max/mean)" for f in fields]
    html = ['<div class="chart-card">',
            f'<div class="chart-title">{_esc(chart.get("title") or "统计表")}</div>',
            '<div style="overflow-x:auto;"><table class="stats"><thead><tr>']
    for h in headers:
        html.append(f'<th>{_esc(h)}</th>')
    html.append('</tr></thead><tbody>')
    for r in rows_data:
        html.append('<tr>')
        html.append(f'<td>{_esc(r["name"])}</td>')
        html.append(f'<td>{r["_total"]}</td>')
        html.append(f'<td>{r["_nofix"]}</td>')
        for f in fields:
            if r.get(f"{f}_n"):
                html.append(f'<td>{r[f"{f}_min"]} / {r[f"{f}_max"]} / {r[f"{f}_mean"]}</td>')
            else:
                html.append('<td>-</td>')
        html.append('</tr>')
    html.append('</tbody></table></div></div>')
    return "".join(html)


def _build_charts_js(cfg, chart_data):
    """生成前端 CHARTS 数组（含聚合数据）。"""
    out = []
    for i, chart in enumerate(cfg["charts"]):
        ctype = chart["type"]
        payload = {
            "type": ctype,
            "title": chart.get("title", ""),
            "height": cfg.get("chart_height", 420),
        }
        if ctype == "line" or ctype == "nofix_timeline":
            payload["fields"] = chart.get("fields") or []
            payload["y_name"] = chart.get("y_name", "")
        elif ctype == "bar":
            payload["fields"] = chart.get("fields") or []
        elif ctype == "pie":
            payload["fields"] = chart.get("fields") or []
        elif ctype == "scatter":
            payload["x_field"] = chart.get("x_field", "")
            payload["y_field"] = chart.get("y_field", "")
        out.append(payload)
    return json.dumps(out, ensure_ascii=False)


def _build_series_data(files_data):
    """按文件生成 data js payload（line/scatter/nofix_timeline 需要的聚合数据）。"""
    payloads = []
    for fd in files_data:
        payload = {
            "name": fd["name"],
            "note": fd["note"],
            "range": [fd["t0"], fd["t1"]] if fd["t0"] is not None else [0, 0],
            "series": {},
            "nofix": [],
        }
        # 为每个声明的字段生成时间序列
        for fld in fd["fields"]:
            payload["series"][fld] = _series_for_chart(fd["rows"], fld)
        # is_nofix 时间线（内置字段）
        for r in fd["rows"]:
            if r["_t"] is None:
                continue
            payload["nofix"].append([r["_t"], 1 if r["is_nofix"] == 1 else 0])
        payloads.append(payload)
    return payloads


def generate_report(config, output_dir=None, log_callback=None):
    """主入口：按配置生成 HTML 报告，返回报告路径。"""
    log = log_callback or (lambda msg: print(msg))
    cfg = load_config(config)

    # ---- 输出目录 ----
    if output_dir and str(output_dir).strip():
        out_dir = Path(output_dir)
    else:
        from app.output_dirs import get_feature_output_dir
        out_dir = get_feature_output_dir("clock_drift")
    out_dir.mkdir(parents=True, exist_ok=True)

    # ---- 解析所有文件 ----
    files_data = []
    active = []
    for i, f in enumerate(cfg["files"]):
        if not Path(f["path"]).exists():
            log(f"[跳过] 文件不存在: {f['path']}")
            continue
        rows, stats = _parse_one_file(f["path"], cfg["fields"], log)
        if not rows:
            log(f"[错误] {Path(f['path']).name} 未解析到数据")
            continue
        t0 = min((r["_t"] for r in rows if r["_t"] is not None), default=None)
        t1 = max((r["_t"] for r in rows if r["_t"] is not None), default=None)
        color = FILE_COLORS[i % len(FILE_COLORS)]
        fd = {
            "name": f["name"],
            "note": f["note"],
            "path": f["path"],
            "rows": rows,
            "fields": [fl["name"] for fl in cfg["fields"]],
            "color": color,
            "t0": t0,
            "t1": t1,
            "stats": stats,
        }
        files_data.append(fd)
        key = f"f{len(active) + 1}"
        active.append({"key": key, "label": f["name"], "note": f["note"], "color": color})
        # 写数据 js
        payload = _build_series_data([fd])[0]
        with open(out_dir / f"data_{key}.js", "w", encoding="utf-8") as fh:
            fh.write("window.DATA = " + json.dumps(payload) + ";")
        log(f"       已生成 data_{key}.js")

    if not files_data:
        raise RuntimeError("没有成功解析任何文件")

    # ---- 温度（可选）----
    temp_available = False
    temp_load_js = ""
    temp_desc = "未提供温度曲线"
    if cfg.get("temp_csv") and Path(cfg["temp_csv"]).exists():
        from bpdebug_framework.bpdebug_framework import parse_temp_file
        temp = parse_temp_file(cfg["temp_csv"], log_callback=log)
        if temp and temp["ts"]:
            with open(out_dir / "data_temp.js", "w", encoding="utf-8") as fh:
                fh.write("window.TEMP_DATA = " + json.dumps(temp) + ";")
            temp_available = True
            temp_load_js = ("(function(){ var s=document.createElement('script'); s.src='data_temp.js';"
                            " s.onload=function(){ tempData=window.TEMP_DATA; }; document.body.appendChild(s); })();")
            temp_desc = "含温度曲线"
            log("[温度] 已生成 data_temp.js")

    # ---- 聚合各图表数据 ----
    for chart in cfg["charts"]:
        agg = _AGGREGATORS[chart["type"]](files_data, chart)
        chart["_agg"] = agg
        if chart["type"] == "table":
            chart["_rows"] = agg["rows"]
        elif chart["type"] == "scatter":
            # scatter 数据放入每个文件的 series0
            for fd, s in zip(files_data, agg["series"]):
                fd["series0"] = s["points"]

    # ---- 时间范围 ----
    all_t = [t for fd in files_data for t in (fd["t0"], fd["t1"]) if t is not None]
    time_range = f"{_fmt_ms(min(all_t))} ~ {_fmt_ms(max(all_t))} UTC" if all_t else ""

    # ---- 渲染 HTML ----
    charts_html = "".join(_chart_html(cfg, c, i) for i, c in enumerate(cfg["charts"]))
    nofix_toggle = ""
    if cfg["show_nofix_default"]:
        nofix_toggle = (
            '<div class="controls" style="margin-top:-6px;">'
            '<label class="file-check" style="border-color:#e74c3c;cursor:pointer;">'
            '<input type="checkbox" id="nofixToggle" onchange="toggleNofix(this.checked)">'
            '<span class="swatch" style="background:#e74c3c;"></span>'
            '<span>显示不定位点</span></label></div>'
        )
    html = HTML_TEMPLATE.format(
        TITLE=_esc(cfg["title"]),
        NFILES=len(files_data),
        TIME_RANGE=_esc(time_range),
        TEMP_DESC=temp_desc,
        ACTIVE_JS=json.dumps(active, ensure_ascii=False),
        TEMP_AVAILABLE="true" if temp_available else "false",
        TEMP_LOAD_JS=temp_load_js,
        SHOW_NOFIX="true" if cfg["show_nofix_default"] else "false",
        CHARTS_HTML=charts_html,
        CHARTS_JS=_build_charts_js(cfg, files_data),
        NOFIX_TOGGLE=nofix_toggle,
    )
    out_html = out_dir / cfg["output_html"]
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(html)

    # ---- 拷贝 echarts ----
    if ECHARTS_SRC.exists():
        shutil.copy2(ECHARTS_SRC, out_dir / "echarts.min.js")

    log(f"完成 -> HTML: {out_html}")
    return str(out_html)
