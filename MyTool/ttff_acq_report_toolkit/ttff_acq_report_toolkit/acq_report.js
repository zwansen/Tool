const PALETTE = [
  "#0f766e","#2563eb","#d97706","#dc2626","#7c3aed","#0891b2",
  "#65a30d","#db2777","#4f46e5","#ea580c","#059669","#9333ea"
];

const GGA_HZ = REPORT.gga_hz || 10;
const COLOR_TRACK = '#9ca3af';
const COLOR_EPH = '#eab308';
const COLOR_FIX = '#16a34a';

/** 选中的设备下标 */
let selected = new Set([0]);
/** 当前对齐的 Reset 号 */
let selectedResetN = null;
/** name|reset -> 已加载的重数据 */
const cycleCache = {};
const charts = {};
let hoverIdx = null;
/** 卫星表要显示的频点；默认仅 B2b */
let satFreqSelected = new Set(['B2b']);
/** 曲线图（RawObs / 可参与）频点菜单选中项；默认仅 B2b */
let curveFreqSelected = new Set(['B2b']);
/**
 * 卫星表数据源：
 * rawobs | pvt | eph | fix
 */
let satSource = 'fix';
/** Gantt：当前文件名、频点过滤 */
let ganttFileName = null;
let ganttFreqFilter = 'B2b';
/** 最近一次用于卫星表的 entries */
let lastSatEntries = [];

function el(id){ return document.getElementById(id); }
function devices(){ return REPORT.devices || []; }
function selectedDevices(){
  return [...selected].sort((a,b)=>a-b).map(i => devices()[i]).filter(Boolean);
}
function shortName(name){
  return String(name || '').replace(/\.log$/i, '');
}
function curveToXY(arr){
  return (arr || []).map((v, i) => [+(i / GGA_HZ).toFixed(3), v]);
}
function ensureChart(id){
  if(!charts[id]) charts[id] = echarts.init(el(id), 'bpDark');
  return charts[id];
}
function cycleKey(name, resetN){ return `${name}|${resetN}`; }

function detailMeta(d, resetN){
  return (d.details || []).find(x => x.reset_n === resetN) || null;
}

function loadScript(src){
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = src;
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('加载失败: ' + src));
    document.head.appendChild(s);
  });
}

async function ensureCycleLoaded(d, resetN){
  const key = cycleKey(d.name, resetN);
  if(cycleCache[key]) return cycleCache[key];
  if(window.__CYCLE_STORE && window.__CYCLE_STORE[key]){
    cycleCache[key] = window.__CYCLE_STORE[key];
    return cycleCache[key];
  }
  const meta = detailMeta(d, resetN);
  if(!meta?.data_js) throw new Error(`无数据: ${d.name} Reset#${resetN}`);
  await loadScript(meta.data_js);
  const payload = window.__CYCLE_STORE?.[key];
  if(!payload) throw new Error(`注册失败: ${key}`);
  cycleCache[key] = payload;
  return payload;
}

async function selectedEntries(){
  if(selectedResetN == null) return [];
  const out = [];
  for(const i of [...selected].sort((a,b)=>a-b)){
    const d = devices()[i];
    if(!d || !detailMeta(d, selectedResetN)) continue;
    const c = await ensureCycleLoaded(d, selectedResetN);
    out.push({ di: i, d, c });
  }
  return out;
}

function fixMarkLines(entries){
  const data = [];
  entries.forEach(({ d, c }, i) => {
    if(c.ttff_s == null) return;
    const color = PALETTE[i % PALETTE.length];
    data.push({
      xAxis: c.ttff_s,
      label: {
        formatter: `${shortName(d.name)} TTFF ${c.ttff_s}s`,
        color,
        fontSize: 11,
      },
      lineStyle: { type: 'dashed', color, width: 1.5 },
    });
  });
  if(!data.length) return undefined;
  return { symbol: 'none', animation: false, data };
}

function allResetNumbers(){
  const set = new Set();
  selectedDevices().forEach(d => {
    (d.details || []).forEach(c => set.add(c.reset_n));
  });
  return [...set].sort((a,b)=>a-b);
}

