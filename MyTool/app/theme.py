"""应用全局样式表与主题工具函数（深色科技风 + 毛玻璃质感 + 微发光）。

设计要点：
- 主风格：深蓝黑渐变（#0B1024 → #050714）+ 半透明面板 + 蓝青/紫渐变强调
  （#00D4FF → #7B61FF），参考专业测绘 / 卫星分析软件观感。
- 布局不变：左侧功能导航栏 + 右侧配置区；顶部菜单栏；底部状态栏。
- 实现约束（Qt QSS 能力边界，需在视觉上做近似）：
  * 毛玻璃模糊（backdrop-filter）→ 用「半透明面板 rgba(255,255,255,0.04) +
    深色渐变底」近似，半透明会自然透出背景光晕。
  * 外发光（box-shadow）→ 用「聚焦时边框 #00D4FF + 背景微亮」近似。
  * 200ms ease 过渡 → QSS 无 transition，选中/悬停为瞬时切换（流畅度优先）。
  * 微发光 → 用「渐变底色 + 高亮边框」近似。
- 层次感全部用「背景色差 + 1px 极细边框 + 半透明」实现。
  **禁止使用 QGraphicsDropShadowEffect / QGraphicsOpacityEffect**——
  这两者会强制控件走离屏软件合成，是切页卡顿的主因。
- 圆角规范：输入框 10px、按钮 12px、卡片 16px、选中块 10px、芯片 6px。
- 所有颜色集中在 THEMES 令牌表，样式表由令牌动态拼装，便于一键切换。
- 提示/说明文字不用红色，改用低饱和琥珀 #FFB454（小型信息芯片样式）；
  红色仅保留给「停止」等危险操作（低饱和 #FF6B6B，少用）。

支持两种主题：
- dark ：深色科技风（默认，本方案主角）
- light：浅色科技风（毛玻璃浅色，Ctrl+D / 状态栏胶囊可切换）
"""

from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QSettings


