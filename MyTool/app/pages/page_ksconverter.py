"""Ksconverter 集成页。

Ksconverter.exe 是一个独立的 PyQt5 桌面程序（测绘/地理数据格式互转：
KML / KMZ / GPX / SHP / DBF / DXF / DWG / CSV / GeoJSON 等）。
它本身是图形界面、无可用命令行模式，因此本页不调用其内部逻辑，而是作为
“启动器 + 信息管理”集成进工具箱：

- 显示程序路径与功能说明，可一键打开所在文件夹；
- 主按钮在本机直接启动 Ksconverter（以独立进程方式运行，关闭工具箱不影响它）；
- 可选“命令行参数”输入框，供高级用户尝试（GUI 程序可能忽略）；
- 集成统一的“输出目录”（output/ksconverter/），一键打开。
"""

import subprocess
import sys
from pathlib import Path

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.output_dirs import get_feature_output_dir
from app.pages.base_page import HelpDialog
from app.paths import get_project_root
from app.utils import open_directory

HELP_TEXT = """
<h3>Ksconverter 格式转换</h3>
<p>Ksconverter 是一个独立的测绘 / 地理数据格式转换工具，可在 KML、KMZ、GPX、
SHP、DBF、DXF、DWG、CSV、GeoJSON 等格式之间互转。</p>
<p><b>使用方式：</b></p>
<ol>
  <li>点击「启动 Ksconverter」按钮，会在本机直接打开 Ksconverter 的图形界面。</li>
  <li>在 Ksconverter 自己的窗口中完成格式选择与转换操作。</li>
  <li>转换结果默认建议保存到本工具箱的「输出目录」（output/ksconverter/），
      可点击「打开输出目录」快速查看。</li>
</ol>
<p><b>说明：</b>该工具为独立程序，启动后作为单独进程运行；关闭本工具箱不会
自动关闭它。如需要停止，可在本页点击「停止」。</p>
"""


