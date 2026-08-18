# -*- coding: utf-8 -*-
"""
BPDEBUG 通用解析框架（基础层）

设计目标：
  BPDEBUG 数据分析中，**按 $CHEND 切分历元 + 从 RMC/GGA 提取 UTC 时间 + 跨天检测 +
  不定位历元 10Hz 打点** 是所有分析共用的基础步骤。本模块把这部分固化为框架，
  之后分析任意语句任意字段时，只需声明「分析哪条语句的哪个字段」，无需重复描述历元逻辑。

用法：
  from bpdebug_framework import BPDebugFrame, FieldSpec

  # 1. 声明要提取的字段（语句前缀 + 字段索引 + 解析方式）
  fields = [
      FieldSpec('$CNRCV', 10, 'flashclkdrifft', 'bare'),
      FieldSpec('$CNRCV', 11, 'curclkdrifft', 'bare'),
      FieldSpec('$CNRCV', 12, 'recvclkdrifft', 'bare'),
  ]

  # 2. 创建框架并解析文件
  frame = BPDebugFrame(fields)
  rows = frame.parse_file('xxx.txt')
  # rows: [{'utc_time': datetime, 'is_nofix': 0/1, 'epoch_index': n, 'flashclkdrifft': ..., ...}]

  # 3. rows 可直接用于生成数据/CSV/图表（配合 clock_drift_data / 自研逻辑）
"""
import csv
import datetime
import re
from typing import Callable, Optional

_HHMMSS_RE = re.compile(r'^\d{6}(\.\d+)?$')


# ---------------------------------------------------------------------------
# 解析工具（内置解析器）
# ---------------------------------------------------------------------------

def parse_hhmmss(s):
    """HHMMSS.mmm -> seconds of day (float)。严格校验防止乱码时间字段。"""
    if not s or not _HHMMSS_RE.match(s):
        return None
    try:
        h = int(s[0:2]); m = int(s[2:4]); sec = float(s[4:])
        if h > 23 or m > 59 or sec >= 60:
            return None
        return h * 3600 + m * 60 + sec
    except Exception:
        return None


def parse_date_ddmmyy(s):
    """DDMMYY -> date"""
    try:
        d = int(s[0:2]); m = int(s[2:4]); y = 2000 + int(s[4:6])
        return datetime.date(y, m, d)
    except Exception:
        return None


def _parse_bare(x):
    """'数值(置信度)' -> 括号前数值"""
    try:
        return float(x.split('(')[0])
    except Exception:
        return None


def _parse_float(x):
    try:
        return float(x)
    except Exception:
        return None


def _parse_int(x):
    try:
        return int(float(x))
    except Exception:
        return None


def _parse_raw(x):
    return x


# 内置解析器注册表
_BUILTIN_PARSERS = {
    'bare': _parse_bare,
    'float': _parse_float,
    'int': _parse_int,
    'raw': _parse_raw,
}


# ---------------------------------------------------------------------------
# 字段声明
# ---------------------------------------------------------------------------

class FieldSpec:
    """声明要提取的一个字段。

    stmt_prefix: 语句前缀，如 '$CNRCV'、'$GNGGA'、'$GPGSV'（不区分大小写，自动匹配前缀）
    field_index: 字段索引（0-based，split(',') 后；0 是语句名本身）
    name:        输出字段名
    parser:      'bare' / 'float' / 'int' / 'raw' / 或自定义函数 fn(str)->value
    take:        'first'=取历元内第一条（默认）; 'last'=取最后一条; 'count'=计数;
                 'all'=取全部(返回列表)
    min_len:     该语句最少字段数（防御字段缺失，默认取到 field_index+1）
    """

    def __init__(self, stmt_prefix: str, field_index: int, name: str,
                 parser: str = 'raw', take: str = 'first', min_len: Optional[int] = None):
        self.stmt_prefix = stmt_prefix.upper()
        self.field_index = field_index
        self.name = name
        if isinstance(parser, str):
            if parser not in _BUILTIN_PARSERS:
                raise ValueError(f"未知内置解析器: {parser}（可选 bare/float/int/raw 或传函数）")
            self.parser = _BUILTIN_PARSERS[parser]
        else:
            self.parser = parser
        self.take = take
        if min_len is None:
            min_len = field_index + 1
        self.min_len = min_len

    def extract(self, parts: list) -> Optional[object]:
        """从一条语句的 split(',') 结果中提取本字段的值。"""
        if len(parts) < self.min_len:
            return None
        return self.parser(parts[self.field_index])


# ---------------------------------------------------------------------------
# 框架主体：历元切分 + 时间提取 + 跨天 + 不定位打点 + 字段聚合
# ---------------------------------------------------------------------------

