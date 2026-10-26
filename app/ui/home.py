"""The Vehicles tab's landing page: the showroom as a hero plus shelves.

What the customer sees first (app/ui/main_window.py's default tab):

    hero carousel    the newest of each make, bookable from the slide itself
    Browse & filter  one brand plus an inclusive daily-price range
    Browse by type   one chip per body style the catalog actually holds
    shelves          "Best cars for 4 seaters", "Popular cars for road trips",
                     and the rest of vehicle_service.SHOP_SECTIONS

"See all", the type chips and the top bar's search all open the same catalog
grid (app/ui/vehicles.py's CatalogPage) over one in-memory snapshot taken at
startup, so nothing on this page waits on a second database round trip.

Photographs load only once a card actually scrolls into view -- the snapshot
holds ~900 vehicles, and eager cards would queue every shelf's CarImages
fetches at once. The hero prefetches its neighbouring slides instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QLinearGradient,
    QPainter,
)
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.services import vehicle_service
from app.services.errors import ServiceError
from app.ui import theme
from app.ui.theme import (
    TOKENS,
    ghost_button,
    hint_label,
    make_label,
    overline,
    per_day_text,
    pill,
    primary_button,
    section_title,
    text_button,
)
from app.ui.vehicle_photo import VehiclePhoto, warm_thumbnail
from app.ui.vehicles import CatalogPage, VehicleCard, card_for

#: The hero stage: the giant name and the car share this band. The car is a
#: transparent cutout drawn over the name, so the name reads behind it.
HERO_STAGE_HEIGHT = 516
HERO_NAME_TOP = 4
HERO_PHOTO_TOP = 100

#: On short windows the hero gives vertical room back so the Browse & Filter
#: bar -- the primary way into the catalog -- is reachable without scrolling.
#: 1024x768 is the tight target: its customer viewport is only ~696px tall.
HERO_STAGE_HEIGHT_COMPACT = 380
HERO_COMPACT_BELOW = 810

#: Every name is set at this size and only shrinks when it would run wide
#: (the reference's type is one size; the short names simply look shorter).
HERO_NAME_PX = 200
HERO_NAME_MIN = 48
HERO_NAME_WIDTH_SHARE = 0.9

#: Slide transition: the name fades and rises, the photo fades in from blank.
HERO_FADE_MS = 340
HERO_RISE_PX = 16

#: How long a slide holds before the showroom moves on by itself.
HERO_AUTOPLAY_MS = 6500

#: One shelf card, and how far the arrows travel per click.
CARD_WIDTH = 296
SHELF_STEP_SHARE = 0.85
SHELF_SPACING = 16

#: Between a scroll and the photo sweep that follows it.
SWEEP_DEBOUNCE_MS = 120

#: How wide the gradient over each end of a shelf is. A card clipped by the
#: viewport fades into the page instead of ending on a hard vertical line.
SHELF_FADE_WIDTH = 64


class _EdgeFade(QWidget):
    """One end of a shelf painted with the page's own colour.

    The strip is transparent for the mouse, so it never steals a card click or
    a wheel event; it only softens what the viewport cuts off.
    """

    def __init__(self, left: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._left = left
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:
        colour = QColor(TOKENS["bg_cream"])
        transparent = QColor(colour.red(), colour.green(), colour.blue(), 0)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        if self._left:
            gradient.setColorAt(0.0, colour)
            gradient.setColorAt(1.0, transparent)
        else:
            gradient.setColorAt(0.0, transparent)
            gradient.setColorAt(1.0, colour)
        painter = QPainter(self)
        painter.fillRect(self.rect(), QBrush(gradient))


class HeroCarousel(QWidget):
    """One slide at a time -- the newest of each make -- with arrows to walk.

    Layout follows the showroom reference: the model name is huge type on the
    cream page, the car sits over its lower half, price bottom-left and the
    two actions bottom-right. CarImages returns the cars as WebP cutouts, so
    the photo label needs no white plate -- the name shows through around the
    bodywork exactly as it did in the mock.
    """

    vehicle_selected = Signal(int)
    book_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("hero")
        self._vehicles: list[vehicle_service.ShopVehicle] = []
        self._index = 0
        self._hovered = False
        self._name_rise: QPropertyAnimation | None = None
        self._stage_height = HERO_STAGE_HEIGHT

        self.eyebrow = make_label("", "heroEyebrow")
        self.eyebrow.setAlignment(Qt.AlignCenter)

        # The stage positions its two children by hand so they overlap.
        self.stage = QWidget(self)
        self.stage.setObjectName("heroStage")
        self.stage.setFixedHeight(self._stage_height)

        self.name = make_label("", "heroName")
        self.name.setParent(self.stage)
        self.name.setAlignment(Qt.AlignCenter)

        self.photo = VehiclePhoto(
            self.stage, thumbnail=True, cutout=True, skeleton=False
        )

        self.price = make_label("", "heroPrice")
        self.specs = make_label("", "heroSpecs")

        self.view_button = ghost_button("VIEW DETAILS")
        self.book_button = primary_button("BOOK THIS VEHICLE")
        self.view_button.clicked.connect(self._open_current)
        self.book_button.clicked.connect(self._book_current)

        self.arrow_prev = self._make_arrow(
            "arrow_left", "Previous vehicle", self.previous
        )
        self.arrow_next = self._make_arrow("arrow_right", "Next vehicle", self.next)

        price_col = QVBoxLayout()
        price_col.setContentsMargins(0, 0, 0, 0)
        price_col.setSpacing(2)
        price_col.addWidget(self.price)
        price_col.addWidget(self.specs)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(12)
        bottom.addLayout(price_col)
        bottom.addStretch(1)
        bottom.addWidget(self.view_button, 0, Qt.AlignVCenter)
        bottom.addWidget(self.book_button, 0, Qt.AlignVCenter)

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 6, 0, 0)
        col.setSpacing(10)
        col.addWidget(self.eyebrow, 0, Qt.AlignHCenter)
        col.addWidget(self.stage)
        col.addLayout(bottom)

        # The layout reparents its widgets above children created earlier
        # (the arrows); put the arrows back on top of the stage.
        self.arrow_prev.raise_()
        self.arrow_next.raise_()

        # The stage's width settles one event-loop turn after a resize.
        self._place_timer = QTimer(self)
        self._place_timer.setSingleShot(True)
        self._place_timer.setInterval(0)
        self._place_timer.timeout.connect(self._place_stage)

        self._autoplay = QTimer(self)
        self._autoplay.setInterval(HERO_AUTOPLAY_MS)
        self._autoplay.timeout.connect(self._auto_next)

        self._sync_arrows()

    # ---------- data ----------
    def set_vehicles(self, vehicles) -> None:
        self._vehicles = list(vehicles)
        self._index = 0
        self._sync_arrows()
        if not self._vehicles:
            self.eyebrow.setText("THE SHOWROOM IS EMPTY")
            self._set_name("BACK SOON")
            self.price.setText("")
            self.specs.setText("")
            self.photo.set_vehicle(None)
            self.view_button.setEnabled(False)
            self.book_button.setEnabled(False)
            self._autoplay.stop()
            return
        self.view_button.setEnabled(True)
        self.book_button.setEnabled(True)
        self._apply()
        self._restart_autoplay()

    def previous(self) -> None:
        self._move(-1)

    def next(self) -> None:
        self._move(1)

    def _move(self, delta: int) -> None:
        if len(self._vehicles) > 1:
            self._index = (self._index + delta) % len(self._vehicles)
            self._apply()
        self._restart_autoplay()

    def _apply(self) -> None:
        vehicle = self._vehicles[self._index]
        self.eyebrow.setText(vehicle.eyebrow)
        self._set_name(vehicle.model.upper())
        self.price.setText(per_day_text(vehicle.rate))
        self.specs.setText(vehicle.specs)
        self.photo.set_vehicle(vehicle.photo_ref)
        self._warm_neighbour()
        self._animate_slide()

    def _warm_neighbour(self) -> None:
        """Fetch the next slide's bytes while this one is being looked at."""
        if len(self._vehicles) > 1:
            ahead = self._vehicles[(self._index + 1) % len(self._vehicles)]
            warm_thumbnail(ahead.photo_ref)

    def _open_current(self) -> None:
        if self._vehicles:
            self.vehicle_selected.emit(self._vehicles[self._index].vehicle_id)

    def _book_current(self) -> None:
        if self._vehicles:
            self.book_requested.emit(self._vehicles[self._index].vehicle_id)

    # ---------- animation ----------
    def _animate_slide(self) -> None:
        """The car fades in on its own (VehiclePhoto); the text follows it."""
        self._fade_in(self.eyebrow)
        self._fade_in(self.price)
        self._fade_in(self.specs)
        self._animate_name()

    def _fade_in(self, widget: QWidget) -> None:
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", widget)
        fade.setDuration(HERO_FADE_MS)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)

        def clear(w=widget, e=effect) -> None:
            # A newer fade may already own the widget (rapid arrow clicks).
            if w.graphicsEffect() == e:
                w.setGraphicsEffect(None)

        fade.finished.connect(clear)
        fade.start()

    def _animate_name(self) -> None:
        if self._name_rise is not None:
            self._name_rise.stop()
        target = self.name.pos()
        rise = QPropertyAnimation(self.name, b"pos", self)
        rise.setDuration(HERO_FADE_MS + 80)
        rise.setStartValue(QPoint(target.x(), target.y() + HERO_RISE_PX))
        rise.setEndValue(target)
        rise.setEasingCurve(QEasingCurve.OutCubic)
        rise.start()
        self._name_rise = rise
        self._fade_in(self.name)

    def _restart_autoplay(self) -> None:
        if len(self._vehicles) > 1 and not self._hovered:
            self._autoplay.start()

    def _auto_next(self) -> None:
        """Let the showroom move on -- unless a dialog is holding the customer."""
        if QApplication.activeModalWidget() is not None:
            return
        if self._hovered or not self.isVisible():
            return
        self.next()

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self._hovered = True
        self._autoplay.stop()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self._hovered = False
        self._restart_autoplay()

    # ---------- chrome ----------
    def _make_arrow(self, icon_name: str, tooltip: str, slot) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("heroArrow")
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(46, 46)
        button.setIcon(theme.icon_from(icon_name, TOKENS["text_dark"], 18))
        button.setIconSize(QSize(18, 18))
        button.clicked.connect(slot)
        return button

    def _sync_arrows(self) -> None:
        many = len(self._vehicles) > 1
        self.arrow_prev.setVisible(many)
        self.arrow_next.setVisible(many)
        self._place_arrows()

    def _set_name(self, text: str) -> None:
        self.name.setText(text)
        self._fit_name()

    def _fit_name(self) -> None:
        """Size the name once, then only shrink it so it never runs off."""
        text = self.name.text()
        if not text:
            self.name.hide()
            return
        stage_width = max(360, self.stage.width())
        font = QFont(self.name.font())
        font.setPixelSize(HERO_NAME_PX)
        font.setWeight(QFont.Weight.Bold)
        metrics = QFontMetrics(font)
        natural = metrics.horizontalAdvance(text)
        limit = int(stage_width * HERO_NAME_WIDTH_SHARE)
        if natural > limit and natural > 0:
            size = max(HERO_NAME_MIN, round(HERO_NAME_PX * limit / natural))
            font.setPixelSize(size)
            metrics = QFontMetrics(font)
            natural = metrics.horizontalAdvance(text)
        self.name.setFont(font)
        self.name.setFixedSize(natural + 4, metrics.height())
        self.name.move((stage_width - self.name.width()) // 2, HERO_NAME_TOP)
        self.name.show()

    def _place_stage(self) -> None:
        if self._name_rise is not None:
            self._name_rise.stop()
        width = self.stage.width()
        if width <= 0:
            return
        self._fit_name()
        self.photo.setGeometry(
            0, HERO_PHOTO_TOP, width, self._stage_height - HERO_PHOTO_TOP
        )
        # The name is the backdrop; the car's cutout is the foreground.
        self.name.raise_()
        self.photo.raise_()
        self._place_arrows()

    def _place_arrows(self) -> None:
        centre = (
            self.stage.y()
            + HERO_PHOTO_TOP
            + (self._stage_height - HERO_PHOTO_TOP) // 2
        )
        self.arrow_prev.move(0, centre - 23)
        self.arrow_next.move(self.width() - 46, centre - 23)

    def set_compact(self, compact: bool) -> None:
        """Shrink the stage on short windows so the filter bar stays above fold."""
        height = HERO_STAGE_HEIGHT_COMPACT if compact else HERO_STAGE_HEIGHT
        if height == self._stage_height:
            return
        self._stage_height = height
        self.stage.setFixedHeight(height)
        self._place_stage()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._place_timer.start()


class Shelf(QScrollArea):
    """A horizontal strip of cards with arrow buttons over its ends."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("shelf")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.NoFocus)

        self._content = QWidget()
        self._content.setObjectName("shelfContent")
        self._row = QHBoxLayout(self._content)
        self._row.setContentsMargins(0, 2, 0, 2)
        self._row.setSpacing(SHELF_SPACING)
        self._row.addStretch(1)
        self.setWidget(self._content)

        self.cards: list[VehicleCard] = []

        # The two soft ends sit under the arrows, above the cards.
        self.fade_left = _EdgeFade(True, self)
        self.fade_right = _EdgeFade(False, self)
        self.fade_left.hide()
        self.fade_right.hide()

        self.arrow_prev = self._make_arrow("arrow_left", "Scroll left", -1)
        self.arrow_next = self._make_arrow("arrow_right", "Scroll right", 1)

        bar = self.horizontalScrollBar()
        bar.rangeChanged.connect(self._sync_arrows)
        bar.valueChanged.connect(self._sync_arrows)

        self._animation = QPropertyAnimation(bar, b"value", self)
        self._animation.setDuration(180)
        self._animation.setEasingCurve(QEasingCurve.OutCubic)

    # ---------- cards ----------
    def add(self, card: VehicleCard) -> None:
        self.cards.append(card)
        self._row.insertWidget(self._row.count() - 1, card)

    def finalize(self) -> None:
        """Lock the height to the cards' own (the strip never grows).

        `minimumSizeHint` is the one that matters here: a wrapped card label
        can report a looser `sizeHint` than its actual minimum, and using the
        former clipped the bottom few pixels off every shelf card -- including
        its rounded corners.
        """
        height = max(
            self._content.sizeHint().height(),
            self._content.minimumSizeHint().height(),
        )
        if height > 0:
            self.setFixedHeight(height)
        # The range may settle before the first resize; place the ends now.
        self._sync_arrows()

    # ---------- scrolling ----------
    def _make_arrow(self, icon_name: str, tooltip: str, direction: int) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("shelfArrow")
        button.setToolTip(tooltip)
        button.setCursor(Qt.PointingHandCursor)
        button.setFixedSize(36, 36)
        button.setIcon(theme.icon_from(icon_name, TOKENS["text_dark"], 16))
        button.setIconSize(QSize(16, 16))
        button.clicked.connect(lambda: self._nudge(direction))
        return button

    def _nudge(self, direction: int) -> None:
        bar = self.horizontalScrollBar()
        step = max(260, int(self.viewport().width() * SHELF_STEP_SHARE))
        target = max(bar.minimum(), min(bar.value() + direction * step, bar.maximum()))
        self._animation.stop()
        self._animation.setStartValue(bar.value())
        self._animation.setEndValue(target)
        self._animation.start()

    def _sync_arrows(self, *_args) -> None:
        bar = self.horizontalScrollBar()
        scrollable = bar.maximum() > bar.minimum()
        self.arrow_prev.setVisible(scrollable)
        self.arrow_next.setVisible(scrollable)
        self.arrow_prev.setEnabled(bar.value() > bar.minimum())
        self.arrow_next.setEnabled(bar.value() < bar.maximum())
        self.arrow_prev.setIcon(
            theme.icon_from(
                "arrow_left",
                TOKENS["text_dark"] if bar.value() > bar.minimum() else TOKENS["muted_soft"],
                16,
            )
        )
        self.arrow_next.setIcon(
            theme.icon_from(
                "arrow_right",
                TOKENS["text_dark"] if bar.value() < bar.maximum() else TOKENS["muted_soft"],
                16,
            )
        )
        self._place_fades()

    def wheelEvent(self, event) -> None:
        # A vertical wheel belongs to the page behind, so pass it up by hand:
        # a scroll area with its vertical bar off swallows the event otherwise.
        if abs(event.angleDelta().x()) > abs(event.angleDelta().y()):
            super().wheelEvent(event)
            return
        page = self._page_scroll()
        delta = event.angleDelta().y()
        if page is not None and delta:
            bar = page.verticalScrollBar()
            bar.setValue(bar.value() - delta // 2)
            event.accept()
            return
        event.ignore()

    def _page_scroll(self) -> QScrollArea | None:
        """The dashboard scroll area this shelf sits in."""
        widget = self.parentWidget()
        while widget is not None:
            if isinstance(widget, QScrollArea):
                return widget
            widget = widget.parentWidget()
        return None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._place_arrows()
        self._place_fades()

    def _place_arrows(self) -> None:
        viewport = self.viewport().geometry()
        top = viewport.y() + max(0, (viewport.height() - 36) // 2)
        self.arrow_prev.move(viewport.x() + 6, top)
        self.arrow_next.move(viewport.right() - 36 - 6, top)
        # The fades are siblings and must stay behind the round buttons.
        self.arrow_prev.raise_()
        self.arrow_next.raise_()

    def _place_fades(self) -> None:
        """Fit both soft ends to the viewport and show only where cards clip."""
        viewport = self.viewport().geometry()
        width = min(SHELF_FADE_WIDTH, max(0, viewport.width() // 3))
        self.fade_left.setGeometry(
            viewport.x(), viewport.y(), width, viewport.height()
        )
        self.fade_right.setGeometry(
            viewport.right() - width + 1, viewport.y(), width, viewport.height()
        )

        bar = self.horizontalScrollBar()
        scrollable = bar.maximum() > bar.minimum()
        self.fade_left.setVisible(scrollable and bar.value() > bar.minimum())
        self.fade_right.setVisible(scrollable and bar.value() < bar.maximum())
        self.fade_left.raise_()
        self.fade_right.raise_()
        self.arrow_prev.raise_()
        self.arrow_next.raise_()


class SectionRow(QWidget):
    """A shelf with its title and the "See all" that opens the catalog."""

    vehicle_selected = Signal(int)
    see_all_requested = Signal(str)

    def __init__(
        self,
        section: vehicle_service.ShopSection,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.section = section

        self.title = section_title(section.title)
        self.see_all = text_button("See all")
        self.see_all.setIcon(theme.icon_from("forward", TOKENS["text_muted"], 14))
        self.see_all.setIconSize(QSize(14, 14))
        # Right-to-left puts the chevron after the text, like the design.
        self.see_all.setLayoutDirection(Qt.RightToLeft)
        self.see_all.clicked.connect(
            lambda: self.see_all_requested.emit(self.section.key)
        )

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.title, 1)
        head.addWidget(self.see_all, 0, Qt.AlignVCenter)

        self.shelf = Shelf()
        for item in section.vehicles:
            card = VehicleCard(card_for(item), width=CARD_WIDTH, defer_photo=True)
            card.open_requested.connect(self.vehicle_selected)
            self.shelf.add(card)
        self.shelf.finalize()

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        col.addLayout(head)
        col.addWidget(self.shelf)

        self.see_all.setVisible(section.total > len(section.vehicles))


class ChipRow(QWidget):
    """'Browse by type' -- one pill per body style the catalog holds."""

    chip_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.caption = overline("BROWSE BY TYPE")

        self.holder = QWidget()
        self._row = QHBoxLayout(self.holder)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(10)
        self._row.addStretch(1)
        self._buttons: list[QPushButton] = []

        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(8)
        col.addWidget(self.caption)
        col.addWidget(self.holder)

    def set_types(self, types) -> None:
        for button in self._buttons:
            self._row.removeWidget(button)
            button.deleteLater()
        self._buttons.clear()

        entries = [("all", "All vehicles")] + [
            (f"body:{name}", name) for name, _count in types
        ]
        for key, caption in entries:
            button = pill(caption)
            button.setCheckable(False)
            button.clicked.connect(
                lambda _checked=False, chip=key: self.chip_selected.emit(chip)
            )
            self._row.insertWidget(self._row.count() - 1, button)
            self._buttons.append(button)


#: The price spin boxes stop here; daily rates in this fleet are far below it.
BROWSE_RATE_MAX = 1_000_000


@dataclass(frozen=True)
class BrowseFilters:
    """The browse bar's choice, with "all" and "any" normalised to None."""

    make: str | None = None
    min_rate: int | None = None
    max_rate: int | None = None

    def describe(self) -> str:
        """A one-line summary for the catalog header, or '' with no filters."""
        parts: list[str] = []
        if self.make:
            parts.append(self.make)
        if self.min_rate is not None and self.max_rate is not None:
            parts.append(f"\u20b1{self.min_rate:,} \u2013 \u20b1{self.max_rate:,} / day")
        elif self.min_rate is not None:
            parts.append(f"\u20b1{self.min_rate:,}+ / day")
        elif self.max_rate is not None:
            parts.append(f"Up to \u20b1{self.max_rate:,} / day")
        return " \u00b7 ".join(parts)


class BrowseFilterBar(QFrame):
    """Brand and daily-price controls between the hero and the type chips.

    `browse_requested` carries a :class:`BrowseFilters`; the bar validates the
    range itself and stays put (with an inline error) when min > max.
    """

    browse_requested = Signal(object)
    browse_all_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("browseFilterBar")
        self.setFrameShape(QFrame.NoFrame)

        self.caption = overline("Browse & filter")

        self.brand_combo = QComboBox()
        self.brand_combo.setObjectName("browseBrand")
        self.brand_combo.setMinimumWidth(160)
        self.brand_combo.setMaximumWidth(240)
        self.brand_combo.setToolTip("Show vehicles from one brand")
        self.brand_combo.addItem("All brands", None)
        self.brand_combo.currentIndexChanged.connect(self._clear_error)

        self.min_rate = self._rate_spin("browseMinRate", "Minimum daily price")
        self.max_rate = self._rate_spin("browseMaxRate", "Maximum daily price")
        self.min_caption = make_label("Min / day", "browseRateLabel")
        self.max_caption = make_label("Max / day", "browseRateLabel")

        self.browse_button = primary_button("Browse vehicles")
        self.browse_all_button = ghost_button("Browse all vehicles")
        self.reset_button = ghost_button("Reset")

        self.browse_button.clicked.connect(self._on_browse)
        self.browse_all_button.clicked.connect(self._on_browse_all)
        self.reset_button.clicked.connect(self.reset)

        self.error = hint_label("", tone="danger")
        self.error.hide()

        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(8)
        controls.addWidget(self.brand_combo, 2, Qt.AlignBottom)
        controls.addLayout(self._rate_field(self.min_caption, self.min_rate))
        controls.addLayout(self._rate_field(self.max_caption, self.max_rate))
        controls.addStretch(1)
        controls.addWidget(self.reset_button, 0, Qt.AlignBottom)
        controls.addWidget(self.browse_all_button, 0, Qt.AlignBottom)
        controls.addWidget(self.browse_button, 0, Qt.AlignBottom)

        col = QVBoxLayout(self)
        col.setContentsMargins(20, 16, 20, 16)
        col.setSpacing(8)
        col.addWidget(self.caption)
        col.addLayout(controls)
        col.addWidget(self.error)

    @staticmethod
    def _rate_field(caption: QLabel, spin: QSpinBox) -> QVBoxLayout:
        """One labelled price input: the caption names which end it is."""
        field = QVBoxLayout()
        field.setContentsMargins(0, 0, 0, 0)
        field.setSpacing(4)
        field.addWidget(caption)
        field.addWidget(spin)
        return field

    @staticmethod
    def _rate_spin(object_name: str, tooltip: str) -> QSpinBox:
        spin = QSpinBox()
        spin.setObjectName(object_name)
        spin.setRange(0, BROWSE_RATE_MAX)
        spin.setSpecialValueText("Any")
        spin.setPrefix("\u20b1 ")
        # The "Min / day" and "Max / day" captions carry the unit. Keeping it
        # out of the value lets "₱ 1,000,000" fit at 1024px.
        spin.setGroupSeparatorShown(True)
        spin.setKeyboardTracking(False)
        spin.setMinimumWidth(128)
        spin.setToolTip(tooltip)
        return spin

    # ---------- data ----------
    def set_makes(self, makes) -> None:
        """Fill the brand list, keeping 'All brands' and any current choice."""
        previous = self.brand_combo.currentData()
        blocked = self.brand_combo.blockSignals(True)
        try:
            self.brand_combo.clear()
            self.brand_combo.addItem("All brands", None)
            seen: set[str] = set()
            for make in makes:
                name = (make or "").strip()
                if name and name not in seen:
                    seen.add(name)
                    self.brand_combo.addItem(name, name)
            index = self.brand_combo.findData(previous)
            self.brand_combo.setCurrentIndex(max(0, index))
        finally:
            self.brand_combo.blockSignals(blocked)

    def filters(self) -> BrowseFilters:
        return BrowseFilters(
            make=self.brand_combo.currentData() or None,
            min_rate=self.min_rate.value() or None,
            max_rate=self.max_rate.value() or None,
        )

    def reset(self) -> None:
        for widget in (self.brand_combo, self.min_rate, self.max_rate):
            blocked = widget.blockSignals(True)
            try:
                if widget is self.brand_combo:
                    widget.setCurrentIndex(0)
                else:
                    widget.setValue(0)
            finally:
                widget.blockSignals(blocked)
        self._clear_error()

    # ---------- actions ----------
    def _on_browse(self) -> None:
        current = self.filters()
        if (
            current.min_rate is not None
            and current.max_rate is not None
            and current.min_rate > current.max_rate
        ):
            self.error.setText("Minimum price can't be higher than the maximum.")
            self.error.show()
            return
        self._clear_error()
        self.browse_requested.emit(current)

    def _on_browse_all(self) -> None:
        self._clear_error()
        self.browse_all_requested.emit()

    def _clear_error(self, *_args) -> None:
        self.error.clear()
        self.error.hide()


class HomePage(QWidget):
    """The landing tab: hero, browse filter, type chips, shelves -- catalog behind."""

    vehicle_selected = Signal(int)
    book_requested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self._snapshot: list[vehicle_service.ShopVehicle] = []
        self._pending: list[VehicleCard] = []

        self.error = hint_label("", tone="danger")
        self.error.hide()

        self.hero = HeroCarousel()
        self.hero.vehicle_selected.connect(self.vehicle_selected)
        self.hero.book_requested.connect(self.book_requested)

        self.browse_filter = BrowseFilterBar()
        self.browse_filter.browse_requested.connect(self._browse)
        self.browse_filter.browse_all_requested.connect(self._browse_all)

        self.chips = ChipRow()
        self.chips.chip_selected.connect(self._open_chip)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setContentsMargins(0, 0, 0, 0)
        self.rows_layout.setSpacing(26)

        body = QVBoxLayout()
        body.setContentsMargins(40, 24, 40, 36)
        body.setSpacing(22)
        body.addWidget(self.error)
        body.addWidget(self.hero)
        body.addWidget(self.browse_filter)
        body.addWidget(self.chips)
        body.addLayout(self.rows_layout)
        body.addStretch(1)

        holder = QWidget()
        holder.setObjectName("pageBody")
        holder.setLayout(body)

        self.dashboard = QScrollArea()
        self.dashboard.setObjectName("pageScroll")
        self.dashboard.setWidgetResizable(True)
        self.dashboard.setFrameShape(QFrame.NoFrame)
        self.dashboard.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.dashboard.setWidget(holder)

        self.catalog = CatalogPage()
        self.catalog.vehicle_selected.connect(self.vehicle_selected)
        self.catalog.back_requested.connect(self.show_dashboard)

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self._stack.addWidget(self.dashboard)
        self._stack.addWidget(self.catalog)

        self._sweep_timer = QTimer(self)
        self._sweep_timer.setSingleShot(True)
        self._sweep_timer.setInterval(SWEEP_DEBOUNCE_MS)
        self._sweep_timer.timeout.connect(self._sweep)
        self.dashboard.verticalScrollBar().valueChanged.connect(self._schedule_sweep)

        self._sync_compact_hero()
        self.load()

    # ---------- data ----------
    def load(self) -> None:
        """One pass over the available fleet; everything else slices it."""
        try:
            with SessionLocal() as session:
                snapshot = vehicle_service.shop_snapshot(session)
        except ServiceError as exc:
            self._fail(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not load the showroom: {exc}")
            return

        self.error.hide()
        self._snapshot = snapshot
        self.browse_filter.set_makes(vehicle_service.shop_makes(snapshot))
        self.hero.set_vehicles(vehicle_service.shop_hero(snapshot))
        self.chips.set_types(vehicle_service.shop_body_types(snapshot))
        self._build_rows(vehicle_service.shop_sections(snapshot))
        self._schedule_sweep()

    def _build_rows(self, sections) -> None:
        while self.rows_layout.count():
            taken = self.rows_layout.takeAt(0)
            widget = taken.widget()
            if widget is not None:
                widget.deleteLater()
        self._pending = []

        for section in sections:
            row = SectionRow(section)
            row.vehicle_selected.connect(self.vehicle_selected)
            row.see_all_requested.connect(self._open_section)
            row.shelf.horizontalScrollBar().valueChanged.connect(self._schedule_sweep)
            self.rows_layout.addWidget(row)
            self._pending.extend(row.shelf.cards)

    # ---------- navigation ----------
    def set_search(self, text: str) -> None:
        """The top bar's search box: results over the whole snapshot."""
        text = (text or "").strip()
        if text:
            items = vehicle_service.search_snapshot(self._snapshot, text)
            self._open_listing(
                "Search results", items, f"No vehicles match \u201c{text}\u201d."
            )
        else:
            self.show_dashboard()

    def show_dashboard(self) -> None:
        self._stack.setCurrentWidget(self.dashboard)
        self._schedule_sweep()

    def _browse(self, filters: BrowseFilters) -> None:
        """The Browse & Filter bar: one brand, an inclusive daily-rate range."""
        items = vehicle_service.filter_shop_snapshot(
            self._snapshot,
            make=filters.make,
            min_rate=filters.min_rate,
            max_rate=filters.max_rate,
        )
        self._open_listing(
            "Browse vehicles",
            items,
            "No vehicles match these filters. Try another brand or widen the "
            "price range.",
            summary=filters.describe(),
        )

    def _browse_all(self) -> None:
        """Browse all ignores the controls and opens the whole showroom."""
        self._open_listing(
            "All vehicles", self._snapshot, "The showroom is empty right now."
        )

    def _open_section(self, key: str) -> None:
        spec = vehicle_service.shop_section_spec(key)
        if spec is None:
            return
        items = spec.pick(self._snapshot)
        self._open_listing(
            spec.title, items, f"No vehicles in \u201c{spec.title}\u201d right now."
        )

    def _open_chip(self, key: str) -> None:
        if key == "all":
            self._open_listing(
                "All vehicles", self._snapshot, "The showroom is empty right now."
            )
            return
        spec = vehicle_service.shop_section_spec(key)
        if spec is None:
            return
        items = spec.pick(self._snapshot)
        self._open_listing(
            spec.title, items, f"No {spec.title} available right now."
        )

    def _open_listing(
        self, title: str, items, empty_text: str, *, summary: str = ""
    ) -> None:
        self.catalog.open_listing(
            title, items, empty_text=empty_text, summary=summary
        )
        self._stack.setCurrentWidget(self.catalog)

    # ---------- deferred photos ----------
    def _schedule_sweep(self, *_args) -> None:
        self._sweep_timer.start()

    def _sweep(self) -> None:
        """Load every pending card that is actually on screen by now."""
        if not self._pending:
            return
        remaining: list[VehicleCard] = []
        for card in self._pending:
            if card.visibleRegion().isEmpty():
                remaining.append(card)
            else:
                card.ensure_photo()
        self._pending = remaining

    # ---------- lifecycle ----------
    def _sync_compact_hero(self) -> None:
        """Give the filter bar room back when the window is short."""
        self.hero.set_compact(self.height() < HERO_COMPACT_BELOW)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_compact_hero()
        self._schedule_sweep()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_compact_hero()
        self._schedule_sweep()

    def _fail(self, message: str) -> None:
        self.error.setText(message)
        self.error.show()


__all__ = ["BrowseFilterBar", "BrowseFilters", "HomePage"]