function initDeviceTabs(){
  const box = el('device-tabs');
  box.innerHTML = '';
  selected = new Set(devices().map((_, i) => i));
  devices().forEach((d, i)=>{
    const b = document.createElement('button');
    b.className = 'tab' + (selected.has(i) ? ' active' : '');
    b.textContent = shortName(d.name);
    b.title = '点击切换选中（可多选）';
    b.onclick = ()=>{
      if(selected.has(i)){
        if(selected.size <= 1) return;
        selected.delete(i);
      } else {
        selected.add(i);
      }
      b.classList.toggle('active', selected.has(i));
      fillResetSelect();
      renderAll();
    };
    box.appendChild(b);
  });
}

function fillResetSelect(){
  const sel = el('cycle-select');
  const resets = allResetNumbers();
  sel.innerHTML = '';
  resets.forEach(n => {
    const opt = document.createElement('option');
    opt.value = String(n);
    const have = selectedDevices().filter(d => (d.details||[]).some(c => c.reset_n === n)).length;
    opt.textContent = `Reset #${n}  （${have}/${selected.size} 个文件）`;
    sel.appendChild(opt);
  });
  if(!resets.length){
    selectedResetN = null;
    return;
  }
  if(selectedResetN == null || !resets.includes(selectedResetN)){
    selectedResetN = resets[0];
  }
  sel.value = String(selectedResetN);
  sel.onchange = ()=>{
    selectedResetN = parseInt(sel.value, 10);
    hoverIdx = null;
    renderAll();
  };
}

function initMeta(entries){
  const previewTag = REPORT.preview
    ? ` · 【预览】每文件最多 ${REPORT.max_cycles ?? '-'} 次`
    : ' · 全量';
  const pvtTag = REPORT.pvt_source
    ? ` · Track: ${REPORT.pvt_source}`
    : '';
  el('subtitle').textContent =
    `数据目录: ${REPORT.input_dir} · 生成: ${REPORT.generated_at} · CN0阈值: ${REPORT.cn0_min} dB-Hz · Reset起 ${GGA_HZ}Hz（无 UTC）${previewTag}${pvtTag}`;
  el('cycle-chip').textContent = `已选 ${selected.size} 个文件 · Reset #${selectedResetN ?? '-'}`;

  if(!entries.length){
    el('meta').innerHTML = '<div class="card"><div class="note">当前 Reset 下无可用数据</div></div>';
    return;
  }

  const head = ['文件', 'Reset 号', '定位时刻', 'TTFF', '定位质量', '本轮时长']
    .map(h => `<th>${h}</th>`).join('');
  const body = entries.map(({ d, c }, i) => {
    const color = PALETTE[i % PALETTE.length];
    const cells = [
      `<td class="file" style="color:${color}">● ${shortName(d.name)}</td>`,
      `<td>${c.reset_n ?? '-'}</td>`,
      `<td>${c.fix_gga_time ?? '-'}</td>`,
      `<td>${c.ttff_s != null ? c.ttff_s + ' s' : '-'}</td>`,
      `<td>${c.fix_quality ?? '-'}</td>`,
      `<td>${c.duration_s != null ? c.duration_s + ' s' : '-'}</td>`,
    ].join('');
    return `<tr>${cells}</tr>`;
  }).join('');

  el('meta').innerHTML = `<div class="card" style="padding:10px 12px">
    <div class="sat-table-wrap"><table class="sat-table meta-table">
      <thead><tr>${head}</tr></thead><tbody>${body}</tbody>
    </table></div>
  </div>`;
}

function freqsForSource(entries, source){
  const freqSet = new Set();
  const freqOrder = [];
  (entries || []).forEach(({ d, c }) => {
    let list;
    if(source === 'pvt') list = c.pvt_freqs || Object.keys(c.pvt_freq_curves || {});
    else if(source === 'eph' || source === 'fix') {
      // 卫星级列表不按频点列，用占位列
      list = ['ALL'];
    } else list = c.freqs || d.freqs || [];
    list.forEach(f => {
      if(!freqSet.has(f)){
        freqSet.add(f);
        freqOrder.push(f);
      }
    });
  });
  return freqOrder;
}

function allFreqsFromEntries(entries){
  return freqsForSource(entries, satSource);
}