# 主题令牌表
THEMES = {
    "dark": {
        # —— 工作区 ——
        "bg_app": "#0B1024",        # 渐变起点（深蓝黑）
        "bg_app2": "#050714",       # 渐变终点
        "bg_surface": "rgba(255,255,255,0.04)",   # 卡片半透明面板
        "bg_surface_hover": "rgba(255,255,255,0.07)",
        "bg_sunk": "rgba(0,0,0,0.30)",            # 凹陷区 / 输入框底
        "bg_hover": "rgba(255,255,255,0.06)",     # 悬停浅底
        "border": "rgba(0,212,255,0.12)",         # 卡片边框
        "border_strong": "rgba(255,255,255,0.08)",# 控件描边
        "border_hover": "rgba(255,255,255,0.22)",
        "text_primary": "#E6EAF2",  # 主文字
        "text_secondary": "rgba(255,255,255,0.55)",
        "text_muted": "rgba(255,255,255,0.35)",
        "input_text": "rgba(255,255,255,0.85)",   # 输入框文字（浅色主题下为深色）
        "checkbox_border": "rgba(255,255,255,0.25)",  # 勾选框未选边框
        "scroll_handle": "rgba(255,255,255,0.14)",
        "scroll_handle_hover": "rgba(255,255,255,0.28)",
        "btn_border": "rgba(255,255,255,0.15)",      # 次级按钮边框
        "btn_border_hover": "rgba(255,255,255,0.30)",
        "focus_bg": "rgba(0,0,0,0.38)",             # 输入框聚焦背景（浅色下提亮）
        "primary": "#00D4FF",       # 主强调色（青色）
        "primary2": "#7B61FF",      # 辅助强调色（紫色，渐变副色）
        "primary_hover": "#33DDFF",
        "primary_press": "#00A8CC",
        "primary_soft": "rgba(0,212,255,0.10)",   # 选中/柔和高亮底
        "danger": "rgba(255,107,107,0.85)",       # 低饱和红，少用
        "danger_hover": "rgba(255,107,107,1.0)",
        "success": "#00E676",       # 完成
        "success_hover": "#2BF58C",
        "warning": "#FFB454",       # 提示/警告（琥珀）
        # —— 日志控制台 ——
        "log_bg": "rgba(5,7,20,0.85)",
        "log_text": "#B9C2D0",
        "log_border": "rgba(255,255,255,0.06)",
        # —— 侧边栏（半透明毛玻璃面板）——
        "sb_bg": "rgba(255,255,255,0.04)",
        "sb_bg_hover": "rgba(255,255,255,0.06)",
        "sb_bg_active": "rgba(0,212,255,0.10)",
        "sb_text": "rgba(255,255,255,0.80)",
        "sb_text_active": "#FFFFFF",
        "sb_text_muted": "rgba(255,255,255,0.45)",
        "sb_border": "rgba(255,255,255,0.06)",
    },
    "light": {
        # —— 工作区 ——
        "bg_app": "#EDF2FB",        # 浅色科技风（冷白蓝）
        "bg_app2": "#DFE8F6",
        "bg_surface": "rgba(255,255,255,0.72)",
        "bg_surface_hover": "rgba(255,255,255,0.9)",
        "bg_sunk": "rgba(15,23,42,0.05)",
        "bg_hover": "rgba(15,23,42,0.05)",
        "border": "rgba(0,180,230,0.25)",
        "border_strong": "rgba(15,23,42,0.12)",
        "border_hover": "rgba(15,23,42,0.25)",
        "text_primary": "#16203A",
        "text_secondary": "rgba(22,32,58,0.62)",
        "text_muted": "rgba(22,32,58,0.38)",
        "input_text": "#1B2436",
        "checkbox_border": "rgba(15,23,42,0.35)",
        "scroll_handle": "rgba(15,23,42,0.16)",
        "scroll_handle_hover": "rgba(15,23,42,0.28)",
        "btn_border": "rgba(15,23,42,0.18)",
        "btn_border_hover": "rgba(15,23,42,0.32)",
        "focus_bg": "rgba(255,255,255,0.95)",
        "primary": "#00A9E0",
        "primary2": "#6C5CE7",
        "primary_hover": "#33BBEC",
        "primary_press": "#008FBF",
        "primary_soft": "rgba(0,169,224,0.10)",
        "danger": "rgba(230,90,90,0.85)",
        "danger_hover": "rgba(230,90,90,1.0)",
        "success": "#00A86B",
        "success_hover": "#00C27C",
        "warning": "#C97B16",
        # —— 日志控制台 ——
        "log_bg": "rgba(255,255,255,0.85)",
        "log_text": "#33405C",
        "log_border": "rgba(15,23,42,0.10)",
        # —— 侧边栏 ——
        "sb_bg": "rgba(255,255,255,0.55)",
        "sb_bg_hover": "rgba(15,23,42,0.05)",
        "sb_bg_active": "rgba(0,169,224,0.12)",
        "sb_text": "rgba(22,32,58,0.85)",
        "sb_text_active": "#0B6E9E",
        "sb_text_muted": "rgba(22,32,58,0.45)",
        "sb_border": "rgba(15,23,42,0.08)",
    },
}

# 兼容旧引用（历史代码里直接 import 过这两个常量）
LOG_BG = THEMES["light"]["log_bg"]
LOG_TEXT = THEMES["light"]["log_text"]

# 优先 Inter / SF Pro；中文回落到微软雅黑 / 苹方
FONT_STACK = (
    '"Inter", "SF Pro Display", "SF Pro Text", "Segoe UI Variable Text", '
    '"Segoe UI", "Microsoft YaHei UI", "PingFang SC", system-ui, sans-serif'
)
MONO_STACK = '"JetBrains Mono", "Cascadia Code", "SF Mono", Consolas, "Courier New", monospace'


def current_mode() -> str:
    """返回当前主题模式（light / dark），未记录时默认 dark。"""
    val = QSettings("GNSS_ToolBox", "UI").value("theme_mode")
    return val if val in THEMES else "dark"