class BPDebugFrame:
    """BPDEBUG 通用解析框架。

    负责（用户无需关心的通用逻辑）：
      - 以 $CHEND 切分历元
      - 历元内从任意系统 RMC/GGA（$GNRMC/$GPRMC/$GNGGA/$GPGGA/$GBGGA...）提取 UTC 时间，$xxRMC 的 DDMMYY 维护日期（跨天）
      - 不定位历元（无 RMC/GGA 时间）从最近有效时间按 10Hz 打点
      - 对每个 FieldSpec，在历元内匹配语句并提取字段值

    用户只需要：构造 FieldSpec 列表 -> parse_file() -> 得到每历元记录列表。
    """

    TIME_STATEMENTS = ('RMC', 'GGA')      # 时间源语句类型后缀（全系统：GNRMC/GPRMC/GBGGA/GPGGA... 均识别）
    DATE_FIELD = 9                            # $xxRMC 中 DDMMYY 的字段索引

    def __init__(self, fields: list, fallback_date: Optional[datetime.date] = None,
                 log_callback: Optional[Callable] = None):
        self.fields = fields
        self.fallback_date = fallback_date or datetime.date.today()
        self.log = log_callback or (lambda msg: None)
        # 预编译字段 -> 语句前缀索引；同时建“类型后缀”索引，使 $GNGGA 声明的字段
        # 也能匹配 GPGGA/GBGGA 等其它系统前缀的同一语句
        self._fields_by_stmt: dict = {}
        self._fields_by_suffix: dict = {}
        for f in self.fields:
            self._fields_by_stmt.setdefault(f.stmt_prefix, []).append(f)
            self._fields_by_suffix.setdefault(f.stmt_prefix[3:], []).append(f)

    # ---------- 解析主流程 ----------

    def parse_file(self, fname: str) -> list:
        """解析 BPDEBUG 文件，返回每历元记录列表。

        每条记录:
          {'utc_time': datetime 或 None, 'is_nofix': 0/1, 'epoch_index': int,
           各 FieldSpec.name: 提取值或 None}
        不定位且无参考时间的历元 utc_time 为 None（is_nofix=1）。
        """
        cur_date = None
        last_valid_dt = None
        last_valid_epoch = -1
        cur_epoch = 0
        total_epochs = 0
        nofix_epochs = 0

        # 历元缓冲
        buf_time = None
        buf_date = None
        buf_has_time = False
        buf_values = {f.name: None for f in self.fields}   # first/last 用
        buf_lists = {f.name: [] for f in self.fields}      # all/count 用

        rows = []

        with open(fname, 'r', errors='replace', newline='') as f:
            for line in f:
                line = line.strip()
                if not line.startswith('$'):
                    continue
                stmt = line.split(',')[0].upper()

                # --- 时间源处理（全系统 GGA/RMC：GNRMC/GPRMC/GNGGA/GPGGA/GBGGA...） ---
                if stmt.endswith('RMC'):
                    parts = line.split(',')
                    if len(parts) >= 10:
                        t = parse_hhmmss(parts[1])
                        if t is not None:
                            buf_has_time = True
                            buf_time = t
                            d = parse_date_ddmmyy(parts[self.DATE_FIELD])
                            if d is not None:
                                buf_date = d
                elif stmt.endswith('GGA'):
                    parts = line.split(',')
                    if len(parts) >= 7:
                        t = parse_hhmmss(parts[1])
                        if t is not None:
                            buf_has_time = True
                            buf_time = t

                # --- 声明字段提取（先精确前缀，再按类型后缀匹配任意系统） ---
                specs = self._fields_by_stmt.get(stmt)
                if specs is None and stmt.startswith('$') and len(stmt) >= 5:
                    specs = self._fields_by_suffix.get(stmt[3:])
                if specs:
                    parts = line.split(',')
                    for spec in specs:
                        val = spec.extract(parts)
                        if spec.take == 'all':
                            if val is not None:
                                buf_lists[spec.name].append(val)
                        elif spec.take == 'count':
                            buf_lists[spec.name].append(1)
                        elif spec.take == 'last':
                            if val is not None:
                                buf_values[spec.name] = val
                        else:  # first: 只取第一条
                            if buf_values[spec.name] is None and val is not None:
                                buf_values[spec.name] = val

                # --- 历元结束 ---
                elif stmt == '$CHEND':
                    total_epochs += 1
                    dt = None
                    is_nofix = 0
                    if buf_has_time and buf_time is not None:
                        if buf_date is not None:
                            cur_date = buf_date
                        if cur_date is None:
                            cur_date = self.fallback_date
                        dt = datetime.datetime.combine(cur_date, datetime.time()) + datetime.timedelta(seconds=buf_time)
                        # 兜底跨天检测
                        if last_valid_dt is not None:
                            diff = (dt - last_valid_dt).total_seconds()
                            if diff < -12 * 3600:
                                cur_date = cur_date + datetime.timedelta(days=1)
                                dt = datetime.datetime.combine(cur_date, datetime.time()) + datetime.timedelta(seconds=buf_time)
                        last_valid_dt = dt
                        last_valid_epoch = cur_epoch
                        is_nofix = 0
                    else:
                        nofix_epochs += 1
                        is_nofix = 1
                        if last_valid_dt is not None:
                            dt = last_valid_dt + datetime.timedelta(seconds=(cur_epoch - last_valid_epoch) * 0.1)

                    # 组装记录
                    rec = {'utc_time': dt, 'is_nofix': is_nofix, 'epoch_index': cur_epoch}
                    for f in self.fields:
                        if f.take == 'all':
                            rec[f.name] = list(buf_lists[f.name])
                        elif f.take == 'count':
                            rec[f.name] = len(buf_lists[f.name])
                        else:
                            rec[f.name] = buf_values[f.name]
                    rows.append(rec)

                    # 重置缓冲
                    buf_time = None
                    buf_date = None
                    buf_has_time = False
                    buf_values = {f.name: None for f in self.fields}
                    buf_lists = {f.name: [] for f in self.fields}
                    cur_epoch += 1

        self._stats = {
            'total_epochs': total_epochs,
            'output_rows': len(rows),
            'nofix_epochs': nofix_epochs,
            'time_start': rows[0]['utc_time'].isoformat() if rows and rows[0]['utc_time'] else None,
            'time_end': rows[-1]['utc_time'].isoformat() if rows and rows[-1]['utc_time'] else None,
        }
        return rows

    @property
    def stats(self) -> dict:
        return getattr(self, '_stats', {})

    # ---------- 便捷输出 ----------

    def to_csv(self, rows: list, out_csv: str):
        """将记录列表写为 CSV（utc_time / is_nofix / 各字段列）。"""
        field_names = [f.name for f in self.fields]
        with open(out_csv, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['utc_time', 'is_nofix', 'epoch_index'] + field_names)
            for r in rows:
                vals = [r.get('utc_time').isoformat() if r.get('utc_time') else '',
                        r.get('is_nofix', ''), r.get('epoch_index', '')]
                for fn in field_names:
                    v = r.get(fn)
                    if isinstance(v, list):
                        v = ';'.join(str(x) for x in v)
                    elif v is None:
                        v = ''
                    vals.append(v)
                w.writerow(vals)


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def parse_bpdebug(fname: str, fields: list,
                  fallback_date: Optional[datetime.date] = None,
                  log_callback: Optional[Callable] = None):
    """一行式入口：声明字段 -> 解析 -> (rows, stats)。"""
    frame = BPDebugFrame(fields, fallback_date=fallback_date, log_callback=log_callback)
    rows = frame.parse_file(fname)
    return rows, frame.stats


