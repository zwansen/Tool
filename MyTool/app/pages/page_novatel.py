from typing import Any

from PyQt6.QtWidgets import QCheckBox

from app.core import novatel_runner
from app.pages.base_page import BasePage


class NovatelPage(BasePage):
    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>配置驱动的通用 NovAtel 风格二进制解析器。通过一份 JSON 字段定义描述同步头、消息头与各
    消息体字段，即可把二进制日志拆解成按消息类型分文件的 CSV，无需改代码。</p>
    <h3>使用方法</h3>
    <ol>
        <li>选择要解析的二进制日志（*.bin / *.dat / *.log）。</li>
        <li>字段定义默认使用内置的 <code>message_definitions.json</code>，需要解析自定义协议时换成自己的配置。</li>
        <li>输出目录留空则在输入文件旁生成 <code>&lt;文件名&gt;_parsed/</code>。</li>
        <li>勾选“仅输出原始数值”可去掉 <code>*_name</code> / <code>*_desc</code> 枚举解释列。</li>
    </ol>
    <h3>实现逻辑</h3>
    <p>按配置中的 <code>sync</code> 同步头在字节流里滑动查找消息起点，读取消息头得到
    messageId 与消息体长度，再按该 ID 对应的字段表逐字段拆包；带 <code>enum</code> 的字段
    会查枚举表补出可读名称与描述（支持精确值、<code>a-b</code> 区间与 <code>_default</code> 兜底）。
    最后按 messageId 分组写出 CSV。</p>
    <h3>提示</h3>
    <p>若提示“未解析到任何消息”，通常是字段定义里的同步头与实际协议不匹配。</p>
    """

    def __init__(self, parent=None):
        self._output_feature_key = "novatel"
        super().__init__("NovAtel 通用解析", parent)

    def build_form(self):
        self.add_file_row(
            "输入二进制",
            "input_path",
            "选择二进制日志",
            "二进制文件 (*.bin *.dat *.log);;所有文件 (*.*)",
        )
        cfg = self.add_file_row(
            "字段定义",
            "config_path",
            "选择 JSON 字段定义",
            "JSON 文件 (*.json);;所有文件 (*.*)",
            placeholder="留空使用内置 message_definitions.json",
            required=False,
            must_exist=False,
        )
        cfg.setToolTip(f"内置配置：{novatel_runner.default_config_path()}")
        self.add_directory_row(
            "输出目录",
            "output_dir",
            "选择输出目录",
            placeholder="留空则输出到 output/novatel/",
        )

        form = self.add_form_layout()
        self._raw_only = QCheckBox("仅输出原始数值（不展开枚举名称/描述列）")
        self._raw_only.setToolTip("勾选后 CSV 更精简，适合后续程序处理")
        form.addRow("解析选项:", self._raw_only)
        self._tab_chain.append(self._raw_only)

        # 字段定义若填了路径就必须存在
        self.register_validator("config_path", self._validate_config)

    @staticmethod
    def _validate_config(text: str):
        from pathlib import Path

        text = text.strip().strip('"')
        if not text:
            return None
        p = Path(text)
        if not p.is_file():
            return f"字段定义文件不存在：{text}"
        if p.suffix.lower() != ".json":
            return "字段定义应为 .json 文件"
        return None

    def get_params(self) -> dict[str, Any]:
        return {
            "input_path": self._inputs["input_path"].text().strip().strip('"'),
            "config_path": self._inputs["config_path"].text().strip().strip('"'),
            "output_dir": self._inputs["output_dir"].text().strip().strip('"'),
            "raw_only": self._raw_only.isChecked(),
        }

    def run_task(self, params: dict[str, Any], log_callback, progress_callback=None, should_stop=None):
        return novatel_runner.run(
            params["input_path"],
            output_dir=params["output_dir"],
            config_path=params["config_path"],
            raw_only=params["raw_only"],
            log_callback=log_callback,
            progress_callback=progress_callback,
            should_stop=should_stop,
        )
