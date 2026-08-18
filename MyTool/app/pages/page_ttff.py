"""TTFF 分析（统一入口）：合并 NMEA 文本日志分析与 BPDEBUG 二进制捕获分析。

本页把原先两个独立功能（NMEA 首次定位 TTFF 分析、BPDEBUG 冷启动捕获分析）合并为
**一个功能入口**：用户添加日志文件（可多选/文件夹），工具按内容自动识别每个文件是
NMEA 文本还是 BPDEBUG 二进制，分别路由到对应引擎，最终生成**一份**合并 HTML 报告
（merged_ttff_report.html），通过标签页切换两个板块。

同时保留原 NMEA 页的「加载/保存配置」能力，配置文件沿用 ttff_tool/ttff_config.json。
"""

import json
from pathlib import Path
from typing import Any

from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QCheckBox,
    QVBoxLayout,
    QWidget,
)

from app.core import ttff_unified
from app.pages.base_page import BasePage
from app.paths import get_project_root
from app.utils import make_file_selector

_LOG_SUFFIXES = (".log", ".txt", ".nmea")
DEFAULT_CONFIG = get_project_root() / "ttff_tool" / "ttff_config.json"


class TTFFPage(BasePage):
    HELP_TEXT = """
    <h3>功能说明</h3>
    <p>本功能将两类 GNSS 日志的 TTFF 相关分析<b>合并为一个入口</b>，并输出<b>一份合并报告</b>：</p>
    <ul>
      <li><b>TTFF 分析（必出）</b>：无论输入是纯 NMEA 文本还是 BPDEBUG 二进制，都统计每次复位后到首次有效定位的耗时（BPDEBUG 内嵌的 NMEA 同样可解析）。</li>
      <li><b>BPDEBUG 搜星情况（可选）</b>：仅当输入含 BPDEBUG 文件且勾选「输出 BPDEBUG 报告」时，额外分析冷启动上星/捕获速度（在视星数、星历有效、参与解算、可参与位置解）。需配套 bpdebug_track_dump.exe + ProtocolDecoder.dll。</li>
    </ul>
    <h3>自动识别</h3>
    <p>添加文件后，工具会按内容自动判断格式（含同步头即 BPDEBUG，含 NMEA 语句即 NMEA），并在每行右侧显示格式徽章。<b>复位标志对 NMEA 与 BPDEBUG 均生效</b>（BPDEBUG 内嵌 NMEA 同样可算 TTFF）；BPDEBUG 搜星报告由「冷启动复位码后缀」检测冷启动，并受「输出 BPDEBUG 报告」开关控制。</p>
    <h3>使用方法</h3>
    <ol>
      <li>「添加文件」（可多选）或「添加文件夹」（自动纳入其下日志文件）。</li>
      <li>为每条文件填写对应的复位标志（如 $PSTMCOLD*1E、$KMDOK,KMDRST,h3F*4C；NMEA 与 BPDEBUG 通用）。</li>
      <li>（可选）设置默认日期、冷启动后缀、CN0 阈值、最大循环数；勾选是否输出 BPDEBUG 搜星报告。</li>
      <li>点击「运行」，完成后底部「结果预览」会渲染<b>合并 HTML 报告</b>（⏳ TTFF 分析必现；📡 BPDEBUG 搜星情况仅在勾选且含 BPDEBUG 文件时出现）。</li>
      <li>（可选）「加载配置 / 保存配置」可复用 <code>ttff_tool/ttff_config.json</code> 中的文件列表与设置。</li>
    </ol>
    """

    def __init__(self, parent=None):
        self._output_feature_key = "ttff_merged"
        self._file_rows: list[dict] = []
        self._files_layout = None
        super().__init__("TTFF 分析", parent)

    # ---------- 构建界面 ----------

    def build_form(self):
        op_layout = QHBoxLayout()
        op_layout.setSpacing(8)
        add_files_btn = QPushButton("添加文件…")
        add_files_btn.setObjectName("primaryButton")
        add_files_btn.clicked.connect(self._on_add_files)
        add_folder_btn = QPushButton("添加文件夹…")
        add_folder_btn.clicked.connect(self._on_add_folder)
        load_btn = QPushButton("加载配置")
        load_btn.setObjectName("ghostButton")
        load_btn.clicked.connect(self._on_load_config)
        save_btn = QPushButton("保存配置")
        save_btn.setObjectName("ghostButton")
        save_btn.clicked.connect(self._on_save_config)
        op_layout.addWidget(add_files_btn)
        op_layout.addWidget(add_folder_btn)
        op_layout.addStretch()
        op_layout.addWidget(load_btn)
        op_layout.addWidget(save_btn)
        self._config_layout.addLayout(op_layout)

        cfg_row = QWidget()
        cfg_h = QHBoxLayout(cfg_row)
        cfg_h.setContentsMargins(0, 0, 0, 0)
        cfg_label = QLabel("配置文件:")
        cfg_label.setObjectName("fieldLabel")
        cfg_label.setMinimumWidth(90)
        self._cfg_path = QLineEdit(str(DEFAULT_CONFIG))
        self._cfg_path.setReadOnly(True)
        self._cfg_path.setObjectName("configPathEdit")
        cfg_h.addWidget(cfg_label)
        cfg_h.addWidget(self._cfg_path, 1)
        self._config_layout.addWidget(cfg_row)

        hint = QLabel("支持同时加入 NMEA 文本日志与 BPDEBUG 二进制日志，工具会自动识别格式。")
        hint.setObjectName("fieldHint")
        self._config_layout.addWidget(hint)

        self._files_container = QWidget()
        self._files_layout = QVBoxLayout(self._files_container)
        self._files_layout.setSpacing(10)
        self._files_layout.setContentsMargins(0, 0, 0, 0)
        self._config_layout.addWidget(self._files_container)

        add_more = QPushButton("＋ 添加输入文件")
        add_more.setObjectName("primaryButton")
        add_more.clicked.connect(lambda _=False: self._add_file_row({}))
        self._config_layout.addWidget(add_more)

        settings_form = self.add_form_layout()
        self._def_date = QLineEdit("040826")
        settings_form.addRow("默认日期(NMEA):", self._def_date)
        self._nmea_html = QLineEdit("TTFF统计报告.html")
        settings_form.addRow("NMEA 报告名:", self._nmea_html)
        self._nmea_json = QLineEdit("ttff_results.json")
        settings_form.addRow("NMEA 明细名:", self._nmea_json)
        self._cold_suffix = QLineEdit("13F")
        settings_form.addRow("冷启动后缀(BPDEBUG):", self._cold_suffix)
        self._cn0_min = QLineEdit("0.0")
        settings_form.addRow("CN0 阈值(dB-Hz):", self._cn0_min)
        self._max_cycles = QLineEdit("")
        self._max_cycles.setPlaceholderText("留空=全量；填数字=每文件限次（预览）")
        settings_form.addRow("最大循环数:", self._max_cycles)
        self._skip_track = QCheckBox("跳过 PVT/星历曲线（仅 RawObs，不需 DLL）")
        settings_form.addRow(self._skip_track)
        self._inc_bpdebug = QCheckBox("输出 BPDEBUG 搜星情况报告（仅 BPDEBUG 文件；不勾选则只做 TTFF）")
        self._inc_bpdebug.setChecked(True)
        settings_form.addRow(self._inc_bpdebug)
        self._out_dir = QLineEdit()
        self._out_dir.setPlaceholderText("默认：output/ttff_merged/")
        # 输出目录：支持手动输入，也可点浏览选取目录
        out_row = QWidget()
        out_h = QHBoxLayout(out_row)
        out_h.setContentsMargins(0, 0, 0, 0)
        out_h.setSpacing(8)
        out_btn = make_file_selector(self, self._out_dir, "选择输出目录", "", directory=True)
        out_btn.setToolTip("选择输出目录")
        out_h.addWidget(self._out_dir, 1)
        out_h.addWidget(out_btn)
        settings_form.addRow("输出目录:", out_row)

        self._add_file_row({})

    # ---------- 文件行 ----------

    def _add_file_row(self, spec: dict):
        file_path = spec.get("file", "")
        marker = spec.get("reset_marker", "")
        name = spec.get("name", "")
        note = spec.get("note", "")

        card = QWidget()
        card.setObjectName("card")
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        # 路径 + 格式徽章 + 浏览/删除
        h1 = QHBoxLayout()
        path_label = QLabel(file_path or "（未选择文件）")
        path_label.setObjectName("filePathLabel")
        path_label.setWordWrap(True)
        fmt_label = QLabel("")
        fmt_label.setObjectName("fieldHint")
        fmt_label.setFixedWidth(74)
        browse_btn = QPushButton("浏览")
        browse_btn.setObjectName("ghostButton")
        browse_btn.clicked.connect(lambda _=False, c=card: self._on_browse(c))
        del_btn = QPushButton("删除")
        del_btn.setObjectName("ghostButton")
        del_btn.clicked.connect(lambda _=False, c=card: self._on_remove_row(c))
        h1.addWidget(path_label, 1)
        h1.addWidget(fmt_label)
        h1.addWidget(browse_btn)
        h1.addWidget(del_btn)
        v.addLayout(h1)

        # 复位标志 / 名称
        h2 = QHBoxLayout()
        m_label = QLabel("复位标志:")
        m_label.setObjectName("fieldLabel")
        m_label.setMinimumWidth(96)
        marker_edit = QLineEdit(marker or "$RESET")
        marker_edit.setPlaceholderText("如 $PSTMCOLD*1E（NMEA/BPDEBUG 通用）")
        n_label = QLabel("名称:")
        n_label.setObjectName("fieldLabel")
        n_label.setMinimumWidth(48)
        name_edit = QLineEdit(name)
        name_edit.setPlaceholderText("报告中的显示名（缺省取文件名）")
        h2.addWidget(m_label)
        h2.addWidget(marker_edit, 3)
        h2.addWidget(n_label)
        h2.addWidget(name_edit, 2)
        v.addLayout(h2)

        # 备注
        h3 = QHBoxLayout()
        nt_label = QLabel("备注:")
        nt_label.setObjectName("fieldLabel")
        nt_label.setMinimumWidth(72)
        note_edit = QLineEdit(note)
        note_edit.setPlaceholderText("可选")
        h3.addWidget(nt_label)
        h3.addWidget(note_edit, 1)
        v.addLayout(h3)

        self._files_layout.addWidget(card)
        self._file_rows.append({
            "widget": card,
            "path_label": path_label,
            "fmt_label": fmt_label,
            "marker_edit": marker_edit,
            "name_edit": name_edit,
            "note_edit": note_edit,
        })
        # 已有路径则立即识别格式
        if file_path:
            self._update_fmt(card, file_path)

    def _update_fmt(self, card: QWidget, path: str):
        row = next((r for r in self._file_rows if r["widget"] is card), None)
        if not row:
            return
        if not Path(path).exists():
            row["fmt_label"].setText("未找到")
            return
        fmt = ttff_unified.detect_format(path)
        if fmt == "nmea":
            row["fmt_label"].setText("● NMEA")
            row["fmt_label"].setStyleSheet("color:#1b8a3a;")
        elif fmt == "bpdebug":
            row["fmt_label"].setText("● BPDEBUG")
            row["fmt_label"].setStyleSheet("color:#1b6ef3;")
        else:
            row["fmt_label"].setText("? 未知")
            row["fmt_label"].setStyleSheet("color:#c0392b;")

    def _on_browse(self, card: QWidget):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择日志文件", "",
            "日志文件 (*.log *.txt *.nmea);;所有文件 (*.*)")
        if not path:
            return
        for row in self._file_rows:
            if row["widget"] is card:
                row["path_label"].setText(path)
                if not row["name_edit"].text().strip():
                    row["name_edit"].setText(Path(path).stem)
                self._update_fmt(card, path)
                break

    def _on_remove_row(self, card: QWidget):
        for i, row in enumerate(self._file_rows):
            if row["widget"] is card:
                self._file_rows.pop(i)
                card.deleteLater()
                break

    def _on_add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择日志文件（可多选）", "",
            "日志文件 (*.log *.txt *.nmea);;所有文件 (*.*)")
        for p in paths:
            self._add_file_row({"file": p})

    def _on_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "选择包含日志的文件夹")
        if not folder:
            return
        files = sorted(
            str(p) for p in Path(folder).rglob("*")
            if p.is_file() and p.suffix.lower() in _LOG_SUFFIXES
        )
        if not files:
            self._append_log(f"[提示] 文件夹下未找到日志文件：{folder}")
            return
        for f in files:
            self._add_file_row({"file": f})

    # ---------- 配置读写（兼容原 NMEA 配置与合并配置两种格式） ----------

    def _collect_specs(self) -> list[dict]:
        specs = []
        for row in self._file_rows:
            file_path = row["path_label"].text().strip()
            if not file_path or file_path == "（未选择文件）":
                continue
            specs.append({
                "file": file_path,
                "reset_marker": row["marker_edit"].text().strip() or "$RESET",
                "name": row["name_edit"].text().strip(),
                "note": row["note_edit"].text().strip(),
            })
        return specs

    def _load_config_into_ui(self, path: str):
        with open(path, "r", encoding="utf-8") as fh:
            cfg = json.load(fh)
        files_cfg = cfg.get("files", [])
        settings = cfg.get("settings", {})
        # 清空现有行
        for row in list(self._file_rows):
            row["widget"].deleteLater()
        self._file_rows.clear()
        for f in files_cfg:
            self._add_file_row(f)
        if not files_cfg:
            self._add_file_row({})
        # 回填设置（兼容旧 NMEA 配置与合并配置两种格式）
        self._def_date.setText(settings.get("default_date") or "040826")
        self._nmea_html.setText(
            settings.get("nmea_output_html")
            or settings.get("output_html")
            or "TTFF统计报告.html")
        self._nmea_json.setText(
            settings.get("nmea_output_json")
            or settings.get("output_json")
            or "ttff_results.json")
        self._cold_suffix.setText(settings.get("cold_suffix") or "13F")
        self._cn0_min.setText(str(settings.get("cn0_min", 0.0)))
        max_cycles = settings.get("max_cycles")
        self._max_cycles.setText("" if max_cycles in (None, "") else str(max_cycles))
        self._skip_track.setChecked(bool(settings.get("skip_track", False)))
        self._inc_bpdebug.setChecked(bool(settings.get("include_bpdebug_report", True)))
        self._cfg_path.setText(path)

    def _on_load_config(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "加载 TTFF 配置", str(DEFAULT_CONFIG), "JSON 配置 (*.json)")
        if not path:
            return
        try:
            self._load_config_into_ui(path)
            self._append_log(f"[信息] 已加载配置：{path}")
        except Exception as exc:
            self._append_log(f"[错误] 配置读取失败：{exc}")

    def _on_save_config(self):
        files = self._collect_specs()
        if not files:
            self._append_log("[错误] 没有可保存的文件（请先添加文件）")
            return
        settings = self.get_params()["settings"]
        path = self._cfg_path.text().strip() or str(DEFAULT_CONFIG)
        cfg = {"settings": settings, "files": files}
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, ensure_ascii=False, indent=2)
            self._append_log(f"[信息] 配置已保存到：{path}")
        except Exception as exc:
            self._append_log(f"[错误] 配置保存失败：{exc}")

    # ---------- 参数 ----------

    def get_params(self) -> dict[str, Any]:
        max_cycles_txt = self._max_cycles.text().strip()
        max_cycles = int(max_cycles_txt) if max_cycles_txt.isdigit() else None
        files = self._collect_specs()
        settings = {
            "default_date": self._def_date.text().strip() or "040826",
            "nmea_output_html": self._nmea_html.text().strip() or "TTFF统计报告.html",
            "nmea_output_json": self._nmea_json.text().strip() or "ttff_results.json",
            "cold_suffix": self._cold_suffix.text().strip() or "13F",
            "cn0_min": float(self._cn0_min.text().strip() or "0.0"),
            "max_cycles": max_cycles,
            "skip_track": self._skip_track.isChecked(),
            "include_bpdebug_report": self._inc_bpdebug.isChecked(),
        }
        return {
            "files": files,
            "settings": settings,
            "output_dir": self._out_dir.text().strip(),
        }

    def validate_params(self, params: dict[str, Any]) -> bool:
        if not params.get("files"):
            self._append_log("[错误] 请至少添加一个有效的输入文件")
            return False
        missing = [f["file"] for f in params["files"] if not Path(f["file"]).exists()]
        if missing:
            self._append_log(f"[错误] 以下文件不存在：{', '.join(missing)}")
            return False
        return True

    def run_task(self, params: dict[str, Any], log_callback):
        return ttff_unified.run_unified(
            params["files"],
            params["settings"],
            output_dir=params["output_dir"],
            log_callback=log_callback,
        )

    def on_result_ready(self, result: str):
        if result and Path(result).exists():
            self.show_result_preview_html(result)
