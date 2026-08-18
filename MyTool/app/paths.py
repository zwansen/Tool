import sys
from pathlib import Path


def get_project_root() -> Path:
    """
    返回项目根目录：
    - 脚本运行时：D:/Tool/MyTool
    - PyInstaller 打包后：_internal 目录（数据文件所在位置）
    """
    if getattr(sys, "frozen", False):
        # sys._MEIPASS 指向 PyInstaller 解压目录（onedir 模式下为 _internal）
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent
