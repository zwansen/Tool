import os
import sys
import tempfile
from pathlib import Path

# 动态将项目根目录加入模块搜索路径
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# === 嵌入式 Chromium (QWebEngine) 启动参数 ===
# 必须在 QApplication / WebEngine 初始化前设置，否则不生效。
# 源码与打包模式都需要，故无条件设置：
#   --no-proxy-server        避免系统/公司代理拦截 127.0.0.1 本地 Streamlit 服务
#   --disable-gpu            规避显卡驱动导致的渲染进程崩溃（表现为页面加载失败）
#   --disable-dev-shm-usage  规避共享内存不足导致的渲染进程崩溃
#   --no-sandbox             桌面应用内嵌场景下的稳定性兜底
#   --disable-software-rasterizer  进一步降低渲染相关崩溃概率
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-proxy-server --disable-gpu --disable-dev-shm-usage --no-sandbox --disable-software-rasterizer",
)

# === 诊断：未捕获异常 / 段错误写盘，避免 GUI 崩溃"无迹可寻" ===
# faulthandler 日志写到系统临时目录（不再在工程根目录生成，避免反复出现删不掉的 log）
import faulthandler

try:
    _fh = open(
        Path(tempfile.gettempdir()) / "gnss_toolbox_faulthandler.log",
        "w", encoding="utf-8", errors="replace",
    )
    faulthandler.enable(_fh)
except Exception:
    faulthandler.enable()


def _global_except_hook(exc_type, exc_value, exc_tb):
    import traceback

    try:
        with open(ROOT / "main_crash.log", "a", encoding="utf-8") as _f:
            _f.write("=== uncaught exception ===\n")
            traceback.print_exception(exc_type, exc_value, exc_tb, file=_f)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc_value, exc_tb)


sys.excepthook = _global_except_hook
# === 诊断结束 ===

# 打包后支持作为 Streamlit 子进程启动器
# 通过环境变量 GNSS_STREAMLIT_RUNNER=1 识别（比命令行参数更可靠，PyInstaller 窗口程序可能丢失参数）
if os.environ.get("GNSS_STREAMLIT_RUNNER") == "1" or "--streamlit-runner" in sys.argv:
    if "--streamlit-runner" in sys.argv:
        sys.argv.remove("--streamlit-runner")
    sys.argv[0] = "streamlit"
    from streamlit.web.cli import main as streamlit_main

    streamlit_main()
    sys.exit(0)

# PyInstaller 打包后，显式指定 QtWebEngineProcess 路径，避免内嵌浏览器找不到渲染进程
if getattr(sys, "frozen", False):
    meipass = Path(getattr(sys, "_MEIPASS", ROOT))
    webengine_process = meipass / "PyQt6" / "Qt6" / "bin" / "QtWebEngineProcess.exe"
    if webengine_process.exists():
        os.environ["QTWEBENGINEPROCESS_PATH"] = str(webengine_process)
        # 与源码模式保持一致（含 --no-proxy-server / --disable-dev-shm-usage）
        os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
            "--no-proxy-server --disable-gpu --disable-dev-shm-usage --no-sandbox --disable-software-rasterizer"
        )

# 强制 matplotlib 使用 Qt 后端，避免依赖 tkinter
import matplotlib
matplotlib.use("qtagg")

# PyQt6 WebEngine 的导入已从启动关键路径移除：
# 原「必须在 QApplication 前导入」的写法，会在 WebEngine 渲染子进程无法拉起的环境
# （如从资源管理器双击启动、无控制台）下导致整个工具箱静默退出。
# 现改为在 main_window.main() 中创建 QApplication 之后按需导入，且任何失败都不致命
# （绘图页会给出友好提示，不影响 TTFF 等其它功能）。
from app.main_window import main

if __name__ == "__main__":
    try:
        main()
    except Exception:
        _global_except_hook(*sys.exc_info())
        raise
