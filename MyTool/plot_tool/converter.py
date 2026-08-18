import re
import streamlit as st
import pandas as pd
import io
from io import BytesIO
from typing import Callable, Optional

st.set_page_config(page_title="列格式转换工具", page_icon="🔧", layout="wide")

st.title("🔧 列格式转换工具")
st.markdown("上传 CSV / TXT / LOG / Excel 文件，按分隔符解析后选择列进行经纬度或 UTC 时间格式转换。")


# ==================== 转换函数 ====================

def nmea_latlon_to_decimal(value, kind="自动识别"):
    """
    把 NMEA 经纬度格式转换为十进制度。
     Latitude : ddmm.mmmm  -> 2位度 + 分/60
    Longitude : dddmm.mmmm -> 3位度 + 分/60
    例如 3100.0002267 -> 31.000003778
    """
    if pd.isna(value) or str(value).strip() == "":
        return None
    try:
        s = str(value).strip()
        # 只保留数字和小数点
        s = re.sub(r"[^\d.]", "", s)
        if not s or s.count(".") != 1:
            return None

        int_part, frac_part = s.split(".")
        if not int_part or not frac_part:
            return None

        # 自动识别：整数位 4 位为纬度，5 位为经度
        if kind == "自动识别":
            if len(int_part) == 4:
                kind = "纬度"
            elif len(int_part) == 5:
                kind = "经度"
            else:
                return None

        if kind == "纬度":
            deg_len = 2
        elif kind == "经度":
            deg_len = 3
        else:
            return None

        if len(int_part) < deg_len:
            return None

        deg = float(int_part[:deg_len])
        minutes = float(int_part[deg_len:] + "." + frac_part)
        return round(deg + minutes / 60.0, 10)
    except Exception:
        return None


def apply_hemisphere(value, hemisphere):
    """根据半球符号给十进制度添加正负号。"""
    if value is None or pd.isna(value):
        return value
    try:
        h = str(hemisphere).strip().upper()
        if h in ("S", "W"):
            return -float(value)
        return float(value)
    except Exception:
        return value


def nmea_utc_to_hms(value):
    """
    080502.00 -> 08:05:02.000
    080502    -> 08:05:02.000
    """
    if pd.isna(value) or str(value).strip() == "":
        return None
    try:
        s = re.sub(r"[^\d.]", "", str(value).strip())
        if not s:
            return None
        parts = s.split(".")
        int_part = parts[0]
        if len(int_part) > 6:
            return str(value)
        int_part = int_part.zfill(6)
        hh = int_part[:2]
        mm = int_part[2:4]
        ss = int_part[4:6]
        frac = parts[1] if len(parts) > 1 else ""
        frac = (frac + "000")[:3]
        return f"{hh}:{mm}:{ss}.{frac}"
    except Exception:
        return str(value)


def hms_to_nmea_utc(value):
    """
    08:05:02.00 -> 080502.00
    08:05:02    -> 080502
    """
    if pd.isna(value) or str(value).strip() == "":
        return None
    try:
        nums = re.findall(r"\d+", str(value).strip())
        if len(nums) < 3:
            return str(value)
        hh = nums[0][-2:].zfill(2)
        mm = nums[1][-2:].zfill(2)
        ss = nums[2][-2:].zfill(2)
        frac = nums[3] if len(nums) > 3 else ""
        return f"{hh}{mm}{ss}" + (f".{frac}" if frac else "")
    except Exception:
        return str(value)


CONVERSION_TYPES = {
    "经纬度：ddmm.mmmm / dddmm.mmmm → 十进制度": "latlon",
    "UTC：HHMMSS.SSS → HH:MM:SS.SSS": "utc_to_hms",
    "UTC：HH:MM:SS.SSS → HHMMSS.SSS": "hms_to_utc",
}


# ==================== 侧边栏：上传与解析 ====================

st.sidebar.header("📁 文件上传")
uploaded_file = st.sidebar.file_uploader(
    "选择文件",
    type=["csv", "txt", "log", "tsv", "xlsx", "xls"],
)

