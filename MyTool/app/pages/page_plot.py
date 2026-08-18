from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import QTabWidget, QVBoxLayout

from app.pages.base_page import BasePage
from app.paths import get_project_root
from app.streamlit_embed import StreamlitProcess


class PlotPage(BasePage):
    """交互式绘图页面：内嵌 plot_tool/app.py 与 converter.py。"""

    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>内嵌交互式 Streamlit 页面，支持上传数据文件后在线绘制图表，以及进行列格式转换。</p>
    <h3>使用方法</h3>
    <ol>
        <li>进入页面后，内嵌浏览器会自动加载 Streamlit 服务。</li>
        <li>在“主绘图工具”标签页上传 CSV / Excel / TXT 文件，选择 X/Y 列并绘制折线图、散点图等。</li>
        <li>切换到“列格式转换”标签页，可进行经纬度、UTC 时间等列格式转换。</li>
        <li>如需在外部浏览器操作，点击“在浏览器中打开”。</li>
    </ol>
    <h3>实现逻辑</h3>
    <p>使用 QWebEngineView 内嵌本地 Streamlit 子进程，通过自定义文件选择对话框支持文件上传；Streamlit 负责数据解析与图表渲染。</p>
    """

    def __init__(self, parent=None):
        # 本页为内嵌 Streamlit，不需要“结果预览”卡，关闭以腾出全部空间给绘图区
        self._show_preview_card = False
        super().__init__("交互式绘图工具", parent)

    def build_form(self):
        root = get_project_root()
        plot_script = root / "plot_tool" / "app.py"
        converter_script = root / "plot_tool" / "converter.py"

        self._tabs = QTabWidget()
        self._tabs.setMinimumHeight(600)
        self._tabs.setDocumentMode(True)
        self._tabs.setSizePolicy(
            self._tabs.sizePolicy().Policy.Expanding,
            self._tabs.sizePolicy().Policy.Expanding,
        )

        # plot_tool 主绘图
        self._plot_embed = StreamlitProcess(str(plot_script), port=8501, title="plot_tool")
        self._plot_embed.load_failed.connect(self._on_load_failed)
        self._tabs.addTab(self._plot_embed, "主绘图工具")

        # converter 列格式转换
        self._converter_embed = StreamlitProcess(str(converter_script), port=8502, title="converter")
        self._converter_embed.load_failed.connect(self._on_load_failed)
        self._tabs.addTab(self._converter_embed, "列格式转换")

        self._config_layout.addWidget(self._tabs)

        # 绘图页面：标签页占满主区；运行日志改为独立弹窗（标题栏“日志”按钮）

        # 切换标签页时按需启动第二个 Streamlit
        self._tabs.currentChanged.connect(self._on_tab_changed)

        # 页面创建后自动启动主绘图工具（Streamlit 启动已改为非阻塞）
        self._plot_embed.start()

    def _on_tab_changed(self, index: int):
        if index == 1:
            self._converter_embed.start()

    def _on_load_failed(self, message: str):
        self.log(f"[Streamlit 加载失败] {message}")

    def get_params(self) -> dict[str, Any]:
        return {"output_dir": str(get_project_root())}

    def run_task(self, params: dict[str, Any], log_callback):
        # 绘图工具本身由 Streamlit 自己驱动；此处确保服务已启动并返回真实状态
        if not self._plot_embed.is_running():
            log_callback("正在启动 Streamlit 服务，请稍候...")
            self._plot_embed.start()
        else:
            log_callback("Streamlit 服务已在运行。")
        log_callback("提示：请在上方内嵌页面中上传文件并操作。")
        return "绘图工具已就绪；若上方页面显示加载失败，可点击'启动'或'刷新'重试"

    def validate_params(self, params: dict[str, Any]) -> bool:
        return True

    def cleanup(self):
        self._plot_embed.cleanup()
        self._converter_embed.cleanup()
