import socket
import sys
import webbrowser
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QProcess, QProcessEnvironment, QTimer, QUrl, pyqtSignal

# WebEngine 在当前环境可能不可用（如渲染子进程无法拉起），不可用时降级而非崩溃。
try:
    from PyQt6.QtWebEngineCore import QWebEnginePage
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    WEBENGINE_AVAILABLE = True
except Exception:
    QWebEnginePage = None  # type: ignore
    QWebEngineView = None  # type: ignore
    WEBENGINE_AVAILABLE = False

from PyQt6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from app.paths import get_project_root


def get_streamlit_program() -> str:
    """返回用于启动 Streamlit 的可执行文件路径。"""
    # 打包后复用主 exe 自身作为 Streamlit 启动器（通过 --streamlit-runner 参数）
    return sys.executable


def build_streamlit_args(script: Path, port: int) -> list[str]:
    """根据是否打包构造 streamlit 启动参数。"""
    base_args = [
        "run", str(script),
        "--server.headless", "true",
        "--server.port", str(port),
        "--browser.gatherUsageStats", "false",
        "--server.address", "127.0.0.1",
        "--server.enableXsrfProtection", "false",
        "--server.enableCORS", "false",
        "--server.fileWatcherType", "none",
        "--server.runOnSave", "false",
        "--global.developmentMode", "false",
        "--logger.level", "debug",
    ]
    if getattr(sys, "frozen", False):
        # 打包后使用主 exe 的 --streamlit-runner 模式
        return ["--streamlit-runner"] + base_args
    else:
        # 源码运行时使用 python -m streamlit
        return ["-m", "streamlit"] + base_args


if WEBENGINE_AVAILABLE:

    class StreamlitWebPage(QWebEnginePage):
        """自定义 WebEngine 页面，用于正确处理 <input type="file"> 的文件选择对话框。"""

        def chooseFiles(self, mode, oldFiles, acceptedMimeTypes):
            parent = self.parent()
            if mode == QWebEnginePage.FileSelectionMode.FileSelectOpenMultiple:
                files, _ = QFileDialog.getOpenFileNames(
                    parent, "选择文件", str(Path.home()), "所有文件 (*.*)"
                )
                return files
            else:
                file, _ = QFileDialog.getOpenFileName(
                    parent, "选择文件", str(Path.home()), "所有文件 (*.*)"
                )
                return [file] if file else []

        def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
            # 仅记录错误级日志，便于排查"加载失败"（用户点"显示启动日志"可见）
            if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
                parent = self.parent()
                if isinstance(parent, StreamlitProcess):
                    parent._log(f"[JS错误] {message} ({sourceID}:{lineNumber})")
            # 其他级别不记录，避免刷屏

else:

    class StreamlitWebPage:  # WebEngine 不可用时的占位类
        pass