function renderSatSourceToggle(){
  const box = el('sat-source-toggle');
  if(!box) return;
  const opts = [
    ['fix', '参与解算'],
    ['eph', '星历有效'],
    ['pvt', '可参与位置解'],
    ['rawobs', 'RawObs 在视'],
  ];
  box.innerHTML = `
    <span class="note" style="margin:0">列表数据：</span>
    ${opts.map(([k, lab]) =>
      `<button type="button" class="flt${satSource===k?' active':''}" data-src="${k}">${lab}</button>`
    ).join('')}
  `;
  box.querySelectorAll('button[data-src]').forEach(btn => {
    btn.onclick = () => {
      satSource = btn.dataset.src;
      renderSatSourceToggle();
      renderSatFreqFilters(lastSatEntries);
      renderSatPanel(lastSatEntries, hoverIdx);
    };
  });
}

function renderSatFreqFilters(entries){
  const box = el('sat-freq-filters');
  if(!box) return;
  if(satSource === 'eph' || satSource === 'fix'){
    box.innerHTML = '<span class="note" style="margin:0">卫星级列表（不分频点列）</span>';
    return;
  }
  const freqs = allFreqsFromEntries(entries);
  if(!freqs.length){
    box.innerHTML = '';
    return;
  }
  const valid = freqs.filter(f => satFreqSelected.has(f));
  if(!valid.length){
    satFreqSelected = new Set(freqs.includes('B2b') ? ['B2b'] : [freqs[0]]);
  }

  const chips = freqs.map(f => {
    const on = satFreqSelected.has(f) ? ' active' : '';
    return `<button type="button" class="flt${on}" data-freq="${f}">${f}</button>`;
  }).join('');
  box.innerHTML = `
    <span class="note" style="margin:0">表列筛选：</span>
    ${chips}
    <button type="button" class="flt flt-act" data-act="all">全选</button>
    <button type="button" class="flt flt-act" data-act="b2b">仅 B2b</button>
    <button type="button" class="flt flt-act" data-act="none">清空</button>
  `;
  box.onclick = (ev) => {
    const btn = ev.target.closest('button[data-freq], button[data-act]');
    if(!btn) return;
    if(btn.dataset.act === 'all'){
      satFreqSelected = new Set(freqs);
    } else if(btn.dataset.act === 'b2b'){
      satFreqSelected = new Set(freqs.includes('B2b') ? ['B2b'] : [freqs[0]]);
    } else if(btn.dataset.act === 'none'){
      satFreqSelected = new Set();
    } else if(btn.dataset.freq){
      const f = btn.dataset.freq;
      if(satFreqSelected.has(f)) satFreqSelected.delete(f);
      else satFreqSelected.add(f);
    }
    renderSatFreqFilters(lastSatEntries);
    renderSatPanel(lastSatEntries, hoverIdx);
  };
}

function labelsAt(c, source, idx){
  if(source === 'eph') return c.eph_prns?.[idx] || [];
  if(source === 'fix') return c.fix_prns?.[idx] || [];
  return [];
}

