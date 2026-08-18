"""主窗口：macOS 风格侧边栏 + 首页宫格 + 页面栈。

流畅度设计原则（重要，勿回退）：
1. **绝不使用 QGraphicsEffect**（Opacity / DropShadow）。这类特效会强制控件
   走离屏软件合成，页面控件越多掉帧越明显，是切页卡顿的根因。
   视觉层次一律用「背景色差 + 1px 边框」在 QSS 里实现。
2. 页面切换为瞬时切换，无过渡动画——本地工具的响应感优先于动效。
3. 选中态由 QSS 的 `::item:selected` 直接绘制（macOS 圆角块），
   不再使用需要逐帧动画的指示条控件。
4. 启动后在事件循环空闲时逐个预热页面实例，消除首次点击的构建停顿。
"""

import sys
from typing import Callable, Optional

from PyQt6.QtCore import Qt, QSettings, QTimer
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from app.theme import apply_theme

PageFactory = Callable[[], QWidget]

# 侧边栏展开态默认宽度（用户拖动后会记住新值）
SIDEBAR_DEFAULT_WIDTH = 176
# 折叠态 = 图标导航栏宽度（深色科技风设计）
SIDEBAR_COLLAPSED_WIDTH = 60


class PageEntry:
    """页面注册信息。"""

    __slots__ = ("name", "icon", "desc", "category", "factory", "widget", "preheat")

    def __init__(
        self,
        name: str,
        icon: str,
        desc: str,
        category: str,
        factory: PageFactory,
        preheat: bool = True,
    ):
        self.name = name
        self.icon = icon
        self.desc = desc
        self.category = category
        self.factory = factory
        self.widget: Optional[QWidget] = None
        # 含子进程 / 内嵌浏览器的重型页面不参与预热，避免启动即占资源
        self.preheat = preheat


