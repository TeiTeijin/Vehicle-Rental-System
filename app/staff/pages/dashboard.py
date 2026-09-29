"""The dashboard: the numbers, and the role split.

Staff and admins see the same operational tiles -- what is out, what is late,
what is owed -- because both need them to run a counter. Admins additionally
see the trend charts, which are about the branch rather than about the next
customer.

The split is enforced in two places. `admin_only` on the chart block means
staff never get the widgets built at all, and the numbers themselves are
derived from the same service functions the working screens use, so a tile can
never disagree with the table it summarises. That is the failure that matters
on a dashboard: two figures for the same thing that quietly differ.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from sqlalchemy import func, select

from app.models import Booking, Payment, Vehicle
from app.services import booking_service, payment_service
from app.staff import theme
from app.staff.charts import HandDrawnBars, HandDrawnChart, daily_series
from app.staff.pages.base import StaffPage

#: How long a count-up takes. Long enough to be noticed, short enough that
#: someone glancing at the screen mid-animation still reads the right number
#: within a moment.
COUNT_UP_MS = 550


class Tile(QWidget):
    """One number, its label, and a 14-day sparkline.

    The number is the tile. The sparkline is a hint of direction, and it is
    deliberately unlabelled -- a full axis on a 34-pixel strip is noise.
    """

    def __init__(self, label: str, colour: str = theme.INK, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(96)
        self._colour = colour
        self._count = 0.0

        self.value_label = QLabel("0", self)
        self.value_label.setStyleSheet(
            f"background: transparent; color: {colour}; font-size: 26px; font-weight: 700;"
        )

        self.caption = QLabel(label, self)
        self.caption.setObjectName("pageSubtitle")

        self.sparkline_host = QVBoxLayout()
        self.sparkline_host.setContentsMargins(0, 0, 0, 0)
        self._sparkline = None
        self._animation: QPropertyAnimation | None = None
        self._current = 0.0
        #: False until the first `set_value`. Without it, a tile whose value
        #: starts at zero returns early on that first call and never renders --
        #: so a money tile shows a bare "0" instead of "PHP 0", and the prefix
        #: is missing only for the tiles that happen to be nil.
        self._rendered = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        layout.addWidget(self.value_label)
        layout.addWidget(self.caption)
        layout.addLayout(self.sparkline_host)

    #: The animated property. Declared in the class body because
    #: `QPropertyAnimation` resolves a real Qt property by name; attaching one
    #: after the fact would not register it with the meta-object system.
    def _get_count(self) -> float:
        return self._count

    def _set_count(self, value: float) -> None:
        self._count = value

    countValue = Property(float, _get_count, _set_count)

    def set_value(
        self,
        value: float,
        *,
        animate: bool = True,
        prefix: str = "",
        suffix: str = "",
        decimals: int | None = None,
    ) -> None:
        """Set the number, counting up from the previous one.

        `decimals` forces the number of decimal places. Left as None, a whole
        number renders without any: right for a count of cars, wrong for a
        balance, where PHP 15,000 and PHP 15,000.90 are different sums and the
        second one has to be visible.

        The animation is skipped when the value did not change, so a 30-second
        refresh of an unchanged number does not make the tile twitch.
        """
        target = float(value)

        def render(number: float) -> None:
            if decimals is not None:
                shown = f"{number:,.{decimals}f}"
            elif abs(number - round(number)) < 0.5:
                shown = f"{number:,.0f}"
            else:
                shown = f"{number:,.2f}"
            self.value_label.setText(f"{prefix}{shown}{suffix}")
            self._rendered = True

        if not animate:
            self._current = target
            render(target)
            return

        # Skipped only once the tile has actually shown something. Skipping on
        # the first call would leave the label showing the placeholder "0"
        # without its prefix or its formatting.
        if self._rendered and abs(target - self._current) < 1e-9 and self._animation is None:
            return

        self._animation = QPropertyAnimation(self, b"countValue", self)
        self._animation.setDuration(COUNT_UP_MS)
        self._animation.setStartValue(self._current)
        self._animation.setEndValue(target)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def on_value_changed(number: float) -> None:
            self._current = number
            render(number)

        self._animation.valueChanged.connect(on_value_changed)
        self._animation.finished.connect(lambda: setattr(self, "_current", target))
        # Skipped animations leave the count mid-way; the finished handler puts
        # the exact target back so the next comparison is against the real
        # value and not a rounded frame.
        self._animation.start()

    def set_sparkline(self, widget) -> None:
        if self._sparkline is not None:
            self.sparkline_host.removeWidget(self._sparkline)
            self._sparkline.deleteLater()
        self._sparkline = widget
        if widget is not None:
            self.sparkline_host.addWidget(widget)


class DashboardPage(StaffPage):
    #: Set False to land every value immediately instead of counting up. A
    #: test cannot read a number out of a label mid-animation, and anything
    #: embedding this page as a widget may well want the same.
    def __init__(self, shell, *, animate: bool = True) -> None:
        super().__init__(shell, "Dashboard", "The branch at a glance.")
        self.animate = animate

        self.tiles = QGridLayout()
        self.tiles.setContentsMargins(0, 0, 0, 0)
        self.tiles.setSpacing(14)

        # The six tiles, in a plain dict rather than the layout. `QGridLayout`
        # is not a mapping, so `self.tiles["out"] = tile` would fail at
        # runtime with a bare AttributeError.
        #
        # "Available" counts `available` minus what is already promised: a car
        # whose status says available can still have a confirmed booking for
        # tomorrow, and counting it as free is how the number ends up
        # disagreeing with the Fleet screen.
        tile_widgets = {
            "out": Tile("Out on rent now", theme.WARN),
            "due_back": Tile("Due back today", theme.TEXT),
            "overdue": Tile("Overdue", theme.DANGER),
            "available": Tile("Free to rent today", theme.OK),
            "outstanding": Tile("Owed to the branch", theme.DANGER),
            "takings": Tile("Takings this month", theme.OK),
        }
        for index, key in enumerate(
            ("out", "due_back", "overdue", "available", "outstanding", "takings")
        ):
            self.tiles.addWidget(tile_widgets[key], index // 3, index % 3)
        self._tile = tile_widgets

        self.body.addLayout(self.tiles)

        # Charts are admin-only, and are *constructed* on the admin branch of
        # `refresh` rather than here. Building them in `__init__` and hiding the
        # host would work visually but would still run the three series queries
        # for a staff member, who is not shown any of it.
        self.charts_host = QWidget(self)
        self.charts_layout = QVBoxLayout(self.charts_host)
        self.charts_layout.setContentsMargins(0, 14, 0, 0)
        self.charts_layout.setSpacing(12)
        self.body.addWidget(self.charts_host)
        self.charts_host.setVisible(False)

        self.bookings_chart = None
        self.takings_chart = None
        self.status_chart = None

        self._first_load = True

    def _ensure_charts(self) -> None:
        """Build the chart widgets once, the first time an admin looks."""
        if self.bookings_chart is not None:
            return
        self.bookings_chart = HandDrawnChart("Bookings started, last 14 days", [])
        self.takings_chart = HandDrawnChart("Takings, last 14 days", [])
        self.status_chart = HandDrawnBars(
            "Where the fleet is", [("Available", 0), ("Out", 0), ("Workshop", 0)]
        )
        for chart in (self.bookings_chart, self.takings_chart, self.status_chart):
            self.charts_layout.addWidget(chart)

    def refresh(self) -> None:
        super().refresh()
        # Charts are admin-only; a staff member's refresh must not query for
        # figures they are not shown.
        self.charts_host.setVisible(self.context.is_admin)

        today = date.today()
        show_charts = self.context.is_admin
        if show_charts:
            self._ensure_charts()
        else:
            self.charts_host.setVisible(False)

        with self.context.reading() as session:
            # `active_bookings` is the *availability* question -- it includes
            # pending and confirmed, which still hold a car for their dates.
            # "Out on rent now" is a different question: physically gone. Only
            # `ongoing` counts, and the tile is labelled accordingly.
            out_now = session.query(func.count(Booking.booking_id)).filter(
                Booking.status == "ongoing"
            ).scalar()
            due_back = session.query(Booking).filter(
                Booking.status == "ongoing", Booking.end_date == today
            ).count()
            overdue = len(booking_service.overdue_returns(session, today))
            # A car whose `status` says available can still have a confirmed
            # booking starting today or later -- the status column tracks where
            # the car physically is, not what it is promised to. Counting the
            # raw status is how the tile ends up disagreeing with the Fleet
            # screen, which does apply the commitment.
            free_now = session.query(func.count(Vehicle.vehicle_id)).filter(
                Vehicle.status == "available",
                ~Vehicle.vehicle_id.in_(
                    select(Booking.vehicle_id).where(
                        Booking.status.in_(("confirmed", "ongoing")),
                        Booking.start_date <= today,
                        Booking.end_date > today,
                    )
                ),
            ).scalar()

            takings = payment_service.takings(
                session,
                from_date=self._month_start(today),
            )
            outstanding = self._outstanding(session)

            # Queried on the admin branch only, and only for the chart
            # lines -- which is why the two tiles with sparklines are admin
            # tiles too. A staff member's refresh issues no series query at all.
            booking_series = (
                daily_series(session, 14, Booking.created_at) if show_charts else []
            )
            takings_series = self._daily_takings(session, today) if show_charts else []
            fleet_pairs = self._fleet_split(session) if show_charts else []

        animate = self.animate and not self._first_load
        self._tile["out"].set_value(out_now, animate=animate)
        self._tile["due_back"].set_value(due_back, animate=animate)
        self._tile["overdue"].set_value(overdue, animate=animate)
        self._tile["available"].set_value(free_now, animate=animate)
        # Money keeps its centavos; a balance that rounds away pesos is the
        # sort of thing a customer notices and an auditor finds.
        self._tile["outstanding"].set_value(
            outstanding, animate=animate, prefix="PHP ", decimals=2
        )
        self._tile["takings"].set_value(
            takings, animate=animate, prefix="PHP ", decimals=2
        )

        if show_charts:
            from app.staff.charts import Sparkline

            self._tile["out"].set_sparkline(Sparkline(booking_series, colour=theme.WARN))
            self._tile["overdue"].set_sparkline(Sparkline(booking_series, colour=theme.DANGER))
            self.charts_host.setVisible(True)
            self.bookings_chart.set_values(booking_series)
            self.takings_chart.set_values(takings_series)
            self.status_chart.set_pairs(fleet_pairs)

        self._first_load = False

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _month_start(today: date) -> datetime:
        return datetime(today.year, today.month, 1)

    @staticmethod
    def _outstanding(session) -> Decimal:
        """What customers still owe, across everything not yet settled.

        Only `paid` rows count as received, and the arithmetic is done in
        Decimal. Summing floats here would show a total that disagrees with
        the Payments tab by a centavo, which is exactly the kind of thing that
        destroys trust in a dashboard.
        """
        total = Decimal("0.00")
        bookings = session.query(Booking).filter(
            Booking.status.in_(("confirmed", "ongoing", "completed"))
        ).all()
        for booking in bookings:
            balance = payment_service.booking_balance(session, booking)
            if balance.balance > Decimal("0.00"):
                total += balance.balance
        return total

    @staticmethod
    def _daily_takings(session, today: date) -> list[float]:
        """Takings per day for the last 14 days, gaps filled with zero."""
        first = today - timedelta(days=13)
        buckets = {first + timedelta(days=i): Decimal("0.00") for i in range(14)}
        payments = session.query(Payment).filter(Payment.paid_at >= first).all()
        for payment in payments:
            if payment.status != "paid" or payment.paid_at is None:
                continue
            day = payment.paid_at.date()
            if day in buckets:
                buckets[day] += Decimal(payment.amount)
        return [float(buckets[first + timedelta(days=i)]) for i in range(14)]

    @staticmethod
    def _fleet_split(session) -> list[tuple[str, float]]:
        counts = {"Available": 0, "Out": 0, "Workshop": 0}
        for vehicle in session.query(Vehicle).all():
            if vehicle.status == "available":
                counts["Available"] += 1
            elif vehicle.status == "maintenance":
                counts["Workshop"] += 1
            else:
                counts["Out"] += 1
        return list(counts.items())


def build_dashboard_page(shell, *, animate: bool = True) -> DashboardPage:
    return DashboardPage(shell, animate=animate)