function renderSatPanel(entries, idx){
  const panel = el('sat-panel');
  const timeEl = el('sat-time');
  if(entries) lastSatEntries = entries;

  if(idx == null || idx < 0){
    timeEl.textContent = '在曲线上悬停或点击某一时刻，显示该历元卫星/频点列表。';
    panel.innerHTML = '<div class="loading">等待选择时刻…</div>';
    return;
  }
  const t = +(idx / GGA_HZ).toFixed(1);
  const srcMap = {
    fix: '参与解算', eph: '星历有效', pvt: '可参与位置解', rawobs: 'RawObs 在视',
  };
  timeEl.textContent = `时刻 t = ${t} s（相对 Reset，样本 #${idx}，10Hz）· ${srcMap[satSource] || satSource} · 每文件一行`;

  if(satSource === 'eph' || satSource === 'fix'){
    const head = ['文件', '合计', '卫星']
      .map(h => `<th>${h}</th>`).join('');
    const body = entries.map(({ d, c }, i) => {
      const color = PALETTE[i % PALETTE.length];
      const labs = labelsAt(c, satSource, idx);
      return `<tr>
        <td class="file" style="color:${color}">● ${shortName(d.name)}</td>
        <td class="total">${labs.length}</td>
        <td>${labs.length ? labs.join(', ') : '<span class="empty">—</span>'}</td>
      </tr>`;
    }).join('');
    panel.innerHTML = `<div class="sat-table-wrap"><table class="sat-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
    return;
  }

  const freqOrder = allFreqsFromEntries(entries).filter(f => satFreqSelected.has(f));
  if(!freqOrder.length){
    panel.innerHTML = '<div class="loading">未选择频点列（上方点选要显示的频点）</div>';
    return;
  }

  const prnMapFn = (c) => satSource === 'pvt' ? (c.pvt_freq_prns || {}) : (c.freq_prns || {});
  const head = ['文件', '合计', ...freqOrder]
    .map(h => `<th>${h}</th>`).join('');
  const body = entries.map(({ d, c }, i) => {
    const color = PALETTE[i % PALETTE.length];
    const prnMap = prnMapFn(c);
    let total = 0;
    const cells = freqOrder.map(f => {
      const prns = prnMap?.[f]?.[idx] || [];
      total += prns.length;
      if(!prns.length) return '<td class="empty">—</td>';
      return `<td title="n=${prns.length}"><span class="n">${prns.length}</span> ${prns.join(',')}</td>`;
    }).join('');
    return `<tr>
      <td class="file" style="color:${color}">● ${shortName(d.name)}</td>
      <td class="total">${total}</td>
      ${cells}
    </tr>`;
  }).join('');

  panel.innerHTML = `<div class="sat-table-wrap"><table class="sat-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function bindChartEvents(chart, entries){
  chart.off('updateAxisPointer');
  chart.off('click');
  chart.on('updateAxisPointer', (ev) => {
    const ax = ev?.axesInfo?.[0];
    if(!ax || ax.value == null) return;
    const idx = Math.round(ax.value * GGA_HZ);
    if(idx === hoverIdx) return;
    hoverIdx = idx;
    renderSatPanel(entries, idx);
  });
  chart.on('click', (params) => {
    let idx = null;
    if(params?.dataIndex != null) idx = params.dataIndex;
    else if(Array.isArray(params?.data)) idx = Math.round(params.data[0] * GGA_HZ);
    if(idx != null){
      hoverIdx = idx;
      renderSatPanel(entries, idx);
    }
  });
}

function collectFreqsFromEntries(entries, freqListFn){
  const set = new Set();
  const order = [];
  (entries || []).forEach(({ d, c }) => {
    (freqListFn(d, c) || []).forEach(f => {
      if(!set.has(f)){
        set.add(f);
        order.push(f);
      }
    });
  });
  return order;
}

function ensureFreqSelection(freqs, selected){
  const valid = freqs.filter(f => selected.has(f));
  if(valid.length) return;
  if(freqs.includes('B2b')) selected.add('B2b');
  else if(freqs.length) selected.add(freqs[0]);
}

/** 频点菜单：点选即重绘（可多选） */
function renderCurveFreqMenu(boxId, freqs, selected, onChange){
  const box = el(boxId);
  if(!box) return;
  if(!freqs.length){
    box.innerHTML = '<span class="note" style="margin:0">无可用频点</span>';
    return;
  }
  ensureFreqSelection(freqs, selected);
  const chips = freqs.map(f => {
    const on = selected.has(f) ? ' active' : '';
    return `<button type="button" class="flt${on}" data-freq="${f}">${f}</button>`;
  }).join('');
  box.innerHTML = `
    <span class="note" style="margin:0">绘制频点：</span>
    ${chips}
    <button type="button" class="flt flt-act" data-act="all">全选</button>
    <button type="button" class="flt flt-act" data-act="b2b">仅 B2b</button>
    <button type="button" class="flt flt-act" data-act="none">清空</button>
  `;
  box.onclick = (ev) => {
    const btn = ev.target.closest('button[data-freq], button[data-act]');
    if(!btn) return;
    if(btn.dataset.act === 'all'){
      freqs.forEach(f => selected.add(f));
    } else if(btn.dataset.act === 'b2b'){
      selected.clear();
      if(freqs.includes('B2b')) selected.add('B2b');
      else if(freqs.length) selected.add(freqs[0]);
    } else if(btn.dataset.act === 'none'){
      selected.clear();
    } else if(btn.dataset.freq){
      const f = btn.dataset.freq;
      if(selected.has(f)) selected.delete(f);
      else selected.add(f);
    }
    onChange();
  };
}

function renderFreqSeriesChart(chartId, entries, {
  freqListFn,
  curveFn,
  prnFn,
  yName,
  emptyNote,
  selectedFreqs,
}){
  const chart = ensureChart(chartId);
  if(!entries.length){ chart.clear(); return; }

  const multi = entries.length > 1;
  let series = [];
  let any = false;
  const want = selectedFreqs || curveFreqSelected;

  entries.forEach(({ d, c }, di) => {
    const freqs = (freqListFn(d, c) || []).filter(f => want.has(f));
    freqs.forEach((f, fi) => {
      const arr = curveFn(c, f) || [];
      if(!arr.length) return;
      any = true;
      const name = multi ? `${shortName(d.name)} / ${f}` : f;
      const color = PALETTE[(multi ? di * 3 + fi : fi) % PALETTE.length];
      series.push({
        name, type: 'line', showSymbol: false,
        data: curveToXY(arr),
        lineStyle: { width: 2, color },
        itemStyle: { color },
        freqName: f,
        deviceName: d.name,
      });
    });
  });

  if(!any){
    chart.clear();
    chart.setOption({
      title: {
        text: want.size ? (emptyNote || '无数据') : '请在上方菜单选择要绘制的频点',
        left: 'center', top: 'middle',
        textStyle: { color: '#6b7280', fontSize: 14, fontWeight: 400 },
      },
    }, true);
    return;
  }

  if(series.length){
    series[0] = { ...series[0], markLine: fixMarkLines(entries) };
  }

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        if(!params?.length) return '';
        const t = params[0].axisValue;
        const idx = Math.round(Number(t) * GGA_HZ);
        let html = `<div><b>t=${Number(t).toFixed(1)}s</b> (#${idx})</div>`;
        params.forEach(p => {
          if(p.seriesName == null) return;
          const s = series[p.seriesIndex];
          const freq = s?.freqName;
          const dev = s?.deviceName;
          const entry = entries.find(e => e.d.name === dev) || entries[0];
          const prns = (prnFn(entry?.c, freq)?.[idx]) || [];
          const prnTxt = prns.length ? prns.join(',') : '-';
          html += `<div>${p.marker}${p.seriesName}: <b>${p.data?.[1] ?? '-'}</b> 颗 &nbsp; PRN: ${prnTxt}</div>`;
        });
        return html;
      },
    },
    legend: { type: 'scroll', top: 0 },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: { type: 'value', name: '复位后时间 (s, Reset=0, 10Hz)', nameLocation: 'middle', nameGap: 28 },
    yAxis: { type: 'value', name: yName },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', height: 18, bottom: 8, start: 0, end: 100 },
    ],
    series,
  }, true);

  bindChartEvents(chart, entries);
}

