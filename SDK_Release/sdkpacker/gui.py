"""PySide6 GUI for SDK 资料包发布工具。"""

from __future__ import annotations

import io
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import (QObject, QSize, Qt, QThread, QTimer, Signal, Slot)
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QImage, QPixmap)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDialogButtonBox, QFileDialog, QFormLayout,
                               QFrame, QGroupBox, QHBoxLayout, QInputDialog,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMessageBox, QProgressBar,
                               QPushButton, QRadioButton, QScrollArea,
                               QSizePolicy, QSplitter, QStatusBar, QTextEdit,
                               QTreeWidget, QTreeWidgetItem,
                               QTreeWidgetItemIterator, QVBoxLayout, QWidget)

from .core import (CUSTOM_FOLDER, CUSTOM_TEXT, CustomItem, Node, Source,
                   build_tree, is_pdf, iter_archive_entries, parse_root_info,
                   PDF_MODE_LABELS, PDF_MODE_ORDER, selected_files,
                   set_pdf_mode, summarize_pdf_modes)
# 注意：pymupdf / pdfproc / pipeline 都是“重”模块（C 扩展 + 大依赖），
# 不在模块顶层导入，改为真正用到时再延迟导入，以显著加快 GUI 启动速度。

APP_NAME = "SDK Release"


# ----------------------------------------------------------------------
# 后台 worker
# ----------------------------------------------------------------------

class WorkerSignals(QObject):
    progress = Signal(str, float)   # message, fraction (-1 = n/a)
    finished = Signal(object)        # ProcessResult
    failed = Signal(str)


class Worker(QThread):
    def __init__(self, params: ProcessParams):
        super().__init__()
        self.params = params
        self.signals = WorkerSignals()
        self._cancel = False
        # 关键：把进度/取消回调接到信号上，否则进度条收不到任何更新
        self.params.progress_cb = self.signals.progress.emit
        self.params.cancelled_cb = lambda: self._cancel

    def cancel(self):
        self._cancel = True

    def run(self):
        try:
            from .pipeline import run   # 延迟导入，避免拖慢启动
            res = run(self.params)
            self.signals.finished.emit(res)
        except Exception as e:
            self.signals.failed.emit(f"{e}\n{traceback.format_exc()}")


# ----------------------------------------------------------------------
# 文本模板存储（放在 %APPDATA% 下，跟随用户账户）
# ----------------------------------------------------------------------

TEMPLATE_DIR = Path(os.environ.get("APPDATA", Path.home())) / "SDKRelease"
TEMPLATE_FILE = TEMPLATE_DIR / "templates.json"

BUILTIN_TEMPLATES = {
    "（空白）": "",
    "通用交付说明": (
        "凯芯 SDK 交付说明\r\n"
        "================================\r\n"
        "客户名称：{customer}\r\n"
        "SDK 类型：{type}\r\n"
        "版 本 号：{version}\r\n"
        "交付日期：{date}\r\n"
        "================================\r\n"
        "\r\n"
        "1. 本文档包含的资料仅供上述客户在授权范围内使用。\r\n"
        "2. PDF 文档已加密，支持打印，禁止复制与修改。\r\n"
        "3. 如有技术问题请联系凯芯技术支持。\r\n"
    ),
    "版本变更记录": (
        "版本变更记录\r\n"
        "================================\r\n"
        "版本：{version}\r\n"
        "日期：{date}\r\n"
        "客户：{customer}\r\n"
        "================================\r\n"
        "\r\n"
        "新增：\r\n"
        "修复：\r\n"
        "已知问题：\r\n"
    ),
}


def _load_templates() -> dict:
    data = dict(BUILTIN_TEMPLATES)
    try:
        if TEMPLATE_FILE.exists():
            data.update(json.loads(TEMPLATE_FILE.read_text("utf-8")))
    except Exception:
        pass
    return data


def _save_templates(data: dict) -> None:
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    TEMPLATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ----------------------------------------------------------------------
# 自建文本文件的编辑对话框
# ----------------------------------------------------------------------