class StreamlitProcess(QWidget):
    """管理 Streamlit 子进程并在 QWebEngineView 中展示。"""

    loaded = pyqtSignal()
    load_failed = pyqtSignal(str)

    def __init__(
        self,
        script_path: str,
        port: int = 8501,
        title: str = "Streamlit",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.script_path = script_path
        self.port = port
        self.title = title
        self._process: Optional[QProcess] = None
        self._wait_timer: Optional[QTimer] = None
        self._wait_attempts = 0
        self._max_attempts = 60  # 最多等待 30 秒（Streamlit 冷启动可能较慢）
        self._retry_count = 0    # 页面加载失败后的自动重试计数
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        # 工具栏
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        self._start_btn = QPushButton("▶ 启动")
        self._start_btn.setObjectName("primaryButton")
        self._start_btn.clicked.connect(self.start)
        self._stop_btn = QPushButton("⏹ 停止")
        self._stop_btn.setObjectName("dangerButton")
        self._stop_btn.clicked.connect(self.stop)
        self._reload_btn = QPushButton("🔄 刷新")
        self._reload_btn.clicked.connect(self._reload)
        self._reload_btn.setEnabled(False)
        self._browser_btn = QPushButton("🌐 在浏览器中打开")
        self._browser_btn.clicked.connect(self.open_in_browser)
        self._browser_btn.setEnabled(False)
        toolbar.addWidget(self._start_btn)
        toolbar.addWidget(self._stop_btn)
        toolbar.addWidget(self._reload_btn)
        toolbar.addStretch()
        toolbar.addWidget(self._browser_btn)
        layout.addLayout(toolbar)

        # 状态/进度
        self._status_label = QLabel("状态: 等待启动")
        self._status_label.setStyleSheet("color: #6B7280; padding: 2px 4px;")
        layout.addWidget(self._status_label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)
        self._progress.setVisible(False)
        layout.addWidget(self._progress)

        # 浏览器视图（占据主要空间）；WebEngine 不可用时降级为提示
        if WEBENGINE_AVAILABLE:
            self._webview = QWebEngineView()
            self._webview.setMinimumHeight(520)
            self._webview.setSizePolicy(
                self._webview.sizePolicy().Policy.Expanding,
                self._webview.sizePolicy().Policy.Expanding,
            )
            self._webview.setPage(StreamlitWebPage(self._webview))
            self._webview.loadStarted.connect(self._on_load_started)
            self._webview.loadProgress.connect(self._on_load_progress)
            self._webview.loadFinished.connect(self._on_load_finished)
            self._webview.renderProcessTerminated.connect(self._on_render_terminated)
            layout.addWidget(self._webview, 1)
        else:
            self._webview = None
            self._status_label.setText("状态: WebEngine 不可用")
            warn = QLabel(
                "⚠️ 当前环境未启用 WebEngine（内嵌浏览器无法加载），绘图页暂不可用。\n"
                "其它功能（TTFF 分析、各项转换等）不受影响。\n"
                "如需使用绘图页，请确认 PyQt6 的 WebEngine 组件已正确安装。"
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color:#b45309; padding:16px; font-size:13px;")
            layout.addWidget(warn, 1)

        # 日志区（启动时默认折叠，节省空间）
        self._log_edit = QTextEdit()
        self._log_edit.setReadOnly(True)
        self._log_edit.setMaximumHeight(120)
        self._log_edit.setVisible(False)
        layout.addWidget(self._log_edit)

        self._toggle_log_btn = QPushButton("显示启动日志")
        self._toggle_log_btn.setCheckable(True)
        self._toggle_log_btn.setChecked(False)
        self._toggle_log_btn.clicked.connect(self._on_toggle_log)
        layout.addWidget(self._toggle_log_btn)

    def _on_toggle_log(self):
        checked = self._toggle_log_btn.isChecked()
        self._log_edit.setVisible(checked)
        self._toggle_log_btn.setText("隐藏启动日志" if checked else "显示启动日志")

    def _log(self, text: str):
        self._log_edit.append(text)

    def _set_status(self, text: str):
        self._status_label.setText(f"状态: {text}")

    def _on_load_started(self):
        self._set_status("页面加载中...")
        self._log(f"[页面] 开始加载 http://127.0.0.1:{self.port}")

    def _on_load_progress(self, progress: int):
        self._set_status(f"页面加载中... {progress}%")
        self._log(f"[页面] 加载进度 {progress}%")

    def _on_load_finished(self, success: bool):
        if success:
            self._retry_count = 0
            self._set_status("页面加载完成")
            self._log("[页面] Streamlit 页面加载完成")
            self._progress.setVisible(False)
            self._browser_btn.setEnabled(True)
            self._reload_btn.setEnabled(True)
            self.loaded.emit()
            return

        # 加载失败：自动重试最多 3 次，规避 Chromium 本地加载的偶发失败
        self._retry_count += 1
        if self._retry_count < 3:
            self._set_status(f"页面加载失败，正在重试 ({self._retry_count}/3)...")
            self._log(f"[重试] 第 {self._retry_count} 次重新加载页面 (http://127.0.0.1:{self.port})")
            if self._webview is not None:
                QTimer.singleShot(1000, self._webview.reload)
            return

        self._set_status("页面加载失败")
        self._log("[页面] Streamlit 页面加载失败（已重试 3 次）")
        self.load_failed.emit("Streamlit 页面加载失败")
        self._progress.setVisible(False)
        self._start_btn.setEnabled(True)

    def _on_render_terminated(self, status, exit_code: int):
        # 渲染进程崩溃（GPU/共享内存等）会直接导致页面加载失败
        self._log(f"[WebEngine] 渲染进程终止: status={status}, exit_code={exit_code}")
        self._log("[WebEngine] 若页面无法加载，多半是 Chromium 渲染进程崩溃，已通过启动参数规避，请重试")

    def _reload(self):
        self._retry_count = 0
        if self._webview is not None:
            self._webview.reload()

    def _resolve_port(self, preferred: int) -> int:
        """从 preferred 起查找第一个空闲端口，避免与残留 Streamlit 子进程冲突导致启动失败。"""
        for p in range(preferred, preferred + 100):
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.2)
                s.bind(("127.0.0.1", p))
                s.close()
                return p
            except OSError:
                continue
        # 兜底：交给操作系统分配任意空闲端口
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def start(self):
        if self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning:
            return

        self._retry_count = 0
        script = Path(self.script_path)
        if not script.exists():
            self.load_failed.emit(f"Streamlit 脚本不存在: {self.script_path}")
            return

        # 解析实际可用端口，避免与残留 Streamlit 子进程冲突导致启动失败
        self.port = self._resolve_port(self.port)

        python_exe = get_streamlit_program()
        args = build_streamlit_args(script, self.port)

        self._log_edit.clear()
        self._log(f"启动: {python_exe} {' '.join(args)}")
        self._set_status("正在启动 Streamlit 服务...")
        self._progress.setVisible(True)
        self._start_btn.setEnabled(False)
        self._browser_btn.setEnabled(False)
        self._reload_btn.setEnabled(False)

        self._process = QProcess(self)
        self._process.setProgram(python_exe)
        self._process.setArguments(args)
        # 通过环境变量通知子进程进入 Streamlit 启动模式（避免窗口程序丢失命令行参数）
        env = QProcessEnvironment.systemEnvironment()
        env.insert("GNSS_STREAMLIT_RUNNER", "1")
        self._process.setProcessEnvironment(env)
        self._process.readyReadStandardOutput.connect(self._on_stdout)
        self._process.readyReadStandardError.connect(self._on_stderr)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error_occurred)
        self._process.start()
        self._log(f"[进程] 已启动 PID={self._process.processId()}")

        # 使用 QTimer 异步轮询端口，避免阻塞 Qt 事件循环
        self._wait_attempts = 0
        self._wait_timer = QTimer(self)
        self._wait_timer.timeout.connect(self._check_port)
        self._wait_timer.start(500)

    def is_running(self) -> bool:
        return self._process is not None and self._process.state() != QProcess.ProcessState.NotRunning

    def _check_port(self):
        if self._process is None:
            self._stop_waiting("Streamlit 进程未创建")
            return

        if self._process.state() == QProcess.ProcessState.NotRunning:
            self._stop_waiting("Streamlit 进程意外退出")
            return

        self._wait_attempts += 1
        if self._is_port_open():
            self._set_status("端口已开放，正在加载页面...")
            # embed=true 隐藏 Streamlit 顶部工具栏，留出更多可操作区域
            if self._webview is not None:
                self._webview.load(QUrl(f"http://127.0.0.1:{self.port}/?embed=true"))
            self._stop_waiting()
            return

        self._set_status(f"等待 Streamlit 启动... ({self._wait_attempts}/{self._max_attempts})")

        if self._wait_attempts >= self._max_attempts:
            self._stop_waiting("Streamlit 启动超时，请检查日志")

    def _stop_waiting(self, error_message: Optional[str] = None):
        if self._wait_timer is not None:
            self._wait_timer.stop()
            self._wait_timer.deleteLater()
            self._wait_timer = None
        if error_message:
            self._set_status(error_message)
            self.load_failed.emit(error_message)
            self._progress.setVisible(False)
            self._start_btn.setEnabled(True)

    def _is_port_open(self) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=0.3):
                return True
        except (OSError, ConnectionRefusedError):
            return False

    def _on_stdout(self):
        if self._process:
            data = self._process.readAllStandardOutput().data().decode("utf-8", errors="ignore")
            self._log(data)

    def _on_stderr(self):
        if self._process:
            data = self._process.readAllStandardError().data().decode("utf-8", errors="ignore")
            self._log(data)

    def _on_finished(self, exit_code: int, exit_status: QProcess.ExitStatus):
        self._set_status(f"Streamlit 进程已结束 (exit_code={exit_code}, status={exit_status})")
        self._start_btn.setEnabled(True)
        self._browser_btn.setEnabled(False)
        self._reload_btn.setEnabled(False)
        self._progress.setVisible(False)
        self._log(f"[信息] Streamlit 进程已结束，退出码={exit_code}，状态={exit_status}")
        self._drain_output()
        self._stop_waiting()

    def _on_error_occurred(self, error: QProcess.ProcessError):
        self._set_status(f"进程错误: {error}")
        self._log(f"[进程错误] {error}")
        self._drain_output()

    def _drain_output(self):
        if self._process is None:
            return
        stdout = self._process.readAllStandardOutput().data().decode("utf-8", errors="ignore")
        stderr = self._process.readAllStandardError().data().decode("utf-8", errors="ignore")
        if stdout:
            self._log(f"[残留 stdout]\n{stdout}")
        if stderr:
            self._log(f"[残留 stderr]\n{stderr}")

    def stop(self, force: bool = False):
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            if force:
                # 强制结束：关闭窗口时直接使用 kill，避免 waitForFinished 阻塞主线程造成卡顿
                self._process.kill()
                self._process.waitForFinished(500)
            else:
                self._process.terminate()
                if not self._process.waitForFinished(3000):
                    self._process.kill()
        self._set_status("已停止")
        self._browser_btn.setEnabled(False)
        self._reload_btn.setEnabled(False)
        self._stop_waiting()

    def open_in_browser(self):
        webbrowser.open(f"http://127.0.0.1:{self.port}")

    def cleanup(self):
        # 应用退出时强制结束 Streamlit 子进程并释放 WebEngine 视图
        self.stop(force=True)
        if self._webview is not None:
            self._webview.close()
            self._webview.deleteLater()
            self._webview = None