def set_mode(mode: str):
    """持久化主题模式。"""
    if mode in THEMES:
        QSettings("GNSS_ToolBox", "UI").setValue("theme_mode", mode)


def _build_stylesheet(t: dict) -> str:
    """根据令牌表拼装工作区样式表（深色科技风）。"""
    return f"""
QWidget {{
    color: {t['text_primary']};
    font-family: {FONT_STACK};
    font-size: 10pt;
}}
QMainWindow, QDialog {{
    background-color: {t['bg_app2']};
    background: qlineargradient(x1:0, y1:0, x2:0.65, y2:1,
        stop:0 {t['bg_app']}, stop:1 {t['bg_app2']});
}}

/* ===== 通用按钮（默认=次级按钮：透明底细边框；主按钮=蓝紫渐变） ===== */
QPushButton {{
    background-color: transparent;
    border: 1px solid {t['btn_border']};
    border-radius: 12px;
    padding: 6px 14px;
    min-width: 68px;
    min-height: 16px;
    color: {t['text_secondary']};
}}
QPushButton:hover {{
    background-color: {t['bg_hover']};
    border-color: {t['btn_border_hover']};
    color: {t['text_primary']};
}}
QPushButton:pressed {{
    background-color: {t['bg_sunk']};
}}
QPushButton:disabled {{
    background-color: transparent;
    border-color: {t['border_strong']};
    color: {t['text_muted']};
}}

QPushButton#primaryButton {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {t['primary']}, stop:1 {t['primary2']});
    border: 1px solid rgba(255,255,255,0.18);
    border-radius: 12px;
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#primaryButton:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {t['primary_hover']}, stop:1 #9B8AFF);
    border-color: rgba(255,255,255,0.32);
}}
QPushButton#primaryButton:pressed {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {t['primary_press']}, stop:1 {t['primary2']});
}}
QPushButton#primaryButton:disabled {{
    background: rgba(255,255,255,0.06);
    border-color: rgba(255,255,255,0.08);
    color: {t['text_muted']};
}}

QPushButton#dangerButton {{
    background-color: {t['danger']};
    border: 1px solid rgba(255,107,107,0.35);
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton#dangerButton:hover {{
    background-color: {t['danger_hover']};
    border-color: rgba(255,107,107,0.55);
}}
QPushButton#dangerButton:disabled {{
    background: rgba(255,255,255,0.06);
    color: {t['text_muted']};
}}

QPushButton#successButton {{
    background-color: {t['success']};
    border: 1px solid rgba(0,230,118,0.35);
    color: #06160F;
    font-weight: 600;
}}
QPushButton#successButton:hover {{
    background-color: {t['success_hover']};
}}

/* 无边框轻量按钮（工具栏 / 图标位） */
QPushButton#ghostButton {{
    background-color: transparent;
    border: none;
    border-radius: 10px;
    padding: 5px 10px;
    min-width: 0px;
    color: {t['text_secondary']};
}}
QPushButton#ghostButton:hover {{
    background-color: {t['bg_hover']};
    color: {t['text_primary']};
}}
QPushButton#ghostButton:pressed {{
    background-color: {t['bg_sunk']};
}}
QPushButton#ghostButton:checked {{
    background-color: {t['primary_soft']};
    color: {t['primary']};
}}

/* ===== 首页宫格卡片 ===== */
QPushButton#homeCard {{
    background-color: {t['bg_surface']};
    border: 1px solid {t['border']};
    border-radius: 16px;
    padding: 0px;
    min-width: 0px;
    text-align: left;
}}
QPushButton#homeCard:hover {{
    background-color: {t['bg_surface_hover']};
    border-color: {t['primary']};
}}
QPushButton#homeCard:pressed {{
    background-color: {t['primary_soft']};
    border-color: {t['primary']};
}}
QLabel#homeCardIcon {{
    font-size: 21pt;
    background: transparent;
}}
QLabel#homeCardTitle {{
    font-size: 11pt;
    font-weight: 600;
    color: {t['text_primary']};
    background: transparent;
}}
QLabel#homeCardDesc {{
    font-size: 9pt;
    color: {t['text_secondary']};
    background: transparent;
}}
QLabel#homeSectionTitle {{
    font-size: 8pt;
    font-weight: 700;
    color: {t['text_muted']};
    padding: 2px 2px 0px 2px;
}}
QLabel#homeHeroTitle {{
    font-size: 20pt;
    font-weight: 700;
    color: {t['text_primary']};
}}
QLabel#homeHeroSub {{
    font-size: 10pt;
    color: {t['text_secondary']};
}}

/* ===== 下拉框 ===== */
QComboBox {{
    background-color: {t['bg_sunk']};
    border: 1px solid {t['border_strong']};
    border-radius: 10px;
    padding: 5px 10px;
    min-width: 88px;
    color: {t['input_text']};
}}
QComboBox:hover {{ border-color: {t['border_hover']}; }}
QComboBox:focus {{ border-color: {t['primary']}; background-color: {t['focus_bg']}; }}
QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border: none;
}}
QComboBox::down-arrow {{
    image: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='10'%20height='6'%20viewBox='0%200%2010%206'%3E%3Cpath%20d='M1%201l4%204%204-4'%20fill='none'%20stroke='%2399A3B8'%20stroke-width='1.6'%20stroke-linecap='round'%20stroke-linejoin='round'/%3E%3C/svg%3E");
    margin-right: 8px;
    width: 10px;
    height: 6px;
}}
QComboBox QAbstractItemView {{
    background-color: {t['bg_app2']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    selection-background-color: {t['primary_soft']};
    selection-color: {t['primary']};
    padding: 6px;
    outline: none;
    color: {t['text_primary']};
}}
QComboBox QAbstractItemView::item {{
    padding: 6px 8px;
    border-radius: 8px;
}}
QComboBox QAbstractItemView::item:selected {{
    background-color: {t['primary_soft']};
    color: {t['primary']};
}}

/* ===== 输入框 ===== */
QLineEdit {{
    background-color: {t['bg_sunk']};
    border: 1px solid {t['border_strong']};
    border-radius: 10px;
    padding: 6px 10px;
    selection-background-color: {t['primary']};
    selection-color: #FFFFFF;
    color: {t['input_text']};
}}
QLineEdit:hover {{ border-color: {t['border_hover']}; }}
QLineEdit:focus {{
    border-color: {t['primary']};
    background-color: {t['focus_bg']};
}}
QLineEdit:disabled {{
    background-color: {t['bg_sunk']};
    color: {t['text_muted']};
}}
/* 校验未通过（BasePage 设置动态属性 invalid=true 触发） */
QLineEdit[invalid="true"] {{
    border-color: rgba(255,107,107,0.75);
}}
QLineEdit[invalid="true"]:focus {{
    border-color: rgba(255,107,107,0.9);
}}
/* 占位符文字 */
QLineEdit {{
    font-size: 10pt;
}}

QPlainTextEdit, QTextEdit {{
    background-color: {t['bg_sunk']};
    border: 1px solid {t['border_strong']};
    border-radius: 10px;
    padding: 6px;
    selection-background-color: {t['primary']};
    selection-color: #FFFFFF;
    color: {t['input_text']};
}}
QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {t['primary']}; background-color: {t['focus_bg']}; }}

/* ===== 侧边栏搜索框 ===== */
QLineEdit#navSearch {{
    background-color: {t['bg_sunk']};
    border: 1px solid {t['sb_border']};
    border-radius: 10px;
    padding: 5px 9px;
    color: {t['sb_text']};
    font-size: 9pt;
}}
QLineEdit#navSearch:focus {{ border-color: {t['primary']}; }}

/* ===== 文本标签 ===== */
QLabel {{ color: {t['text_primary']}; background: transparent; }}
/* 提示 / 说明文字：低饱和琥珀色信息芯片（不再用红色） */
QLabel#fieldHint {{
    color: {t['warning']};
    background-color: rgba(255,180,84,0.10);
    border: 1px solid rgba(255,180,84,0.20);
    border-radius: 6px;
    padding: 2px 7px;
    font-size: 8.5pt;
}}
QLabel#fieldLabel {{
    color: {t['text_secondary']};
    font-size: 9.5pt;
}}
QLabel#statusLabel {{
    color: {t['text_secondary']};
    padding: 0 4px;
}}
QLabel#statusLabel[state="running"] {{ color: {t['primary']}; font-weight: 600; }}
QLabel#statusLabel[state="ok"] {{ color: {t['success']}; font-weight: 600; }}
QLabel#statusLabel[state="error"] {{ color: rgba(255,107,107,0.9); font-weight: 600; }}
QLabel#pageTitle {{
    color: {t['text_primary']};
    font-size: 12pt;
    font-weight: 700;
}}
QLabel#pageSubtitle {{
    color: {t['text_secondary']};
    font-size: 9pt;
}}
QLabel#cardTitle {{
    color: {t['text_secondary']};
    font-size: 8.5pt;
    font-weight: 700;
    padding-bottom: 2px;
}}
QLabel#filePathLabel {{
    color: {t['text_primary']};
    font-size: 9pt;
}}
QLineEdit#configPathEdit {{
    background-color: {t['bg_sunk']};
    border: 1px solid {t['border']};
    border-radius: 10px;
    padding: 5px 8px;
    color: {t['text_secondary']};
}}

/* ===== 卡片容器（毛玻璃面板近似） ===== */
QFrame#card {{
    background-color: {t['bg_surface']};
    border: 1px solid {t['border']};
    border-radius: 16px;
}}
QFrame#toolbarFrame {{
    background-color: {t['bg_surface']};
    border: 1px solid {t['border']};
    border-radius: 16px;
}}
QFrame#hLine {{
    background-color: {t['sb_border']};
    border: none;
    max-height: 1px;
}}
QWidget#previewContent {{ background: transparent; }}

/* ===== 分组框 ===== */
QGroupBox {{
    background-color: {t['bg_surface']};
    border: 1px solid {t['border']};
    border-radius: 16px;
    margin-top: 10px;
    padding: 14px;
    font-weight: 600;
    color: {t['text_primary']};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 6px;
    color: {t['text_secondary']};
    font-size: 8.5pt;
    font-weight: 700;
    background-color: transparent;
}}

/* ===== 复选框 / 单选（科技风勾选框） ===== */
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 20px; height: 20px;
}}
QCheckBox::indicator:unchecked, QRadioButton::indicator:unchecked {{
    background-color: {t['bg_sunk']};
    border: 1.5px solid {t['checkbox_border']};
    border-radius: 6px;
}}
QCheckBox::indicator:unchecked:hover, QRadioButton::indicator:unchecked:hover {{
    border-color: {t['primary']};
    background-color: {t['primary_soft']};
}}
QCheckBox::indicator:indeterminate, QRadioButton::indicator:indeterminate {{
    background-color: {t['bg_sunk']};
    border: 1.5px solid {t['checkbox_border']};
    border-radius: 6px;
}}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {t['primary']};
    border: 1.5px solid {t['primary']};
    border-radius: 6px;
    image: url("data:image/svg+xml,%3Csvg%20xmlns='http://www.w3.org/2000/svg'%20width='14'%20height='14'%20viewBox='0%200%2014%2014'%3E%3Cpath%20d='M3%207.5%20L6%2010.5%20L11%203.5'%20fill='none'%20stroke='%23ffffff'%20stroke-width='2.2'%20stroke-linecap='round'%20stroke-linejoin='round'/%3E%3C/svg%3E");
}}
QCheckBox::indicator:checked:hover, QRadioButton::indicator:checked:hover {{
    background-color: {t['primary_hover']};
    border-color: {t['primary_hover']};
}}
QCheckBox:hover, QRadioButton:hover {{ color: {t['text_primary']}; }}

/* ===== 进度条 ===== */
QProgressBar {{
    border: none;
    border-radius: 3px;
    text-align: center;
    background-color: {t['bg_sunk']};
    color: {t['text_secondary']};
    max-height: 6px;
    min-height: 6px;
    font-size: 1pt;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {t['primary']}, stop:1 {t['primary2']});
    border-radius: 3px;
}}

/* ===== 日志控制台 ===== */
QPlainTextEdit#logEdit, QTextEdit#logEdit {{
    background-color: {t['log_bg']};
    color: {t['log_text']};
    border: 1px solid {t['log_border']};
    border-radius: 12px;
    padding: 8px;
    font-family: {MONO_STACK};
    font-size: 9pt;
}}
QPlainTextEdit#monoPreview {{
    background-color: {t['bg_sunk']};
    color: {t['text_primary']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    padding: 8px;
    font-family: {MONO_STACK};
    font-size: 9pt;
}}
QTextBrowser {{
    background-color: {t['bg_surface']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    padding: 10px;
    color: {t['text_primary']};
}}

/* ===== 标签页（下划线指示器风格） ===== */
QTabWidget::pane {{
    border: 1px solid {t['border']};
    border-radius: 12px;
    background-color: {t['bg_surface']};
    top: -1px;
}}
QTabBar::tab {{
    background-color: transparent;
    border: none;
    border-bottom: 2px solid transparent;
    border-radius: 0px;
    padding: 7px 16px;
    margin-right: 4px;
    color: {t['text_secondary']};
    font-size: 9.5pt;
}}
QTabBar::tab:selected {{
    border-bottom: 2px solid {t['primary']};
    color: {t['primary']};
    font-weight: 600;
}}
QTabBar::tab:!selected:hover {{
    color: {t['text_primary']};
    border-bottom: 2px solid {t['border_hover']};
}}

/* ===== 菜单 ===== */
QMenuBar {{
    background-color: transparent;
    border-bottom: 1px solid {t['sb_border']};
    padding: 2px;
}}
QMenuBar::item {{
    padding: 5px 12px;
    border-radius: 8px;
    background: transparent;
    color: {t['text_secondary']};
}}
QMenuBar::item:selected {{ background-color: {t['bg_hover']}; color: {t['text_primary']}; }}
QMenu {{
    background-color: {t['bg_app2']};
    border: 1px solid {t['border']};
    border-radius: 12px;
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 24px 6px 14px;
    border-radius: 8px;
    color: {t['text_primary']};
}}
QMenu::item:selected {{ background-color: {t['primary_soft']}; color: {t['primary']}; }}
QMenu::separator {{ height: 1px; background: {t['sb_border']}; margin: 4px 8px; }}

/* ===== 分隔条 ===== */
QSplitter::handle {{ background-color: transparent; }}
QSplitter::handle:hover {{ background-color: {t['border_hover']}; }}

/* ===== 滚动条（细长悬浮风格） ===== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px 2px 2px 0px;
}}
QScrollBar::handle:vertical {{
    background: {t['scroll_handle']};
    border-radius: 5px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: {t['scroll_handle_hover']}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0px; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0px 2px 2px 2px;
}}
QScrollBar::handle:horizontal {{
    background: {t['scroll_handle']};
    border-radius: 5px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: {t['scroll_handle_hover']}; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0px; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

/* ===== 工具提示 ===== */
QToolTip {{
    background-color: #101733;
    color: {t['text_primary']};
    border: 1px solid {t['border']};
    border-radius: 8px;
    padding: 5px 9px;
    font-size: 9pt;
}}

/* ===== 状态栏（极简半透明） ===== */
QStatusBar {{
    background-color: {t['sb_bg']};
    border-top: 1px solid {t['sb_border']};
    color: {t['text_muted']};
    padding: 2px 10px;
}}
QStatusBar::item {{ border: none; }}
/* 深浅色切换胶囊按钮 */
QPushButton#themeCapsule {{
    background-color: rgba(0,212,255,0.08);
    border: 1px solid rgba(0,212,255,0.25);
    border-radius: 13px;
    padding: 3px 14px;
    min-width: 0px;
    min-height: 0px;
    color: {t['sb_text']};
    font-size: 8.5pt;
}}
QPushButton#themeCapsule:hover {{
    background-color: rgba(0,212,255,0.16);
    border-color: rgba(0,212,255,0.45);
    color: {t['text_primary']};
}}
QPushButton#themeCapsule:pressed {{
    background-color: rgba(0,212,255,0.22);
}}

/* ===== 滚动区域透明化，避免出现多余底色块 ===== */
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
"""