class KsconverterPage(QWidget):
    def __init__(self):
        super().__init__()
        # 功能专属输出目录键（见 app.output_dirs.FEATURE_DIRS）
        self._output_feature_key = "ksconverter"
        self._exe_path = get_project_root() / "ksconverter" / "Ksconverter.exe"
        self._work_dir = Path(get_feature_output_dir("ksconverter"))
        self._work_dir.mkdir(parents=True, exist_ok=True)
        self._proc = None  # 当前启动的子进程

        self._build_ui()
        self._refresh_status()

    # ---------- UI 构建 ----------

    def _build_ui(self):
        main = QVBoxLayout(self)
        main.setContentsMargins(16, 16, 16, 16)
        main.setSpacing(12)

        # 标题栏 + 帮助
        tl = QHBoxLayout()
        tl.setSpacing(10)
        title = QLabel("Ksconverter 格式转换")
        title.setObjectName("pageTitle")
        tl.addWidget(title)
        tl.addStretch()
        help_btn = QPushButton("?")
        help_btn.setObjectName("primaryButton")
        help_btn.setFixedSize(32, 32)
        help_btn.setToolTip("使用说明")
        help_btn.clicked.connect(self._show_help)
        tl.addWidget(help_btn)
        main.addLayout(tl)

        # 卡片：程序信息
        info_card, info_body = self._card("程序信息")
        info_body.addWidget(self._row("程序路径", self._path_widget(), stretch_path=True))
        info_body.addWidget(self._row("功能说明",
            QLabel("独立的测绘 / 地理数据格式转换工具（KML / KMZ / GPX / SHP / "
                    "DXF / DWG / CSV / GeoJSON 等互转）。转换操作在其自带图形界面中完成。")))
        main.addWidget(info_card)

        # 卡片：启动
        launch_card, launch_body = self._card("启动")
        launch_body.setSpacing(10)
        self._launch_btn = QPushButton("启动 Ksconverter")
        self._launch_btn.setObjectName("primaryButton")
        self._launch_btn.setMinimumHeight(42)
        self._launch_btn.clicked.connect(self._launch)
        launch_body.addWidget(self._launch_btn)

        btn_row = QHBoxLayout()
        self._status_label = QLabel("状态：未启动")
        self._status_label.setObjectName("statusLabel")
        self._stop_btn = QPushButton("停止")
        self._stop_btn.setObjectName("dangerButton")
        self._stop_btn.setMinimumWidth(96)
        self._stop_btn.setEnabled(False)
        self._stop_btn.clicked.connect(self._stop)
        btn_row.addWidget(self._status_label)
        btn_row.addStretch()
        btn_row.addWidget(self._stop_btn)
        launch_body.addLayout(btn_row)
        main.addWidget(launch_card)

        # 卡片：高级（可选命令行参数）
        adv_card, adv_body = self._card("高级（可选）")
        adv_body.setSpacing(10)
        self._args_edit = QLineEdit()
        self._args_edit.setPlaceholderText(
            "可选命令行参数；该工具为图形界面，参数可能无效。多个参数以空格分隔。")
        adv_body.addWidget(self._row("命令行参数", self._args_edit, stretch_path=True))
        adv_body.addWidget(self._hint(
            "提示：Ksconverter 是图形界面程序，直接点击「启动」即可；"
            "此处的参数仅在你知道对应版本支持命令行调用时才需要填写。"))
        main.addWidget(adv_card)

        # 卡片：输出目录
        out_card, out_body = self._card("输出目录")
        self._out_edit = QLineEdit()
        self._out_edit.setReadOnly(True)
        self._out_edit.setText(str(self._work_dir))
        open_out_btn = QPushButton("打开输出目录")
        open_out_btn.setObjectName("ghostButton")
        open_out_btn.clicked.connect(lambda _=False: open_directory(str(self._work_dir)))
        out_body.addWidget(self._row("输出目录", self._out_edit, extra=open_out_btn))
        out_body.addWidget(self._hint(
            "Ksconverter 在其 own 窗口中选定的保存位置为准；此处仅提供快捷打开，"
            "便于把结果统一归集到 output/ksconverter/。"))
        main.addWidget(out_card)

        main.addStretch(1)

        # 状态轮询定时器
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)

    # ---------- 辅助控件 ----------

    def _card(self, title: str):
        card = QWidget()
        card.setObjectName("card")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 11, 12, 12)
        layout.setSpacing(8)
        t = QLabel(title)
        t.setObjectName("cardTitle")
        layout.addWidget(t)
        body = QVBoxLayout()
        body.setContentsMargins(0, 4, 0, 0)
        body.setSpacing(8)
        layout.addLayout(body)
        return card, body

    def _row(self, label: str, widget: QWidget, stretch_path: bool = False, extra: QPushButton = None):
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label)
        lbl.setObjectName("fieldLabel")
        lbl.setMinimumWidth(84)
        h.addWidget(lbl)
        h.addWidget(widget, 1 if stretch_path else 0)
        if extra is not None:
            h.addWidget(extra)
        return row

    def _path_widget(self):
        w = QLineEdit()
        w.setReadOnly(True)
        w.setText(str(self._exe_path))
        self._open_exe_btn = QPushButton("打开所在文件夹")
        self._open_exe_btn.setObjectName("ghostButton")
        self._open_exe_btn.clicked.connect(
            lambda _=False: open_directory(str(self._exe_path.parent)))
        box = QWidget()
        h = QHBoxLayout(box)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(w, 1)
        h.addWidget(self._open_exe_btn)
        return box

    def _hint(self, text: str) -> QLabel:
        h = QLabel(text)
        h.setObjectName("fieldHint")
        h.setWordWrap(True)
        return h

    # ---------- 行为 ----------

    def _launch(self):
        if not self._exe_path.exists():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(
                self, "未找到程序",
                f"未能在以下位置找到 Ksconverter.exe：\n{self._exe_path}\n\n"
                "请将 Ksconverter.exe 放到工程根目录的 ksconverter/ 文件夹，或联系管理员。")
            return

        args = self._args_edit.text().strip()
        cmd = [str(self._exe_path)]
        if args:
            cmd += args.split()

        try:
            # 以独立进程方式启动：关闭工具箱不影响它继续运行
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self._work_dir),
                close_fds=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                | getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        except Exception as exc:  # noqa: BLE001
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "启动失败", f"无法启动 Ksconverter：\n{exc}")
            return

        self._set_running(True)
        self._set_status(f"已启动（PID {self._proc.pid}）", "running")
        self._timer.start()

    def _stop(self):
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                self._proc.kill()
        self._after_exit()

    def _poll(self):
        if self._proc is None:
            self._timer.stop()
            return
        rc = self._proc.poll()
        if rc is not None:
            self._after_exit(returncode=rc)

    def _after_exit(self, returncode=None):
        self._timer.stop()
        self._set_running(False)
        if returncode is not None:
            self._set_status(f"已退出（返回码 {returncode}）", "ok")
        else:
            self._set_status("已停止", "ok")
        self._proc = None

    def _set_running(self, running: bool):
        self._launch_btn.setEnabled(not running)
        self._stop_btn.setEnabled(running)

    def _set_status(self, text: str, state: str = ""):
        self._status_label.setText(f"状态：{text}")
        self._status_label.setToolTip(text)
        if self._status_label.property("state") != state:
            self._status_label.setProperty("state", state)
            self._status_label.style().unpolish(self._status_label)
            self._status_label.style().polish(self._status_label)
        win = self.window()
        bar = getattr(win, "statusBar", None)
        if callable(bar):
            try:
                bar().showMessage(f"Ksconverter：{text}")
            except Exception:  # noqa: BLE001
                pass

    def _refresh_status(self):
        if self._exe_path.exists():
            self._set_status("未启动", "")
            self._open_exe_btn.setEnabled(True)
        else:
            self._set_status("未找到程序（ksconverter/Ksconverter.exe）", "error")
            self._launch_btn.setEnabled(False)
            self._open_exe_btn.setEnabled(False)

    def _show_help(self):
        HelpDialog("Ksconverter 格式转换", HELP_TEXT, self).exec()