delimiter_options = {
    "逗号 ,": ",",
    "制表符 \\t": "\t",
    "分号 ;": ";",
    "空格": " ",
    "多个空格 \\s+": r"\s+",
    "竖线 |": "|",
    "自定义": "custom",
}

selected_delim_label = st.sidebar.selectbox("分隔符", list(delimiter_options.keys()), index=0)
delimiter = delimiter_options[selected_delim_label]
if delimiter == "custom":
    delimiter = st.sidebar.text_input("自定义分隔符", value=",")

has_header = st.sidebar.checkbox("首行为表头", value=True)
skip_rows = st.sidebar.number_input("跳过前 N 行", min_value=0, value=0, step=1)


def _open_text_stream(file_bytes):
    """根据字节内容检测编码，返回可逐行读取的 TextIOWrapper。"""
    encoding = "utf-8"
    try:
        file_bytes.decode("utf-8")
    except UnicodeDecodeError:
        encoding = "gbk"
    return io.TextIOWrapper(io.BytesIO(file_bytes), encoding=encoding, errors="replace")


def _make_progress_callback(progress_bar):
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


# ==================== 解析文件 ====================

def parse_uploaded_file(file, delimiter, has_header, skip_rows, progress_callback=None):
    if file is None:
        return None
    fname = file.name.lower()
    try:
        if fname.endswith((".xlsx", ".xls")):
            if progress_callback:
                progress_callback(50)
            df = pd.read_excel(file, dtype=str)
            if progress_callback:
                progress_callback(100)
            return df

        content = file.getvalue()
        sep = delimiter
        if sep == r"\s+":
            sep = r"\s+"

        if progress_callback:
            progress_callback(10)

        # 使用 TextIOWrapper 直接交给 pandas，避免 decode + splitlines + StringIO 的额外拷贝
        stream = _open_text_stream(content)
        try:
            skiprows = range(skip_rows) if skip_rows > 0 else None
            if has_header:
                df = pd.read_csv(stream, sep=sep, dtype=str, keep_default_na=False, skiprows=skiprows)
            else:
                df = pd.read_csv(stream, sep=sep, header=None, dtype=str, keep_default_na=False, skiprows=skiprows)
                df.columns = [f"列{i + 1}" for i in range(len(df.columns))]
            if progress_callback:
                progress_callback(100)
            return df
        finally:
            stream.close()
    except Exception as e:
        st.error(f"解析文件失败：{e}")
        return None


# ==================== session state ====================

if "conversions" not in st.session_state:
    st.session_state.conversions = []


# ==================== 主界面 ====================

if uploaded_file is None:
    st.info("👈 请在左侧上传文件开始转换")
