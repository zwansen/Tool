from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.core import rtk_viewer_runner
from app.pages.base_page import BasePage


class RtkViewerPage(BasePage):
    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>把 RTK 定位日志渲染成可交互的 3D 点云网页：轨迹、定位状态着色、置信度、真值误差对比。</p>
    <h3>支持输入（可多选，自动识别格式）</h3>
    <ul>
        <li>NMEA 日志（.nmea / .log / 二进制容器内嵌 NMEA 的 .bin）</li>
        <li>bag_*.txt 点云文本</li>
        <li>rosbag2 记录目录（含 zstd 压缩 .db3.zstd）</li>
    </ul>
    <h3>使用方法</h3>
    <ol>
        <li>添加一个或多个输入文件/目录。</li>
        <li>（可选）选择真值 NMEA 文件，用于同图对比并计算水平/高程/速度误差。</li>
        <li>点击“运行”，生成自包含 HTML 并自动弹出预览；可一键在浏览器中全屏打开。</li>
    </ol>
    <h3>网页交互</h3>
    <p>左拖旋转 / 右键或中键拖平移 / 滚轮光标处缩放 / 双击放大 / 适配与复位；着色支持定位状态、置信度、误差超差（可勾选条件组合）。</p>
    """

    def __init__(self, parent=None):
        self._output_feature_key = "rtk_viewer"
        super().__init__("RTK 3D 查看器", parent)

    def build_form(self):
        # —— 多文件输入列表 ——
        files_card = self._ensure_card("rtk_files", "输入数据（多文件：NMEA / bag_*.txt / rosbag2 目录）")
        self._file_list = QListWidget()
        self._file_list.setObjectName("pathList")
        self._file_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._file_list.setMinimumHeight(120)
        self._file_list.setToolTip("支持多选；可添加文件或整个 rosbag2 目录")
        files_card.addWidget(self._file_list)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        add_file_btn = QPushButton("添加文件…")
        add_file_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_file_btn.clicked.connect(self._add_files)
        add_dir_btn = QPushButton("添加目录…")
        add_dir_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_dir_btn.clicked.connect(self._add_directory)
        remove_btn = QPushButton("移除选中")
        remove_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        remove_btn.clicked.connect(self._remove_selected)
        clear_btn = QPushButton("清空")
        clear_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        clear_btn.clicked.connect(self._file_list.clear)
        for b in (add_file_btn, add_dir_btn, remove_btn, clear_btn):
            btns.addWidget(b)
        btns.addStretch()
        files_card.addLayout(btns)

        # —— 真值与输出 ——
        self.add_file_row(
            "真值文件（可选）", "truth_path", "选择真值 NMEA 文件",
            "所有文件 (*.*)",
            placeholder="可选：用于误差对比（水平/高程/速度）",
            required=False,
            must_exist=True,
        )
        self.add_file_row(
            "输出 HTML", "output_path", "保存位置",
            "HTML 文件 (*.html)",
            placeholder="默认输出到 output/rtk_viewer/",
        )

    # ---------- 多文件列表操作 ----------

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择输入文件（可多选）",
            "",
            "所有文件 (*.*);;NMEA 日志 (*.nmea *.log *.bin);;文本 (*.txt)",
        )
        for p in paths:
            if not self._contains(p):
                self._file_list.addItem(p)

    def _add_directory(self):
        d = QFileDialog.getExistingDirectory(self, "选择 rosbag2 记录目录（或含数据的文件夹）")
        if d and not self._contains(d):
            self._file_list.addItem(d)

    def _remove_selected(self):
        for item in self._file_list.selectedItems():
            self._file_list.takeItem(self._file_list.row(item))

    def _contains(self, path: str) -> bool:
        return any(
            self._file_list.item(i).text() == path
            for i in range(self._file_list.count())
        )

    # ---------- 参数 / 运行 ----------

    def get_params(self) -> dict[str, Any]:
        files = [
            self._file_list.item(i).text()
            for i in range(self._file_list.count())
        ]
        truth = self._inputs["truth_path"].text().strip()
        output = self._inputs["output_path"].text().strip()
        if not output:
            output = self.default_output_path("rtk3d_3d_viewer.html")
        return {
            "input_paths": files,
            "output_path": output,
            "truth_path": truth or None,
        }

    def validate_params(self, params: dict[str, Any]) -> bool:
        if not params.get("input_paths"):
            from app.utils import show_error

            show_error(self, "请至少添加一个输入文件或目录")
            return False
        return True

    def run_task(self, params: dict[str, Any], log_callback):
        return rtk_viewer_runner.run_rtk_viewer(
            params["input_paths"],
            params["output_path"],
            truth_path=params.get("truth_path"),
            log_callback=log_callback,
        )

    def on_result_ready(self, result):
        """HTML 结果用网页预览（含“在浏览器中打开”按钮）。"""
        if result and isinstance(result, str) and Path(result).suffix.lower() == ".html" and Path(result).exists():
            self.show_result_preview_html(result)
        else:
            super().on_result_ready(result)