CATEGORY_ICONS = {
    "数据解析": "📂",
    "质量分析": "✅",
    "完好性监测": "🛡",
    "可视化": "🎨",
    "外部工具": "🧰",
}


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GNSS ToolBox")
        self.setMinimumSize(1120, 720)

        # —— 侧边栏状态 ——
        self._nav_meta: list[tuple[str, str, bool]] = []  # (icon, text, is_category)
        self._sidebar_min_width = 150
        self._sidebar_max_width = 340
        self._sidebar_collapsed_width = SIDEBAR_COLLAPSED_WIDTH
        _s = QSettings("GNSS_ToolBox", "UI")
        try:
            _saved_w = int(_s.value("sidebar_width", SIDEBAR_DEFAULT_WIDTH))
        except (TypeError, ValueError):
            _saved_w = SIDEBAR_DEFAULT_WIDTH
        self._sidebar_expanded_width = max(
            self._sidebar_min_width, min(self._sidebar_max_width, _saved_w)
        )
        # 深色科技风默认图标栏（60px 折叠态）；用户可随时展开，选择会被记住
        self._sidebar_collapsed = True
        _s.setValue("sidebar_collapsed", True)
        self._restore_geometry()

        # 导航行 -> 页面索引（分类标题为 -1）
        self._nav_to_page_index: list[int] = []
        self._page_entries: list[PageEntry] = []

        self._build_ui()
        self._register_default_pages()

        self._apply_collapse(self._sidebar_collapsed)
        self.nav_list.setCurrentRow(0)
        self._setup_menu()
        self._start_preheat()

    # ---------- 界面搭建 ----------

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setObjectName("mainSplitter")
        layout.addWidget(self.splitter)

        self.splitter.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.splitter.addWidget(self.stack)

        # 侧边栏不参与拉伸，窗口变宽时空间全给内容区
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setChildrenCollapsible(False)
        self.sidebar.setMinimumWidth(self._sidebar_collapsed_width)
        self.splitter.setSizes([
            self._sidebar_collapsed_width
            if self._sidebar_collapsed
            else self._sidebar_expanded_width,
            900,
        ])
        self.splitter.splitterMoved.connect(self._on_splitter_moved)

    def _build_sidebar(self) -> QWidget:
        self.sidebar = QWidget()
        self.sidebar.setObjectName("sidebarFrame")
        col = QVBoxLayout(self.sidebar)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(0)

        # —— 顶部品牌行 ——
        head = QWidget()
        head_row = QHBoxLayout(head)
        head_row.setContentsMargins(12, 12, 8, 8)
        head_row.setSpacing(8)

        brand_col = QVBoxLayout()
        brand_col.setContentsMargins(0, 0, 0, 0)
        brand_col.setSpacing(0)
        self._brand = QLabel("GNSS ToolBox")
        self._brand.setObjectName("sidebarBrand")
        self._brand_sub = QLabel("数据分析工具箱")
        self._brand_sub.setObjectName("sidebarBrandSub")
        brand_col.addWidget(self._brand)
        brand_col.addWidget(self._brand_sub)
        head_row.addLayout(brand_col, 1)

        self._collapse_btn = QPushButton("⟨")
        self._collapse_btn.setObjectName("sidebarBtn")
        self._collapse_btn.setToolTip("折叠 / 展开侧边栏  (Ctrl+B)")
        self._collapse_btn.setFixedSize(26, 26)
        self._collapse_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._collapse_btn.clicked.connect(self._toggle_sidebar)
        head_row.addWidget(self._collapse_btn, 0, Qt.AlignmentFlag.AlignTop)
        col.addWidget(head)

        # —— 搜索过滤框 ——
        self._search_wrap = QWidget()
        sw = QHBoxLayout(self._search_wrap)
        sw.setContentsMargins(10, 0, 10, 8)
        self._search = QLineEdit()
        self._search.setObjectName("navSearch")
        self._search.setPlaceholderText("搜索功能…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._filter_nav)
        sw.addWidget(self._search)
        col.addWidget(self._search_wrap)

        # —— 导航列表 ——
        self.nav_list = QListWidget()
        self.nav_list.setObjectName("navList")
        self.nav_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # 均匀行高可跳过逐项测量，长列表滚动更顺滑
        self.nav_list.setUniformItemSizes(True)
        self.nav_list.currentRowChanged.connect(self._on_nav_changed)
        col.addWidget(self.nav_list, 1)

        self._build_statusbar_theme()
        return self.sidebar

    def _build_statusbar_theme(self):
        """状态栏左下角：深浅色切换胶囊按钮（深色科技风）。"""
        bar = self.statusBar()
        self._theme_capsule = QPushButton("🌙 浅色")
        self._theme_capsule.setObjectName("themeCapsule")
        self._theme_capsule.setToolTip("切换深色 / 浅色主题  (Ctrl+D)")
        self._theme_capsule.setCursor(Qt.CursorShape.PointingHandCursor)
        self._theme_capsule.clicked.connect(self._toggle_theme)
        bar.addWidget(self._theme_capsule)
        self._update_theme_button()

    def _setup_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("文件")
        act_home = QAction("回到首页", self)
        act_home.setShortcut("Ctrl+H")
        act_home.triggered.connect(lambda: self.nav_list.setCurrentRow(0))
        file_menu.addAction(act_home)
        file_menu.addSeparator()
        act_exit = QAction("退出", self)
        act_exit.setShortcut("Ctrl+Q")
        act_exit.triggered.connect(self.close)
        file_menu.addAction(act_exit)

        view_menu = menu.addMenu("视图")
        act_theme = QAction("切换浅色 / 深色主题", self)
        act_theme.setShortcut("Ctrl+D")
        act_theme.triggered.connect(self._toggle_theme)
        view_menu.addAction(act_theme)
        act_side = QAction("折叠 / 展开侧边栏", self)
        act_side.setShortcut("Ctrl+B")
        act_side.triggered.connect(self._toggle_sidebar)
        view_menu.addAction(act_side)
        act_find = QAction("搜索功能", self)
        act_find.setShortcut("Ctrl+K")
        act_find.triggered.connect(self._focus_search)
        view_menu.addAction(act_find)

        help_menu = menu.addMenu("帮助")
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._show_about)
        help_menu.addAction(act_about)

    # ---------- 页面注册 ----------

    def _register_default_pages(self):
        from app.pages.page_home import HomePage

        # 首页立即创建，保证启动即可见
        self.add_page(
            "首页", "🏠", "全部功能总览", "",
            lambda: HomePage(self._page_entries, self.goto_page),
            eager=True,
        )

        self.add_category("数据解析")

        def _make_ublox():
            from app.pages.page_ublox import UbloxPage
            return UbloxPage()
        self.add_page("u-blox UBX 解析", "🔧", "解析 u-blox UBX 二进制协议报文",
                      "数据解析", _make_ublox)

        def _make_true():
            from app.pages.page_true import TruePage
            return TruePage()
        self.add_page("IE真值经纬度转换", "🌐", "IE 输出文本经纬度格式转换（十进制度/度分/GGA 语句）",
                      "数据解析", _make_true)

        def _make_novatel():
            from app.pages.page_novatel import NovatelPage
            return NovatelPage()
        self.add_page("NovAtel 通用解析", "🧩", "JSON 配置驱动的通用二进制解析",
                      "数据解析", _make_novatel)

        self.add_category("质量分析")

        def _make_time_continuity():
            from app.pages.page_time_continuity import TimeContinuityPage
            return TimeContinuityPage()
        self.add_page("时间连续性检测", "🔗", "检查日志时间戳是否连续、有无跳变",
                      "质量分析", _make_time_continuity)

        def _make_same_detect():
            from app.pages.page_same_detect import SameDetectPage
            return SameDetectPage()
        self.add_page("重复行检测", "🔍", "查找日志中的重复数据行",
                      "质量分析", _make_same_detect)

        def _make_ttff():
            from app.pages.page_ttff import TTFFPage
            return TTFFPage()
        self.add_page("TTFF 分析", "⏳", "合并 NMEA 与 BPDEBUG 两类 TTFF 分析，自动识别格式并输出一份报告",
                      "质量分析", _make_ttff)

        def _make_clock_drift():
            from app.pages.page_clock_drift import ClockDriftPage
            return ClockDriftPage()
        self.add_page("钟漂变化分析", "📉", "解析 BPDEBUG 的 flash/cur/recv 钟漂值，多文件叠加对比，可选温度曲线",
                      "质量分析", _make_clock_drift)

        def _make_dop():
            from app.pages.page_dop import DopPage
            return DopPage()
        self.add_page("DOP 精度因子计算", "🛰", "由 GSV 星历解算 PDOP / HDOP / VDOP",
                      "质量分析", _make_dop)

        self.add_category("可视化")

        def _make_plot():
            from app.pages.page_plot import PlotPage
            return PlotPage()
        # 内嵌 Streamlit 子进程，预热会提前拉起进程，故排除
        self.add_page("交互式绘图", "📊", "基于 Streamlit 的交互式数据可视化",
                      "可视化", _make_plot, preheat=False)

        def _make_rtk_viewer():
            from app.pages.page_rtk_viewer import RtkViewerPage
            return RtkViewerPage()
        self.add_page("RTK 3D 查看器", "🗺", "多文件 NMEA / bag / rosbag2 轨迹 3D 可视化，支持真值误差对比与超差着色",
                      "可视化", _make_rtk_viewer)

        self.add_category("外部工具")

        def _make_ksconverter():
            from app.pages.page_ksconverter import KsconverterPage
            return KsconverterPage()
        self.add_page("Ksconverter 格式转换", "🧰", "启动 Ksconverter 进行地理数据格式互转",
                      "外部工具", _make_ksconverter)

    def add_page(
        self,
        name: str,
        icon: str,
        desc: str,
        category: str,
        factory: PageFactory,
        eager: bool = False,
        preheat: bool = True,
    ):
        """注册页面工厂到导航栏与页面栈。默认延迟实例化。"""
        entry = PageEntry(name, icon, desc, category, factory, preheat)
        page_index = len(self._page_entries)
        self._page_entries.append(entry)
        self._nav_to_page_index.append(page_index)
        self._nav_meta.append((icon, name, False))

        self.nav_list.addItem(QListWidgetItem(f"{icon}  {name}"))
        if eager:
            entry.widget = factory()
            self.stack.addWidget(entry.widget)
        else:
            # 延迟创建的页面先占位，实例化后再替换
            self.stack.addWidget(QWidget())

    def add_category(self, title: str):
        """在导航栏添加不可选中的分类标题。"""
        icon = CATEGORY_ICONS.get(title, "▸")
        self._nav_meta.append((icon, title, True))
        item = QListWidgetItem(f"{title}")
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.nav_list.addItem(item)
        self._nav_to_page_index.append(-1)

    # ---------- 页面切换 ----------

    def _ensure_page_created(self, page_index: int) -> QWidget:
        """按需创建页面实例（若尚未创建）。"""
        entry = self._page_entries[page_index]
        if entry.widget is not None:
            return entry.widget

        entry.widget = entry.factory()
        old = self.stack.widget(page_index)
        self.stack.removeWidget(old)
        old.deleteLater()
        self.stack.insertWidget(page_index, entry.widget)
        return entry.widget

    def _on_nav_changed(self, index: int):
        if not (0 <= index < len(self._nav_to_page_index)):
            return
        page_index = self._nav_to_page_index[index]
        if page_index < 0:
            return
        self._ensure_page_created(page_index)
        # 瞬时切换：不加任何过渡动画，避免离屏合成造成掉帧
        self.stack.setCurrentIndex(page_index)
        self.setWindowTitle(f"GNSS ToolBox — {self._page_entries[page_index].name}")

    def goto_page(self, name: str):
        """按页面名称跳转（供首页宫格卡片调用）。"""
        for row, page_index in enumerate(self._nav_to_page_index):
            if page_index >= 0 and self._page_entries[page_index].name == name:
                self.nav_list.setCurrentRow(row)
                return

    # ---------- 页面预热 ----------

    def _start_preheat(self):
        """启动后在事件循环空闲时逐个创建页面，消除首次点击的构建停顿。"""
        self._preheat_queue = [
            i for i, e in enumerate(self._page_entries)
            if e.widget is None and e.preheat
        ]
        if self._preheat_queue:
            QTimer.singleShot(400, self._preheat_step)

    def _preheat_step(self):
        """一次只建一个页面，把控制权交还事件循环，保证界面始终可响应。"""
        if not self._preheat_queue:
            return
        idx = self._preheat_queue.pop(0)
        try:
            if self._page_entries[idx].widget is None:
                self._ensure_page_created(idx)
        except Exception:
            # 预热失败不影响使用：真正点击时会再次尝试并抛出真实错误
            pass
        if self._preheat_queue:
            QTimer.singleShot(50, self._preheat_step)

    # ---------- 侧边栏交互 ----------

    def _focus_search(self):
        if self._sidebar_collapsed:
            self._apply_collapse(False)
        self._search.setFocus()
        self._search.selectAll()

    def _filter_nav(self, text: str):
        """按关键词过滤导航项；分类标题在其下无匹配项时一并隐藏。"""
        kw = text.strip().lower()
        # 先决定每个功能项的可见性
        visible = []
        for row, (icon, name, is_cat) in enumerate(self._nav_meta):
            if is_cat:
                visible.append(None)  # 稍后按其下功能项决定
                continue
            show = True
            if kw:
                page_index = self._nav_to_page_index[row]
                desc = self._page_entries[page_index].desc if page_index >= 0 else ""
                show = kw in name.lower() or kw in desc.lower()
            visible.append(show)

        # 分类标题：其后到下一个分类之间若有可见项则显示
        for row, (icon, name, is_cat) in enumerate(self._nav_meta):
            if not is_cat:
                continue
            has_child = False
            for j in range(row + 1, len(self._nav_meta)):
                if self._nav_meta[j][2]:
                    break
                if visible[j]:
                    has_child = True
                    break
            visible[row] = has_child

        for row, show in enumerate(visible):
            item = self.nav_list.item(row)
            if item is not None:
                item.setHidden(not show)

    def _refresh_nav_texts(self):
        """折叠时只留图标，展开时显示图标 + 名称。"""
        for i, (icon, name, is_cat) in enumerate(self._nav_meta):
            item = self.nav_list.item(i)
            if item is None:
                continue
            if is_cat:
                # 折叠态分类标题只留一条占位，避免文字挤成一团
                item.setText("" if self._sidebar_collapsed else name)
            else:
                item.setText(icon if self._sidebar_collapsed else f"{icon}  {name}")
                item.setToolTip(name if self._sidebar_collapsed else "")

    def _apply_collapse(self, collapsed: bool):
        """应用折叠 / 展开状态。

        宽度由 splitter 控制（而非 setFixedWidth），确保展开时手柄会重新布局，
        避免「折叠后无法展开」的问题。
        """
        self._sidebar_collapsed = collapsed
        width = self._sidebar_collapsed_width if collapsed else self._sidebar_expanded_width

        # 先设宽度约束再 setSizes，否则会被上一状态遗留的 max 宽度钳制
        if collapsed:
            self.sidebar.setMinimumWidth(width)
            self.sidebar.setMaximumWidth(width)
        else:
            self.sidebar.setMinimumWidth(self._sidebar_min_width)
            self.sidebar.setMaximumWidth(self._sidebar_max_width)

        total = self.splitter.width() or self.width() or 1200
        self.splitter.setSizes([width, max(320, total - width)])

        self._brand.setVisible(not collapsed)
        self._brand_sub.setVisible(not collapsed)
        self._search_wrap.setVisible(not collapsed)
        self._collapse_btn.setText("⟩" if collapsed else "⟨")
        self._refresh_nav_texts()
        QSettings("GNSS_ToolBox", "UI").setValue("sidebar_collapsed", collapsed)

    def _toggle_sidebar(self):
        self._apply_collapse(not self._sidebar_collapsed)

    def _on_splitter_moved(self, pos: int, index: int):
        """记住用户手动拖动后的展开宽度（折叠态不记录）。"""
        if self._sidebar_collapsed:
            return
        w = self.splitter.sizes()[0]
        if w <= self._sidebar_collapsed_width:
            return
        self._sidebar_expanded_width = max(
            self._sidebar_min_width, min(self._sidebar_max_width, w)
        )
        QSettings("GNSS_ToolBox", "UI").setValue(
            "sidebar_width", self._sidebar_expanded_width
        )

    # ---------- 主题 ----------

    def _update_theme_button(self):
        from app.theme import current_mode

        if current_mode() == "dark":
            self._theme_capsule.setText("☀︎ 深色模式")
        else:
            self._theme_capsule.setText("🌙 浅色模式")

    def _toggle_theme(self):
        from app.theme import current_mode, set_mode, apply_theme

        new_mode = "dark" if current_mode() == "light" else "light"
        set_mode(new_mode)
        apply_theme(QApplication.instance(), new_mode)
        self._update_theme_button()
        self.statusBar().showMessage(
            f"已切换到{'深色' if new_mode == 'dark' else '浅色'}主题", 2000
        )

    def _show_about(self):
        from PyQt6.QtWidgets import QMessageBox

        QMessageBox.about(
            self,
            "关于 GNSS ToolBox",
            "GNSS ToolBox v1.0\n\n集成式 GNSS 数据分析工具箱。",
        )

    # ---------- 生命周期 ----------

    def closeEvent(self, event):
        # 通知已创建的页面释放资源（例如终止 Streamlit 子进程）
        for entry in self._page_entries:
            if entry.widget is not None and hasattr(entry.widget, "cleanup"):
                try:
                    entry.widget.cleanup()
                except Exception:
                    pass
        settings = QSettings("GNSS_ToolBox", "MainWindow")
        settings.setValue("geometry", self.saveGeometry())
        settings.setValue("state", self.saveState())
        event.accept()

    def _restore_geometry(self):
        settings = QSettings("GNSS_ToolBox", "MainWindow")
        geom = settings.value("geometry")
        state = settings.value("state")
        if geom is not None:
            self.restoreGeometry(geom)
        if state is not None:
            self.restoreState(state)
        # 校验窗口是否落在某个可见屏幕内。若恢复出的位置在屏幕外
        # （如更换显示器/分辨率后），重置到主屏中央，避免“启动后看不到窗口”。
        try:
            app = QApplication.instance()
            screens = app.screens() if app else []
            if screens:
                fg = self.frameGeometry()
                on_screen = any(fg.intersects(sc.availableGeometry()) for sc in screens)
                if not on_screen:
                    prim = app.primaryScreen()
                    sg = prim.availableGeometry()
                    w = min(1280, max(self.minimumWidth(), sg.width() - 80))
                    h = min(820, max(self.minimumHeight(), sg.height() - 80))
                    x = sg.x() + max(0, (sg.width() - w) // 2)
                    y = sg.y() + max(0, (sg.height() - h) // 2)
                    self.setGeometry(x, y, w, h)
        except Exception:
            pass


def main():
    # 必须在 QApplication 实例化之前设置：否则之后（报告预览等）才导入
    # QWebEngineView 会抛 "QtWebEngineWidgets must be imported ... before a
    # QCoreApplication" 错误，进而导致整个主窗口创建失败、工具箱起不来。
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # 注意：不要在此处急切 import QtWebEngineWidgets —— 那会在启动时初始化
    # Chromium/ANGLE，在部分显卡/驱动上可能卡死或崩溃，导致主窗口出不来。
    # WebEngine 改为真正需要渲染 HTML 报告时才懒加载（base_page 内惰性导入），
    # 不可用时会友好降级，不影响其它功能。
    apply_theme(app)
    window = MainWindow()
    window.statusBar().showMessage("就绪")
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
