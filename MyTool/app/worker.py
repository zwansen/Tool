import inspect
from typing import Any, Callable, Optional

from PyQt6.QtCore import QThread, pyqtSignal


class WorkerThread(QThread):
    """在后台执行函数并通过信号更新 UI 的工作线程。"""

    log = pyqtSignal(str)
    progress = pyqtSignal(int)
    # 用 object 类型传递结果，避免 str() 把大结果（如检测报告正文）序列化进信号，
    # 否则 base_page._on_finished 只能拿到字符串，既无法还原为 dict，又会把整段报告灌进日志。
    finished_ok = pyqtSignal(object)
    finished_error = pyqtSignal(str)

    def __init__(
        self,
        target: Callable[..., Any],
        *args,
        log_callback: Optional[Callable[[str], None]] = None,
        **kwargs,
    ):
        super().__init__()
        self._target = target
        self._args = args
        self._kwargs = kwargs
        self._log_callback = log_callback or self.log.emit
        self._stopped = False

    def _accepts(self, name: str) -> bool:
        """判断目标函数是否声明了该关键字参数（或有 **kwargs）。"""
        try:
            sig = inspect.signature(self._target)
        except (TypeError, ValueError):
            return False
        for p in sig.parameters.values():
            if p.kind is inspect.Parameter.VAR_KEYWORD:
                return True
            if p.name == name:
                return True
        return False

    def run(self):
        try:
            # 将 log_callback 注入被调用函数，便于统一输出
            extra = {}
            # 仅当目标函数声明了这些参数时才注入，保持对旧任务函数的兼容
            if self._accepts("progress_callback"):
                extra["progress_callback"] = self.progress.emit
            if self._accepts("should_stop"):
                extra["should_stop"] = self.is_stopped
            result = self._target(
                *self._args,
                log_callback=self._log_callback,
                **extra,
                **self._kwargs,
            )
            # 原样传递返回值（dict / str / None 均可），交给 _on_finished 按类型处理
            self.finished_ok.emit(result)
        except Exception as e:
            import traceback

            self._log_callback(f"[错误] {e}")
            self._log_callback(traceback.format_exc())
            self.finished_error.emit(str(e))

    def is_stopped(self) -> bool:
        """供任务函数检查是否收到停止请求，实现协作式取消。"""
        return self._stopped

    def stop(self):
        """请求停止任务。优先协作式等待，超时后再强制终止。"""
        self._stopped = True
        if not self.isRunning():
            return

        # 给任务 3 秒时间自行退出（如 I/O 完成、循环检查 is_stopped）
        if self.wait(3000):
            return

        # 仍存活的线程作为最后手段强制终止
        self.terminate()
        self.wait(1000)
