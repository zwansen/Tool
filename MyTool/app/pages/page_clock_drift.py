"""BPDEBUG 钟漂变化分析。

解析 BPDEBUG 日志（CNRCV 第 11/12/13 字段），绘制 flashclkdrifft / curclkdrifft /
recvclkdrifft 三条钟漂曲线；可叠加多个文件对比，可选温度曲线，生成交互式 HTML 报告。

界面布局与 TTFF 分析页类似：每个输入文件一张卡片（路径 + 名称 + 备注 + 删除），
顶部提供温度 CSV 选择、报告标题自定义等参数。
"""

import json
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core import clock_drift_runner
from app.pages.base_page import BasePage
from app.utils import make_file_selector

_LOG_SUFFIXES = (".log", ".txt", ".nmea")
_TEMP_FILTER = "温度 CSV (*.csv);;所有文件 (*.*)"


class ClockDriftPage(BasePage):
    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>解析 BPDEBUG 日志中的 <b>CNRCV</b> 语句（第 11/12/13 字段），绘制三个钟漂值随时间变化的曲线：</p>
    <ul>
      <li><b>flashclkdrifft</b>：闪存钟漂</li>
      <li><b>curclkdrifft</b>：当前钟漂</li>
      <li><b>recvclkdrifft</b>：接收机钟漂</li>
    </ul>
    <p>支持多文件叠加对比（每个文件不同颜色 + 图例 + 备注）；可选输入温度 CSV 绘制温度曲线。</p>
    <h3>温度 CSV（可选）</h3>
    <p>格式：第 2 列为北京时间 <code>YYYY/M/D H:M:S</code>，第 4 列为 T1（温度）。<b>不填表示恒温测试</b>，
    此时不绘制温度曲线，但保留温度坐标轴位置（三个钟漂图自动均分高度）。</p>
    <h3>使用方法</h3>
    <ol>
      <li>「添加文件」（可多选）或「添加文件夹」，为每个文件填写显示名称与备注（可选）。</li>
      <li>（可选）选择温度 CSV；填写报告标题与报告文件名。</li>
      <li>点击「运行」，完成后结果预览会渲染交互式 HTML 报告。</li>
    </ol>
    <h3>报告交互</h3>
    <p>滚轮缩放 X 轴；Ctrl+滚轮缩放 Y 轴；拖拽平移；底部/右侧滑块缩放；工具栏框选放大。
    悬停查看任意时刻所有文件三个钟漂值（+温度）。「显示不定位点」开关控制大量不定位散点（默认关闭）。</p>
    """

    def __init__(self, parent=None):
        self._output_feature_key = "clock_drift"
        self._file_rows: list[dict] = []
        self._files_layout = None
        super().__init__("钟漂变化分析", parent)

    # ---------- 构建界面 ----------

    def build_form(self):
        op_layout = QHBoxLayout()
        op_layout.setSpacing(8)
        add_files_btn = QPushButton("添加文件…")
        add_files_btn.setObjectName("primaryButton")
        add_files_btn.clicked.connect(self._on_add_files)
        add_folder_btn = QPushButton("添加文件夹…")
        add_folder_btn.clicked.connect(self._on_add_folder)
        op_layout.addWidget(add_files_btn)
        op_layout.addWidget(add_folder_btn)
        op_layout.addStretch()
        self._config_layout.addLayout(op_layout)

        hint = QLabel("支持同时添加多个 BPDEBUG 日志（.log/.txt/.nmea），每个文件可设置显示名称与备注。")
        hint.setObjectName("fieldHint")
        self._config_layout.addWidget(hint)

        self._files_container = QWidget()
        self._files_layout = QVBoxLayout(self._files_container)
        self._files_layout.setSpacing(10)
        self._files_layout.setContentsMargins(0, 0, 0, 0)
        self._config_layout.addWidget(self._files_container)

        add_more = QPushButton("＋ 添加输入文件")
        add_more.setObjectName("primaryButton")
        add_more.clicked.connect(lambda _=False: self._add_file_row({}))
        self._config_layout.addWidget(add_more)

        settings_form = self.add_form_layout("参数设置")

        # 温度 CSV（可选）
        temp_row = QWidget()
        temp_h = QHBoxLayout(temp_row)
        temp_h.setContentsMargins(0, 0, 0, 0)
        temp_h.setSpacing(8)
        self._temp_csv = QLineEdit()
        self._temp_csv.setPlaceholderText("留空 = 恒温测试（不绘制温度曲线，保留坐标轴）")
        temp_btn = make_file_selector(self, self._temp_csv, "选择温度 CSV", _TEMP_FILTER, directory=False)
        temp_btn.setToolTip("选择温度 CSV（可选）")
        temp_h.addWidget(self._temp_csv, 1)
        temp_h.addWidget(temp_btn)
        settings_form.addRow("温度 CSV(可选):", temp_row)

        # 报告标题（自定义）
        self._title = QLineEdit("BPDEBUG 接收机钟漂与温度联合分析")
        settings_form.addRow("报告标题:", self._title)

        # 报告文件名（自定义）
        self._output_html = QLineEdit("钟漂变化分析报告.html")
        settings_form.addRow("报告文件名:", self._output_html)

        # 横轴模式：默认取 GGA/RMC 时间（UTC 时间轴）；勾选后按历元序号绘制
        self._use_epoch_axis = QCheckBox("按历元序号绘图（不取 GGA/RMC 时间）")
        self._use_epoch_axis.setToolTip(
            "勾选后横轴为历元编号 0,1,2,…，不需要时间；"
            "不勾选（默认）则从 GGA/RMC（全系统前缀）提取 UTC 时间作为横轴。"
        )
        settings_form.addRow("", self._use_epoch_axis)

        # 输出目录
        self._out_dir = QLineEdit()
        self._out_dir.setPlaceholderText("默认：output/clock_drift/")
        out_row = QWidget()
        out_h = QHBoxLayout(out_row)
        out_h.setContentsMargins(0, 0, 0, 0)
        out_h.setSpacing(8)
        out_btn = make_file_selector(self, self._out_dir, "选择输出目录", "", directory=True)
        out_btn.setToolTip("选择输出目录")
        out_h.addWidget(self._out_dir, 1)
        out_h.addWidget(out_btn)
        settings_form.addRow("输出目录:", out_row)

        self._add_file_row({})

    # ---------- 文件行 ----------

    def _add_file_row(self, spec: dict):
        file_path = spec.get("file", "")
        name = spec.get("name", "")
        note = spec.get("note", "")

        card = QWidget()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        # 路径 + 浏览/删除
        h1 = QHBoxLayout()
        path_label = QLabel(file_path or "（未选择文件）")
        path_label.setObjectName("filePathLabel")
        path_label.setWordWrap(True)
        browse_btn = QPushButton("浏览")
        browse_btn.setObjectName("ghostButton")
        browse_btn.clicked.connect(lambda _=False, c=card: self._on_browse(c))
        del_btn = QPushButton("删除")
        del_btn.setObjectName("ghostButton")
        del_btn.clicked.connect(lambda _=False, c=card: self._on_remove_row(c))
        h1.addWidget(path_label, 1)
        h1.addWidget(browse_btn)
        h1.addWidget(del_btn)
        v.addLayout(h1)

        # 名称 / 备注
        h2 = QHBoxLayout()
        n_label = QLabel("显示名称:")
        n_label.setObjectName("fieldLabel")
        n_label.setMinimumWidth(70)
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("报告中的显示名（缺省取文件名）")
        nt_label = QLabel("备注:")
        nt_label.setObjectName("fieldLabel")
        nt_label.setMinimumWidth(44)
        note_edit = QLineEdit(note)
        note_edit.setPlaceholderText("可选（图例中显示）")
        h2.addWidget(n_label)
        h2.addWidget(name_edit, 3)
        h2.addWidget(nt_label)
        h2.addWidget(note_edit, 2)
        v.addLayout(h2)

        self._files_layout.addWidget(card)
        self._file_rows.append({
            "widget": card,
            "path_label": path_label,
            "name_edit": name_edit,
            "note_edit": note_edit,
        })

    def _on_browse(self, card: QWidget):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 BPDEBUG 日志", "",
            "日志文件 (*.log *.txt *.nmea);;所有文件 (*.*)")
        if not path:
            return
        for row in self._file_rows:
            if row["widget"] is card:
                row["path_label"].setText(path)
                if not row["name_edit"].text().strip():
                    row["name_edit"].setText(Path(path).stem)
                break

    def _on_remove_row(self, card: QWidget):
        for i, row in enumerate(self._file_rows):
            if row["widget"] is card:
                self._file_rows.pop(i)
                card.deleteLater()
                break

    def _on_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择 BPDEBUG 日志（可多选）", "",
            "日志文件 (*.log *.txt *.nmea);;所有文件 (*.*)")
        for p in paths:
            self._add_file_row({"file": p})

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含日志的文件夹")
        if not folder:
            return
        files = sorted(
            str(p) for p in Path(folder).rglob("*")
            if p.is_file() and p.suffix.lower() in _LOG_SUFFIXES
        )
        if not files:
            self._append_log(f"[提示] 文件夹下未找到日志文件：{folder}")
            return
        for f in files:
            self._add_file_row({"file": f})

    # ---------- 参数 ----------

    def _collect_specs(self) -> list[dict]:
        specs = []
        for row in self._file_rows:
            file_path = row["path_label"].text().strip()
            if not file_path or file_path == "（未选择文件）":
                continue
            specs.append({
                "file": file_path,
                "name": row["name_edit"].text().strip(),
                "note": row["note_edit"].text().strip(),
            })
        return specs

    def get_params(self) -> dict[str, Any]:
        files = self._collect_specs()
        settings = {
            "temp_csv": self._temp_csv.text().strip(),
            "title": self._title.text().strip() or "BPDEBUG 接收机钟漂与温度联合分析",
            "output_html": self._output_html.text().strip() or "钟漂变化分析报告.html",
            "use_epoch_axis": self._use_epoch_axis.isChecked(),
        }
        return {
            "files": files,
            "settings": settings,
            "output_dir": self._out_dir.text().strip(),
        }

    def validate_params(self, params: dict[str, Any]) -> bool:
        if not params.get("files"):
            self._append_log("[错误] 请至少添加一个有效的输入文件")
            return False
        missing = [f["file"] for f in params["files"] if not Path(f["file"]).exists()]
        if missing:
            self._append_log(f"[错误] 以下文件不存在：{', '.join(missing)}")
            return False
        temp_csv = params["settings"].get("temp_csv", "")
        if temp_csv and not Path(temp_csv).exists():
            self._append_log(f"[错误] 温度文件不存在：{temp_csv}")
            return False
        return True

    def run_task(self, params: dict[str, Any], log_callback):
        return clock_drift_runner.run_clock_drift(
            params["files"],
            params["settings"],
            output_dir=params["output_dir"],
            log_callback=log_callback,
        )

    def on_result_ready(self, result: str):
        if result and Path(result).exists():
            self.show_result_preview_html(result)
