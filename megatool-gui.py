"""
ARC Raiders - Recycling Lookup GUI
Click a material to see which items recycle into it, ranked by yield.

Data source: MetaForge (https://metaforge.app/arc-raiders)
"""

import json
import math
import os
import sys
from collections import defaultdict

from PyQt6.QtCore import (
    Qt, QUrl, QSize, pyqtSignal, QObject, QTimer, QPropertyAnimation,
    QEasingCurve, QPoint, QRect, QParallelAnimationGroup, QEvent,
)
from PyQt6.QtGui import (
    QPixmap, QColor, QPainter, QFont, QIcon, QLinearGradient, QPen,
    QFontDatabase, QRadialGradient,
)
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QScrollArea, QLabel, QLineEdit, QFrame, QStackedWidget,
    QSizePolicy, QGraphicsOpacityEffect, QPushButton,
)

INPUT_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arc_raiders_recycling.json")
GID_JSON = os.path.join(os.environ.get("APPDATA", ""), "PioneerTool", "item_names.json")
ICON_URL = "https://cdn.metaforge.app/arc-raiders/icons/{}.webp"

RARITY_COLORS = {
    "Common": "#9ca3af",
    "Uncommon": "#22c55e",
    "Rare": "#3b82f6",
    "Epic": "#a855f7",
    "Legendary": "#f59e0b",
}

DARK_STYLE = """
QMainWindow, QWidget { background-color: #0f1923; color: #e2e8f0; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QLineEdit {
    background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
    border-radius: 6px; padding: 8px 12px; font-size: 14px;
}
QLineEdit:focus { border-color: #00d4aa; }
QLabel { color: #e2e8f0; }
QPushButton#backBtn {
    background: #1e293b; color: #00d4aa; border: 1px solid #334155;
    border-radius: 6px; padding: 8px 16px; font-size: 13px; font-weight: bold;
}
QPushButton#backBtn:hover { background: #253348; border-color: #00d4aa; }
QPushButton#menuBtn {
    background: #1e293b; color: #e2e8f0; border: 1px solid #334155;
    border-radius: 12px; padding: 30px 40px; font-size: 20px; font-weight: bold;
    min-width: 260px; min-height: 80px;
}
QPushButton#menuBtn:hover { background: #253348; border-color: #00d4aa; color: #00d4aa; }
"""


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_data():
    with open(INPUT_JSON, "r", encoding="utf-8") as f:
        return json.load(f)


