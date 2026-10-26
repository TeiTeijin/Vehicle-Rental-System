"""Screen 3: when and where the rental starts and ends (step 2 of 3).

One calendar drives both ends of the rental: click PICKUP or RETURN to say
which date you are choosing, then pick the day. The two cards carry the time
and the branch, so a customer never leaves this screen to answer a question.

Everything is validated by the services, not by the widgets: the times are
checked against the branch's opening hours (app/services/branch_service.py)
and the exact window against what is already booked
(app/services/booking_service.py), so the message the customer sees here is
the same one the booking itself would raise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.database import SessionLocal
from app.domain.pricing import policy_from_settings
from app.models import Branch, Vehicle
from app.services import (
    booking_service,
    branch_service,
    pricing,
    vehicle_photos,
    vehicle_service,
)
from app.services.errors import ServiceError
from app.ui import theme
from app.ui.session import BookingDraft
from app.ui.theme import (
    BOOKING_STEPS,
    TOKENS,
    divider,
    day_label,
    hint_label,
    make_label,
    overline,
    panel,
    per_day_text,
    primary_button,
    section_title,
    value_row,
)
from app.ui.vehicle_photo import VehiclePhoto

#: How far ahead the calendar greys out booked days.
HORIZON_DAYS = 180

#: This is step 2 of `theme.BOOKING_STEPS`.
STEP_INDEX = 2


@dataclass(frozen=True)
class BranchChoice:
    """A branch as the screen needs it: labels, hours, hand-over times."""

    branch_id: int
    name: str
    address: str
    hours: str
    slots: tuple[time, ...]


class MomentCard(QFrame):
    """One end of the rental. Clicking it points the calendar at itself."""

    focused = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("momentCard")
        self.setCursor(Qt.PointingHandCursor)
        self.setProperty("focused", False)

    def mousePressEvent(self, event) -> None:
        super().mousePressEvent(event)
        self.focused.emit()


@dataclass
class Moment:
    """The widgets of one end of the rental (pickup or return)."""

    key: str
    card: MomentCard
    column: QVBoxLayout
    date_label: QLabel
    time_combo: QComboBox
    branch_combo: QComboBox


class BookingSchedulePage(QWidget):
    """Picks the window; hands the draft to the router when it is valid."""

    continue_requested = Signal(object)
    back_requested = Signal()

    def __init__(self, draft: BookingDraft, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("page")
        self.draft = draft
        self._branches: list[BranchChoice] = []
        self._blocked: set[date] = set()
        self._rate = 0
        self._policy = None
        self._motorcycle = False
        self._ref = None
        self._name = ""
        self._focus = "pickup"
        self._loading = True

        # ----- header -----
        self.back_button = theme.text_button("\u2190  Back to vehicle")
        self.back_button.setIcon(theme.icon_from("back", TOKENS["text_dark"], 14))
        self.back_button.clicked.connect(self.back_requested)
        self.steps = theme.step_strip(BOOKING_STEPS, STEP_INDEX)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.addWidget(self.back_button, 0, Qt.AlignLeft | Qt.AlignVCenter)
        head.addSpacing(24)
        head.addWidget(self.steps, 1)

        # ----- pickup / return cards -----
        self.pickup = self._moment("Pickup", "pickup")
        self.return_moment = self._moment("Return", "return")
        self.different_branch = QCheckBox("Return to a different branch")
        self.different_branch.toggled.connect(self._on_different_branch)
        self.return_moment.column.addWidget(self.different_branch)
        self.pickup.column.addStretch(1)

        cards = QHBoxLayout()
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(14)
        cards.addWidget(self.pickup.card, 1)
        cards.addWidget(self.return_moment.card, 1)

        # ----- calendar -----
        self.calendar = theme.MonthCalendar()
        self.calendar.date_selected.connect(self._on_day)

        self.legend = hint_label(
            "Greyed days are already booked out. A day that is only partly "
            "taken stays pickable: whether the hours clash depends on the "
            "time, and that is checked for you."
        )

        left_panel, left_col = panel("panel", spacing=14)
        left_col.addWidget(section_title("Pick your dates and times"))
        left_col.addLayout(cards)
        left_col.addWidget(divider())
        left_col.addWidget(self.calendar)
        left_col.addWidget(self.legend)

        left = QVBoxLayout()
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(14)
        left.addLayout(head)
        left.addWidget(left_panel)
        left.addStretch(1)

        # ----- right column: the running quote -----
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(14)
        right.addWidget(self._vehicle_panel())
        right.addWidget(self._quote_panel())
        right.addStretch(1)

        columns = QHBoxLayout()
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(28)
        columns.addLayout(left, 3)
        columns.addLayout(right, 2)

        body = QVBoxLayout()
        body.setContentsMargins(40, 24, 40, 32)
        body.setSpacing(16)
        body.addLayout(columns)
        body.addStretch(1)

        holder = QWidget()
        holder.setObjectName("pageBody")
        holder.setLayout(body)

        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setWidget(holder)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addWidget(scroll)

        self._loading = False
        self.reload()

    # ---------- construction ----------
    def _moment(self, title: str, key: str) -> Moment:
        card = MomentCard()
        card.focused.connect(lambda k=key: self._set_focus(k))

        col = QVBoxLayout(card)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(8)
        col.addWidget(overline(title))

        date_label = make_label("\u2014", "momentDate")
        col.addWidget(date_label)

        time_combo = QComboBox()
        time_combo.setObjectName("combo")
        time_combo.setCursor(Qt.PointingHandCursor)
        col.addWidget(make_label("Time", "comboCaption"))
        col.addWidget(time_combo)

        branch_combo = QComboBox()
        branch_combo.setObjectName("combo")
        branch_combo.setCursor(Qt.PointingHandCursor)
        col.addWidget(make_label("Branch", "comboCaption"))
        col.addWidget(branch_combo)

        moment = Moment(key, card, col, date_label, time_combo, branch_combo)
        moment.time_combo.currentIndexChanged.connect(
            lambda _index, m=moment: self._on_time_changed(m)
        )
        moment.branch_combo.currentIndexChanged.connect(
            lambda _index, m=moment: self._on_branch_changed(m)
        )
        return moment

    def _vehicle_panel(self) -> QWidget:
        frame, col = panel("panel", spacing=10)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)

        self.photo = VehiclePhoto(thumbnail=True)
        self.photo.setFixedSize(140, 90)
        row.addWidget(self.photo, 0, Qt.AlignTop)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(4)
        self.vehicle_name = make_label("", "gridName")
        self.vehicle_meta = make_label("", "gridMeta")
        self.vehicle_rate = make_label("", "gridPrice")
        info.addWidget(self.vehicle_name)
        info.addWidget(self.vehicle_meta)
        info.addWidget(self.vehicle_rate)
        info.addStretch(1)
        row.addLayout(info, 1)

        col.addLayout(row)
        return frame

    def _quote_panel(self) -> QWidget:
        frame, col = panel("panel", spacing=10)
        col.addWidget(overline("Your rental"))
        self.duration_label = make_label("\u2014", "rentalDuration")
        col.addWidget(self.duration_label)

        self._rows: dict[str, QWidget] = {}
        col.addWidget(divider())
        for key, caption, icon in (
            ("rental", "Rental", "car"),
            ("insurance", "Insurance", "shield"),
            ("service", "Service fee", "receipt"),
            ("vat", "VAT (12%)", "percent"),
        ):
            row = value_row(caption, "\u2014", icon_name=icon)
            self._rows[key] = row
            col.addWidget(row)
        col.addWidget(divider())
        self._rows["total"] = value_row("Total", "\u2014", strong=True)
        col.addWidget(self._rows["total"])

        self.error = hint_label("", tone="danger")
        self.error.hide()
        col.addWidget(self.error)

        self.continue_button = primary_button("CONTINUE TO PAYMENT")
        self.continue_button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.continue_button.clicked.connect(self._on_continue)
        col.addWidget(self.continue_button)
        col.addWidget(
            hint_label(
                "Nothing is charged on this step. You will see the full "
                "breakdown before you pay.",
            )
        )
        return frame

    # ---------- data ----------
    def reload(self) -> None:
        """(Re)read the vehicle, the branches and the booked days."""
        try:
            with SessionLocal() as session:
                vehicle = session.get(Vehicle, self.draft.vehicle_id)
                if vehicle is None:
                    self._fail("That vehicle is no longer in the fleet.")
                    return
                self._name = vehicle_service.display_name(vehicle)
                self._rate = vehicle.daily_rate
                self._policy = policy_from_settings(session)
                self._motorcycle = pricing.is_motorcycle(vehicle)
                self._ref = vehicle_photos.ref_for(vehicle)
                self._specs = vehicle_service.summary_line(vehicle)
                branches = branch_service.active_branches(session)
                self._branches = [
                    BranchChoice(
                        branch_id=branch.branch_id,
                        name=branch.name,
                        address=branch.address,
                        hours=branch.hours_label,
                        slots=tuple(branch_service.time_slots(branch)),
                    )
                    for branch in branches
                ]
                self._blocked = booking_service.booked_days(
                    session,
                    vehicle,
                    date.today(),
                    date.today() + timedelta(days=HORIZON_DAYS),
                )
        except ServiceError as exc:
            self._fail(str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - the database is remote
            self._fail(f"Could not load this booking: {exc}")
            return

        self.vehicle_name.setText(self._name)
        self.vehicle_meta.setText(self._specs)
        self.vehicle_rate.setText(per_day_text(self._rate))
        if self._ref is not None:
            self.photo.set_vehicle(self._ref)

        self._fill_combos()
        self._defaults()
        self._sync()

    def _fill_combos(self) -> None:
        for moment in (self.pickup, self.return_moment):
            branch_combo = moment.branch_combo
            branch_combo.blockSignals(True)
            branch_combo.clear()
            for choice in self._branches:
                branch_combo.addItem(f"{choice.name} \u2014 {choice.hours}", choice.branch_id)
            branch_combo.blockSignals(False)

    def _defaults(self) -> None:
        """First visit: tomorrow-ish, or the values BACK carried over."""
        if not self._branches:
            return
        first = self._branches[0]
        if self.draft.pickup_branch_id is None:
            self.draft.pickup_branch_id = first.branch_id
        if self.draft.pickup_dt is None:
            day = date.today()
            self.draft.pickup_dt = datetime.combine(day, self._default_time(first))
        if self.draft.return_dt is None:
            self.draft.return_dt = self.draft.pickup_dt + timedelta(days=2)

    @staticmethod
    def _default_time(choice: BranchChoice) -> time:
        """An hour after opening: inside the branch's day, never a guess."""
        slots = choice.slots
        return slots[min(2, len(slots) - 1)] if slots else time(9, 0)

    # ---------- interaction ----------
    def _set_focus(self, which: str) -> None:
        self._focus = which
        self._sync()

    def _focused_date(self) -> date | None:
        moment = self.pickup if self._focus == "pickup" else self.return_moment
        value = self._draft_moment(self._focus)
        return value.date() if value else None

    def _on_day(self, day: date) -> None:
        """A day was clicked: it lands on whichever card has the focus."""
        current = self._draft_moment(self._focus)
        clock = current.time() if current else self._default_time(self._branches[0])
        self._set_moment(self._focus, datetime.combine(day, clock))
        self._sync()

    def _on_branch_changed(self, moment: Moment) -> None:
        if self._loading:
            return
        which = moment.key
        choice = self._choice(moment.branch_combo.currentData())
        current = self._draft_moment(which)
        if choice is not None:
            self._fill_times(moment, choice)
            # Keep the clock time if the new branch offers it, else clamp to
            # the nearest slot it does offer: never silently out of hours.
            clock = current.time() if current else self._default_time(choice)
            if clock not in choice.slots:
                clock = min(
                    choice.slots,
                    key=lambda slot: abs(_minutes(slot) - _minutes(clock)),
                )
            day = current.date() if current else date.today()
            self._set_moment(which, datetime.combine(day, clock))
        if which == "return" and self.different_branch.isChecked():
            self.draft.return_branch_id = moment.branch_combo.currentData()
        elif which == "pickup" and not self.different_branch.isChecked():
            # The drop-off branch follows the pickup one while the box is clear.
            self.draft.return_branch_id = None
        self._sync()

    def _on_different_branch(self, checked: bool) -> None:
        self.return_moment.branch_combo.setEnabled(checked)
        self.draft.return_branch_id = (
            self.return_moment.branch_combo.currentData() if checked else None
        )
        self._sync()

    def _on_time_changed(self, moment: Moment) -> None:
        if self._loading:
            return
        current = self._draft_moment(moment.key)
        slot = moment.time_combo.currentData()
        if current is not None and slot is not None:
            self._set_moment(moment.key, datetime.combine(current.date(), slot))
        self._sync()

    def _on_continue(self) -> None:
        if self._window_problem() is not None:
            self._sync()  # re-show the reason, but keep the customer here
            return
        self.continue_requested.emit(self.draft)

    # ---------- the draft <-> widgets ----------
    def _draft_moment(self, which: str) -> datetime | None:
        return self.draft.pickup_dt if which == "pickup" else self.draft.return_dt

    def _set_moment(self, which: str, value: datetime) -> None:
        if which == "pickup":
            self.draft.pickup_dt = value
            if self.draft.return_dt is None or self.draft.return_dt <= value:
                # Dragging the pickup past the return would leave a window
                # that cannot exist: push the return out the same clock time.
                self.draft.return_dt = value + timedelta(days=1)
        else:
            self.draft.return_dt = value

    def _choice(self, branch_id) -> BranchChoice | None:
        for choice in self._branches:
            if choice.branch_id == branch_id:
                return choice
        return None

    def _fill_times(self, moment: Moment, choice: BranchChoice) -> None:
        combo = moment.time_combo
        combo.blockSignals(True)
        combo.clear()
        for slot in choice.slots:
            combo.addItem(theme.clock_text(slot), slot)
        combo.blockSignals(False)

    def _select_combo(self, combo: QComboBox, value) -> None:
        """Select the item carrying `value`.

        Hand-scanned rather than `findData`: item data here is a Python object
        (`datetime.time` for the slots), and Qt's own lookup does not match
        wrapped Python objects reliably.
        """
        index = next(
            (i for i in range(combo.count()) if combo.itemData(i) == value), -1
        )
        combo.blockSignals(True)
        if index >= 0:
            combo.setCurrentIndex(index)
        combo.blockSignals(False)

    # ---------- rendering ----------
    def _sync(self) -> None:
        pickup_dt, return_dt = self.draft.pickup_dt, self.draft.return_dt
        if pickup_dt is None or return_dt is None:
            return

        self.pickup.date_label.setText(day_label(pickup_dt.date()))
        self.return_moment.date_label.setText(day_label(return_dt.date()))

        # Branches and times, without firing the change handlers.
        pickup_choice = self._choice(self.draft.pickup_branch_id) or (self._branches[0] if self._branches else None)
        if pickup_choice is not None:
            self._select_combo(self.pickup.branch_combo, pickup_choice.branch_id)
            if self.pickup.time_combo.count() != len(pickup_choice.slots):
                self._fill_times(self.pickup, pickup_choice)
            self._select_combo(self.pickup.time_combo, pickup_dt.time())

        drop_choice = self._choice(self.draft.return_branch_id) or pickup_choice
        if drop_choice is not None:
            self._select_combo(self.return_moment.branch_combo, drop_choice.branch_id)
            if self.return_moment.time_combo.count() != len(drop_choice.slots):
                self._fill_times(self.return_moment, drop_choice)
            self._select_combo(self.return_moment.time_combo, return_dt.time())
        self.return_moment.branch_combo.setEnabled(self.different_branch.isChecked())
        for name, moment in (("pickup", self.pickup), ("return", self.return_moment)):
            moment.card.setProperty("focused", name == self._focus)
            moment.card.style().unpolish(moment.card)
            moment.card.style().polish(moment.card)

        # The calendar follows the focused card (blocked work is per vehicle).
        self.calendar.set_blocked(self._blocked)
        self.calendar.set_minimum(date.today())
        self.calendar.set_selected(self._focused_date())

        self._price()

    def _price(self) -> None:
        pickup_dt, return_dt = self.draft.pickup_dt, self.draft.return_dt
        problem = self._window_problem()
        if problem is not None:
            self._set_error(problem)
            self._clear_rows()
            return

        quote = pricing.quote(
            self._rate,
            pickup_dt,
            return_dt,
            policy=self._policy,
            motorcycle=self._motorcycle,
        )
        self._set_error(None)
        self.duration_label.setText(
            f"{quote.duration.label} \u00b7 {quote.days_label}"
        )
        self._row("rental").findChild(QLabel, "valueAmount").setText(quote.subtotal_text)
        self._row("insurance").findChild(QLabel, "valueAmount").setText(quote.insurance_text)
        self._row("service").findChild(QLabel, "valueAmount").setText(quote.service_fee_text)
        self._row("vat").findChild(QLabel, "valueAmount").setText(quote.vat_text)
        self._row("total").findChild(QLabel, "valueAmount").setText(quote.total_text)

    def _row(self, key: str) -> QWidget:
        return self._rows[key]

    def _clear_rows(self) -> None:
        for key, row in self._rows.items():
            amount = row.findChild(QLabel, "valueAmount")
            if amount is not None:
                amount.setText("\u2014")
        self.duration_label.setText("\u2014")

    def _set_error(self, message: str | None) -> None:
        """The red line under the summary; also the on/off switch for Pay."""
        if message:
            self.error.setText(message)
            self.error.show()
            self.continue_button.setEnabled(False)
        else:
            self.error.hide()
            self.continue_button.setEnabled(True)

    def _fail(self, message: str) -> None:
        self._set_error(message)

    # ---------- the one validation the services own ----------
    def _window_problem(self) -> str | None:
        """Ask the services whether this window can be booked.

        Runs on a short session of its own: the rules live in the services,
        and the screen must never keep a database row alive between clicks.
        """
        pickup_dt, return_dt = self.draft.pickup_dt, self.draft.return_dt
        if pickup_dt is None or return_dt is None:
            return "Pick the pickup and return dates."
        if not self._branches:
            return "No branch is open for bookings right now."
        try:
            with SessionLocal() as session:
                vehicle = session.get(Vehicle, self.draft.vehicle_id)
                if vehicle is None:
                    return "That vehicle is no longer in the fleet."
                pickup_branch = session.get(Branch, self.draft.pickup_branch_id)
                return_branch = (
                    session.get(Branch, self.draft.return_branch_id)
                    if self.draft.return_branch_id
                    else None
                )
                branch_service.validate_window(
                    pickup_dt, return_dt, pickup_branch, return_branch
                )
                booking_service.assert_window_free(
                    session, vehicle, pickup_dt, return_dt
                )
        except ServiceError as exc:
            return str(exc)
        except Exception as exc:  # noqa: BLE001 - the database is remote
            return f"Could not check the calendar: {exc}"
        return None


def _minutes(value: time) -> int:
    return value.hour * 60 + value.minute


__all__ = ["BranchChoice", "BookingSchedulePage", "Moment", "STEP_INDEX"]
