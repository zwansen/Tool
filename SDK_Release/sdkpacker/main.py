"""包入口。"""

import sys


def _try_relative():
    try:
        from .cli import main as cli_main
        from .gui import run_gui
        return cli_main, run_gui
    except ImportError:
        return None, None


def _try_absolute():
    try:
        from sdkpacker.cli import main as cli_main
        from sdkpacker.gui import run_gui
        return cli_main, run_gui
    except ImportError:
        return None, None


def main():
    cli_main, run_gui = _try_relative()
    if cli_main is None:
        cli_main, run_gui = _try_absolute()
    if "--source" in sys.argv:
        cli_main()
    else:
        run_gui()


if __name__ == "__main__":
    main()