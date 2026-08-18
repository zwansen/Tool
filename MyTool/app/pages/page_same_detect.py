import os
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QCheckBox, QDoubleSpinBox, QFormLayout, QPlainTextEdit

from app.core import same_detect_runner
from app.pages.base_page import BasePage


class SameDetectPage(BasePage):
    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>检测文本文件中的重复行，支持完全重复与按相似度阈值检测近似重复。</p>
    <h3>使用方法</h3>
    <ol>
        <li>选择待检测的文本文件。</li>
        <li>选择输出报告路径，留空则生成“输入名_duplicate_report.txt”。</li>
        <li>设置相似度阈值：留空/0 表示只检测逐字完全相同的重复行；填入 0~1 的值（如 0.9）则额外按“相似度不低于该值”找出近似重复行。</li>
        <li>点击“运行”开始检测。</li>
    </ol>
    <h3>实现逻辑</h3>
    <p>对每行文本进行规范化后两两比较，使用相似度算法（如编辑距离或集合相似度）判断是否重复，输出重复行位置及内容。</p>
    <h3>结果查看</h3>
    <p>检测结果（重复行分组、行号、统计）显示在界面下方“结果预览”窗口；运行日志仅显示运行进度。</p>
    """

    def __init__(self, parent=None):
        self._output_feature_key = "duplicate_detect"
        super().__init__("重复行检测", parent)
        # 运行日志只显示进度，默认保持收起（结果在下方预览窗口）
        self._auto_expand_log_on_run = False

    def on_result_ready(self, result: str):
        """检测结果渲染到“结果预览”面板。

        run_task 返回 dict：{"result_path", "report_text", "html_path"}。
        优先用可视化 HTML 报告（分组表）+ QWebEngineView 预览。
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
        self.add_file_row("输入文件", "input_path", "选择文本文件")
        self.add_file_row("输出文件", "output_path", "保存报告", placeholder="默认保存到 output/duplicate_detect/")

        form = self.add_form_layout()
        self._similarity_spin = QDoubleSpinBox()
        self._similarity_spin.setRange(0.0, 1.0)
        self._similarity_spin.setSingleStep(0.05)
        self._similarity_spin.setValue(0.9)
        self._similarity_spin.setDecimals(2)
        self._similarity_spin.setSpecialValueText("完全重复")
        self._similarity_spin.setValue(0.0)
        form.addRow("相似度阈值 (0=完全重复):", self._similarity_spin)

        self._verbose_cb = QCheckBox("显示完整重复内容")
        self._verbose_cb.setChecked(False)
        form.addRow("", self._verbose_cb)

    def get_params(self) -> dict[str, Any]:
        input_path = self._inputs["input_path"].text().strip()
        output_path = self._inputs["output_path"].text().strip()
        similarity = self._similarity_spin.value()
        if similarity == 0.0:
            similarity = None
        if not output_path and input_path:
            stem = Path(input_path).stem
            output_path = self.default_output_path(f"{stem}_duplicate_report.txt")
        return {
            "input_path": input_path,
            "output_path": output_path,
            "similarity": similarity,
            "verbose": self._verbose_cb.isChecked(),
        }

    def validate_params(self, params: dict[str, Any]) -> bool:
        if not params.get("input_path"):
            from app.utils import show_error

            show_error(self, "请选择输入文件")
            return False
        return True

    def run_task(self, params: dict[str, Any], log_callback):
        return same_detect_runner.run(
            params["input_path"],
            params["output_path"],
            similarity=params["similarity"],
            verbose=params["verbose"],
            log_callback=log_callback,
        )