def _build_sidebar(t: dict) -> str:
    """拼装侧边栏样式表（半透明毛玻璃面板 + 选中渐变竖条）。"""
    return f"""
QWidget#sidebarFrame {{
    background-color: {t['sb_bg']};
    border: none;
    border-right: 1px solid {t['sb_border']};
}}

QLabel#sidebarBrand {{
    color: {t['sb_text']};
    font-size: 12pt;
    font-weight: 700;
}}
QLabel#sidebarBrandSub {{
    color: {t['sb_text_muted']};
    font-size: 8pt;
}}

QPushButton#sidebarBtn {{
    background-color: transparent;
    border: 1px solid {t['border_strong']};
    border-radius: 10px;
    padding: 2px;
    min-width: 0px;
    color: {t['sb_text_muted']};
    font-size: 12pt;
}}
QPushButton#sidebarBtn:hover {{
    background-color: {t['sb_bg_hover']};
    color: {t['sb_text']};
    border-color: {t['border_hover']};
}}
QPushButton#sidebarBtn:pressed {{
    background-color: {t['sb_bg_hover']};
}}

QListWidget#navList {{
    background-color: transparent;
    border: none;
    padding: 4px 0px;
    outline: none;
}}
QListWidget#navList::item {{
    padding: 6px 10px 6px 12px;
    border-radius: 10px;
    margin: 2px 8px;
    border: none;
    color: {t['sb_text_muted']};
    font-size: 9.5pt;
}}
QListWidget#navList::item:hover {{
    background-color: {t['sb_bg_hover']};
    color: {t['sb_text']};
}}
/* 选中态：左侧蓝紫渐变竖条 + 半透明高亮块 + 纯白文字 */
QListWidget#navList::item:selected {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(0,212,255,0.16),
        stop:0.18 rgba(0,212,255,0.10),
        stop:1 rgba(0,212,255,0.05));
    border-left: 3px solid qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 {t['primary']}, stop:1 {t['primary2']});
    color: {t['sb_text_active']};
    font-weight: 600;
}}
QListWidget#navList::item:selected:hover {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(0,212,255,0.20),
        stop:0.18 rgba(0,212,255,0.12),
        stop:1 rgba(0,212,255,0.06));
}}
/* 分类标题：不可选，小号低透明度小字 */
QListWidget#navList::item:disabled {{
    color: {t['text_muted']};
    background-color: transparent;
    border: none;
    font-size: 7.5pt;
    font-weight: 700;
    padding: 12px 12px 2px 12px;
    margin: 0px 8px;
}}
"""


def apply_theme(app: QApplication, mode: str = None):
    """为整个应用加载指定（或当前）主题的样式表，并刷新所有控件。"""
    mode = mode or current_mode()
    t = THEMES[mode]
    app.setStyleSheet(_build_stylesheet(t) + "\n" + _build_sidebar(t))
    apply_palette(app, t)
    # 强制刷新已存在的控件，使主题切换立即生效
    for widget in app.allWidgets():
        try:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
        except Exception:
            pass


def apply_palette(app: QApplication, t: dict = None):
    """设置应用调色板（窗口背景、文字颜色等）。"""
    if t is None:
        t = THEMES[current_mode()]
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(t["bg_app2"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(t["text_primary"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(t["bg_app2"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(t["bg_sunk"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(t["text_primary"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(t["bg_app2"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(t["text_primary"]))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(t["text_muted"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(t["primary"]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    app.setPalette(palette)
