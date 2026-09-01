# SDK Release Tool

Windows 桌面程序（exe + 依赖文件夹 `_internal`），双击即可运行，用于把原始 SDK 资料包（压缩包或文件夹）按客户、版本、日期打包发布，里面的 PDF 文档自动加水印并加密。

## 功能

- 选择源：`.zip` / `.7z` / `.tar(.gz)` / 文件夹 任意一种
- 自动解析版本号（如 `Delos_SDK_V2.3.5.8.zip` → 版本 `2.3.5.8`）
- 树状文件列表，三态勾选决定是否输出
- **逐文件 PDF 处理方式**：每个 PDF（或某个文件夹）可单独设为「同默认 / 水印+加密 / 仅水印 / 仅加密 / 原样」，互不影响
- 自定义输出文件名：`SDK_Release_To_{客户名}_{SDK|SDKLite}_{版本号}_{yyyyMMdd}.zip/.7z/.tar.gz/`
- PDF 处理：
  - 倾斜、半透明、可模板化的中文水印（如 `SDK_Release_To_华为 · 20260901`）
  - AES-256 加密，可设所有者口令、可设用户口令（留空则免密查看）
  - 权限位：允许打印 / 允许复制 / 允许修改 / 无障碍阅读
- **附加内容（可选）**：可在发布包里额外塞入
  - 本地文件夹（整目录递归加入，可指定放入包内哪个子目录）
  - 自建 `.txt` 文本说明（内置「通用交付说明 / 版本变更记录」等模板，支持存为自定义模板，双击列表可再次编辑）
  - 文本支持变量 `{customer}`（客户名）`{type}`（SDK 类型）`{version}`（版本号）`{date}`（日期），发布时自动替换为实际值
- 输出格式：ZIP / 7z / TAR.GZ / 文件夹（按需选择）
- 进度条按真实处理进度推进 + 实时日志 + 取消按钮
- 窗口大小随屏幕自适应，底部「开始发布 / 取消 / 打开输出目录」始终可见
- 双击默认进入 GUI 模式；带 `--source` 参数走命令行模式

## 使用

### GUI 模式

直接双击 `SDK_Release.exe`，按界面提示操作。

### 命令行模式

```
SDK_Release.exe --source <原始包> --out <输出目录> \
                    --customer <客户名> --format <zip|7z|tar.gz|folder> \
                    [--sdk-type SDK|SDKLite] [--version <版本号>] [--date <yyyyMMdd>] \
                    [--encrypt] [--owner-pw <口令>]>] [--user-pw <口令>]>] \
                    [--wm <水印模板>] [--wm-opacity <0~1>] [--wm-angle <度>] \
                    [--allow-print/--no-print] [--allow-copy/--no-copy] [--allow-modify/--no-modify]
```

水印模板里可用占位符：`{customer}`（客户名）`{date}`（日期）。

## 构建（开发者）

依赖：Python 3.11 + PySide6 + pymupdf + py7zr + pillow + pyinstaller

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pymupdf py7zr PySide6 pillow pyinstaller
build\build.bat
```

输出：`dist/SDK_Release/SDK_Release.exe`（连同 `_internal` 文件夹一起分发）。

### 启动慢？已优化

`build.bat` 默认使用 `--onedir`（exe + `_internal` 依赖文件夹），**不再每次启动都解压整个包**，双击即可秒开。原因与对策：

- **单文件 onefile 模式**：PyInstaller 每次启动都要把全部依赖解压到临时目录再运行，包越大越慢——这是之前启动慢的主因。已改为 onedir。
- **延迟加载重依赖**：`pymupdf` / `py7zr` / `PIL` 改为真正用到时才导入（启动阶段只加载 PySide6 与标准库）。实测 GUI 模块导入从"加载一整套 C 扩展"降为 ~0.2s 且不触碰 pymupdf/py7zr。
- 若一定要单文件发布，把 `build.bat` 里的 `--onedir` 改回 `--onefile` 即可（代价是启动变慢）。

## 所有者口令 vs 用户口令

- **所有者口令（Owner Password）**：PDF 的“管理员”密码。知道它即可拥有全部权限：解密、改权限、去密码、重新加密。即使限制了复制/打印，持所有者口令也能绕过。程序内部用它来加密，**建议固定且保密，不要发给客户**（留空会自动用默认值）。
- **用户口令（User Password）**：打开/查看 PDF 所需的密码。设了它，客户必须输入口令才能打开；**留空则任何人都能直接打开**，只是受下方“权限”限制（如禁止复制）。
- 常见做法：所有者口令自己留着，用户口令留空（客户免密打开但受限）。

## 依赖说明

- **PyMuPDF** —— PDF 水印 + AES-256 加密
- **py7zr** —— 7z 读写
- **zipfile / tarfile**（标准库）—— zip、tar 读写
- **PySide6** —— GUI

完全内置，无需福昕高级 PDF 编辑器 / 批量PDF加密工具 / qpdf / pdftk 等任何外部软件。