else:
    upload_progress = st.progress(0, text="准备解析...")
    df = parse_uploaded_file(
        uploaded_file,
        delimiter,
        has_header,
        skip_rows,
        progress_callback=_make_progress_callback(upload_progress),
    )
    if df is None:
        st.stop()

    st.subheader("📋 原始数据预览")
    st.dataframe(df.head(30), use_container_width=True)
    st.caption(f"共 {df.shape[0]} 行 × {df.shape[1]} 列")

    st.divider()
    st.subheader("🔧 添加转换规则")

    cols = df.columns.tolist()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        source_col = st.selectbox("源列", cols, key="conv_source")
    with col2:
        conv_label = st.selectbox("转换方式", list(CONVERSION_TYPES.keys()), key="conv_type")
        conv_code = CONVERSION_TYPES[conv_label]
    with col3:
        if conv_code == "latlon":
            latlon_kind = st.selectbox("类型", ["自动识别", "纬度", "经度"], key="conv_latlon_kind")
            hemisphere_col = st.selectbox("半球列（可选）", ["无"] + cols, key="conv_hemisphere")
        else:
            latlon_kind = "自动识别"
            hemisphere_col = "无"
            st.markdown("*无需额外选项*")
    with col4:
        default_new_col = f"{source_col}_converted"
        new_col = st.text_input("输出列名", value=default_new_col, key="conv_new_col")

    if st.button("➕ 添加转换", key="conv_add"):
        new_col_clean = new_col.strip()
        existing_cols = set(df.columns.tolist())
        existing_new_cols = {c["new_col"] for c in st.session_state.conversions}
        if not new_col_clean:
            st.error("输出列名不能为空")
        elif new_col_clean in existing_cols:
            st.error(f"输出列名 **{new_col_clean}** 已存在于原始数据中，请更换")
        elif new_col_clean in existing_new_cols:
            st.error(f"输出列名 **{new_col_clean}** 已被其他转换规则使用，请更换")
        elif source_col:
            st.session_state.conversions.append({
                "source_col": source_col,
                "conv_code": conv_code,
                "conv_label": conv_label,
                "latlon_kind": latlon_kind,
                "hemisphere_col": hemisphere_col,
                "new_col": new_col_clean,
            })
            st.rerun()

    # 显示已添加规则
    if st.session_state.conversions:
        st.markdown("---")
        st.subheader("已添加的转换规则")
        for idx, conv in enumerate(st.session_state.conversions):
            c1, c2 = st.columns([6, 1])
            with c1:
                extra = ""
                if conv["conv_code"] == "latlon":
                    extra = f"（{conv['latlon_kind']}"
                    if conv["hemisphere_col"] != "无":
                        extra += f", 半球列 {conv['hemisphere_col']}"
                    extra += "）"
                st.caption(f"**{conv['new_col']}** ← {conv['source_col']}：{conv['conv_label']}{extra}")
            with c2:
                if st.button("🗑️ 删除", key=f"del_conv_{idx}"):
                    st.session_state.conversions.pop(idx)
                    st.rerun()

    st.divider()
    st.subheader("📊 转换结果预览")

    result_df = df.copy()
    for conv in st.session_state.conversions:
        src = conv["source_col"]
        dst = conv["new_col"]
        code = conv["conv_code"]
        if src not in result_df.columns:
            st.error(f"源列 {src} 不存在")
            continue
        try:
            if code == "latlon":
                result_df[dst] = result_df[src].apply(
                    lambda x: nmea_latlon_to_decimal(x, conv["latlon_kind"])
                )
                if conv["hemisphere_col"] != "无" and conv["hemisphere_col"] in result_df.columns:
                    result_df[dst] = result_df.apply(
                        lambda row: apply_hemisphere(row[dst], row[conv["hemisphere_col"]]),
                        axis=1,
                    )
            elif code == "utc_to_hms":
                result_df[dst] = result_df[src].apply(nmea_utc_to_hms)
            elif code == "hms_to_utc":
                result_df[dst] = result_df[src].apply(hms_to_nmea_utc)
        except Exception as e:
            st.error(f"转换 {src} → {dst} 失败：{e}")

    st.dataframe(result_df.head(30), use_container_width=True)

    st.divider()
    st.subheader("💾 导出")
    export_format = st.radio("导出格式", ["CSV", "Excel", "LOG/TXT"], horizontal=True)

    def _fmt_cell(v):
        if pd.isna(v) or v is None:
            return ""
        return str(v)

    if export_format == "CSV":
        csv_bytes = result_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            label="下载 CSV",
            data=csv_bytes,
            file_name="converted.csv",
            mime="text/csv",
        )
    elif export_format == "Excel":
        buf = BytesIO()
        result_df.to_excel(buf, index=False)
        buf.seek(0)
        st.download_button(
            label="下载 Excel",
            data=buf,
            file_name="converted.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        ext = st.radio("文件扩展名", [".log", ".txt"], horizontal=True)
        out_lines = []
        if has_header:
            out_lines.append(",".join(str(c) for c in result_df.columns))
        for _, row in result_df.iterrows():
            out_lines.append(",".join(_fmt_cell(v) for v in row))
        text_bytes = "\n".join(out_lines).encode("utf-8-sig")
        st.download_button(
            label=f"下载 {ext}",
            data=text_bytes,
            file_name=f"converted{ext}",
            mime="text/plain",
        )

st.divider()
st.caption("提示：经纬度转换只接收类似 `3100.0002267` 的 ddmm.mmmm / dddmm.mmmm 格式；UTC 转换支持 `080502.00` → `08:05:02.000`（固定 3 位小数）互换。")
