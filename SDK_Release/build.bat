@echo off
REM 打包 SDK Release 工具（onedir：exe + _internal 文件夹，启动更快）
REM 用法：在 sdkpacker 目录双击本文件即可
setlocal
cd /d "%~dp0"

REM 用「干净 venv」（只装了 PySide6/pymupdf/py7zr/pillow/pyinstaller，不带系统全量包）
REM 这样 PyInstaller 不会去扫描 pandas/jupyter 等无关包，打包从数分钟降到一分钟内。
set VENV=C:\Users\yaoyu\.workbuddy\binaries\python\envs\sdkpacker_clean
if not exist "%VENV%\Scripts\python.exe" (
  echo [错误] 干净 venv 不存在，请先运行：
  echo   python -m venv %VENV%
  echo   %VENV%\Scripts\python.exe -m pip install pymupdf py7zr PySide6 pillow pyinstaller
  pause
  exit /b 1
)

echo [1/2] 使用干净 venv 打包中，请稍候...
"%VENV%\Scripts\python.exe" -m PyInstaller --noconfirm --onedir --windowed --name "SDK_Release" --icon "icon.ico" --hidden-import "PySide6.QtPrintSupport" --hidden-import "pymupdf" --hidden-import "py7zr" --collect-data "pymupdf" --exclude-module "PyQt5" --exclude-module "PyQt6" --exclude-module "PySide2" sdkpacker\main.py
if errorlevel 1 (
  echo.
  echo [失败] PyInstaller 返回了错误，请查看上面的红色报错信息。
  echo 常见原因：干净 venv 里漏装了某个包（pymupdf/py7zr/PySide6/pillow/pyinstaller）。
  pause
  exit /b 1
)

echo.
echo [2/2] 打包完成！
echo 产物：dist\SDK_Release\SDK_Release.exe
echo 注意：必须把同级的 _internal 文件夹一起拷贝，程序才能运行。
echo.
echo 是否现在运行程序？(Y/N)
set /p RUN=
if /i "%RUN%"=="Y" (
  start "" "dist\SDK_Release\SDK_Release.exe"
)
pause
endlocal
