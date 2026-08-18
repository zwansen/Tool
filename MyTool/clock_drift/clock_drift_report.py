# -*- coding: utf-8 -*-
"""钟漂分析：HTML 报告生成。

特性：
- 支持多文件叠加（每个文件独立颜色 + 图例 + 备注）
- 支持可选温度曲线：无温度数据时保留温度坐标轴（显示"恒温/未提供"提示），不绘制曲线
- 支持自定义报告标题 / 文件显示名
- 不定位点一键开关（默认关闭，避免卡顿）
- 全量数据 tooltip 查询（每个历元精确对应）
- X 轴滚轮缩放 / Ctrl+滚轮 Y 轴缩放 / 拖拽平移 / 框选放大
"""
import html as _html
import json
import os


def _esc(s):
    return _html.escape(str(s), quote=True)


def _build_option_js(active):
    """生成前端 ACTIVE 数组 JS 片段。active: [{key,label,note,color}]"""
    arr = []
    for a in active:
        arr.append(
            "{key:'%s', label:'%s', note:'%s', color:'%s'}" % (
                _esc(a['key']), _esc(a['label']), _esc(a['note']), a['color'],
            )
        )
    return "[\n      " + ",\n      ".join(arr) + "\n    ]"


REPORT_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{TITLE}</title>
<script src="echarts.min.js"></script>
<style>
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: "Microsoft YaHei", "PingFang SC", Arial, sans-serif;
    background: #f5f6fa; color: #2c3e50; padding: 20px;
  }}
  .header {{
    background: linear-gradient(135deg, #1a2a6c, #b21f1f, #fdbb2d);
    border-radius: 12px; color: #fff; padding: 24px 30px; margin-bottom: 20px;
    box-shadow: 0 4px 15px rgba(0,0,0,0.15);
  }}
  .header h1 {{ font-size: 22px; font-weight: 600; letter-spacing: 1px; }}
  .header p {{ font-size: 13px; opacity: 0.9; margin-top: 6px; }}
  .controls {{ display: flex; align-items: center; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }}
  .file-check {{
    display: inline-flex; align-items: center; gap: 6px; padding: 7px 14px;
    border: 2px solid #ccc; border-radius: 20px; background: #fff;
    font-size: 13px; font-weight: 600; cursor: pointer; user-select: none;
    transition: all 0.2s;
  }}
  .file-check:hover {{ transform: translateY(-2px); box-shadow: 0 3px 8px rgba(0,0,0,0.15); }}
  .file-check input {{ margin: 0; cursor: pointer; }}
  .info {{
    font-size: 13px; color: #555; background: #eef1f7; padding: 8px 14px; border-radius: 8px;
  }}
  .chart-container {{ background: #fff; border-radius: 12px; box-shadow: 0 2px 12px rgba(0,0,0,0.08); padding: 10px; }}
  #chart {{ width: 100%; height: {CHART_H}px; }}
  .legend-note {{
    margin-top: 12px; padding: 12px 16px; background: #fff; border-radius: 10px;
    font-size: 13px; line-height: 2; box-shadow: 0 2px 8px rgba(0,0,0,0.06);
  }}
  .legend-note .red {{ color: #e74c3c; font-weight: 700; }}
  .tip {{ font-size: 12px; color: #999; margin-top: 6px; }}
  .swatch {{ display:inline-block; width:14px; height:14px; border-radius:3px; margin-right:5px; vertical-align:middle; }}
</style>
</head>
<body>

<div class="header">
  <h1>📡 {TITLE}</h1>
  <p>横轴: {AXIS_LABEL} ｜ {TEMP_DESC}｜ 可多选文件叠加对比, 不同文件不同颜色</p>
</div>

<div class="controls" id="fileControls">
  <span class="info" id="fileInfo">加载中...</span>
</div>

<div class="controls" style="margin-top:-6px;">
  <label class="file-check" style="border-color:#e74c3c;cursor:pointer;">
    <input type="checkbox" id="nofixToggle" onchange="toggleNofix(this.checked)">
    <span class="swatch" style="background:#e74c3c;"></span>
    <span id="nofixLabel">显示不定位点（<b>当前关闭</b>，可提升流畅度）</span>
  </label>
  <span class="info" style="background:#fdecea;color:#c0392b;font-size:12px;">💡 不定位点较多时默认关闭以避免卡顿；勾选后显示全部不定位历元</span>
</div>

<div class="chart-container">
  <div id="chart"></div>
</div>

<div class="legend-note">
  <b>图例说明:</b><br>
  <span id="fileColorNote"></span>
  {TEMP_LEGEND}
  <span class="red">● 不定位历元</span> —— GGA/RMC 无时间的历元, 以该文件颜色的空心圆点标注<br>
  <span class="tip">💡 操作提示: <b>滚轮</b>: 缩放 X 轴; <b>Ctrl+滚轮</b>: 缩放 Y 轴; <b>鼠标左键拖动</b>: 平移 X 轴; <b>Ctrl+拖动</b>: 平移 Y 轴; 底部滑块缩放 X, 每个图右侧垂直滑块缩放/平移 Y; 工具栏 <b>框选放大</b> / <b>还原</b>; 悬停查看数值; 图例点击隐藏/显示曲线</span>
</div>

<script>
var myChart = echarts.init(document.getElementById('chart'));

// 激活文件列表（由后端生成）：key=数据文件标识, label=显示名, note=备注
var ACTIVE = {ACTIVE_JS};
var TEMP_AVAILABLE = {TEMP_AVAILABLE};
// 横轴模式: 'time' = UTC 时间轴; 'epoch' = 历元序号轴（不取 GGA/RMC 时间）
var AXIS_MODE = '{AXIS_MODE}';
var FILES = [];
ACTIVE.forEach(function(a, i) {{
  FILES.push({{ id: i + 1, key: a.key, label: a.label, note: a.note, color: a.color, dataJs: 'data_' + a.key + '.js' }});
}});

var dataStore = {{}};   // id -> 数据对象
var tempData = null;
var selected = {{}};
var loadPending = 0;
var showNofix = false;

// 加载共用温度数据
{TEMP_LOAD_JS}

// 动态生成文件多选按钮 + 颜色说明
(function(){{
  var ctrl = document.getElementById('fileControls');
  var note = document.getElementById('fileColorNote');
  var html = '';
  FILES.forEach(function(f){{
    selected[f.id] = true;
    var lbl = document.createElement('label');
    lbl.className = 'file-check';
    lbl.style.borderColor = f.color;
    var cb = document.createElement('input');
    cb.type = 'checkbox';
    cb.checked = true;
    cb.onchange = function(){{ toggleFile(f.id); }};
    lbl.appendChild(cb);
    var sw = document.createElement('span');
    sw.className = 'swatch';
    sw.style.background = f.color;
    lbl.appendChild(sw);
    var txt = f.label;
    if (f.note) txt += '（' + f.note + '）';
    lbl.appendChild(document.createTextNode(txt));
    ctrl.insertBefore(lbl, ctrl.firstChild);
    html += '<span class="swatch" style="background:' + f.color + '"></span><b>' + txt + '</b> ';
  }});
  note.innerHTML = html;
}})();

function fmtTime(ms) {{
  var d = new Date(ms);
  function p(n){{ return n < 10 ? '0'+n : ''+n; }}
  return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds());
}}
function fmtFull(ms) {{
  var d = new Date(ms);
  function p(n){{ return n < 10 ? '0'+n : ''+n; }}
  return p(d.getUTCMonth()+1) + '-' + p(d.getUTCDate()) + ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds());
}}
function findNearest(ts, vals, x) {{
  if (!ts || ts.length === 0) return null;
  if (x <= ts[0]) return vals[0];
  if (x >= ts[ts.length-1]) return vals[vals.length-1];
  var lo = 0, hi = ts.length - 1;
  while (lo < hi - 1) {{ var mid = (lo+hi)>>1; if (ts[mid] <= x) lo = mid; else hi = mid; }}
  return (x - ts[lo] <= ts[hi] - x) ? vals[lo] : vals[hi];
}}

function buildOption(activeFiles) {{
  var grids = [], xAxes = [], yAxes = [], series = [];
  var labels = ['flashclkdrifft (闪存钟漂)', 'curclkdrifft (当前钟漂)', 'recvclkdrifft (接收机钟漂)'];

  if (TEMP_AVAILABLE) {{
    // 4 图: 温度(11%) + 三个钟漂(各21%)
    grids.push({{ left: 70, right: 55, top: '3%', height: '11%' }});
    grids.push({{ left: 70, right: 55, top: '16%', height: '21%' }});
    grids.push({{ left: 70, right: 55, top: '39%', height: '21%' }});
    grids.push({{ left: 70, right: 55, top: '62%', height: '21%' }});
  }} else {{
    // 3 图: 三个钟漂均分
    grids.push({{ left: 70, right: 55, top: '5%', height: '27%' }});
    grids.push({{ left: 70, right: 55, top: '35%', height: '27%' }});
    grids.push({{ left: 70, right: 55, top: '65%', height: '27%' }});
  }}
  var nGrids = TEMP_AVAILABLE ? 4 : 3;
  var isEpoch = AXIS_MODE === 'epoch';

  for (var i = 0; i < nGrids; i++) {{
    xAxes.push({{ type: isEpoch ? 'value' : 'time', gridIndex: i,
      axisLabel: i === nGrids-1 ? {{ formatter: isEpoch ? function(v){{ return v; }} : fmtTime }} : {{ show: false }},
      axisLine: {{ lineStyle: {{ color: '#aaa' }} }}, axisTick: {{ show: false }}, splitLine: {{ show: false }} }});
  }}

  if (TEMP_AVAILABLE) {{
    yAxes.push({{ type: 'value', gridIndex: 0, name: 'T1 (℃)', nameTextStyle: {{ fontSize: 11 }},
      scale: true, splitLine: {{ lineStyle: {{ type: 'dashed', color: '#e8e8e8' }} }}, axisLabel: {{ fontSize: 11 }} }});
    var gBase = 1;
  }} else {{
    var gBase = 0;
  }}
  for (var i = 0; i < 3; i++) {{
    yAxes.push({{ type: 'value', gridIndex: i + gBase, name: labels[i], nameTextStyle: {{ fontSize: 11 }},
      scale: true, splitLine: {{ lineStyle: {{ type: 'dashed', color: '#e8e8e8' }} }}, axisLabel: {{ fontSize: 11 }} }});
  }}

  // 温度: 一条黑线
  if (TEMP_AVAILABLE && tempData && tempData.ts && tempData.val) {{
    var tempArr = tempData.ts.map(function(t, i){{ return [t, tempData.val[i]]; }});
    series.push({{ name: '温度 T1', type: 'line', showSymbol: false, data: tempArr,
      xAxisIndex: 0, yAxisIndex: 0, lineStyle: {{ width: 1.5, color: '#333' }},
      itemStyle: {{ color: '#333' }}, sampling: 'lttb', emphasis: {{ disabled: true }},
      large: true, progressive: 5000 }});
  }}

  // 每个激活文件的三个钟漂
  var keys = ['flash', 'cur', 'recv'];
  activeFiles.forEach(function(af){{
    var f = af.file, d = af.data;
    keys.forEach(function(key, gi){{
      var gi2 = gi + gBase;
      var fixData = d[key].fix_ts.map(function(t, i){{ return [t, d[key].fix_val[i]]; }});
      var baseName = f.label + (f.note ? '(' + f.note + ')' : '') + ' · ' + labels[gi];
      series.push({{ name: baseName, type: 'line', showSymbol: false, data: fixData,
        xAxisIndex: gi2, yAxisIndex: gi2, lineStyle: {{ width: 1.1, color: f.color }},
        itemStyle: {{ color: f.color }}, sampling: 'lttb', emphasis: {{ disabled: true }},
        large: true, progressive: 5000 }});
      if (showNofix && d[key].nofix_ts.length > 0) {{
        var nofixData = d[key].nofix_ts.map(function(t, i){{ return [t, d[key].nofix_val[i]]; }});
        series.push({{ name: baseName + ' (不定位)', type: 'scatter', data: nofixData,
          xAxisIndex: gi2, yAxisIndex: gi2, symbol: 'circle', symbolSize: 4,
          itemStyle: {{ color: f.color, borderColor: '#fff', borderWidth: 1, opacity: 0.95 }},
          z: 10, emphasis: {{ scale: 1.5 }}, large: true, largeThreshold: 2000, progressive: 5000 }});
      }}
    }});
  }});

  // legend: 每个 grid 独立
  var legend = [];
  if (TEMP_AVAILABLE) {{
    legend.push({{ gridIndex: 0, top: '3%', right: 70, textStyle: {{ fontSize: 10 }}, data: ['温度 T1'], selectedMode: true }});
  }}
  for (var gi = 0; gi < 3; gi++) {{
    var names = [];
    activeFiles.forEach(function(af){{
      var nm = af.file.label + (af.file.note ? '(' + af.file.note + ')' : '') + ' · ' + labels[gi];
      names.push(nm);
      if (showNofix) names.push(nm + ' (不定位)');
    }});
    var topPct = TEMP_AVAILABLE ? ['16%', '39%', '62%'][gi] : ['5%', '35%', '65%'][gi];
    legend.push({{ gridIndex: gi + gBase, top: topPct, right: 70, type: 'scroll',
      textStyle: {{ fontSize: 10 }}, data: names, selectedMode: true }});
  }}

  var option = {{
    backgroundColor: '#ffffff',
    animation: false,
    tooltip: {{
      trigger: 'axis', confine: true,
      axisPointer: {{ type: 'cross', link: [{{ xAxisIndex: 'all' }}], label: {{ backgroundColor: '#333' }} }},
      formatter: function(params) {{
        if (!params || params.length === 0) return '';
        var x = params[0].axisValue != null ? params[0].axisValue : params[0].value[0];
        var lines = ['<b>' + (isEpoch ? ('历元 ' + x) : (fmtFull(x) + ' UTC')) + '</b>'];
        var L = window.lookup;
        if (L) {{
          if (L.temp) {{
            var tv = findNearest(L.temp.ts, L.temp.val, x);
            if (tv !== null) lines.push('<span style="display:inline-block;width:10px;height:10px;background:#333;border-radius:50%;margin-right:6px;"></span>' +
              '温度 T1: <b>' + Number(tv).toFixed(2) + ' ℃</b>');
          }}
          var keyNames = ['flashclkdrifft', 'curclkdrifft', 'recvclkdrifft'];
          L.files.forEach(function(f){{
            for (var i = 0; i < 3; i++) {{
              var key = ['flash','cur','recv'][i];
              var v = findNearest(f[key].ts, f[key].val, x);
              if (v === null) continue;
              lines.push('<span style="display:inline-block;width:10px;height:10px;background:' + f.color + ';border-radius:50%;margin-right:6px;"></span>' +
                f.label + (f.note ? '(' + f.note + ')' : '') + ' · ' + keyNames[i] + ': <b>' + Number(v).toFixed(2) + '</b>');
            }}
          }});
        }}
        return lines.join('<br/>');
      }}
    }},
    legend: legend,
    grid: grids,
    xAxis: xAxes,
    yAxis: yAxes,
    series: series,
    dataZoom: (function(){{
      var arr = [
        {{ type: 'inside', xAxisIndex: _allIdx(nGrids), start: 0, end: 100,
          zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false, zoomLock: false }},
        {{ type: 'slider', xAxisIndex: _allIdx(nGrids), bottom: 0, height: 22, start: 0, end: 100,
          formatter: function(v){{ return isEpoch ? v : fmtTime(v); }} }}
      ];
      // 右侧 Y 轴缩放滑块：位置/高度与网格布局严格对应（有温度 4 格 / 无温度 3 格）
      var yTops = TEMP_AVAILABLE ? ['3%','16%','39%','62%'] : ['5%','35%','65%'];
      var yHs = TEMP_AVAILABLE ? ['11%','21%','21%','21%'] : ['27%','27%','27%'];
      for (var yi = 0; yi < nGrids; yi++) {{
        arr.push({{ type: 'inside', yAxisIndex: [yi], start: 0, end: 100,
          zoomOnMouseWheel: true, moveOnMouseMove: true, modifierKey: 'ctrl', zoomLock: false }});
        arr.push({{ type: 'slider', yAxisIndex: [yi], orient: 'vertical', right: 12, width: 14,
          top: yTops[yi], height: yHs[yi], start: 0, end: 100 }});
      }}
      return arr;
    }})(),
    toolbox: {{
      show: true, orient: 'vertical', right: 75, top: '35%',
      feature: {{
        dataZoom: {{ yAxisIndex: 'none', title: {{ zoom: '框选放大', back: '还原' }} }},
        restore: {{ title: '还原视图' }},
        saveAsImage: {{ title: '保存为图片', name: '钟漂分析' }}
      }}
    }},
    axisPointer: {{ link: [{{ xAxisIndex: 'all' }}] }},
  }};
  return option;
}}

// 辅助函数
function _allIdx(n) {{ var a = []; for (var i = 0; i < n; i++) a.push(i); return a; }}
function _yZoom(i) {{
  return {{ type: 'inside', yAxisIndex: [i], start: 0, end: 100,
    zoomOnMouseWheel: true, moveOnMouseMove: true, modifierKey: 'ctrl', zoomLock: false }};
}}

function refresh() {{
  var active = [];
  FILES.forEach(function(f){{
    if (selected[f.id] && dataStore[f.id]) active.push({{ file: f, data: dataStore[f.id] }});
  }});
  if (active.length === 0) {{
    document.getElementById('fileInfo').textContent = '请至少勾选一个文件';
    return;
  }}
  myChart.setOption(buildOption(active), true);

  var L = {{ temp: null, files: [] }};
  var keys = ['flash', 'cur', 'recv'];
  if (tempData) L.temp = {{ ts: tempData.ts, val: tempData.val }};
  active.forEach(function(af){{
    var d = af.data;
    var entry = {{ label: af.file.label, note: af.file.note, color: af.file.color, flash: null, cur: null, recv: null }};
    keys.forEach(function(key){{ entry[key] = {{ ts: d.all_ts, val: d[key].all_val }}; }});
    L.files.push(entry);
  }});
  window.lookup = L;

  var first = active[0].data;
  var isEpoch = AXIS_MODE === 'epoch';
  var rngTxt = isEpoch
    ? ('历元范围: ' + first.range[0] + ' ~ ' + first.range[1])
    : ('时间范围: ' + fmtFull(first.range[0]) + ' ~ ' + fmtFull(first.range[1]) + ' UTC');
  document.getElementById('fileInfo').textContent =
    '已显示 ' + active.length + ' 个文件 ｜ ' + rngTxt +
    ' ｜ 定位点: ' + first.flash.fix_ts.length + ' ｜ 不定位历元: ' + first.flash.n_nofix + ' 个';
}}

function loadFile(f) {{
  if (dataStore[f.id]) {{ refresh(); return; }}
  var s = document.createElement('script');
  s.src = f.dataJs + '?t=' + Date.now();
  document.getElementById('fileInfo').textContent = '加载 ' + f.label + ' ...';
  loadPending++;
  s.onload = function() {{
    dataStore[f.id] = window.DATA;
    loadPending--;
    if (loadPending <= 0) refresh();
  }};
  s.onerror = function() {{
    loadPending--;
    document.getElementById('fileInfo').textContent = '数据文件加载失败: ' + f.dataJs;
  }};
  document.body.appendChild(s);
}}

function toggleFile(id) {{ selected[id] = !selected[id]; refresh(); }}
function toggleNofix(checked) {{
  showNofix = checked;
  document.getElementById('nofixLabel').innerHTML = '显示不定位点（<b>' + (checked ? '已开启' : '当前关闭') + '</b>）';
  refresh();
}}

window.addEventListener('resize', function(){{ myChart.resize(); }});
FILES.forEach(function(f){{ loadFile(f); }});
</script>
</body>
</html>
"""


def render_report(
    title: str,
    time_range: str,
    active: list,
    temp_available: bool,
    temp_legend: str,
    temp_desc: str,
    temp_load_js: str,
    chart_h: int = 950,
    axis_mode: str = "time",
) -> str:
    """渲染报告 HTML 字符串。

    axis_mode: 'time' = UTC 时间轴；'epoch' = 历元序号轴（不取 GGA/RMC 时间）。
    """
    if axis_mode == "epoch":
        axis_label = f"{time_range}"  # time_range 形如 "历元序号 0 ~ 99"
    else:
        axis_label = f"UTC 时间 {time_range}"
    return REPORT_TEMPLATE.format(
        TITLE=_esc(title),
        TIME_RANGE=_esc(time_range),
        AXIS_LABEL=_esc(axis_label),
        AXIS_MODE=_esc(axis_mode),
        ACTIVE_JS=_build_option_js(active),
        TEMP_AVAILABLE="true" if temp_available else "false",
        TEMP_LEGEND=temp_legend,
        TEMP_DESC=temp_desc,
        TEMP_LOAD_JS=temp_load_js,
        CHART_H=chart_h,
    )