def load_gid_map():
    """Load GID->name mapping from PioneerTool and return name->GID reverse lookup."""
    if not os.path.exists(GID_JSON):
        return {}
    try:
        with open(GID_JSON, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {}

    # Build normalized name -> GID lookup
    name_to_gid = {}
    for gid, name in raw.items():
        name_to_gid[name.strip().lower()] = gid

    return name_to_gid


# MetaForge name -> PioneerTool name (where they differ)
NAME_OVERRIDES = {
    "Bison Driver": "Hornet Driver",
    "Hullcracker IV ": "Hullcracker IV",
    "Wolfpack": "WolfPack",
}


def match_gid(item_name, name_to_gid):
    """Find GID for an item name, handling minor mismatches."""
    name = NAME_OVERRIDES.get(item_name, item_name).strip()
    key = name.lower()
    if key in name_to_gid:
        return name_to_gid[key]
    # Try stripping punctuation/spacing differences
    normalized = key.replace(".", "").replace(" ", "").replace("'", "")
    for stored, gid in name_to_gid.items():
        if stored.replace(".", "").replace(" ", "").replace("'", "") == normalized:
            return gid
    return None


def build_material_index(data, name_to_gid):
    """material name -> sorted list of source items."""
    materials = defaultdict(list)
    item_lookup = {}

    item_info = {}
    for item in data:
        comps = item["recycle_components"]
        item_info[item["id"]] = {
            "total_yield": sum(c["quantity"] for c in comps),
            "all_components": comps,
        }
        item["gid"] = match_gid(item["name"], name_to_gid)
        item_lookup[item["name"]] = item

    for item in data:
        info = item_info[item["id"]]
        for comp in item["recycle_components"]:
            also = [
                f"{c['quantity']}x {c['name']}"
                for c in info["all_components"]
                if c["name"] != comp["name"]
            ]
            materials[comp["name"]].append({
                "item": item["name"],
                "id": item["id"],
                "gid": item.get("gid"),
                "rarity": item["rarity"],
                "yield": comp["quantity"],
                "total_yield": info["total_yield"],
                "also_gives": also,
            })

    for mat in materials:
        materials[mat].sort(key=lambda x: (-x["yield"], -x["total_yield"]))

    return dict(materials), item_lookup


def resolve_material_id(mat_name, item_lookup):
    if mat_name in item_lookup:
        return item_lookup[mat_name]["id"]
    return mat_name.lower().replace(" ", "-")


# ---------------------------------------------------------------------------
# Async image loader
# ---------------------------------------------------------------------------

class ImageCache(QObject):
    image_ready = pyqtSignal(str, QPixmap)

    def __init__(self):
        super().__init__()
        self._cache = {}
        self._pending = set()
        self._nam = QNetworkAccessManager(self)
        self._nam.finished.connect(self._on_finished)

    def get(self, item_id, size=48):
        key = f"{item_id}_{size}"
        if key in self._cache:
            return self._cache[key]
        if key not in self._pending:
            self._pending.add(key)
            url = ICON_URL.format(item_id)
            req = QNetworkRequest(QUrl(url))
            req.setAttribute(QNetworkRequest.Attribute.User, f"{item_id}||{size}")
            self._nam.get(req)
        return self._placeholder(item_id, size)

    def _on_finished(self, reply: QNetworkReply):
        meta = reply.request().attribute(QNetworkRequest.Attribute.User)
        item_id, size_str = meta.split("||")
        size = int(size_str)
        key = f"{item_id}_{size}"
        self._pending.discard(key)
        if reply.error() == QNetworkReply.NetworkError.NoError:
            data = reply.readAll()
            pm = QPixmap()
            pm.loadFromData(bytes(data))
            if not pm.isNull():
                pm = pm.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
                self._cache[key] = pm
                self.image_ready.emit(key, pm)
        reply.deleteLater()

    def _placeholder(self, item_id, size):
        pm = QPixmap(size, size)
        pm.fill(QColor("#1e293b"))
        p = QPainter(pm)
        p.setPen(QColor("#475569"))
        p.setFont(QFont("Arial", size // 3))
        letter = item_id[0].upper() if item_id else "?"
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, letter)
        p.end()
        return pm


# ---------------------------------------------------------------------------
# Grid card for a material (shop-style product tile)
# ---------------------------------------------------------------------------

class MaterialCard(QFrame):
    clicked = pyqtSignal(str)

    def __init__(self, mat_name, source_count, icon_id, image_cache):
        super().__init__()
        self.mat_name = mat_name
        self._icon_id = icon_id
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet(
            "MaterialCard { background: #1e293b; border: 1px solid #334155; border-radius: 12px; }"
            "MaterialCard:hover { border-color: #00d4aa; background: #1e3a4a; }"
        )
        self.setMinimumSize(160, 200)
        self.setMaximumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Icon area
        icon_container = QWidget()
        icon_container.setFixedHeight(120)
        icon_container.setStyleSheet("background: #162032; border-radius: 12px 12px 0 0;")
        ic_layout = QVBoxLayout(icon_container)
        ic_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(72, 72)
        self._icon_label.setPixmap(image_cache.get(icon_id, 72))
        self._icon_label.setScaledContents(True)
        self._icon_label.setStyleSheet("background: transparent;")
        ic_layout.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_container)

        # Text area
        text_area = QWidget()
        text_area.setStyleSheet("background: transparent;")
        ta_layout = QVBoxLayout(text_area)
        ta_layout.setContentsMargins(10, 8, 10, 8)
        ta_layout.setSpacing(2)

        name_label = QLabel(mat_name)
        name_label.setStyleSheet("font-size: 13px; font-weight: bold; background: transparent;")
        name_label.setWordWrap(True)
        name_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        ta_layout.addWidget(name_label)

        count_label = QLabel(f"{source_count} sources")
        count_label.setStyleSheet("font-size: 11px; color: #00d4aa; background: transparent;")
        ta_layout.addWidget(count_label)

        ta_layout.addStretch()
        layout.addWidget(text_area)

        image_cache.image_ready.connect(self._on_image)

    def _on_image(self, key, pixmap):
        if key == f"{self._icon_id}_72":
            self._icon_label.setPixmap(pixmap)

    def mousePressEvent(self, event):
        self.clicked.emit(self.mat_name)


# ---------------------------------------------------------------------------
# Item card inside the detail view (shop-style product in grid)
# ---------------------------------------------------------------------------

class ItemCard(QFrame):
    def __init__(self, source, image_cache):
        super().__init__()
        self.setStyleSheet(
            "ItemCard { background: #1e293b; border: 1px solid #334155; border-radius: 12px; }"
        )
        self.setMinimumSize(170, 260)
        self.setMaximumHeight(260)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Icon area with yield badge overlay
        icon_container = QWidget()
        icon_container.setFixedHeight(110)
        icon_container.setStyleSheet("background: #162032; border-radius: 12px 12px 0 0;")
        ic_layout = QVBoxLayout(icon_container)
        ic_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._icon_label = QLabel()
        self._icon_label.setFixedSize(64, 64)
        self._icon_label.setPixmap(image_cache.get(source["id"], 64))
        self._icon_label.setScaledContents(True)
        self._icon_label.setStyleSheet("background: transparent;")
        ic_layout.addWidget(self._icon_label, alignment=Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon_container)

        self._source_id = source["id"]
        image_cache.image_ready.connect(self._on_image)

        # Info area
        info_area = QWidget()
        info_area.setStyleSheet("background: transparent;")
        info_layout = QVBoxLayout(info_area)
        info_layout.setContentsMargins(10, 8, 10, 8)
        info_layout.setSpacing(4)

        # Name
        name_label = QLabel(source["item"])
        name_label.setStyleSheet("font-size: 12px; font-weight: bold; background: transparent;")
        name_label.setWordWrap(True)
        info_layout.addWidget(name_label)

        # Rarity + yield row
        badge_row = QHBoxLayout()
        badge_row.setSpacing(6)

        rarity = source["rarity"]
        rc = RARITY_COLORS.get(rarity, "#9ca3af")
        rarity_label = QLabel(rarity)
        rarity_label.setStyleSheet(
            f"font-size: 10px; color: {rc}; background: rgba(0,0,0,0.4); "
            f"border: 1px solid {rc}; border-radius: 4px; padding: 1px 5px;"
        )
        rarity_label.setFixedHeight(18)
        badge_row.addWidget(rarity_label)

        badge_row.addStretch()

        yield_label = QLabel(f"{source['yield']}x")
        yield_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: #4ade80; background: #0d3320; "
            "border: 1px solid #16a34a; border-radius: 6px; padding: 2px 8px;"
        )
        yield_label.setFixedHeight(26)
        badge_row.addWidget(yield_label)

        info_layout.addLayout(badge_row)

        # Also gives
        if source["also_gives"]:
            also_text = ", ".join(source["also_gives"])
            also_label = QLabel(f"+ {also_text}")
            also_label.setStyleSheet("font-size: 10px; color: #94a3b8; background: transparent;")
            also_label.setWordWrap(True)
            info_layout.addWidget(also_label)

        # GID
        gid = source.get("gid")
        if gid:
            gid_label = QLabel(f"GID: {gid}")
            gid_label.setStyleSheet("font-size: 9px; color: #475569; background: transparent;")
            info_layout.addWidget(gid_label)

        info_layout.addStretch()
        layout.addWidget(info_area)

    def _on_image(self, key, pixmap):
        if key == f"{self._source_id}_64":
            self._icon_label.setPixmap(pixmap)