class TextItemDialog(QDialog):
    """新建/编辑一个要塞进发布包里的 .txt 文件，支持模板。"""

    def __init__(self, parent=None, item: Optional[CustomItem] = None,
                 variables: Optional[dict] = None):
        super().__init__(parent)
        self.setWindowTitle("编辑文本说明" if item else "新建文本说明")
        self.resize(640, 520)
        self.variables = variables or {}
        self.templates = _load_templates()

        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)
        self.ed_name = QLineEdit(item.filename if item else "使用说明.txt")
        form.addRow("文件名：", self.ed_name)
        self.ed_target = QLineEdit(item.target if item else "")
        self.ed_target.setPlaceholderText("留空 = 放在包根目录，也可填如 凯芯说明")
        form.addRow("放到目录：", self.ed_target)
        lay.addLayout(form)

        tl = QHBoxLayout()
        tl.addWidget(QLabel("模板："))
        self.combo_tpl = QComboBox()
        self.combo_tpl.addItems(list(self.templates.keys()))
        self.combo_tpl.setMinimumWidth(180)
        tl.addWidget(self.combo_tpl)
        b_load = QPushButton("载入模板")
        b_load.clicked.connect(self._load_template)
        b_save_tpl = QPushButton("存为模板…")
        b_save_tpl.clicked.connect(self._save_template)
        tl.addWidget(b_load)
        tl.addWidget(b_save_tpl)
        tl.addStretch(1)
        b_vars = QPushButton("插入变量…")
        b_vars.clicked.connect(self._insert_variable)
        tl.addWidget(b_vars)
        lay.addLayout(tl)

        self.editor = QTextEdit()
        self.editor.setPlainText(item.content if item else
                                 self.templates["通用交付说明"])
        self.editor.setStyleSheet("font-family:Consolas; font-size:13px;")
        lay.addWidget(self.editor, 1)

        hint = QLabel("可用变量：{customer} 客户名 · {type} SDK类型 · "
                      "{version} 版本号 · {date} 日期（发布时自动替换）")
        hint.setStyleSheet("color:#6b7280; font-size:11px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)

        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.button(QDialogButtonBox.Ok).setText("确定")
        box.button(QDialogButtonBox.Cancel).setText("取消")
        box.accepted.connect(self._on_ok)
        box.rejected.connect(self.reject)
        lay.addWidget(box)

    def _load_template(self):
        name = self.combo_tpl.currentText()
        content = self.templates.get(name, "")
        if content and self.editor.toPlainText().strip():
            r = QMessageBox.question(
                self, "覆盖确认", "编辑器里已有内容，确定用模板覆盖吗？")
            if r != QMessageBox.Yes:
                return
        self.editor.setPlainText(content)

    def _save_template(self):
        name, ok = QInputDialog.getText(self, "存为模板", "模板名称：")
        if ok and name.strip():
            self.templates[name.strip()] = self.editor.toPlainText()
            _save_templates(self.templates)
            self.combo_tpl.clear()
            self.combo_tpl.addItems(list(self.templates.keys()))
            self.combo_tpl.setCurrentText(name.strip())

    def _insert_variable(self):
        items = ["{customer}", "{type}", "{version}", "{date}"]
        v, ok = QInputDialog.getItem(self, "插入变量", "选择变量：", items, 0, False)
        if ok:
            self.editor.insertPlainText(v)

    def _on_ok(self):
        name = self.ed_name.text().strip()
        if not name:
            QMessageBox.warning(self, "缺少文件名", "请填写文件名。")
            return
        if not name.lower().endswith(".txt"):
            name += ".txt"
        self.ed_name.setText(name)
        self.accept()

    def result_item(self) -> CustomItem:
        return CustomItem(
            kind=CUSTOM_TEXT,
            name=self.ed_name.text().strip(),
            target=self.ed_target.text().strip().strip("/"),
            filename=self.ed_name.text().strip(),
            content=self.editor.toPlainText(),
        )


# ----------------------------------------------------------------------
# 文件树（三态勾选 + 每文件 PDF 处理方式）
# ----------------------------------------------------------------------

COL_NAME, COL_SIZE, COL_MODE = 0, 1, 2


class FileTree(QTreeWidget):
    """带三态勾选的文件树，PDF 行额外提供「处理方式」下拉框。"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setHeaderLabels(["名称", "大小", "PDF 处理"])
        self.setColumnWidth(COL_NAME, 420)
        self.setColumnWidth(COL_SIZE, 84)
        self.setColumnWidth(COL_MODE, 104)
        self.setAlternatingRowColors(True)
        self.setUniformRowHeights(True)
        self.setStyleSheet("""
            QTreeWidget { background:#ffffff; color:#1f2937; font-size:13px;
                          border:1px solid #e5e7eb; border-radius:6px; }
            QTreeWidget::item { padding:2px 4px; }
            QHeaderView::section { background:#f3f4f6; padding:7px 6px;
                                   border:0; border-bottom:1px solid #e5e7eb;
                                   font-weight:600; }
        """)
        self.itemChanged.connect(self._on_item_changed)
        self._suppress = False   # 程序化改勾选时屏蔽回调，避免递归/重复核算

    # ---------- 填充 ----------
    def populate(self, root_node: Node) -> None:
        self.blockSignals(True)
        self.clear()
        for child in root_node.children:
            item = self._add_node(child)
            self.addTopLevelItem(item)
        # 顶层默认展开一层，深层按需点开
        for i in range(self.topLevelItemCount()):
            top = self.topLevelItem(i)
            top.setExpanded(True)
            for j in range(top.childCount()):
                top.child(j).setExpanded(False)
        # 第二遍：补齐「PDF 处理」下拉框（必须在加入树后 setItemWidget 才生效）
        self._attach_mode_combos(root_node)
        self.blockSignals(False)

    def _add_node(self, node: Node) -> QTreeWidgetItem:
        item = QTreeWidgetItem([node.name,
                                _fmt_size(node.size) if not node.is_dir else "",
                                ""])
        item.setData(COL_NAME, Qt.UserRole, node)
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
        item.setCheckState(COL_NAME, Qt.Checked)
        for c in node.children:
            item.addChild(self._add_node(c))
        return item

    def _attach_mode_combos(self, root_node: Node) -> None:
        """为目录/PDF 行挂上下拉框。"""
        it = QTreeWidgetItemIterator(self)
        while it.value():
            item = it.value()
            node = item.data(COL_NAME, Qt.UserRole)
            if node is not None and (node.is_dir or is_pdf(node.relpath)):
                self._make_mode_combo(item, node)
            it += 1

    def _make_mode_combo(self, item: QTreeWidgetItem, node: Node) -> None:
        combo = QComboBox()
        combo.addItems([PDF_MODE_LABELS[m] for m in PDF_MODE_ORDER])
        combo.setCurrentIndex(0)
        combo.setFixedHeight(22)
        combo.setStyleSheet("QComboBox{font-size:12px;padding:1px 4px;}")
        combo.setProperty("node", node)
        combo.currentIndexChanged.connect(
            lambda idx, c=combo: self._on_mode_changed(c, idx))
        self.setItemWidget(item, COL_MODE, combo)

    # ---------- 勾选同步 ----------
    def _on_item_changed(self, item, col):
        if self._suppress or col != COL_NAME:
            return
        node = item.data(COL_NAME, Qt.UserRole)
        if node is None:
            return
        state = item.checkState(COL_NAME)
        node.checked = {Qt.Checked: 1, Qt.PartiallyChecked: 2}.get(state, 0)
        if state != Qt.PartiallyChecked:
            for i in range(item.childCount()):
                self._sync_child(item.child(i), state)
        parent = item.parent()
        while parent is not None:
            self._refresh_parent(parent)
            parent = parent.parent()

    def _sync_child(self, item, state):
        item.setCheckState(COL_NAME, state)
        node = item.data(COL_NAME, Qt.UserRole)
        if node is not None:
            node.checked = {Qt.Checked: 1, Qt.PartiallyChecked: 2}.get(state, 0)
        for i in range(item.childCount()):
            self._sync_child(item.child(i), state)

    def _refresh_parent(self, item):
        states = {item.child(i).checkState(COL_NAME)
                  for i in range(item.childCount())}
        new = Qt.Checked if states == {Qt.Checked} else \
              Qt.Unchecked if states == {Qt.Unchecked} else Qt.PartiallyChecked
        if item.checkState(COL_NAME) != new:
            self._suppress = True
            item.setCheckState(COL_NAME, new)
            self._suppress = False
        node = item.data(COL_NAME, Qt.UserRole)
        if node is not None:
            node.checked = {Qt.Checked: 1, Qt.PartiallyChecked: 2}.get(new, 0)

    # ---------- 处理方式 ----------
    def _on_mode_changed(self, combo: QComboBox, idx: int) -> None:
        node = combo.property("node")
        if node is None or not (0 <= idx < len(PDF_MODE_ORDER)):
            return
        mode = PDF_MODE_ORDER[idx]
        set_pdf_mode(node, mode)
        # 目录节点自身不参与统计，改为计数提示
        self.window()._refresh_mode_summary()

    # ---------- 批量 ----------
    def set_all_checked(self, on: bool) -> None:
        state = Qt.Checked if on else Qt.Unchecked
        self._suppress = True
        for i in range(self.topLevelItemCount()):
            self._walk_set(self.topLevelItem(i), state)
        self._recompute_all()
        self._suppress = False

    def _walk_set(self, item, state):
        item.setCheckState(COL_NAME, state)
        node = item.data(COL_NAME, Qt.UserRole)
        if node is not None:
            node.checked = 1 if state == Qt.Checked else 0
        for i in range(item.childCount()):
            self._walk_set(item.child(i), state)

    def invert_all(self) -> None:
        self._suppress = True
        for i in range(self.topLevelItemCount()):
            self._walk_invert(self.topLevelItem(i))
        self._recompute_all()
        self._suppress = False

    def _walk_invert(self, item):
        new = (Qt.Unchecked if item.checkState(COL_NAME) == Qt.Checked
               else Qt.Checked)
        item.setCheckState(COL_NAME, new)
        node = item.data(COL_NAME, Qt.UserRole)
        if node is not None:
            node.checked = 1 if new == Qt.Checked else 0
        for i in range(item.childCount()):
            self._walk_invert(item.child(i))

    def _depth(self, item) -> int:
        d = 0
        p = item.parent()
        while p is not None:
            d += 1
            p = p.parent()
        return d

    def _recompute_all(self) -> None:
        """自下而上重算每个目录节点的勾选显示与 node.checked。

        叶子(层级深)先处理，父(层级浅)后处理，保证父级核算时
        子级状态已经是最终值。用于全选/全不选/反选后的整体校正。
        """
        items = []
        it = QTreeWidgetItemIterator(self)
        while it.value():
            items.append(it.value())
            it += 1
        items.sort(key=self._depth, reverse=True)
        for item in items:
            node = item.data(COL_NAME, Qt.UserRole)
            if node is None or item.childCount() == 0:
                continue
            states = {item.child(i).checkState(COL_NAME)
                      for i in range(item.childCount())}
            new = Qt.Checked if states == {Qt.Checked} else \
                  Qt.Unchecked if states == {Qt.Unchecked} else Qt.PartiallyChecked
            item.setCheckState(COL_NAME, new)
            node.checked = {Qt.Checked: 1,
                            Qt.PartiallyChecked: 2}.get(new, 0)


def _fmt_size(n: int) -> str:
    if n <= 0:
        return ""
    units = ["B", "KB", "MB", "GB"]
    s, i = float(n), 0
    while s >= 1024 and i < len(units) - 1:
        s, i = s / 1024, i + 1
    return f"{s:.1f} {units[i]}"


# ----------------------------------------------------------------------
# 主窗口
# ----------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self._root: Optional[Node] = None
        self._worker: Optional[Worker] = None
        self.custom_items: List[CustomItem] = []   # 附加内容（文件夹/文本）
        self._name_auto = True          # 文件名未被用户手动编辑时，随参数自动生成
        self._preview_pdf_bytes = None  # 缓存首张 PDF 字节，供实时水印预览复用
        self._preview_timer = None      # 水印参数变更防抖定时器
        self._build()
        # 根据屏幕可用区域自适应大小：避免默认过高把底部按钮挤出屏幕、
        # 也避免在小屏笔记本上窗口比桌面还大导致看不到底部操作栏。
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            ag = screen.availableGeometry()
            w = min(1280, max(980, ag.width() - 40))
            h = min(860, max(660, ag.height() - 60))
            self.resize(w, h)
            self.move(max(0, (ag.width() - w) // 2),
                      max(0, (ag.height() - h) // 2))
        else:
            self.resize(1180, 820)
        self.setMinimumSize(900, 600)

    # ================= 界面 =================
    def _build(self):
        self.setStyleSheet(_QSS)
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 10)
        root.setSpacing(8)

        root.addLayout(self._build_top())

        body = QSplitter(Qt.Horizontal)
        body.setChildrenCollapsible(False)
        body.addWidget(self._build_left())
        body.addWidget(self._build_right())
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        body.setSizes([720, 520])
        root.addWidget(body, 1)          # 主体占据剩余空间

        root.addLayout(self._build_bottom())

        # 参数联动
        for w in (self.in_customer, self.in_version, self.in_date):
            w.textChanged.connect(self._refresh_out_name)
        self.fmt_combo.currentTextChanged.connect(self._refresh_out_name)
        self.rad_sdk.toggled.connect(self._refresh_out_name)
        self.rad_lite.toggled.connect(self._refresh_out_name)
        self.combo_default_mode.currentTextChanged.connect(
            self._refresh_mode_summary)

        # 水印参数变更 → 实时刷新 PDF 预览
        for w in (self.in_wm, self.in_wm_size, self.in_wm_ang, self.in_wm_op):
            w.textChanged.connect(self._on_wm_param_changed)

        self.statusBar().showMessage("准备就绪")

    def _build_top(self) -> QHBoxLayout:
        box = QHBoxLayout()
        box.setSpacing(8)
        self.src_edit = QLineEdit()
        self.src_edit.setPlaceholderText("选择原始 SDK 资料包（压缩包或文件夹）…")
        self.src_edit.setMinimumWidth(360)
        btn = QPushButton("选择…")
        btn.setFixedWidth(78)
        btn.clicked.connect(self._on_pick_source)
        box.addWidget(self.src_edit, 1)
        box.addWidget(btn)

        self.lbl_summary = QLabel("尚未选择资料包")
        self.lbl_summary.setStyleSheet(
            "color:#4b5563; padding:6px 12px; background:#f9fafb;"
            "border:1px solid #e5e7eb; border-radius:6px;")
        self.lbl_summary.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        box.addWidget(self.lbl_summary, 1)
        return box

    def _build_left(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        # ---- 文件树 ----
        gb = QGroupBox("勾选要发布的文件 / 文件夹")
        inner = QVBoxLayout(gb)

        self.tree = FileTree()          # 先建树，下面的按钮才能引用它

        bar = QHBoxLayout()
        bar.setSpacing(6)
        for text, fn in (("全选", lambda: self.tree.set_all_checked(True)),
                         ("全不选", lambda: self.tree.set_all_checked(False)),
                         ("反选", self.tree.invert_all)):
            b = QPushButton(text)
            b.setFixedHeight(26)
            b.clicked.connect(fn)
            bar.addWidget(b)
        bar.addSpacing(12)
        lbl = QLabel("批量设 PDF 方式：")
        lbl.setStyleSheet("color:#6b7280;")
        bar.addWidget(lbl)
        self.combo_bulk = QComboBox()
        self.combo_bulk.addItems([PDF_MODE_LABELS[m] for m in PDF_MODE_ORDER])
        self.combo_bulk.setFixedHeight(26)
        bar.addWidget(self.combo_bulk)
        b_apply = QPushButton("应用")
        b_apply.setFixedHeight(26)
        b_apply.clicked.connect(self._on_bulk_mode)
        bar.addWidget(b_apply)
        bar.addStretch(1)
        inner.addLayout(bar)
        inner.addWidget(self.tree, 1)

        self.lbl_modes = QLabel("")
        self.lbl_modes.setStyleSheet(
            "color:#4b5563; padding:5px 8px; background:#f3f4f6;"
            "border-radius:6px; font-size:12px;")
        self.lbl_modes.setWordWrap(True)
        inner.addWidget(self.lbl_modes)

        lay.addWidget(gb, 4)

        # ---- 附加内容（文件夹 / 自建文本） ----
        gb_add = QGroupBox("附加内容（可选，随包一起发布）")
        av = QVBoxLayout(gb_add)
        self.custom_list = QListWidget()
        self.custom_list.setFixedHeight(90)
        self.custom_list.setStyleSheet(
            "QListWidget{background:#fff; border:1px solid #e5e7eb;"
            "border-radius:6px; font-size:12px;}")
        self.custom_list.itemDoubleClicked.connect(self._on_edit_item)
        av.addWidget(self.custom_list)

        ab = QHBoxLayout()
        ab.setSpacing(6)
        b_f = QPushButton("添加文件夹…"); b_f.setFixedHeight(26)
        b_f.clicked.connect(self._on_add_folder)
        b_t = QPushButton("添加文本…"); b_t.setFixedHeight(26)
        b_t.clicked.connect(self._on_add_text)
        b_e = QPushButton("编辑"); b_e.setFixedHeight(26)
        b_e.clicked.connect(self._on_edit_item)
        b_d = QPushButton("删除"); b_d.setFixedHeight(26)
        b_d.clicked.connect(self._on_del_item)
        for b in (b_f, b_t, b_e, b_d):
            ab.addWidget(b)
        ab.addStretch(1)
        av.addLayout(ab)

        hint2 = QLabel("文本支持变量 {customer} {type} {version} {date}，"
                       "发布时自动替换为实际值；双击列表可编辑文本。")
        hint2.setStyleSheet("color:#6b7280; font-size:11px;")
        hint2.setWordWrap(True)
        av.addWidget(hint2)

        lay.addWidget(gb_add)
        self._refresh_custom_list()
        return w

    # ---------- 附加内容管理 ----------
    def _refresh_custom_list(self) -> None:
        self.custom_list.clear()
        for it in self.custom_items:
            QListWidgetItem(it.display(), self.custom_list)

    def _template_vars(self) -> dict:
        return {
            "customer": self.in_customer.text().strip() or "客户",
            "type": "SDK" if self.rad_sdk.isChecked() else "SDKLite",
            "version": self.in_version.text().strip() or "版本",
            "date": self.in_date.text().strip() or "日期",
        }

    def _on_add_folder(self) -> None:
        p = QFileDialog.getExistingDirectory(
            self, "选择要附加的文件夹", str(Path.home()))
        if p:
            item = CustomItem(kind=CUSTOM_FOLDER, name=Path(p).name,
                              src_path=p, target="")
            self.custom_items.append(item)
            self._refresh_custom_list()

    def _on_add_text(self) -> None:
        dlg = TextItemDialog(self, variables=self._template_vars())
        if dlg.exec() == QDialog.Accepted:
            self.custom_items.append(dlg.result_item())
            self._refresh_custom_list()

    def _on_edit_item(self) -> None:
        row = self.custom_list.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在列表中选择一条内容。")
            return
        item = self.custom_items[row]
        if item.kind == CUSTOM_TEXT:
            dlg = TextItemDialog(self, item=item, variables=self._template_vars())
            if dlg.exec() == QDialog.Accepted:
                self.custom_items[row] = dlg.result_item()
                self._refresh_custom_list()
        else:
            QMessageBox.information(
                self, "提示",
                "文件夹类附加内容不支持编辑，可删除后重新用「添加文件夹…」加入。")

    def _on_del_item(self) -> None:
        row = self.custom_list.currentRow()
        if row < 0:
            return
        self.custom_items.pop(row)
        self._refresh_custom_list()

    def _build_right(self) -> QWidget:
        """右侧参数区：放进滚动条，窗口变小时也能完整看到。"""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(430)

        inner = QWidget()
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 4, 0)
        lay.setSpacing(8)

        # ---- 发布参数 ----
        gb = QGroupBox("发布参数")
        f = QFormLayout(gb)
        f.setLabelAlignment(Qt.AlignRight)
        f.setHorizontalSpacing(10)
        f.setVerticalSpacing(8)

        self.in_customer = QLineEdit()
        f.addRow("客户名：", self.in_customer)

        tw = QWidget(); thl = QHBoxLayout(tw); thl.setContentsMargins(0, 0, 0, 0)
        self.rad_sdk = QRadioButton("SDK")
        self.rad_lite = QRadioButton("SDKLite")
        self.rad_sdk.setChecked(True)
        thl.addWidget(self.rad_sdk); thl.addWidget(self.rad_lite); thl.addStretch(1)
        f.addRow("类型：", tw)

        self.in_version = QLineEdit()
        f.addRow("版本号：", self.in_version)

        dw = QWidget(); dhl = QHBoxLayout(dw); dhl.setContentsMargins(0, 0, 0, 0)
        self.in_date = QLineEdit(datetime.now().strftime("%Y%m%d"))
        b_today = QPushButton("今天"); b_today.setFixedWidth(52)
        b_today.clicked.connect(self._set_today)
        dhl.addWidget(self.in_date, 1); dhl.addWidget(b_today)
        f.addRow("日期：", dw)
        lay.addWidget(gb)

        # ---- PDF 默认处理 ----
        gb2 = QGroupBox("PDF 处理（默认）")
        f2 = QFormLayout(gb2)
        f2.setLabelAlignment(Qt.AlignRight)
        f2.setHorizontalSpacing(10)
        f2.setVerticalSpacing(8)

        self.combo_default_mode = QComboBox()
        self.combo_default_mode.addItems(
            [PDF_MODE_LABELS[m] for m in PDF_MODE_ORDER[1:]])   # 去掉"同默认"
        self.combo_default_mode.setCurrentText(PDF_MODE_LABELS["both"])
        f2.addRow("默认方式：", self.combo_default_mode)

        self.in_wm = QLineEdit("SDK_Release_To_{customer} · {date}")
        f2.addRow("水印文本：", self.in_wm)

        row = QWidget(); rl = QHBoxLayout(row); rl.setContentsMargins(0, 0, 0, 0)
        self.in_wm_size = QLineEdit("72")
        self.in_wm_size.setFixedWidth(60)
        rl.addWidget(self.in_wm_size)
        rl.addWidget(QLabel("pt"))
        rl.addStretch(1)
        f2.addRow("文字大小：", row)

        row2 = QWidget(); r2 = QHBoxLayout(row2); r2.setContentsMargins(0, 0, 0, 0)
        self.in_wm_ang = QLineEdit("-45")
        self.in_wm_ang.setFixedWidth(60)
        r2.addWidget(self.in_wm_ang)
        r2.addWidget(QLabel("°"))
        r2.addStretch(1)
        f2.addRow("文字方向：", row2)

        self.in_wm_op = QLineEdit("0.5")
        self.in_wm_op.setFixedWidth(60)
        f2.addRow("不透明度：", self.in_wm_op)

        self.in_owner_pw = QLineEdit("KaixinOwner2026!")
        self.in_owner_pw.setToolTip(
            "所有者口令（Owner Password）：PDF 的“管理员”密码。\n"
            "知道它即可拥有全部权限：解密、修改权限、去除密码、重新加密。\n"
            "即使限制了复制/打印，持所有者口令也能绕过。程序内部用它来加密，"
            "建议固定且保密，不要发给客户。留空会自动用默认值。")
        f2.addRow("所有者口令：", self.in_owner_pw)
        self.in_user_pw = QLineEdit("")
        self.in_user_pw.setPlaceholderText("留空 = 无需密码即可查看")
        self.in_user_pw.setToolTip(
            "用户口令（User Password）：打开/查看 PDF 所需的密码。\n"
            "设置了才会真正加密——客户必须输入口令才能打开，同时受下方「权限」限制。\n"
            "注意：当前底层库在“用户口令留空”时不会生成加密，\n"
            "因此要加密分发请务必填写用户口令（可把口令随包一起发给客户）。")
        f2.addRow("用户口令：", self.in_user_pw)

        pw = QWidget(); phl = QHBoxLayout(pw); phl.setContentsMargins(0, 0, 0, 0)
        self.cb_print = QCheckBox("打印"); self.cb_print.setChecked(True)
        self.cb_copy = QCheckBox("复制")
        self.cb_modify = QCheckBox("修改")
        self.cb_acc = QCheckBox("无障碍"); self.cb_acc.setChecked(True)
        for w in (self.cb_print, self.cb_copy, self.cb_modify, self.cb_acc):
            phl.addWidget(w)
        phl.addStretch(1)
        f2.addRow("权限：", pw)
        lay.addWidget(gb2)

        # ---- 输出 ----
        gb3 = QGroupBox("输出")
        f3 = QFormLayout(gb3)
        f3.setLabelAlignment(Qt.AlignRight)
        f3.setHorizontalSpacing(10)
        f3.setVerticalSpacing(8)

        self.fmt_combo = QComboBox()
        self.fmt_combo.addItems(["ZIP（通用）", "7z（高压缩）", "TAR.GZ", "文件夹"])
        f3.addRow("格式：", self.fmt_combo)

        ow = QWidget(); ohl = QHBoxLayout(ow); ohl.setContentsMargins(0, 0, 0, 0)
        self.out_edit = QLineEdit(str(Path.home() / "Desktop" / "SDK发布"))
        b_out = QPushButton("选择…"); b_out.setFixedWidth(60)
        b_out.clicked.connect(self._on_pick_out)
        ohl.addWidget(self.out_edit, 1); ohl.addWidget(b_out)
        f3.addRow("输出目录：", ow)

        self.out_name_edit = QLineEdit("SDK_Release_To_Customer_SDK_Version_release_Date")
        self.out_name_edit.setToolTip(
            "输出文件名（不含扩展名）。手动改过之后，"
            "上方「客户/类型/版本/日期」变化时不再自动覆盖。")
        self.out_name_edit.textEdited.connect(
            lambda: setattr(self, "_name_auto", False))
        f3.addRow("文件名：", self.out_name_edit)

        self.out_name_preview = QLabel("—")
        self.out_name_preview.setWordWrap(True)
        self.out_name_preview.setStyleSheet(
            "color:#1e40af; padding:6px 10px; background:#eff6ff;"
            "border:1px solid #bfdbfe; border-radius:6px;")
        f3.addRow("将生成：", self.out_name_preview)
        lay.addWidget(gb3)

        # ---- 水印预览 ----
        gb4 = QGroupBox("PDF 首页预览")
        v = QVBoxLayout(gb4)
        self.preview = QLabel("选择资料包后显示 PDF 首页")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumHeight(320)
        self.preview.setStyleSheet(
            "color:#9ca3af; background:#f9fafb; border:1px dashed #d1d5db;"
            "border-radius:6px;")
        v.addWidget(self.preview)
        lay.addWidget(gb4)

        lay.addStretch(1)
        scroll.setWidget(inner)
        return scroll

    def _build_bottom(self) -> QVBoxLayout:
        box = QVBoxLayout()
        box.setSpacing(6)

        head = QHBoxLayout()
        self.lbl_status = QLabel("准备就绪")
        self.lbl_status.setStyleSheet("color:#374151; font-size:12px;")
        self.lbl_pct = QLabel("0%")
        self.lbl_pct.setStyleSheet(
            "color:#2563eb; font-size:12px; font-weight:600;")
        head.addWidget(self.lbl_status, 1)
        head.addWidget(self.lbl_pct)
        box.addLayout(head)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setTextVisible(False)
        box.addWidget(self.progress_bar)

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(132)
        self.log_view.setStyleSheet(
            "background:#0f172a; color:#a7f3d0; font-family:Consolas;"
            "font-size:12px; border-radius:6px; padding:4px 8px;")
        box.addWidget(self.log_view)

        act = QHBoxLayout()
        self.btn_start = QPushButton("开始发布")
        self.btn_start.setObjectName("primary")
        self.btn_start.setFixedHeight(34)
        self.btn_start.setMinimumWidth(120)
        self.btn_start.clicked.connect(self._on_start)
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setFixedHeight(34)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._on_cancel)
        self.btn_open = QPushButton("打开输出目录")
        self.btn_open.setFixedHeight(34)
        self.btn_open.clicked.connect(self._on_open_out)
        act.addStretch(1)
        act.addWidget(self.btn_start)
        act.addWidget(self.btn_cancel)
        act.addWidget(self.btn_open)
        box.addLayout(act)
        return box

    # ================= 交互 =================
    def _set_today(self):
        self.in_date.setText(datetime.now().strftime("%Y%m%d"))

    def _on_pick_source(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 SDK 资料包", str(Path.home()),
            "压缩包 (*.zip *.7z *.tar *.tgz *.tar.gz);;所有文件 (*)")
        if not path:
            path = QFileDialog.getExistingDirectory(
                self, "选择 SDK 文件夹", str(Path.home()))
        if path:
            self.src_edit.setText(path)
            self._load_source(path)

    def _on_pick_out(self):
        p = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                             self.out_edit.text())
        if p:
            self.out_edit.setText(p)

    def _load_source(self, path: str):
        p = Path(path)
        if not p.exists():
            QMessageBox.warning(self, "路径不存在", path)
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            entries = list(iter_archive_entries(p))
            self._root = build_tree(entries)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.critical(self, "读取失败", str(e))
            return
        QApplication.restoreOverrideCursor()

        typ, ver = parse_root_info(p.stem)
        self.in_version.setText(ver)
        (self.rad_sdk if typ == "SDK" else self.rad_lite).setChecked(True)

        self.tree.populate(self._root)

        allf = [n for n in self._root.walk() if not n.is_dir]
        self.lbl_summary.setText(
            f"已扫描 <b>{len(allf)}</b> 个文件 · "
            f"PDF <b>{sum(1 for n in allf if is_pdf(n.relpath))}</b> 个")
        self.lbl_summary.setTextFormat(Qt.RichText)

        self._refresh_mode_summary()
        self._render_preview(p)
        self._refresh_out_name()

    def _render_preview(self, src_path: Path):
        """读取首张 PDF 字节并渲染带水印的实时预览。"""
        if not self._root:
            return
        rel = next((n.relpath for n in self._root.walk()
                    if not n.is_dir and is_pdf(n.relpath)), None)
        if not rel:
            self.preview.setText("未发现 PDF，无法预览")
            self.preview.setPixmap(QPixmap())
            self._preview_pdf_bytes = None
            return
        try:
            with Source(src_path) as s:
                raw = s.read(rel)
            self._preview_pdf_bytes = raw
        except Exception as e:
            self.preview.setText(f"读取失败：{e}")
            self.preview.setPixmap(QPixmap())
            self._preview_pdf_bytes = None
            return
        self._render_watermarked_preview()

    def _resolve_wm_text(self) -> str:
        """把水印文本里的 {customer}/{date} 等变量换成当前值。"""
        try:
            return self.in_wm.text().format(**{
                "customer": self.in_customer.text().strip(),
                "日期": self.in_date.text().strip(),
                "date": self.in_date.text().strip(),
            })
        except Exception:
            return self.in_wm.text()

    def _render_watermarked_preview(self):
        """用当前水印参数实时渲染首张 PDF 预览（带水印）。"""
        import pymupdf   # 延迟导入：预览才需要，启动时不加载
        if not getattr(self, "_preview_pdf_bytes", None):
            return
        try:
            wm_text = self._resolve_wm_text()
            wm_size = int(self.in_wm_size.text() or "72")
            wm_ang = float(self.in_wm_ang.text() or "-45")
            wm_op = float(self.in_wm_op.text() or "0.5")
        except ValueError:
            return
        try:
            d = pymupdf.open(stream=self._preview_pdf_bytes, filetype="pdf")
            if wm_text.strip():
                from .pdfproc import apply_watermark
                apply_watermark(d, text=wm_text, fontsize=wm_size,
                                angle=wm_ang, opacity=wm_op)
            pix = d[0].get_pixmap(dpi=72)
            img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                         QImage.Format_RGB888)
            d.close()
        except Exception as e:
            self.preview.setText(f"预览失败：{e}")
            self.preview.setPixmap(QPixmap())
            return
        pm = QPixmap.fromImage(img)
        self._preview_src = pm
        self._update_preview_pixmap()

    def _on_wm_param_changed(self):
        """水印参数变更：防抖 300ms 后重渲染预览，避免逐字卡顿。"""
        if self._preview_timer is None:
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self._render_watermarked_preview)
        self._preview_timer.start(300)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if getattr(self, "_preview_src", None):
            self._update_preview_pixmap()

    def _update_preview_pixmap(self):
        pm = self._preview_src
        if not pm:
            return
        avail_h = max(180, self.preview.height() - 8)
        avail_w = max(180, self.preview.width() - 8)
        self.preview.setPixmap(pm.scaled(avail_w, avail_h, Qt.KeepAspectRatio,
                                         Qt.SmoothTransformation))
        self.preview.setText("")

    def _refresh_mode_summary(self):
        """刷新底部「各处理方式 PDF 数量」统计。"""
        if not self._root:
            self.lbl_modes.setText("")
            return
        default = self._current_default_mode()
        files = selected_files(self._root)
        c = summarize_pdf_modes(files, default)
        total = sum(c.values())
        self.lbl_modes.setText(
            f"PDF {total} 个 → 水印+加密 {c['both']} · 仅水印 {c['watermark']} · "
            f"仅加密 {c['encrypt']} · 原样 {c['none']}"
            f"（未勾选的不计）")

    def _current_default_mode(self) -> str:
        label = self.combo_default_mode.currentText()
        for k, v in PDF_MODE_LABELS.items():
            if v == label:
                return k
        return "both"

    def _on_bulk_mode(self):
        """把下拉框里的模式批量应用到所有 PDF。"""
        if not self._root:
            return
        idx = self.combo_bulk.currentIndex()
        mode = PDF_MODE_ORDER[idx]
        set_pdf_mode(self._root, mode)
        # 同步树上的下拉框显示
        self._sync_mode_combos(self._root)
        self._refresh_mode_summary()

    def _sync_mode_combos(self, root: Node):
        it = QTreeWidgetItemIterator(self.tree)
        while it.value():
            item = it.value()
            node = item.data(COL_NAME, Qt.UserRole)
            w = self.tree.itemWidget(item, COL_MODE)
            if node is not None and isinstance(w, QComboBox) and is_pdf(node.relpath):
                w.blockSignals(True)
                w.setCurrentIndex(PDF_MODE_ORDER.index(node.pdf_mode))
                w.blockSignals(False)
            it += 1

    def _refresh_out_name(self):
        customer = self.in_customer.text().strip() or "客户"
        ver = self.in_version.text().strip() or "版本"
        date = self.in_date.text().strip() or "日期"
        typ = "SDK" if self.rad_sdk.isChecked() else "SDKLite"
        ext = {"ZIP（通用）": "zip", "7z（高压缩）": "7z",
               "TAR.GZ": "tar.gz", "文件夹": ""}.get(self.fmt_combo.currentText(), "zip")
        # 默认文件名：日期前加 release，形如 SDK_Release_To_客户_SDK_版本_release_日期
        parts = [f"SDK_Release_To_{customer}", typ]
        if ver:
            parts.append(ver)
        parts += ["release", date]
        base = "_".join(parts)
        if self._name_auto:
            self.out_name_edit.setText(base)
        name = self.out_name_edit.text().strip() or base
        self.out_name_preview.setText(name + (f".{ext}" if ext else ""))

    # ---------- 运行 ----------
    def _on_start(self):
        from .pipeline import ProcessParams   # 延迟导入，加快启动
        if not self._root:
            QMessageBox.warning(self, "未选择资料包", "请先选择源资料包。")
            return
        if not self.in_customer.text().strip():
            QMessageBox.warning(self, "未填客户名", "请填写客户名。")
            return
        files = selected_files(self._root)
        if not files:
            QMessageBox.warning(self, "未勾选文件", "请至少勾选一个文件。")
            return
        try:
            wm_size = int(self.in_wm_size.text())
            wm_ang = float(self.in_wm_ang.text())
            wm_op = float(self.in_wm_op.text())
        except ValueError:
            QMessageBox.warning(self, "数值错误",
                                "文字大小 / 方向 / 不透明度 必须是数字")
            return

        fmt = {"ZIP（通用）": "zip", "7z（高压缩）": "7z",
               "TAR.GZ": "tar.gz", "文件夹": "folder"}[self.fmt_combo.currentText()]

        # Python 3 在 Windows 上对中文 keyword arg 名直接传 .format(...) 会报 KeyError，
        # 所以走 dict + ** 的方式
        wm_text = self.in_wm.text().format(**{
            "customer": self.in_customer.text().strip(),
            "日期": self.in_date.text().strip(),
            "date": self.in_date.text().strip(),
        })

        params = ProcessParams(
            source_path=self.src_edit.text(),
            output_dir=self.out_edit.text(),
            output_format=fmt,
            output_name_override=self.out_name_edit.text().strip(),
            customer=self.in_customer.text().strip(),
            sdk_type="SDK" if self.rad_sdk.isChecked() else "SDKLite",
            version=self.in_version.text().strip(),
            date_str=self.in_date.text().strip(),
            default_pdf_mode=self._current_default_mode(),
            owner_pw=self.in_owner_pw.text(),
            user_pw=self.in_user_pw.text(),
            allow_print=self.cb_print.isChecked(),
            allow_copy=self.cb_copy.isChecked(),
            allow_modify=self.cb_modify.isChecked(),
            allow_access=self.cb_acc.isChecked(),
            watermark_text=wm_text,
            watermark_opacity=wm_op,
            watermark_angle=wm_ang,
            watermark_fontsize=wm_size,
            # 复用界面上已完成勾选 / 逐文件设置的树，否则界面选择会被丢弃
            tree=self._root,
            custom_items=list(self.custom_items),
        )

        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(0)
        self.lbl_pct.setText("0%")
        self.log_view.clear()
        self._log("▶ 开始发布…")
        self._log(f"   勾选 {len(files)} 个文件，附加内容 {len(self.custom_items)} 项")

        self._worker = Worker(params)
        self._worker.signals.progress.connect(self._on_progress)
        self._worker.signals.finished.connect(self._on_finished)
        self._worker.signals.failed.connect(self._on_failed)
        self._worker.start()

    def _on_cancel(self):
        if self._worker:
            self._worker.cancel()
            self._log("⏹ 正在取消…")

    def _on_open_out(self):
        out = self.out_edit.text()
        Path(out).mkdir(parents=True, exist_ok=True)
        os.startfile(out)   # type: ignore[attr-defined]

    # ---------- 回调 ----------
    @Slot(str, float)
    def _on_progress(self, msg: str, frac: float):
        if frac >= 0:
            pct = int(min(1.0, max(0.0, frac)) * 100)
            self.progress_bar.setValue(pct)
            self.lbl_pct.setText(f"{pct}%")
        if msg:
            if msg.startswith("  "):
                self._log(msg.strip())
            else:
                self.lbl_status.setText(msg)
                self._log(msg)

    @Slot(object)
    def _on_finished(self, res: ProcessResult):
        self.progress_bar.setValue(100)
        self.lbl_pct.setText("100%")
        self.lbl_status.setText("完成")
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self._log(f"\n✅ 完成：{res.output_path}\n"
                  f"   共 {res.total_files} 个文件 / PDF {res.pdf_files} 个"
                  f"（加水印 {res.watermarked} · 加密 {res.encrypted}），"
                  f"用时 {res.elapsed:.1f}s")
        QMessageBox.information(
            self, "发布完成",
            f"输出：\n{res.output_path}\n\n"
            f"共 {res.total_files} 个文件，PDF {res.pdf_files} 个\n"
            f"加水印 {res.watermarked} 个 · 加密 {res.encrypted} 个\n"
            f"用时 {res.elapsed:.1f} 秒")

    @Slot(str)
    def _on_failed(self, msg: str):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.lbl_status.setText("失败")
        self._log(f"\n❌ 失败：{msg}")
        QMessageBox.critical(self, "失败", msg)

    def _log(self, msg: str):
        self.log_view.append(msg)


# ----------------------------------------------------------------------
# QSS 主题
# ----------------------------------------------------------------------

_QSS = """
QMainWindow { background:#f9fafb; }
QGroupBox {
    background:#ffffff; border:1px solid #e5e7eb; border-radius:8px;
    margin-top:14px; padding:12px 10px 8px 10px;
    font-weight:600; color:#1f2937;
}
QGroupBox::title {
    subcontrol-origin:margin; subcontrol-position:top left;
    padding:0 8px; left:10px; color:#2563eb;
}
QLineEdit, QComboBox {
    background:#ffffff; border:1px solid #d1d5db; border-radius:6px;
    padding:5px 8px; color:#1f2937; selection-background-color:#93c5fd;
}
QLineEdit:focus, QComboBox:focus { border-color:#2563eb; }
QPushButton {
    background:#ffffff; border:1px solid #d1d5db; border-radius:6px;
    padding:5px 14px; color:#1f2937;
}
QPushButton:hover { background:#f3f4f6; border-color:#9ca3af; }
QPushButton:pressed { background:#e5e7eb; }
QPushButton#primary {
    background:#2563eb; color:#ffffff; border:1px solid #2563eb;
    font-weight:600;
}
QPushButton#primary:hover { background:#1d4ed8; }
QPushButton#primary:disabled { background:#93c5fd; border-color:#93c5fd; }
QRadioButton, QCheckBox { color:#1f2937; padding:2px 6px; }
QProgressBar {
    background:#e5e7eb; border:0; border-radius:8px; height:16px;
}
QProgressBar::chunk { background:#2563eb; border-radius:8px; }
QStatusBar { background:#f3f4f6; color:#374151; }
QScrollArea { border:0; background:transparent; }
QScrollBar:vertical { background:#f3f4f6; width:10px; margin:0; }
QScrollBar::handle:vertical { background:#cbd5e1; border-radius:5px; min-height:24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
"""


def run_gui():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())