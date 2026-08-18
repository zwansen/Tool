from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QComboBox, QGroupBox, QPlainTextEdit, QVBoxLayout

from app.core import dop_runner
from app.pages.base_page import BasePage


class DopPage(BasePage):
    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>从 NMEA <code>GSV</code> 语句中提取各系统可见卫星的仰角/方位角，统计有效星数并解算
    <b>PDOP / HDOP / VDOP</b> 精度因子。</p>
    <h3>使用方法</h3>
    <ol>
        <li>方式一：选择包含 GSV 语句的日志文件（会自动从整段日志里筛出 GSV 行）。</li>
        <li>方式二：留空文件，直接把 GSV 语句粘贴到下方文本框。</li>
        <li>选择输出格式（CSV / JSON / Python 列表）。</li>
        <li>输出文件留空时只在日志区展示结果，不落盘。</li>
        <li>点击“运行”（Ctrl+Enter）。</li>
    </ol>
    <h3>实现逻辑</h3>
    <p>按 talker ID 区分 GPS/GLONASS/Galileo/BDS/QZSS 等系统，逐组读取
    (PRN, 仰角, 方位角, 信噪比)，同系统同 PRN 只保留首次出现。随后用各卫星视线单位向量
    构造几何矩阵 G，取 <code>Q = (GᵀG)⁻¹</code> 的对角线元素开方得到各项 DOP。
    卫星数少于 4 颗时无法解算，会在日志中给出提示。</p>
    """

    def __init__(self, parent=None):
        self._output_feature_key = "dop"
        super().__init__("DOP 精度因子计算", parent)

    def build_form(self):
        self.add_file_row(
            "输入日志",
            "input_path",
            "选择包含 GSV 语句的日志",
            "日志/文本 (*.log *.txt *.nmea);;所有文件 (*.*)",
            placeholder="可留空，改用下方粘贴的 GSV 文本",
            required=False,
            must_exist=False,
        )
        self.add_file_row(
            "输出文件",
            "output_path",
            "保存解析结果",
            placeholder="留空则自动保存到本次运行目录 output/dop/dop_report_<时间>/dop.csv",
        )

        form = self.add_form_layout()
        self._format_combo = QComboBox()
        self._format_combo.addItems(["CSV", "JSON", "Python 列表"])
        self._format_combo.setToolTip("导出的卫星列表格式")
        form.addRow("输出格式:", self._format_combo)

        box = QGroupBox("直接粘贴 GSV 语句（未选择文件时生效）")
        layout = QVBoxLayout(box)
        self._gsv_edit = QPlainTextEdit()
        self._gsv_edit.setPlaceholderText(
            "$GPGSV,3,1,11,01,45,120,42,03,20,250,38,...*7A\n$GBGSV,2,1,08,05,60,310,45,...*6C"
        )
        self._gsv_edit.setMinimumHeight(110)
        layout.addWidget(self._gsv_edit)
        self._config_layout.addWidget(box)
        self._tab_chain.append(self._gsv_edit)

        # 文件与粘贴文本至少要有一个，复用已有行内提示
        self.register_validator("input_path", self._validate_source, self._input_hints["input_path"])
        self._gsv_edit.textChanged.connect(self._validate_timer_start)

    def _validate_timer_start(self):
        timer = getattr(self, "_validate_timer", None)
        if timer is not None:
            timer.start()

    def _validate_source(self, text: str):
        text = text.strip().strip('"')
        if text:
            if not Path(text).is_file():
                return f"文件不存在：{text}"
            return None
        if not self._gsv_edit.toPlainText().strip():
            return "请选择日志文件，或在下方粘贴 GSV 语句"
        return None

    _FMT_MAP = {"CSV": "csv", "JSON": "json", "Python 列表": "python"}

    def get_params(self) -> dict[str, Any]:
        fmt = self._FMT_MAP[self._format_combo.currentText()]
        out = self._inputs["output_path"].text().strip().strip('"')
        if not out:
            out = self.default_output_path("dop.csv")
        return {
            "input_path": self._inputs["input_path"].text().strip().strip('"'),
            "gsv_text": self._gsv_edit.toPlainText(),
            "output_path": out,
            "output_format": fmt,
        }

    def run_task(self, params: dict[str, Any], log_callback):
        return dop_runner.run(
            input_path=params["input_path"],
            gsv_text=params["gsv_text"],
            output_path=params["output_path"],
            output_format=params["output_format"],
            log_callback=log_callback,
        )
