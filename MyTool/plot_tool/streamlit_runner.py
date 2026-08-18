"""
PyInstaller 辅助入口：用于在打包后的 exe 中启动 Streamlit。
构建后会生成 streamlit_runner.exe，主程序通过它启动 plot_tool。
"""

import sys


def main():
    # 将第一个参数替换为 'streamlit'，让 streamlit.web.cli 正确解析
    args = list(sys.argv)
    args[0] = "streamlit"
    sys.argv = args

    from streamlit.web.cli import main as streamlit_main

    streamlit_main()


if __name__ == "__main__":
    main()