def parse_temp_file(temp_csv: str, log_callback: Optional[Callable] = None):
    """解析温度 CSV（可选）。格式：Index, Time(北京时间), t(seconds), T1, Tenv。

    返回 {'ts': [ms], 'val': [T1]} 或 None（解析失败时）。
    """
    log = log_callback or (lambda msg: None)
    try:
        with open(temp_csv, 'r', errors='replace', newline='') as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                log("[警告] 温度文件为空")
                return None
            header = [h.strip() for h in header]
            time_col = None
            t1_col = None
            for i, h in enumerate(header):
                hl = h.lower()
                if 'time' in hl:
                    time_col = i
                if hl == 't1':
                    t1_col = i
            if time_col is None:
                time_col = 1
            if t1_col is None:
                t1_col = 3
            ts_list = []
            val_list = []
            for row in reader:
                if len(row) <= max(time_col, t1_col):
                    continue
                t_str = row[time_col].strip()
                v_str = row[t1_col].strip()
                if not t_str or not v_str:
                    continue
                try:
                    dt = datetime.datetime.strptime(t_str, '%Y/%m/%d %H:%M:%S') - datetime.timedelta(hours=8)
                    val = float(v_str)
                except Exception:
                    continue
                ts_list.append(int(dt.timestamp() * 1000))
                val_list.append(val)
        if not ts_list:
            log("[警告] 温度文件无可解析数据")
            return None
        pairs = sorted(zip(ts_list, val_list), key=lambda p: p[0])
        return {'ts': [p[0] for p in pairs], 'val': [p[1] for p in pairs]}
    except Exception as exc:
        log(f"[错误] 温度文件解析失败: {exc}")
        return None
