import os
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QComboBox, QFormLayout, QPlainTextEdit

from app.core import time_continuity_runner
from app.pages.base_page import BasePage

FREQ_OPTIONS = (1, 2, 5, 10, 20)


class TimeContinuityPage(BasePage):
    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>检测 GNSS 日志中时间戳是否连续，识别丢点、跳秒或时间回退。</p>
    <h3>使用方法</h3>
    <ol>
        <li>选择 GPS 日志文件。</li>
        <li>选择输出文件路径，留空则生成“输入名_continuity.txt”。</li>
        <li>选择数据频率：支持 1 / 2 / 5 / 10 / 20 Hz。</li>
        <li>点击“运行”开始检测。</li>
    </ol>
    <h3>实现逻辑</h3>
    <p>按选定频率计算标称间隔（= 1/频率），允许 ±10% 偏差作为连续判定阈值；
    相邻历元时间差超过阈值即标记为异常点，并输出 RMC / GGA / PVTResult / PVTMeas 的连续性统计报告。</p>
    <h3>结果查看</h3>
    <p>检测结果（连续性统计、异常时间差、缺失定位数据等）显示在界面下方“结果预览”窗口；运行日志仅显示运行进度。</p>
    """

    def __init__(self, parent=None):
        self._output_feature_key = "time_continuity"
        super().__init__("时间连续性检测", parent)
        # 运行日志只显示进度，默认保持收起（结果在下方预览窗口）
        self._auto_expand_log_on_run = False

    def on_result_ready(self, result: str):
        """检测结果渲染到“结果预览”面板。

        run_task 返回 dict：{"result_path", "report_text", "html_path"}。
        优先用可视化 HTML 报告（含时间轴）+ QWebEngineView 预览。
        """
        if isinstance(result, dict):
            html_path = result.get("html_path", "")
            path = result.get("result_path", "")
            report = result.get("report_text", "")
        else:
            html_path, path, report = "", result or "", ""

        if html_path and os.path.exists(html_path):
            self.show_result_preview_html(html_path)
        elif report:
            # 回退：超大报告截断显示，避免一次性渲染卡死界面
            MAX = 800_000
            if len(report) > MAX:
                report = (
                    report[:MAX]
                    + f"\n\n……（报告过长，此处仅显示前 {MAX} 字符；完整报告见输出文件）"
                )
            view = QPlainTextEdit()
            view.setObjectName("monoPreview")
            view.setReadOnly(True)
            view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            view.setPlainText(report)
            self.set_result_preview(view)
        elif path and os.path.exists(path):
            self.show_result_preview_path(path)
        else:
            self._show_preview()

    def build_form(self):
        self.add_file_row("输入文件", "input_path", "选择 GPS 日志文件")
        self.add_file_row("输出文件", "output_path", "保存结果", placeholder="默认保存到 output/time_continuity/")

        form = self.add_form_layout()
        self._freq_combo = QComboBox()
        for f in FREQ_OPTIONS:
            self._freq_combo.addItem(f"{f} Hz")
        self._freq_combo.setCurrentText("10 Hz")
        form.addRow("数据频率:", self._freq_combo)

    def get_params(self) -> dict[str, Any]:
        input_path = self._inputs["input_path"].text().strip()
        output_path = self._inputs["output_path"].text().strip()
        if not output_path and input_path:
            stem = Path(input_path).stem
            output_path = self.default_output_path(f"{stem}_continuity.txt")
        freq = int(self._freq_combo.currentText().split()[0])
        return {
            "input_path": input_path,
            "output_path": output_path,
            "freq": freq,
        }

    def validate_params(self, params: dict[str, Any]) -> bool:
        if not params.get("input_path"):
            from app.utils import show_error

            show_error(self, "请选择输入文件")
            return False
        return True

    def run_task(self, params: dict[str, Any], log_callback):
        return time_continuity_runner.run(
            params["input_path"],
            params["output_path"],
            freq=params["freq"],
            log_callback=log_callback,
        )