function redrawCurveCharts(entries){
  const rawFreqs = collectFreqsFromEntries(entries, (d, c) => c.freqs || d.freqs || []);
  const pvtFreqs = collectFreqsFromEntries(
    entries,
    (d, c) => c.pvt_freqs || Object.keys(c.pvt_freq_curves || {}),
  );
  // 两个菜单共用同一套选中；用并集做菜单项
  const allFreqs = [];
  const seen = new Set();
  [...rawFreqs, ...pvtFreqs].forEach(f => {
    if(!seen.has(f)){ seen.add(f); allFreqs.push(f); }
  });

  const redraw = () => {
    renderCurveFreqMenu('rawobs-freq-menu', rawFreqs.length ? rawFreqs : allFreqs, curveFreqSelected, redraw);
    renderCurveFreqMenu('pvt-freq-menu', pvtFreqs.length ? pvtFreqs : allFreqs, curveFreqSelected, redraw);
    renderFreqSeriesChart('chart-freq-curve', entries, {
      freqListFn: (d, c) => c.freqs || d.freqs || [],
      curveFn: (c, f) => c.freq_curves?.[f],
      prnFn: (c, f) => c?.freq_prns?.[f],
      yName: '在视星数',
      emptyNote: '无 RawObs 曲线（或所选频点无数据）',
      selectedFreqs: curveFreqSelected,
    });
    renderFreqSeriesChart('chart-pvt-curve', entries, {
      freqListFn: (d, c) => c.pvt_freqs || Object.keys(c.pvt_freq_curves || {}),
      curveFn: (c, f) => c.pvt_freq_curves?.[f],
      prnFn: (c, f) => c?.pvt_freq_prns?.[f],
      yName: '可参与位置解星数',
      emptyNote: '无可参与位置解数据（或所选频点无数据）',
      selectedFreqs: curveFreqSelected,
    });
  };
  redraw();
}

