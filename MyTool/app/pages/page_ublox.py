from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QCheckBox, QFormLayout

from app.core import ublox_runner
from app.pages.base_page import BasePage


class UbloxPage(BasePage):
    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>解析 u-blox UBX 二进制协议文件，输出可读的 ASCII 文本日志。</p>
    <h3>使用方法</h3>
    <ol>
        <li>选择 UBX 二进制输入文件（*.ubx / *.bin）。</li>
        <li>选择输出 ASC 文件路径，留空则与输入文件同名。</li>
        <li>勾选“输出详细日志”可输出更详细的解析信息。</li>
        <li>点击“运行”开始解析。</li>
    </ol>
    <h3>实现逻辑</h3>
    <p>按 UBX 协议同步字、消息类别和长度字段逐条解析 NAV、RXM 等消息，并转换为文本格式输出。</p>
    """

    def __init__(self, parent=None):
        self._output_feature_key = "ublox"
        super().__init__("u-blox UBX 解析", parent)

    def build_form(self):
        self.add_file_row("输入 UBX", "input_path", "选择 UBX 二进制文件", "UBX 文件 (*.ubx *.bin);;所有文件 (*.*)")
        self.add_file_row("输出 ASC", "output_path", "保存 ASC 文件", placeholder="默认保存到 output/ublox/")

        form = self.add_form_layout()
        self._verbose_cb = QCheckBox("输出详细日志")
        form.addRow("", self._verbose_cb)

    def get_params(self) -> dict[str, Any]:
        input_path = self._inputs["input_path"].text().strip()
        output_path = self._inputs["output_path"].text().strip()
        if not output_path and input_path:
            output_path = self.default_output_path(f"{Path(input_path).stem}.asc")
        return {
            "input_path": input_path,
            "output_path": output_path,
            "verbose": self._verbose_cb.isChecked(),
        }

    def validate_params(self, params: dict[str, Any]) -> bool:
        if not params.get("input_path"):
            from app.utils import show_error

            show_error(self, "请选择输入 UBX 文件")
            return False
        return True

    def run_task(self, params: dict[str, Any], log_callback):
        return ublox_runner.run(
            params["input_path"],
            params["output_path"],
            verbose=params["verbose"],
            log_callback=log_callback,
        )
