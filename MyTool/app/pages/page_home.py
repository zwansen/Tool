"""首页：按分类展示全部功能的卡片宫格。

实现要点（性能相关，勿改）：
- 卡片用 QPushButton 承载，hover / pressed 全部交给 QSS 原生状态机处理，
  不使用事件过滤器 + unpolish/polish 的方案，也不使用任何 QGraphicsEffect。
- 卡片内部的图标与文字标签设置 WA_TransparentForMouseEvents，
  否则子控件会吃掉鼠标事件，导致按钮的 :hover 伪状态无法触发。
- 采用响应式流式布局：resize 时按可用宽度重排列数，只调整已有控件的
  网格位置，不重建控件。
"""

from typing import Callable, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

CARD_MIN_WIDTH = 250
CARD_HEIGHT = 78
GRID_SPACING = 10


class _FlowGrid(QWidget):
    """按可用宽度自动决定列数的卡片网格。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(GRID_SPACING)
        self._cards: list[QWidget] = []
        self._cols = 0

    def add_card(self, card: QWidget):
        self._cards.append(card)

    def relayout(self, width: int):
        """按给定宽度重排；列数未变化时直接跳过，避免无谓的布局计算。"""
        cols = max(1, (width + GRID_SPACING) // (CARD_MIN_WIDTH + GRID_SPACING))
        cols = min(cols, 4)
        if cols == self._cols:
            return
        self._cols = cols
        for i, card in enumerate(self._cards):
            self._grid.addWidget(card, i // cols, i % cols)
        # 让最后一列右侧不留伸缩空隙，卡片等宽铺满
        for c in range(4):
            self._grid.setColumnStretch(c, 1 if c < cols else 0)


class HomePage(QWidget):
    """功能总览首页。"""

    def __init__(
        self,
        entries: list,
        on_open: Callable[[str], None],
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self._entries = entries
        self._on_open = on_open
        self._grids: list[_FlowGrid] = []
        self._build()

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)

        body = QWidget()
        col = QVBoxLayout(body)
        col.setContentsMargins(28, 24, 28, 24)
        col.setSpacing(18)
        col.setAlignment(Qt.AlignmentFlag.AlignTop)

        # —— 顶部标题区 ——
        hero = QVBoxLayout()
        hero.setSpacing(2)
        title = QLabel("GNSS ToolBox")
        title.setObjectName("homeHeroTitle")
        sub = QLabel("选择下方任意功能开始分析，或使用左侧 Ctrl+K 快速搜索")
        sub.setObjectName("homeHeroSub")
        hero.addWidget(title)
        hero.addWidget(sub)
        col.addLayout(hero)

        # —— 按分类分组渲染卡片 ——
        groups: dict[str, list] = {}
        order: list[str] = []
        for e in self._entries:
            if not e.category:  # 首页自身不入卡片
                continue
            if e.category not in groups:
                groups[e.category] = []
                order.append(e.category)
            groups[e.category].append(e)

        for cat in order:
            sec = QLabel(cat.upper())
            sec.setObjectName("homeSectionTitle")
            col.addWidget(sec)

            grid = _FlowGrid()
            for entry in groups[cat]:
                grid.add_card(self._make_card(entry))
            self._grids.append(grid)
            col.addWidget(grid)

        col.addStretch()
        scroll.setWidget(body)
        self._body = body

    def _make_card(self, entry) -> QPushButton:
        card = QPushButton()
        card.setObjectName("homeCard")
        card.setFixedHeight(CARD_HEIGHT)
        card.setMinimumWidth(CARD_MIN_WIDTH)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setToolTip(entry.desc)
        card.clicked.connect(lambda _=False, n=entry.name: self._on_open(n))

        row = QHBoxLayout(card)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)

        icon = QLabel(entry.icon)
        icon.setObjectName("homeCardIcon")
        icon.setFixedWidth(34)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(icon)

        text = QVBoxLayout()
        text.setSpacing(2)
        text.setContentsMargins(0, 0, 0, 0)
        name = QLabel(entry.name)
        name.setObjectName("homeCardTitle")
        desc = QLabel(entry.desc)
        desc.setObjectName("homeCardDesc")
        desc.setWordWrap(True)
        text.addWidget(name)
        text.addWidget(desc)
        row.addLayout(text, 1)

        # 关键：子控件必须鼠标穿透，否则按钮的 :hover 不会触发
        for child in (icon, name, desc):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        return card

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 延后一帧，避开 resize 过程中的中间态宽度
        QTimer.singleShot(0, self._relayout_grids)

    def showEvent(self, event):
        super().showEvent(event)
        self._relayout_grids()

    def _relayout_grids(self):
        avail = self._body.width() - 56  # 减去左右内边距
        if avail <= 0:
            return
        for grid in self._grids:
            grid.relayout(avail)