async function renderEphFixCurves(entries){
  const chart = ensureChart('chart-eph-fix-curve');
  if(!entries.length){ chart.clear(); return; }

  let series = [];
  let any = false;
  entries.forEach(({ d, c }, di) => {
    const color = PALETTE[di % PALETTE.length];
    const name = shortName(d.name);
    const fixArr = c.fix_total_curve || [];
    const ephArr = c.eph_total_curve || [];
    if(fixArr.length){
      any = true;
      series.push({
        name: `${name} / 参与解算`,
        type: 'line', showSymbol: false,
        data: curveToXY(fixArr),
        lineStyle: { width: 2.2, color },
        itemStyle: { color },
        kind: 'fix', deviceName: d.name,
      });
    }
    if(ephArr.length){
      any = true;
      series.push({
        name: `${name} / 星历有效`,
        type: 'line', showSymbol: false,
        data: curveToXY(ephArr),
        lineStyle: { width: 1.6, color, type: 'dashed' },
        itemStyle: { color },
        kind: 'eph', deviceName: d.name,
      });
    }
  });

  if(!any){
    chart.clear();
    chart.setOption({
      title: {
        text: '无星历/参与解算数据（需 ProtocolDecoder.dll）',
        left: 'center', top: 'middle',
        textStyle: { color: '#6b7280', fontSize: 14, fontWeight: 400 },
      },
    }, true);
    return;
  }

  if(series.length){
    series[0] = { ...series[0], markLine: fixMarkLines(entries) };
  }

  chart.setOption({
    tooltip: {
      trigger: 'axis',
      formatter: (params) => {
        if(!params?.length) return '';
        const t = params[0].axisValue;
        const idx = Math.round(Number(t) * GGA_HZ);
        let html = `<div><b>t=${Number(t).toFixed(1)}s</b> (#${idx})</div>`;
        params.forEach(p => {
          const s = series[p.seriesIndex];
          const entry = entries.find(e => e.d.name === s?.deviceName);
          const labs = s?.kind === 'eph'
            ? (entry?.c?.eph_prns?.[idx] || [])
            : (entry?.c?.fix_prns?.[idx] || []);
          const labTxt = labs.length ? labs.join(', ') : '-';
          html += `<div>${p.marker}${p.seriesName}: <b>${p.data?.[1] ?? '-'}</b> · ${labTxt}</div>`;
        });
        return html;
      },
    },
    legend: { type: 'scroll', top: 0 },
    grid: { left: 50, right: 20, top: 40, bottom: 40 },
    xAxis: { type: 'value', name: '复位后时间 (s, Reset=0, 10Hz)', nameLocation: 'middle', nameGap: 28 },
    yAxis: { type: 'value', name: '卫星颗数' },
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', height: 18, bottom: 8, start: 0, end: 100 },
    ],
    series,
  }, true);
  bindChartEvents(chart, entries);
}

function ganttFreqOptions(sats){
  const set = new Set();
  (sats || []).forEach(s => (s.freqs || []).forEach(f => set.add(f)));
  const ordered = [];
  ['B2b','B1I','B1C','B2a','B3I','L1','L2','L5','E1','E5a','E5b','G1','G2'].forEach(f => {
    if(set.has(f)) ordered.push(f);
  });
  [...set].sort().forEach(f => { if(!ordered.includes(f)) ordered.push(f); });
  return ordered;
}

