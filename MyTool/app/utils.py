import os
import sys
import webbrowser
from pathlib import Path
from typing import Callable, Optional

from PyQt6.QtCore import QObject, QEvent, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)


# 跨会话记住“上次使用的目录”
_SETTINGS = None


def _settings() -> "QSettings":
    global _SETTINGS
    if _SETTINGS is None:
        from PyQt6.QtCore import QSettings

        _SETTINGS = QSettings("GNSS_ToolBox", "Paths")
    return _SETTINGS


def last_directory() -> str:
    """返回上次使用的目录，未记录时回退到用户主目录。"""
    val = _settings().value("last_directory")
    if val and Path(val).exists():
        return str(val)
    return str(Path.home())


def remember_directory(path: str):
    """记录某个路径所在的目录，供下次文件对话框使用。"""
    p = Path(path)
    if p.is_file():
        p = p.parent
    if p.exists():
        _settings().setValue("last_directory", str(p))


def open_directory(path: str):
    """使用系统默认方式打开目录。"""
    if not path:
        return
    target = Path(path)
    if target.is_file():
        target = target.parent
    if not target.exists():
        return
    os.startfile(str(target))


class _DropFilter(QObject):
    """事件过滤器：让任意 widget 接受文件/文件夹拖放并写入 line_edit。"""

    def __init__(self, line_edit: QLineEdit, is_dir: bool, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._line_edit = line_edit
        self._is_dir = is_dir

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.DragEnter:
            if event.mimeData().hasUrls():
                event.acceptProposedAction()
            return True
        if event.type() == QEvent.Type.Drop:
            urls = event.mimeData().urls()
            if urls:
                dropped = urls[0].toLocalFile()
                # 文件夹型输入只接受目录；文件型输入接受文件（拖入目录时取其本身）
                if self._is_dir and Path(dropped).is_dir():
                    self._line_edit.setText(dropped)
                    remember_directory(dropped)
                elif not self._is_dir:
                    self._line_edit.setText(dropped)
                    remember_directory(dropped)
            event.acceptProposedAction()
            return True
        return False


def enable_drop_target(widget: QWidget, line_edit: QLineEdit, is_dir: bool = False):
    """让 widget 可作为拖放目标，拖入的文件/文件夹写入 line_edit。"""
    widget.setAcceptDrops(True)
    widget.installEventFilter(_DropFilter(line_edit, is_dir, widget))


def make_file_selector(
    parent: QWidget,
    line_edit: QLineEdit,
    title: str = "选择文件",
    filter_str: str = "所有文件 (*.*)",
    directory: bool = False,
) -> QPushButton:
    """创建一个按钮，点击后选择文件/文件夹并写入 line_edit。"""

    def on_click():
        current = line_edit.text().strip()
        if current and Path(current).exists():
            start_dir = str(Path(current).parent)
        else:
            start_dir = last_directory()
        if directory:
            path = QFileDialog.getExistingDirectory(parent, title, start_dir)
        else:
            path, _ = QFileDialog.getOpenFileName(parent, title, start_dir, filter_str)
        if path:
            line_edit.setText(path)
            remember_directory(path)

    btn = QPushButton("浏览...")
    btn.clicked.connect(on_click)
    return btn


def make_path_row(
    parent: QWidget,
    label: str,
    directory: bool = False,
    title: str = "选择",
    filter_str: str = "所有文件 (*.*)",
    placeholder: str = "",
) -> tuple[QLineEdit, QPushButton]:
    """创建标签 + 输入框 + 浏览按钮 一行。返回 (line_edit, button)。"""
    layout = QHBoxLayout()
    layout.setContentsMargins(0, 0, 0, 0)
    lbl = QLabel(label)
    lbl.setMinimumWidth(80)
    line = QLineEdit()
    line.setPlaceholderText(placeholder)
    btn = make_file_selector(parent, line, title, filter_str, directory)
    layout.addWidget(lbl)
    layout.addWidget(line, 1)
    layout.addWidget(btn)
    container = QWidget()
    container.setLayout(layout)
    parent.layout().addWidget(container)
    return line, btn


def show_error(parent: QWidget, message: str):
    QMessageBox.critical(parent, "错误", message)


def show_info(parent: QWidget, message: str):
    QMessageBox.information(parent, "提示", message)


class LogStream(QObject):
    """将 Python print 输出重定向到 UI 日志框的流对象。"""

    written = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._buffer = ""

    def write(self, text: str):
        if text:
            self._buffer += text
            if "\n" in self._buffer:
                lines = self._buffer.split("\n")
                for line in lines[:-1]:
                    self.written.emit(line)
                self._buffer = lines[-1]

    def flush(self):
        if self._buffer:
            self.written.emit(self._buffer)
            self._buffer = ""

    def isatty(self) -> bool:
        return False
