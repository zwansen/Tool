from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QComboBox, QFormLayout

from app.core import true_runner
from app.pages.base_page import BasePage


class TruePage(BasePage):
    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>将 Inertial Explorer（IE）输出的真实坐标文本转换为十进制度、度分或 GGA 语句格式。</p>
    <h3>使用方法</h3>
    <ol>
        <li>选择 IE 输出的文本文件（*.txt）。</li>
        <li>选择转换模式：十进制度 (DD)、度分 (DM) 或 GGA 语句。</li>
        <li>选择输出文件路径，留空则按模式自动生成后缀。</li>
        <li>点击“运行”开始转换。</li>
    </ol>
    <h3>实现逻辑</h3>
    <p>读取 IE 文本中的经纬度坐标，按所选模式进行单位换算或格式化为 NMEA GGA 语句后输出。</p>
    """

    def __init__(self, parent=None):
        self._output_feature_key = "true_coord"
        super().__init__("IE真值经纬度转换", parent)

    def build_form(self):
        self.add_file_row("输入 Inertial Explorer", "input_path", "选择 IE 输出文本文件", "文本文件 (*.txt);;所有文件 (*.*)")
        self.add_file_row("输出文件", "output_path", "保存结果", placeholder="默认保存到 output/true_coord/")

        form = self.add_form_layout()
        self._mode_combo = QComboBox()
        self._mode_combo.addItems(["十进制度 (DD)", "度分 (DM)", "GGA 语句"])
        form.addRow("转换模式:", self._mode_combo)

    def get_params(self) -> dict[str, Any]:
        input_path = self._inputs["input_path"].text().strip()
        output_path = self._inputs["output_path"].text().strip()
        mode_map = {0: "dd", 1: "dm", 2: "gga"}
        mode = mode_map[self._mode_combo.currentIndex()]
        if not output_path and input_path:
            suffix = ".csv" if mode in ("dd", "dm") else "_gga.txt"
            output_path = self.default_output_path(f"{Path(input_path).stem}{suffix}")
        return {
            "input_path": input_path,
            "output_path": output_path,
            "mode": mode,
        }

    def validate_params(self, params: dict[str, Any]) -> bool:
        if not params.get("input_path"):
            from app.utils import show_error

            show_error(self, "请选择输入文件")
            return False
        return True

    def run_task(self, params: dict[str, Any], log_callback):
        return true_runner.run(
            params["input_path"],
            params["output_path"],
            mode=params["mode"],
            log_callback=log_callback,
        )