function renderGanttBar(entries){
  const box = el('gantt-bar');
  if(!box) return;
  const files = entries.map(e => e.d.name);
  if(!files.length){
    box.innerHTML = '';
    return;
  }
  if(!ganttFileName || !files.includes(ganttFileName)){
    ganttFileName = files[0];
  }
  const entry = entries.find(e => e.d.name === ganttFileName);
  const freqs = ganttFreqOptions(entry?.c?.sats || []);
  if(ganttFreqFilter !== 'ALL' && freqs.length && !freqs.includes(ganttFreqFilter)){
    ganttFreqFilter = freqs.includes('B2b') ? 'B2b' : (freqs[0] || 'ALL');
  }

  const fileBtns = files.map(n =>
    `<button type="button" class="flt${ganttFileName===n?' active':''}" data-file="${n}">${shortName(n)}</button>`
  ).join('');
  const freqBtns = ['ALL', ...freqs].map(f =>
    `<button type="button" class="flt${ganttFreqFilter===f?' active':''}" data-gfreq="${f}">${f === 'ALL' ? '全部频点' : f}</button>`
  ).join('');

  box.innerHTML = `
    <span class="note" style="margin:0">文件：</span>${fileBtns}
    <span class="note" style="margin:0 0 0 10px">频点过滤：</span>${freqBtns}
  `;
  box.querySelectorAll('button[data-file]').forEach(btn => {
    btn.onclick = () => { ganttFileName = btn.dataset.file; renderSatGantt(entries); renderGanttBar(entries); };
  });
  box.querySelectorAll('button[data-gfreq]').forEach(btn => {
    btn.onclick = () => { ganttFreqFilter = btn.dataset.gfreq; renderSatGantt(entries); renderGanttBar(entries); };
  });
}

function spansToCustomData(spans, rowIndex){
  const out = [];
  (spans || []).forEach(sp => {
    if(!Array.isArray(sp) || sp.length < 2) return;
    const t0 = Number(sp[0]);
    const t1 = Number(sp[1]);
    if(!(t1 >= t0)) return;
    const end = t1 > t0 ? t1 : t0 + (1 / GGA_HZ);
    out.push({ value: [rowIndex, t0, end, end - t0] });
  });
  return out;
}

function makeGanttRenderItem(yOffRatio){
  return (params, api) => {
    const catIndex = api.value(0);
    const start = api.coord([api.value(1), catIndex]);
    const end = api.coord([api.value(2), catIndex]);
    const height = api.size([0, 1])[1] * 0.5;
    const yOff = height * yOffRatio;
    return {
      type: 'rect',
      shape: {
        x: start[0],
        y: start[1] - height / 2 + yOff,
        width: Math.max(end[0] - start[0], 1),
        height,
      },
      style: api.style(),
    };
  };
}