# ---------------------------------------------------------------------------
# Animated menu card
# ---------------------------------------------------------------------------

class MenuCard(QFrame):
    """A single menu tile with painted glow border animation."""
    clicked = pyqtSignal(str)

    ACCENT_COLORS = {
        "Moneymaker": ("#f59e0b", "#d97706"),
        "Autocraft":  ("#3b82f6", "#2563eb"),
        "Materials":  ("#00d4aa", "#00a882"),
    }
    DESCRIPTIONS = {
        "Moneymaker": "Profit optimization",
        "Autocraft":  "Automated crafting",
        "Materials":  "Recycling database",
    }

    def __init__(self, name):
        super().__init__()
        self._name = name
        self._glow = 0.0
        self._hovered = False
        self._mouse_pos = QPoint(140, 90)  # center default
        accent, _ = self.ACCENT_COLORS.get(name, ("#00d4aa", "#00a882"))

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFixedSize(280, 180)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 28, 24, 20)
        layout.setSpacing(8)

        title = QLabel(name)
        title.setStyleSheet(
            f"font-size: 22px; font-weight: bold; color: {accent}; background: transparent;"
        )
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(title)

        desc = QLabel(self.DESCRIPTIONS.get(name, ""))
        desc.setStyleSheet("font-size: 12px; color: #64748b; background: transparent;")
        desc.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(desc)

        layout.addStretch()

        arrow = QLabel(">")
        arrow.setStyleSheet(f"font-size: 20px; color: {accent}; background: transparent;")
        arrow.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
        arrow.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(arrow)

        # Glow pulse timer
        self._glow_timer = QTimer(self)
        self._glow_timer.timeout.connect(self._tick)
        self._glow_timer.start(30)

    def _tick(self):
        if self._hovered:
            self._glow = min(self._glow + 0.08, 1.0)
        else:
            self._glow = max(self._glow - 0.15, 0.0)
        if self._glow > 0:
            self.update()

    def enterEvent(self, event):
        self._hovered = True

    def leaveEvent(self, event):
        self._hovered = False

    def mouseMoveEvent(self, event):
        self._mouse_pos = event.position().toPoint()
        if self._hovered:
            self.update()

    def mousePressEvent(self, event):
        self.clicked.emit(self._name)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        accent, accent_dark = self.ACCENT_COLORS.get(self._name, ("#00d4aa", "#00a882"))
        r = self.rect().adjusted(1, 1, -1, -1)

        # Background
        bg = QColor("#1e293b") if self._glow < 0.01 else QColor("#1e293b").lighter(100 + int(10 * self._glow))
        p.setBrush(bg)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(r, 14, 14)

        # Border — static dim + animated glow
        base_alpha = 60
        glow_alpha = int(base_alpha + 195 * self._glow)
        ac = QColor(accent)
        ac.setAlpha(glow_alpha)
        pen = QPen(ac)
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(r, 14, 14)

        # Mouse-following glow bloom (subtle)
        if self._glow > 0.05:
            glow_color = QColor(accent)
            glow_color.setAlpha(int(18 * self._glow))
            grad = QRadialGradient(
                float(self._mouse_pos.x()), float(self._mouse_pos.y()), 120,
            )
            grad.setColorAt(0, glow_color)
            grad.setColorAt(1, QColor(0, 0, 0, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawRoundedRect(r, 14, 14)

        p.end()


# ---------------------------------------------------------------------------
# Main menu page
# ---------------------------------------------------------------------------

class MainMenuPage(QWidget):
    menu_selected = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._home_positions = None  # cached after first layout
        self._anim_group = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Card area — horizontal row, vertically centred
        card_container = QWidget()
        card_outer = QVBoxLayout(card_container)
        card_outer.setContentsMargins(0, 0, 0, 0)
        card_outer.addStretch()

        card_row = QHBoxLayout()
        card_row.setSpacing(28)
        card_row.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._cards = []
        for name in ("Moneymaker", "Autocraft", "Materials"):
            card = MenuCard(name)
            card.clicked.connect(self.menu_selected.emit)
            self._cards.append(card)
            card_row.addWidget(card)

        card_outer.addLayout(card_row)
        card_outer.addStretch()
        layout.addWidget(card_container, 1)

        # Prepare opacity effects for entrance animation
        self._opacities = []
        for card in self._cards:
            eff = QGraphicsOpacityEffect(card)
            eff.setOpacity(0.0)
            card.setGraphicsEffect(eff)
            self._opacities.append(eff)

    def showEvent(self, event):
        super().showEvent(event)
        # Reset cards to invisible; defer animation so layout settles first
        for eff in self._opacities:
            eff.setOpacity(0.0)
        QTimer.singleShot(0, self._play_entrance)

    def _play_entrance(self):
        """Staggered slide-up + fade-in for each card."""
        # Stop any running animation
        if self._anim_group is not None:
            self._anim_group.stop()

        # Snapshot settled positions on first run
        if self._home_positions is None:
            self._home_positions = [card.pos() for card in self._cards]

        self._running_anims = []  # prevent GC

        for i, (card, eff) in enumerate(zip(self._cards, self._opacities)):
            home = QPoint(self._home_positions[i])
            eff.setOpacity(0.0)
            card.move(home + QPoint(0, 40))

            # Fade in
            fade = QPropertyAnimation(eff, b"opacity")
            fade.setDuration(400)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.setEasingCurve(QEasingCurve.Type.OutCubic)

            # Slide up
            slide = QPropertyAnimation(card, b"pos")
            slide.setDuration(400)
            slide.setStartValue(home + QPoint(0, 40))
            slide.setEndValue(home)
            slide.setEasingCurve(QEasingCurve.Type.OutCubic)

            # Stagger via timer
            QTimer.singleShot(i * 100, fade.start)
            QTimer.singleShot(i * 100, slide.start)

            self._running_anims.append(fade)
            self._running_anims.append(slide)


class EmptySubPage(QWidget):
    """Placeholder page for submenus that are not yet implemented."""
    back_clicked = pyqtSignal()

    def __init__(self, page_title):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        top_bar = QWidget()
        top_bar.setStyleSheet("background: #0f1923;")
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(20, 14, 20, 14)

        back_btn = QPushButton("< Back")
        back_btn.setObjectName("backBtn")
        back_btn.clicked.connect(self.back_clicked.emit)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tb_layout.addWidget(back_btn)

        title = QLabel(page_title)
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #00d4aa; margin-left: 12px;")
        tb_layout.addWidget(title)

        tb_layout.addStretch()
        layout.addWidget(top_bar)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #1e293b;")
        layout.addWidget(sep)

        # Empty content
        placeholder = QLabel("Coming soon...")
        placeholder.setStyleSheet("font-size: 16px; color: #475569;")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(placeholder, 1)


# ---------------------------------------------------------------------------
# Browse page (grid of materials)
# ---------------------------------------------------------------------------

class BrowsePage(QWidget):
    material_selected = pyqtSignal(str)
    back_clicked = pyqtSignal()

    def __init__(self, material_index, item_lookup, image_cache):
        super().__init__()
        self._tiles = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Top bar
        top_bar = QWidget()
        top_bar.setStyleSheet("background: #0f1923;")
        tb_layout = QHBoxLayout(top_bar)
        tb_layout.setContentsMargins(20, 14, 20, 14)

        back_btn = QPushButton("< Back")
        back_btn.setObjectName("backBtn")
        back_btn.clicked.connect(self.back_clicked.emit)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tb_layout.addWidget(back_btn)

        title = QLabel("Materials")
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #00d4aa; margin-left: 12px;")
        tb_layout.addWidget(title)

        subtitle = QLabel("ARC Raiders Recycling Database")
        subtitle.setStyleSheet("font-size: 13px; color: #64748b; margin-left: 12px;")
        tb_layout.addWidget(subtitle)

        tb_layout.addStretch()

        self._search = QLineEdit()
        self._search.setPlaceholderText("Search materials...")
        self._search.setFixedWidth(260)
        self._search.textChanged.connect(self._filter)
        tb_layout.addWidget(self._search)

        layout.addWidget(top_bar)

        # Separator
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #1e293b;")
        layout.addWidget(sep)

        # Grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        inner = QVBoxLayout(scroll_widget)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)

        grid_container = QWidget()
        self._grid = QGridLayout(grid_container)
        self._grid.setContentsMargins(20, 20, 20, 20)
        self._grid.setSpacing(16)
        for col in range(6):
            self._grid.setColumnStretch(col, 1)
        inner.addWidget(grid_container)
        inner.addStretch()

        sorted_mats = sorted(material_index.keys())
        self._cards = []
        for i, mat in enumerate(sorted_mats):
            sources = material_index[mat]
            icon_id = resolve_material_id(mat, item_lookup)
            card = MaterialCard(mat, len(sources), icon_id, image_cache)
            card.clicked.connect(self.material_selected.emit)
            self._tiles[mat] = card
            self._cards.append(card)
            self._grid.addWidget(card, i // 6, i % 6)

        self._current_cols = 6

        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)

        self._scroll_widget = scroll_widget
        self._scroll = scroll

    def _rebuild_grid(self):
        """Re-place visible cards into the grid with 6 columns."""
        while self._grid.count():
            self._grid.takeAt(0)
        visible = [c for c in self._cards if not c.isHidden()]
        for i, card in enumerate(visible):
            self._grid.addWidget(card, i // 6, i % 6)

    def _filter(self, text):
        text = text.lower()
        for mat_name, card in self._tiles.items():
            card.setVisible(text in mat_name.lower())
        self._rebuild_grid()


# ---------------------------------------------------------------------------
# Detail page (items for a selected material)
# ---------------------------------------------------------------------------

class DetailPage(QWidget):
    back_clicked = pyqtSignal()

    def __init__(self, image_cache):
        super().__init__()
        self._image_cache = image_cache

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(0)

        # Top bar (built once, content updated)
        self._top_bar = QWidget()
        self._top_bar.setStyleSheet("background: #0f1923;")
        tb_layout = QHBoxLayout(self._top_bar)
        tb_layout.setContentsMargins(20, 14, 20, 14)

        back_btn = QPushButton("< Back")
        back_btn.setObjectName("backBtn")
        back_btn.clicked.connect(self.back_clicked.emit)
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        tb_layout.addWidget(back_btn)

        self._header_icon = QLabel()
        self._header_icon.setFixedSize(40, 40)
        self._header_icon.setScaledContents(True)
        self._header_icon.setStyleSheet("margin-left: 12px; background: transparent;")
        tb_layout.addWidget(self._header_icon)

        self._header_name = QLabel()
        self._header_name.setStyleSheet(
            "font-size: 20px; font-weight: bold; color: #00d4aa; margin-left: 8px;"
        )
        tb_layout.addWidget(self._header_name)

        self._header_count = QLabel()
        self._header_count.setStyleSheet("font-size: 13px; color: #64748b; margin-left: 12px;")
        tb_layout.addWidget(self._header_count)

        tb_layout.addStretch()
        self._layout.addWidget(self._top_bar)

        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #1e293b;")
        self._layout.addWidget(sep)

        # Scrollable grid of item cards
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll_widget = QWidget()
        inner = QVBoxLayout(self._scroll_widget)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(0)
        self._grid_container = QWidget()
        self._grid = QGridLayout(self._grid_container)
        self._grid.setContentsMargins(20, 20, 20, 20)
        self._grid.setSpacing(16)
        for col in range(5):
            self._grid.setColumnStretch(col, 1)
        inner.addWidget(self._grid_container)
        inner.addStretch()
        self._scroll.setWidget(self._scroll_widget)
        self._layout.addWidget(self._scroll)

        self._item_cards = []
        self._header_icon_id = None
        image_cache.image_ready.connect(self._on_header_image)

    def show_material(self, mat_name, sources, icon_id, item_lookup):
        self._header_name.setText(mat_name)
        self._header_count.setText(f"{len(sources)} sources - sorted by highest yield")
        self._header_icon.setPixmap(self._image_cache.get(icon_id, 40))
        self._header_icon_id = icon_id

        for card in self._item_cards:
            card.setParent(None)
            card.deleteLater()
        self._item_cards.clear()

        for source in sources:
            card = ItemCard(source, self._image_cache)
            self._item_cards.append(card)

        while self._grid.count():
            self._grid.takeAt(0)
        for i, card in enumerate(self._item_cards):
            self._grid.addWidget(card, i // 5, i % 5)
        self._scroll.verticalScrollBar().setValue(0)

    def animate_cards(self):
        """No-op; fade-in animation removed to prevent visual glitches."""
        pass

    def _on_header_image(self, key, pixmap):
        if self._header_icon_id and key == f"{self._header_icon_id}_40":
            self._header_icon.setPixmap(pixmap)


# ---------------------------------------------------------------------------
# Animated stacked widget with slide + crossfade transitions
# ---------------------------------------------------------------------------

class AnimatedStack(QStackedWidget):
    """QStackedWidget that crossfades + slides between pages."""
    transition_done = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._transitioning = False
        self._anim = None

    def slide_to(self, index, direction="auto"):
        """Animate to *index*.  direction: 'left', 'right', or 'auto'."""
        if index == self.currentIndex() or self._transitioning:
            return
        self._transitioning = True

        old_widget = self.currentWidget()
        new_widget = self.widget(index)

        # Determine slide direction
        if direction == "auto":
            direction = "left" if index > self.currentIndex() else "right"

        # Grab screenshot of old page as an overlay
        overlay = QLabel(self)
        overlay.setPixmap(old_widget.grab())
        overlay.resize(self.size())
        overlay.show()
        overlay.raise_()

        # Opacity effect for fading out the overlay
        fade_eff = QGraphicsOpacityEffect(overlay)
        fade_eff.setOpacity(1.0)
        overlay.setGraphicsEffect(fade_eff)

        # Switch the stack immediately (new page shows under the overlay)
        self.setCurrentIndex(index)

        # Animate overlay: fade out + slide in the chosen direction
        slide_offset = self.width() // 3
        start_pos = overlay.pos()
        end_x = start_pos.x() + (-slide_offset if direction == "left" else slide_offset)

        fade_anim = QPropertyAnimation(fade_eff, b"opacity")
        fade_anim.setDuration(300)
        fade_anim.setStartValue(1.0)
        fade_anim.setEndValue(0.0)
        fade_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        slide_anim = QPropertyAnimation(overlay, b"pos")
        slide_anim.setDuration(300)
        slide_anim.setStartValue(start_pos)
        slide_anim.setEndValue(QPoint(end_x, start_pos.y()))
        slide_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(fade_anim)
        group.addAnimation(slide_anim)

        def _cleanup():
            overlay.deleteLater()
            self._transitioning = False
            self.transition_done.emit()

        group.finished.connect(_cleanup)
        group.start()
        self._anim = group  # prevent GC


# ---------------------------------------------------------------------------
# Custom title bar
# ---------------------------------------------------------------------------

class TitleBar(QWidget):
    """Themed title bar with drag-to-move, minimize, maximize, close."""
    minimize_clicked = pyqtSignal()
    maximize_clicked = pyqtSignal()
    close_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(38)
        self.setStyleSheet("background: #0a1018;")
        self._drag_pos = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(0)

        # App icon dot
        dot = QLabel()
        dot.setFixedSize(12, 12)
        dot.setStyleSheet(
            "background: #00d4aa; border-radius: 6px; margin-right: 8px;"
        )
        layout.addWidget(dot)

        # Title text
        self._title = QLabel("MEGa TOOL")
        self._title.setStyleSheet(
            "font-size: 12px; font-weight: bold; color: #64748b; "
            "background: transparent; letter-spacing: 1px;"
        )
        layout.addWidget(self._title)

        subtitle = QLabel("ARC Raiders Toolkit")
        subtitle.setStyleSheet(
            "font-size: 11px; color: #475569; background: transparent; margin-left: 8px;"
        )
        layout.addWidget(subtitle)

        layout.addStretch()

        # Window control buttons
        btn_style = (
            "QPushButton {{ background: transparent; color: {color}; "
            "border: none; font-size: {size}px; padding: 0; "
            "font-family: Arial; }}"
            "QPushButton:hover {{ background: {hover_bg}; }}"
        )

        for symbol, color, hover_bg, size, signal in [
            ("\u2013", "#64748b", "#1e293b", 14, self.minimize_clicked),   # –
            ("\u25a1", "#64748b", "#1e293b", 12, self.maximize_clicked),   # □
            ("\u2715", "#64748b", "#dc2626", 12, self.close_clicked),      # ✕
        ]:
            btn = QPushButton(symbol)
            btn.setFixedSize(40, 38)
            btn.setStyleSheet(btn_style.format(color=color, hover_bg=hover_bg, size=size))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(signal.emit)
            layout.addWidget(btn)

    def set_title(self, text):
        self._title.setText(text)

    # -- Drag to move --
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.window().pos()

    def mouseMoveEvent(self, event):
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            win = self.window()
            if win.isMaximized():
                # Un-maximize and reposition so cursor stays proportional
                old_width = win.width()
                win.showNormal()
                ratio = event.globalPosition().toPoint().x() / old_width
                new_x = int(event.globalPosition().toPoint().x() - win.width() * ratio)
                new_y = event.globalPosition().toPoint().y() - self.height() // 2
                win.move(new_x, new_y)
                self._drag_pos = event.globalPosition().toPoint() - win.pos()
            else:
                win.move(event.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, event):
        self._drag_pos = None

    def mouseDoubleClickEvent(self, event):
        self.maximize_clicked.emit()


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

RESIZE_MARGIN = 6  # px border for resize handles

class MainWindow(QMainWindow):
    def __init__(self, material_index, item_lookup):
        super().__init__()
        self.setWindowTitle("MEGa TOOL - ARC Raiders")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.resize(1100, 750)

        self._index = material_index
        self._item_lookup = item_lookup
        self._image_cache = ImageCache()
        self._resize_edge = None
        self._resize_start_pos = None
        self._resize_start_geo = None

        # Central wrapper
        central = QWidget()
        central.setStyleSheet(
            "QWidget#centralWrapper { background: #0f1923; border: 1px solid #1e293b; }"
        )
        central.setObjectName("centralWrapper")
        wrapper = QVBoxLayout(central)
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.setSpacing(0)

        # Title bar
        self._title_bar = TitleBar()
        self._title_bar.minimize_clicked.connect(self.showMinimized)
        self._title_bar.maximize_clicked.connect(self._toggle_maximize)
        self._title_bar.close_clicked.connect(self.close)
        wrapper.addWidget(self._title_bar)

        # Thin separator under title bar
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #1e293b;")
        wrapper.addWidget(sep)

        # Page stack
        self._stack = AnimatedStack()
        self._stack.transition_done.connect(self._on_transition_done)
        wrapper.addWidget(self._stack, 1)

        self.setCentralWidget(central)

        # Page 0: Main menu
        self._menu = MainMenuPage()
        self._menu.menu_selected.connect(self._on_menu)
        self._stack.addWidget(self._menu)

        # Page 1: Moneymaker (empty)
        self._moneymaker = EmptySubPage("Moneymaker")
        self._moneymaker.back_clicked.connect(self._go_back)
        self._stack.addWidget(self._moneymaker)

        # Page 2: Autocraft (empty)
        self._autocraft = EmptySubPage("Autocraft")
        self._autocraft.back_clicked.connect(self._go_back)
        self._stack.addWidget(self._autocraft)

        # Page 3: Browse materials
        self._browse = BrowsePage(material_index, item_lookup, self._image_cache)
        self._browse.material_selected.connect(self._open_material)
        self._browse.back_clicked.connect(self._go_back)
        self._stack.addWidget(self._browse)

        # Page 4: Detail view
        self._detail = DetailPage(self._image_cache)
        self._detail.back_clicked.connect(self._go_back)
        self._stack.addWidget(self._detail)

        self._page_map = {
            "Moneymaker": 1,
            "Autocraft": 2,
            "Materials": 3,
        }
        self._nav_history = []  # stack of previous page indices

        # Catch mouse back button on any child widget
        QApplication.instance().installEventFilter(self)

    def _on_transition_done(self):
        """Trigger entry animations on the newly visible page."""
        if self._stack.currentWidget() is self._detail:
            self._detail.animate_cards()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.BackButton:
                self._go_back()
                return True
        return super().eventFilter(obj, event)

    def _toggle_maximize(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _navigate_to(self, index, direction="left"):
        """Push current page onto history and navigate forward."""
        self._nav_history.append(self._stack.currentIndex())
        self._stack.slide_to(index, direction=direction)

    def _go_back(self):
        """Pop history and navigate back one level."""
        if self._nav_history:
            prev = self._nav_history.pop()
            self._stack.slide_to(prev, direction="right")

    def _on_menu(self, name):
        idx = self._page_map.get(name, 0)
        self._navigate_to(idx)

    def _open_material(self, mat_name):
        sources = self._index[mat_name]
        icon_id = resolve_material_id(mat_name, self._item_lookup)
        self._detail.show_material(mat_name, sources, icon_id, self._item_lookup)
        self._navigate_to(4)

    # -- Frameless resize handling --

    def _edge_at(self, pos):
        """Return which edge(s) the cursor is near, or None."""
        r = self.rect()
        m = RESIZE_MARGIN
        edges = []
        if pos.x() <= m:
            edges.append("left")
        elif pos.x() >= r.width() - m:
            edges.append("right")
        if pos.y() <= m:
            edges.append("top")
        elif pos.y() >= r.height() - m:
            edges.append("bottom")
        return tuple(edges) if edges else None

    _CURSOR_MAP = {
        ("left",): Qt.CursorShape.SizeHorCursor,
        ("right",): Qt.CursorShape.SizeHorCursor,
        ("top",): Qt.CursorShape.SizeVerCursor,
        ("bottom",): Qt.CursorShape.SizeVerCursor,
        ("left", "top"): Qt.CursorShape.SizeFDiagCursor,
        ("right", "bottom"): Qt.CursorShape.SizeFDiagCursor,
        ("right", "top"): Qt.CursorShape.SizeBDiagCursor,
        ("left", "bottom"): Qt.CursorShape.SizeBDiagCursor,
        ("top", "left"): Qt.CursorShape.SizeFDiagCursor,
        ("bottom", "right"): Qt.CursorShape.SizeFDiagCursor,
        ("top", "right"): Qt.CursorShape.SizeBDiagCursor,
        ("bottom", "left"): Qt.CursorShape.SizeBDiagCursor,
    }

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and not self.isMaximized():
            edge = self._edge_at(event.position().toPoint())
            if edge:
                self._resize_edge = edge
                self._resize_start_pos = event.globalPosition().toPoint()
                self._resize_start_geo = self.geometry()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._resize_edge and self._resize_start_pos:
            delta = event.globalPosition().toPoint() - self._resize_start_pos
            geo = QRect(self._resize_start_geo)
            min_w, min_h = 600, 400
            if "right" in self._resize_edge:
                geo.setRight(max(geo.left() + min_w, self._resize_start_geo.right() + delta.x()))
            if "bottom" in self._resize_edge:
                geo.setBottom(max(geo.top() + min_h, self._resize_start_geo.bottom() + delta.y()))
            if "left" in self._resize_edge:
                new_left = min(geo.right() - min_w, self._resize_start_geo.left() + delta.x())
                geo.setLeft(new_left)
            if "top" in self._resize_edge:
                new_top = min(geo.bottom() - min_h, self._resize_start_geo.top() + delta.y())
                geo.setTop(new_top)
            self.setGeometry(geo)
        else:
            edge = self._edge_at(event.position().toPoint())
            if edge and not self.isMaximized():
                self.setCursor(self._CURSOR_MAP.get(edge, Qt.CursorShape.ArrowCursor))
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._resize_edge = None
        self._resize_start_pos = None
        self._resize_start_geo = None
        super().mouseReleaseEvent(event)


# ---------------------------------------------------------------------------
# Splash Screen
# ---------------------------------------------------------------------------

class SplashScreen(QWidget):
    finished = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(520, 320)

        screen = QApplication.primaryScreen().geometry()
        self.move(
            (screen.width() - self.width()) // 2,
            (screen.height() - self.height()) // 2,
        )

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)

        self._fade_in = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_in.setDuration(600)
        self._fade_in.setStartValue(0.0)
        self._fade_in.setEndValue(1.0)
        self._fade_in.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade_in.finished.connect(self._on_fade_in_done)

        self._fade_out = QPropertyAnimation(self._opacity, b"opacity")
        self._fade_out.setDuration(500)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self._fade_out.finished.connect(self._on_fade_out_done)

        self._glow_timer = QTimer(self)
        self._glow_timer.timeout.connect(self._glow_tick)
        self._glow_val = 0.0

    def start(self):
        self.show()
        self._fade_in.start()
        self._glow_timer.start(30)

    def _on_fade_in_done(self):
        QTimer.singleShot(1800, self._begin_fade_out)

    def _begin_fade_out(self):
        self._glow_timer.stop()
        self._fade_out.start()

    def _on_fade_out_done(self):
        self.close()
        self.finished.emit()

    def _glow_tick(self):
        self._glow_val += 0.04
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        glow = (math.sin(self._glow_val) + 1) / 2

        grad = QLinearGradient(0, 0, self.width(), self.height())
        grad.setColorAt(0, QColor("#0a0f1a"))
        grad.setColorAt(0.5, QColor("#0f1923"))
        grad.setColorAt(1, QColor("#0a1628"))
        p.setBrush(grad)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 16, 16)

        border_alpha = int(120 + 80 * glow)
        pen = QPen(QColor(0, 212, 170, border_alpha))
        pen.setWidth(2)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 15, 15)

        mega_font = QFont("Arial Black", 52, QFont.Weight.Black)
        p.setFont(mega_font)
        glow_color = QColor(0, 212, 170, int(60 + 40 * glow))
        p.setPen(glow_color)
        mega_rect = self.rect().adjusted(0, -30, 0, -30)
        p.drawText(mega_rect, Qt.AlignmentFlag.AlignCenter, "MEGa")

        mega_grad = QLinearGradient(0, 80, 0, 180)
        mega_grad.setColorAt(0, QColor("#00ffcc"))
        mega_grad.setColorAt(0.5, QColor("#00d4aa"))
        mega_grad.setColorAt(1, QColor("#00a882"))
        pen = QPen()
        pen.setBrush(mega_grad)
        pen.setWidth(1)
        p.setPen(pen)
        p.drawText(mega_rect, Qt.AlignmentFlag.AlignCenter, "MEGa")

        tool_font = QFont("Arial", 28, QFont.Weight.Normal)
        tool_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 18)
        p.setFont(tool_font)
        p.setPen(QColor("#94a3b8"))
        tool_rect = self.rect().adjusted(0, 50, 0, 0)
        p.drawText(tool_rect, Qt.AlignmentFlag.AlignCenter, "TOOL")

        sub_font = QFont("Arial", 11)
        p.setFont(sub_font)
        p.setPen(QColor(148, 163, 184, int(140 + 60 * glow)))
        sub_rect = self.rect().adjusted(0, 130, 0, 0)
        p.drawText(sub_rect, Qt.AlignmentFlag.AlignCenter, "ARC Raiders Recycling Database")

        line_w = int(200 + 60 * glow)
        cx = self.width() // 2
        y = self.height() - 35
        line_grad = QLinearGradient(cx - line_w // 2, y, cx + line_w // 2, y)
        line_grad.setColorAt(0, QColor(0, 212, 170, 0))
        line_grad.setColorAt(0.3, QColor(0, 212, 170, int(180 * glow)))
        line_grad.setColorAt(0.5, QColor(0, 212, 170, 200))
        line_grad.setColorAt(0.7, QColor(0, 212, 170, int(180 * glow)))
        line_grad.setColorAt(1, QColor(0, 212, 170, 0))
        pen = QPen()
        pen.setBrush(line_grad)
        pen.setWidth(2)
        p.setPen(pen)
        p.drawLine(cx - line_w // 2, y, cx + line_w // 2, y)

        p.end()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    if not os.path.exists(INPUT_JSON):
        print(f"Error: {INPUT_JSON} not found. Run arc_recycling.py first.")
        sys.exit(1)

    data = load_data()
    name_to_gid = load_gid_map()
    material_index, item_lookup = build_material_index(data, name_to_gid)

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLE)

    window = MainWindow(material_index, item_lookup)

    splash = SplashScreen()
    splash.finished.connect(window.show)
    splash.start()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
