from abc import abstractmethod
import os
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from app.utils import LogStream, make_file_selector, open_directory, enable_drop_target
from app.worker import WorkerThread


class HelpDialog(QDialog):
    """帮助说明对话框。"""

    def __init__(self, title: str, content: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"{title} - 使用说明")
        self.setMinimumSize(560, 420)
        self.setMaximumSize(800, 600)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(content)
        layout.addWidget(browser)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("primaryButton")
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)


class BasePage(QWidget):
    """所有工具页面的基类，提供统一的输入/输出/参数/日志/运行界面。

    布局（极简，整页即配置区）：
      - 标题栏：页面标题 + “日志”开关 + “结果预览”按钮 + 帮助(?)
      - 主区：可滚动的配置表单（占满全部空间）
      - 按钮行：状态 / 运行 / 停止 / 打开输出目录

    关键设计：**结果预览**与**运行日志**都是**独立弹窗**（非模态 QDialog），
    平时不占主界面空间；点击标题栏的“日志”/“结果预览”按钮即可展开，关闭后
    主界面恢复纯净的配置视图。运行开始时自动弹出日志窗口，运行完成自动弹出
    结果预览窗口，但均为浮动窗口，关闭不影响主界面。
    """

    HELP_TEXT: str = """
    <h3>功能说明</h3>
    <p>暂无详细说明。</p>
    """

    def __init__(self, title: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.title = title
        self._inputs: dict[str, Any] = {}
        self._input_hints: dict[str, QLabel] = {}
        self._worker: Optional[WorkerThread] = None
        # 是否允许自动填充“文件型输出”路径（目录型输出默认开启）
        self.auto_fill_file_output: bool = False
        # key -> (校验函数, 行内提示 QLabel)；校验函数返回错误文案或 None
        self._validators: dict[str, tuple[Callable[[str], Optional[str]], QLabel]] = {}
        self._tab_chain: list[QWidget] = []
        self._log_stream = LogStream(self)
        self._log_stream.written.connect(self._append_log)

        # 日志 / 预览 弹窗（懒加载）
        self._log_dialog: Optional[QDialog] = None
        self._log_edit: Optional[QPlainTextEdit] = None
        self._progress: Optional[QProgressBar] = None
        self._log_visible: bool = False
        self._preview_dialog: Optional[QDialog] = None
        self._preview_content: Optional[QWidget] = None
        self._pclayout: Optional[QVBoxLayout] = None
        self._preview_placeholder: Optional[QLabel] = None
        self._preview_cur: Optional[QWidget] = None
        self._preview_web: Optional[QWidget] = None
        # 是否显示“结果预览”入口（交互式绘图等内嵌页面可关掉，但仍支持弹窗）
        self._show_preview_card: bool = getattr(self, "_show_preview_card", True)
        # 功能专属输出目录键（见 app.output_dirs.FEATURE_DIRS）；子类在 __init__ 里设置，
        # 未显式配置输出时，“打开输出目录”与默认输出路径会回退到这里，避免结果散落根目录
        self._output_feature_key: str = getattr(self, "_output_feature_key", "")

        self._setup_ui()
        self._cards: dict[str, QVBoxLayout] = {}
        self._card_titles: dict[str, QLabel] = {}
        self.build_form()
        self._finalize_cards()
        self._wire_flexibility()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 标题 + “日志”开关 + “结果预览”按钮 + 帮助
        title_layout = QHBoxLayout()
        title_layout.setSpacing(10)
        self._title_label = QLabel(self.title)
        self._title_label.setObjectName("pageTitle")
        title_layout.addWidget(self._title_label)

        self._log_toggle = QPushButton("日志")
        self._log_toggle.setObjectName("ghostButton")
        self._log_toggle.setCheckable(True)
        self._log_toggle.setChecked(False)
        self._log_toggle.setToolTip("显示 / 隐藏运行日志窗口（独立弹窗）")
        self._log_toggle.toggled.connect(self._on_log_toggle)
        title_layout.addWidget(self._log_toggle)

        self._preview_btn = QPushButton("结果预览")
        self._preview_btn.setObjectName("ghostButton")
        self._preview_btn.setToolTip("在独立窗口中查看结果预览")
        self._preview_btn.clicked.connect(self._open_preview)
        title_layout.addWidget(self._preview_btn)

        title_layout.addStretch()
        self._help_btn = QPushButton("?")
        self._help_btn.setObjectName("primaryButton")
        self._help_btn.setFixedSize(32, 32)
        self._help_btn.setToolTip("查看使用说明")
        self._help_btn.clicked.connect(self._show_help)
        title_layout.addWidget(self._help_btn)
        main_layout.addLayout(title_layout)

        # 主区：可滚动配置表单，占满全部空间（无内嵌预览 / 侧栏日志）
        self._config_scroll = QScrollArea()
        self._config_scroll.setWidgetResizable(True)
        self._config_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._config_widget = QWidget()
        self._config_layout = QVBoxLayout(self._config_widget)
        self._config_layout.setSpacing(12)
        self._config_layout.setContentsMargins(4, 0, 14, 0)
        self._config_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._config_scroll.setWidget(self._config_widget)
        main_layout.addWidget(self._config_scroll, 1)

        # 按钮行（始终可见，置于底部）
        btn_layout = QHBoxLayout()
        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("statusLabel")
        self._run_btn = QPushButton("运行")
        self._run_btn.setObjectName("primaryButton")
        self._run_btn.setMinimumWidth(100)
        self._run_btn.clicked.connect(self._on_run)
        self._stop_btn = QPushButton("停止")
        self._stop_btn.setObjectName("dangerButton")
        self._stop_btn.setMinimumWidth(100)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._on_stop)
        self._open_output_btn = QPushButton("打开输出目录")
        self._open_output_btn.setEnabled(False)
        self._open_output_btn.clicked.connect(self._on_open_output)
        btn_layout.addWidget(self._status_label)
        btn_layout.addStretch()
        btn_layout.addWidget(self._run_btn)
        btn_layout.addWidget(self._stop_btn)
        btn_layout.addWidget(self._open_output_btn)
        main_layout.addLayout(btn_layout)

    # ---------- 日志弹窗 ----------

    def _ensure_log_dialog(self) -> QDialog:
        if self._log_dialog is None:
            d = QDialog(self)
            d.setWindowTitle("运行日志")
            d.setMinimumSize(560, 400)
            d.resize(780, 520)
            layout = QVBoxLayout(d)
            layout.setContentsMargins(12, 12, 12, 12)
            layout.setSpacing(8)

            self._log_edit = QPlainTextEdit()
            self._log_edit.setObjectName("logEdit")
            self._log_edit.setReadOnly(True)
            self._log_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            self._log_edit.setMaximumBlockCount(5000)
            layout.addWidget(self._log_edit, 1)

            self._progress = QProgressBar()
            self._progress.setRange(0, 100)
            self._progress.setValue(0)
            self._progress.setTextVisible(False)
            layout.addWidget(self._progress)

            # 用户关闭窗口时同步“日志”按钮状态（阻断信号防递归）
            d.finished.connect(lambda _=False: self._sync_log_btn(False))
            self._log_dialog = d
        return self._log_dialog

    def _sync_log_btn(self, state: bool):
        self._log_toggle.blockSignals(True)
        self._log_toggle.setChecked(state)
        self._log_toggle.blockSignals(False)

    def _on_log_toggle(self, checked: bool):
        self._set_log_visible(checked)

    def _set_log_visible(self, visible: bool):
        """显示 / 隐藏日志弹窗，并同步“日志”按钮状态。"""
        self._log_visible = visible
        self._sync_log_btn(visible)
        d = self._ensure_log_dialog()
        if visible:
            d.show()
            d.raise_()
        else:
            d.hide()

    # ---------- 结果预览弹窗 ----------

    def _ensure_preview_dialog(self) -> QDialog:
        if self._preview_dialog is None:
            d = QDialog(self)
            d.setWindowTitle("结果预览")
            d.setMinimumSize(760, 540)
            d.resize(1000, 680)
            layout = QVBoxLayout(d)
            layout.setContentsMargins(10, 10, 10, 10)
            self._preview_content = QWidget()
            self._pclayout = QVBoxLayout(self._preview_content)
            self._pclayout.setContentsMargins(0, 0, 0, 0)
            self._pclayout.setSpacing(0)
            self._preview_placeholder = QLabel(
                "运行完成后，将在此窗口显示结果预览。"
            )
            self._preview_placeholder.setObjectName("fieldHint")
            self._pclayout.addWidget(self._preview_placeholder)
            self._preview_cur = self._preview_placeholder
            layout.addWidget(self._preview_content, 1)
            self._preview_dialog = d
        return self._preview_dialog

    def _set_preview_widget(self, widget: QWidget):
        """替换预览弹窗内容并弹出窗口。旧内容（除可复用的 WebView 外）被销毁。"""
        d = self._ensure_preview_dialog()
        old = self._preview_cur
        if old is not None and old is not widget:
            if self._pclayout.indexOf(old) != -1:
                self._pclayout.removeWidget(old)
            if old is not self._preview_placeholder and old is not self._preview_web:
                old.deleteLater()
        self._preview_cur = widget
        self._pclayout.addWidget(widget)
        d.show()
        d.raise_()

    def _open_preview(self):
        """点击“结果预览”按钮：打开预览弹窗（已有内容则直接显示，否则显示占位）。"""
        d = self._ensure_preview_dialog()
        d.show()
        d.raise_()

    # ---------- 结果预览（子类调用区） ----------

    def _show_preview(self):
        """确保预览弹窗存在并弹出（子类在无具体内容时的兜底调用）。"""
        d = self._ensure_preview_dialog()
        d.show()
        d.raise_()

    def set_result_preview(self, widget: QWidget):
        """用自定义控件替换结果预览区内容（子类常用）。"""
        self._set_preview_widget(widget)

    def show_result_preview_path(self, path: str):
        """默认预览：展示结果文件路径并提供“打开文件”按钮。"""
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        lab = QLabel(f"结果文件：{path}")
        lab.setWordWrap(True)
        open_btn = QPushButton("打开文件")
        open_btn.setObjectName("ghostButton")
        open_btn.clicked.connect(
            lambda _=False: QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        )
        h.addWidget(lab, 1)
        h.addWidget(open_btn)
        self.set_result_preview(box)

    def show_result_preview_html(self, path: str):
        """懒加载一个 QWebEngineView 渲染 HTML 结果（如 TTFF 报告）。

        预览弹窗顶部提供『在浏览器中打开』按钮：点击后报告以完整网页形式
        占据整个系统默认浏览器窗口（适合图表较多的报告，浏览体验更佳）。
        WebEngine 不可用时降级为提示文字，浏览器打开功能不受影响。
        """
        try:
            from PyQt6.QtWebEngineWidgets import QWebEngineView

            if self._preview_web is None:
                self._preview_web = QWebEngineView()
            self._preview_web.load(QUrl.fromLocalFile(path))
            inner = self._preview_web
        except Exception:
            inner = QLabel(f"内嵌预览不可用（缺少 QtWebEngine），可点击上方按钮在浏览器中打开。\n报告文件：{path}")
            inner.setObjectName("fieldHint")
            inner.setWordWrap(True)

        box = QWidget()
        v = QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        lab = QLabel("报告预览")
        lab.setObjectName("cardTitle")
        open_btn = QPushButton("在浏览器中打开（全屏网页）")
        open_btn.setToolTip("用系统默认浏览器打开报告，占据整个网页窗口")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(
            lambda _=False: QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        )
        bar.addWidget(lab)
        bar.addStretch()
        bar.addWidget(open_btn)
        v.addLayout(bar)
        v.addWidget(inner, 1)
        self.set_result_preview(box)

    def on_result_ready(self, result: str):
        """运行完成钩子：子类可重写以自定义预览（默认展示结果文件路径）。"""
        if result and os.path.exists(result):
            self.show_result_preview_path(result)

    # ---------- 子类需实现的方法 ----------

    @abstractmethod
    def build_form(self):
        """在 self._config_layout 中添加页面特有的表单控件。"""
        raise NotImplementedError

    @abstractmethod
    def get_params(self) -> dict[str, Any]:
        """返回运行参数字典，必须包含 input_path 和 output_path（如果适用）。"""
        raise NotImplementedError

    @abstractmethod
    def run_task(self, params: dict[str, Any], log_callback):
        """实际执行任务的函数，运行在后台线程中。"""
        raise NotImplementedError

    # ---------- 自适应卡片容器 ----------

    def _ensure_card(self, key: str, title: str) -> QVBoxLayout:
        """懒创建一张毛玻璃卡片（QFrame#card）放入配置区，返回其内部布局。

        key 相同则复用卡片，title 只在首次创建时生效（首张卡的标题）。
        """
        card_layout = self._cards.get(key)
        if card_layout is None:
            card = QFrame()
            card.setObjectName("card")
            v = QVBoxLayout(card)
            v.setContentsMargins(20, 18, 20, 18)
            v.setSpacing(12)
            cap = QLabel(title)
            cap.setObjectName("cardTitle")
            v.addWidget(cap)
            self._config_layout.addWidget(card)
            self._cards[key] = v
            self._card_titles[key] = cap
            card_layout = v
        return card_layout

    def _finalize_cards(self):
        """简单页面（总字段 ≤4）把『输出设置』卡并入『输入配置』卡，
        合并为一张『文件与路径』卡，避免字段少时卡片过于碎片化。

        中等/复杂页面（>4 字段）保持两张卡（输入配置 / 输出设置）。
        """
        out_layout = self._cards.get("output")
        if out_layout is None:
            return
        total = len(self._inputs) + sum(
            getattr(f, "rowCount", lambda: 0)()
            for f in getattr(self, "_form_layouts", [])
        )
        if total > 4:
            return
        inp_layout = self._cards.get("input")
        if inp_layout is None:
            return
        cap = self._card_titles.get("input")
        if cap is not None:
            cap.setText("文件与路径")
        items = []
        while out_layout.count():
            item = out_layout.takeAt(0)
            w = item.widget()
            if w is not None and w.objectName() != "cardTitle":
                items.append(w)
        for w in items:
            inp_layout.addWidget(w)
        out_card = out_layout.parentWidget()
        if out_card is not None:
            out_card.deleteLater()
        self._cards.pop("output", None)
        self._card_titles.pop("output", None)

    # ---------- 通用辅助方法 ----------

    def _build_path_row(
        self,
        label: str,
        key: str,
        title: str,
        filter_str: str,
        placeholder: str,
        is_dir: bool,
        required: Optional[bool],
        must_exist: Optional[bool],
        accept: str = "file",
    ) -> QLineEdit:
        """文件行 / 目录行的共用实现：路径输入 + 浏览按钮 + 行内校验提示。

        required / must_exist 传 None 时按 key 推断：
        输出类字段（key 以 output 开头）默认可留空且不校验存在性，
        输入类字段默认必填且必须存在。
        """
        is_output = key.startswith("output")
        if required is None:
            required = not is_output
        if must_exist is None:
            must_exist = not is_output
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setObjectName("fieldLabel")
        lbl.setMinimumWidth(90)
        line = QLineEdit()
        line.setPlaceholderText(placeholder or ("拖入文件夹或点击右侧浏览" if is_dir else "拖入文件或点击右侧浏览"))
        line.setClearButtonEnabled(True)
        btn = make_file_selector(self, line, title, filter_str, directory=is_dir)
        btn.setToolTip(title)
        layout.addWidget(lbl)
        layout.addWidget(line, 1)
        layout.addWidget(btn)
        # 输入 / 输出行自动归入对应卡片（简单页面随后会合并成一张）
        card_layout = self._ensure_card(
            "output" if is_output else "input",
            "输出设置" if is_output else "输入配置",
        )
        card_layout.addWidget(row)

        hint = QLabel("")
        hint.setObjectName("fieldHint")
        hint.setVisible(False)
        hint.setWordWrap(True)
        card_layout.addWidget(hint)

        self._inputs[key] = line
        self._input_hints[key] = hint
        self._tab_chain.extend([line, btn])
        enable_drop_target(row, line, is_dir=is_dir)

        if required or must_exist:
            self.register_validator(
                key,
                self._make_path_validator(label, accept, required, must_exist),
                hint,
            )
        return line

    @staticmethod
    def _make_path_validator(label: str, accept: str, required: bool, must_exist: bool):
        kind = {"file": "文件", "dir": "文件夹", "any": "路径"}.get(accept, "路径")

        def _validate(text: str) -> Optional[str]:
            text = text.strip().strip('"')
            if not text:
                return f"请填写{label}" if required else None
            if must_exist:
                p = Path(text)
                if not p.exists():
                    return f"{kind}不存在：{text}"
                if accept == "dir" and not p.is_dir():
                    return f"该路径不是文件夹：{text}"
                if accept == "file" and not p.is_file():
                    return f"该路径不是文件：{text}"
            return None

        return _validate

    def add_file_row(
        self,
        label: str,
        key: str,
        title: str = "选择文件",
        filter_str: str = "所有文件 (*.*)",
        placeholder: str = "",
        required: Optional[bool] = None,
        must_exist: Optional[bool] = None,
        accept: str = "file",
    ) -> QLineEdit:
        """添加一个文件选择行，并将 QLineEdit 保存到 self._inputs[key]。

        accept="any" 表示该字段同时接受文件与文件夹。
        """
        return self._build_path_row(
            label, key, title, filter_str, placeholder,
            is_dir=False, required=required, must_exist=must_exist, accept=accept,
        )

    def add_directory_row(
        self,
        label: str,
        key: str,
        title: str = "选择文件夹",
        placeholder: str = "",
        required: Optional[bool] = None,
        must_exist: Optional[bool] = None,
    ) -> QLineEdit:
        """添加一个文件夹选择行。"""
        return self._build_path_row(
            label, key, title, "", placeholder,
            is_dir=True, required=required, must_exist=must_exist, accept="dir",
        )

    def add_form_layout(self, title: str = "参数设置") -> QFormLayout:
        """添加一个参数表单卡片（QFrame#card + 标题 + QFormLayout）。

        title 用于卡片标题，可按功能把参数分成多张卡（如“报告与输出”、“运行控制”）。
        返回 QFormLayout，用法与原先完全一致（addRow(label, widget) / addRow(widget)）。
        """
        card_layout = self._ensure_card(f"form{len(self._cards)}", title)
        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        container = QWidget()
        container.setLayout(form)
        card_layout.addWidget(container)
        self._form_layouts = getattr(self, "_form_layouts", [])
        self._form_layouts.append(form)
        return form

    def register_validator(
        self,
        key: str,
        func: Callable[[str], Optional[str]],
        hint_label: Optional[QLabel] = None,
    ):
        """为某个输入项登记实时校验函数；校验失败时禁用“运行”并显示行内提示。"""
        if hint_label is None:
            hint_label = QLabel("")
            hint_label.setObjectName("fieldHint")
            hint_label.setVisible(False)
            hint_label.setWordWrap(True)
            self._config_layout.addWidget(hint_label)
        self._validators[key] = (func, hint_label)

    def log(self, text: str):
        self._append_log(text)

    def _append_log(self, text: str):
        edit = self._log_edit
        if edit is None:
            # 懒加载日志弹窗（默认隐藏），避免运行前就弹窗
            d = self._ensure_log_dialog()
            edit = self._log_edit
            if not self._log_visible:
                d.hide()
        edit.appendPlainText(text)

    # ---------- 灵活性增强 ----------

    def _wire_flexibility(self):
        """连接通用交互增强：自动填充输出、实时校验、Tab 顺序、快捷键。"""
        # 输入变化时尝试自动填充输出路径
        for key, widget in self._inputs.items():
            if isinstance(widget, QLineEdit) and key.startswith("input"):
                widget.textChanged.connect(lambda _=None: self._maybe_autofill_output())

        # 任一输入变化都触发一次（防抖的）实时校验
        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(120)
        self._validate_timer.timeout.connect(self.revalidate)
        for widget in self._inputs.values():
            sig = getattr(widget, "textChanged", None)
            if sig is not None:
                sig.connect(lambda _=None: self._validate_timer.start())
                continue
            sig = getattr(widget, "currentTextChanged", None)
            if sig is not None:
                sig.connect(lambda _=None: self._validate_timer.start())
                continue
            sig = getattr(widget, "valueChanged", None)
            if sig is not None:
                sig.connect(lambda _=None: self._validate_timer.start())

        # Tab 顺序：按控件加入顺序串联，最后落到“运行”按钮
        chain = [w for w in self._tab_chain if w is not None] + [self._run_btn, self._stop_btn]
        for a, b in zip(chain, chain[1:]):
            self.setTabOrder(a, b)

        # 快捷键：Ctrl+Enter 运行，Esc 停止
        QShortcut(QKeySequence("Ctrl+Return"), self).activated.connect(self._on_run)
        QShortcut(QKeySequence("Esc"), self).activated.connect(self._on_stop)
        self._run_btn.setToolTip("运行（Ctrl+Enter）")
        self._stop_btn.setToolTip("停止（Esc）")

        self.revalidate()

    def revalidate(self) -> bool:
        """执行全部登记的校验，更新行内提示与“运行”按钮可用状态。"""
        first_error = None
        for key, (func, hint) in self._validators.items():
            widget = self._inputs.get(key)
            if widget is None:
                continue
            text = widget.text() if hasattr(widget, "text") else ""
            try:
                err = func(text)
            except Exception as exc:  # 校验函数本身出错不应阻塞界面
                err = f"校验异常：{exc}"
            self._set_field_error(widget, hint, err)
            if err and first_error is None:
                first_error = err

        running = bool(self._worker and self._worker.isRunning())
        self._run_btn.setEnabled(first_error is None and not running)
        if not running:
            self._set_status("就绪" if first_error is None else first_error,
                             None if first_error is None else "error")
        return first_error is None

    def _set_field_error(self, widget, hint: QLabel, err: Optional[str]):
        """给输入框打上/取消 invalid 属性，并刷新行内提示。"""
        want = "true" if err else "false"
        if widget.property("invalid") != want:
            widget.setProperty("invalid", want)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        hint.setText(err or "")
        hint.setVisible(bool(err))

    def _maybe_autofill_output(self):
        """输入路径已选而输出为空时，给出合理默认（不覆盖用户已填内容）。

        若页面登记了功能键，默认输出到该功能专属子目录（output/<feature>/）；
        否则退回输入文件所在目录（旧行为）。
        """
        src = self._inputs.get("input_path") or self._inputs.get("input_dir")
        if src is None:
            return
        val = src.text().strip()
        if not val:
            return
        if "output_dir" in self._inputs:
            out_key = "output_dir"
        elif "output_path" in self._inputs and self.auto_fill_file_output:
            out_key = "output_path"
        else:
            return
        out = self._inputs[out_key]
        if out.text().strip():
            return  # 用户已填写，不覆盖
        p = Path(val)
        key = getattr(self, "_output_feature_key", "")
        if key:
            from app.output_dirs import get_feature_output_dir

            d = get_feature_output_dir(key)
            if out_key == "output_dir":
                out.setText(str(d))
            else:
                suffix = p.suffix or ".txt"
                out.setText(str(d / f"{p.stem}_out{suffix}"))
        else:
            if out_key == "output_dir":
                out.setText(str(p.parent))
            else:
                out.setText(str(p.parent / f"{p.stem}_out{p.suffix}"))

    def _set_status(self, text: str, state: Optional[str] = None):
        """同步更新页面状态标签与窗口状态栏。

        state 取 running / ok / error / None，用于驱动主题里的状态色。
        """
        self._status_label.setText(text)
        self._status_label.setToolTip(text)
        want = state or ""
        if self._status_label.property("state") != want:
            self._status_label.setProperty("state", want)
            self._status_label.style().unpolish(self._status_label)
            self._status_label.style().polish(self._status_label)
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(f"{self.title}：{text}")
            except Exception:
                pass

    # ---------- 私有方法 ----------

    def _set_busy(self, busy: bool):
        """运行中锁定表单，避免参数被改动造成结果与界面不一致。"""
        self._config_widget.setEnabled(not busy)
        self._run_btn.setEnabled(not busy)
        self._stop_btn.setEnabled(busy)

    def _start_indeterminate(self):
        """任务时长未知时先走"滚动"样式，收到首个进度回报再切成百分比。"""
        self._progress.setRange(0, 0)
        self._progress.setFormat("处理中…")
        self._determinate = False

    def _on_progress(self, value: int):
        if not getattr(self, "_determinate", False):
            self._progress.setRange(0, 100)
            self._progress.setFormat("%p%")
            self._determinate = True
        self._progress.setValue(value)

    def _on_run(self):
        if self._worker and self._worker.isRunning():
            return
        if not self.revalidate():
            return
        params = self.get_params()
        if not self.validate_params(params):
            return
        # 运行前确保日志弹窗存在并（按子类开关）展开
        self._ensure_log_dialog()
        if getattr(self, "_auto_expand_log_on_run", True):
            self._set_log_visible(True)
        self._log_edit.clear()
        self._open_output_btn.setEnabled(False)
        self._start_indeterminate()
        self._set_status("运行中…", "running")
        self._worker = WorkerThread(self.run_task, params)
        self._set_busy(True)
        self._worker.log.connect(self._append_log)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished)
        self._worker.finished_error.connect(self._on_error)
        # 重定向 print 输出到 UI 日志区
        self._old_stdout = sys.stdout
        sys.stdout = self._log_stream
        self._worker.start()

    def _reset_progress(self, value: int = 0):
        self._progress.setRange(0, 100)
        self._progress.setFormat("%p%")
        self._progress.setValue(value)
        self._determinate = True

    def _on_stop(self):
        if not (self._worker and self._worker.isRunning()):
            return
        self._worker.stop()
        self._append_log("[信息] 任务已停止")
        self._restore_stdout()
        self._set_busy(False)
        self._reset_progress(0)
        self._set_status("已停止", "error")

    def _on_finished(self, result: str):
        self._restore_stdout()
        self._set_busy(False)
        self._open_output_btn.setEnabled(True)
        self._reset_progress(100)
        self._append_log("[信息] 任务完成")
        self._set_status("完成", "ok")
        # 运行完成后自动预览结果（结果详情交给预览弹窗，日志只记录进度/状态）
        if isinstance(result, dict):
            rpath = result.get("result_path")
            if rpath:
                self._append_log(f"[结果] 已生成：{rpath}")
        elif result:
            self._append_log(f"[结果] {result}")
        self.on_result_ready(result)

    def _on_error(self, error: str):
        self._restore_stdout()
        self._set_busy(False)
        self._open_output_btn.setEnabled(False)
        self._reset_progress(0)
        self._append_log(f"[错误] {error}")
        self._set_status("出错", "error")
        QMessageBox.critical(self, "运行错误", error)

    def _restore_stdout(self):
        if hasattr(self, "_old_stdout"):
            sys.stdout = self._old_stdout
            del self._old_stdout

    def get_effective_output_dir(self) -> str:
        """返回“实际输出目录”，供“打开输出目录”按钮使用。

        默认策略：优先用 output_dir / output_path；若用户未配置，则回退到
        该功能专属的输出子目录（output/<feature>/），使结果集中存放、不污染根目录；
        若页面未登记功能键，则退化到首个输入文件所在目录。
        子类（如 TTFF）可重写以精确匹配各自 runner 的解析逻辑。
        """
        params = self.get_params()
        output = (params.get("output_dir") or params.get("output_path") or "").strip()
        if output:
            p = Path(output)
            return str(p if p.is_dir() else p.parent)
        key = getattr(self, "_output_feature_key", "")
        if key:
            from app.output_dirs import get_feature_output_dir

            return str(get_feature_output_dir(key))
        for key in ("input_path", "input_dir", "input_file"):
            val = (params.get(key) or "").strip()
            if val and Path(val).exists():
                return str(Path(val).parent)
        return ""

    def default_output_path(self, default_name: str) -> str:
        """未配置输出时，返回该功能“本次运行”专属目录下的默认文件名路径，并确保目录存在。

        供子类 get_params 构造默认 output_path 使用。每次运行会创建
        output/<feature>/<feature>_report_<时间>/ 子目录，结果文件写入其中，
        避免多次运行互相覆盖；若页面未登记功能键则退回同名文件（当前目录）。
        """
        from app.output_dirs import make_run_dir

        key = getattr(self, "_output_feature_key", "")
        if key:
            return str(make_run_dir(key) / default_name)
        return default_name

    def default_output_dir(self) -> str:
        """未配置输出时，返回该功能专属的输出子目录路径（确保目录存在）。"""
        from app.output_dirs import get_feature_output_dir

        key = getattr(self, "_output_feature_key", "")
        if key:
            return str(get_feature_output_dir(key))
        return ""

    def _on_open_output(self):
        out_dir = self.get_effective_output_dir()
        if out_dir:
            open_directory(out_dir)
        else:
            self._append_log("[提示] 尚未确定输出目录（请先运行，或选择输入文件）")

    def _show_help(self):
        """弹出当前功能的帮助说明。子类通过重写 HELP_TEXT 提供具体内容。"""
        dialog = HelpDialog(self.title, self.HELP_TEXT, self)
        dialog.exec()

    def validate_params(self, params: dict[str, Any]) -> bool:
        """子类可重写以验证参数；默认认为有效。"""
        return True