function renderSatGantt(entries){
  const chart = ensureChart('chart-sat-gantt');
  const entry = entries.find(e => e.d.name === ganttFileName) || entries[0];
  if(!entry){
    chart.clear();
    return;
  }
  let sats = [...(entry.c.sats || [])];
  if(ganttFreqFilter && ganttFreqFilter !== 'ALL'){
    sats = sats.filter(s => (s.freqs || []).includes(ganttFreqFilter));
  }
  // 有参与解算/星历的优先，再按 first_fix / first_eph
  sats.sort((a, b) => {
    const af = a.first_fix_s != null ? a.first_fix_s : 1e9;
    const bf = b.first_fix_s != null ? b.first_fix_s : 1e9;
    if(af !== bf) return af - bf;
    const ae = a.first_eph_s != null ? a.first_eph_s : 1e9;
    const be = b.first_eph_s != null ? b.first_eph_s : 1e9;
    if(ae !== be) return ae - be;
    return String(a.id).localeCompare(String(b.id));
  });
  // 限制行数，避免卡顿
  const MAX_ROWS = 80;
  if(sats.length > MAX_ROWS) sats = sats.slice(0, MAX_ROWS);

  if(!sats.length){
    chart.clear();
    chart.setOption({
      title: {
        text: '无按星数据（或当前频点过滤为空）',
        left: 'center', top: 'middle',
        textStyle: { color: '#6b7280', fontSize: 14, fontWeight: 400 },
      },
    }, true);
    return;
  }

  const cats = sats.map(s => {
    const fe = s.first_eph_s != null ? `eph@${Number(s.first_eph_s).toFixed(1)}s` : 'eph=-';
    const ff = s.first_fix_s != null ? `fix@${Number(s.first_fix_s).toFixed(1)}s` : 'fix=-';
    return `${s.id}  ${fe} ${ff}`;
  });

  const trackData = [];
  const ephData = [];
  const fixData = [];
  sats.forEach((s, i) => {
    trackData.push(...spansToCustomData(s.track_spans, i));
    ephData.push(...spansToCustomData(s.eph_spans, i));
    fixData.push(...spansToCustomData(s.fix_spans, i));
  });

  const dur = entry.c.duration_s || Math.max(0, ((entry.c.n_epochs || 1) - 1) / GGA_HZ);
  const markLine = entry.c.ttff_s != null ? {
    symbol: 'none',
    data: [{
      xAxis: entry.c.ttff_s,
      label: { formatter: `TTFF ${entry.c.ttff_s}s`, fontSize: 11 },
      lineStyle: { type: 'dashed', color: '#dc2626', width: 1.5 },
    }],
  } : undefined;

  const rowH = Math.max(18, Math.min(28, Math.floor(560 / Math.max(sats.length, 1))));
  el('chart-sat-gantt').style.height = `${Math.max(360, sats.length * rowH + 100)}px`;
  chart.resize();

  const mkSeries = (name, color, data, yOff) => ({
    type: 'custom',
    name,
    itemStyle: { color, opacity: name === '跟踪' ? 0.55 : 0.9 },
    renderItem: makeGanttRenderItem(yOff),
    encode: { x: [1, 2], y: 0 },
    data,
    markLine: name === '参与解算' ? markLine : undefined,
  });

  chart.setOption({
    tooltip: {
      formatter: (p) => {
        const v = p.value || [];
        const label = cats[v[0]] || '';
        return `${label}<br/>${p.seriesName}: ${Number(v[1]).toFixed(1)} → ${Number(v[2]).toFixed(1)} s`;
      },
    },
    legend: { top: 0 },
    grid: { left: 170, right: 24, top: 36, bottom: 40 },
    xAxis: {
      type: 'value',
      name: '复位后时间 (s)',
      nameLocation: 'middle',
      nameGap: 28,
      min: 0,
      max: dur > 0 ? dur : undefined,
    },
    yAxis: {
      type: 'category',
      data: cats,
      inverse: true,
      axisLabel: { fontSize: 11 },
    },
    dataZoom: [
      { type: 'inside', xAxisIndex: 0 },
      { type: 'slider', xAxisIndex: 0, height: 16, bottom: 8 },
      { type: 'inside', yAxisIndex: 0 },
      { type: 'slider', yAxisIndex: 0, width: 14, right: 4 },
    ],
    series: [
      mkSeries('跟踪', COLOR_TRACK, trackData, 0),
      mkSeries('星历有效', COLOR_EPH, ephData, -0.15),
      mkSeries('参与解算', COLOR_FIX, fixData, 0.15),
    ],
  }, true);

  chart.off('click');
  chart.on('click', (params) => {
    const v = params?.value;
    if(!v) return;
    const t = Number(v[1]);
    if(!Number.isFinite(t)) return;
    hoverIdx = Math.round(t * GGA_HZ);
    renderSatPanel(entries, hoverIdx);
  });
}

async function renderAll(){
  const status = el('load-status');
  status.textContent = '加载曲线数据…';
  try {
    const entries = await selectedEntries();
    status.textContent = entries.length ? `已加载 ${entries.length} 个文件` : '';
    initMeta(entries);
    lastSatEntries = entries;
    renderSatSourceToggle();
    redrawCurveCharts(entries);
    await renderEphFixCurves(entries);
    renderGanttBar(entries);
    renderSatGantt(entries);
    renderSatFreqFilters(entries);
    if(hoverIdx != null) renderSatPanel(entries, hoverIdx);
    else renderSatPanel(entries, null);
    Object.values(charts).forEach(c => c.resize());
  } catch (e) {
    status.textContent = String(e.message || e);
    console.error(e);
  }
}

initDeviceTabs();
fillResetSelect();
renderAll();
window.addEventListener('resize', ()=>Object.values(charts).forEach(c=>c.resize()));
