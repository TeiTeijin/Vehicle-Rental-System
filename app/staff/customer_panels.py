"""Customer panels: frosted-overlay forms, never OS windows.

Same wizard-step recipe as the bookings/fleet panels:

- CustomerFormPanel: the ONE customer form (Add/Edit here, inline create in
  the rental wizard). Duplicate phone/licence warns live.
- CustomerDetailsPanel: profile + licence docs, current/next + history,
  payments + settle entry, penalties/incidents, staff notes, stats.
- SettlePanel: booking picker (oldest pre-selected), amount with cascade
  preview, one handover split into per-booking Payment rows.
"""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QImage, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from app.models import Booking, Users
from app.services import customer_service, payment_service
from app.staff import theme
from app.staff.cards import Caption, SectionLabel
from app.staff.feedback import describe
from app.staff.rental_wizard import _StepBase, make_choice_button, make_status_box
from app.utils.money import ZERO, money, pesos

DASH = "—"


def _field_label(text: str, parent: QWidget) -> QLabel:
    label = QLabel(text, parent)
    label.setObjectName("fieldLabel")
    return label


def _friendly(error: BaseException) -> str:
    message, _field, _kind = describe(error)
    return message


def _thumb(path: str | None, width: int = 160, height: int = 100) -> QPixmap | None:
    if not path:
        return None
    image = QImage(path)
    if image.isNull():
        return None
    return QPixmap.fromImage(
        image.scaled(
            width, height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    )


class CustomerFormPanel(_StepBase):
    """The single customer form. `preset_licence` for wizard inline-create."""

    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(
        self, context, user_id: int | None = None,
        *, preset_licence: str = "", parent=None,
    ) -> None:
        super().__init__(parent)
        self._context = context
        self._user_id = user_id
        self._head(
            "Edit customer" if user_id else "New customer",
            "Identity, licence, and how to reach them.",
        )
        self._error = self._error_label()
        self._dup_label = Caption("", parent=self.card)
        self._body.addWidget(self._dup_label)

        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.name_edit = QLineEdit(self.card)
        grid.addRow(_field_label("Full name", self.card), self.name_edit)
        self.email_edit = QLineEdit(self.card)
        self.email_edit.setPlaceholderText("For the receipt and app login")
        grid.addRow(_field_label("Email", self.card), self.email_edit)
        self.phone_edit = QLineEdit(self.card)
        grid.addRow(_field_label("Contact number", self.card), self.phone_edit)
        self.address_edit = QLineEdit(self.card)
        grid.addRow(_field_label("Address", self.card), self.address_edit)
        self.birth_edit = QDateEdit(self.card)
        self.birth_edit.setCalendarPopup(True)
        self.birth_edit.setDisplayFormat("dd MMM yyyy")
        self.birth_edit.setDate(QDate(2000, 1, 1))
        self.birth_edit.setMaximumDate(QDate.currentDate())
        grid.addRow(_field_label("Birthdate", self.card), self.birth_edit)
        self.emergency_edit = QLineEdit(self.card)
        self.emergency_edit.setPlaceholderText("Optional")
        grid.addRow(_field_label("Emergency contact", self.card), self.emergency_edit)
        self.licence_edit = QLineEdit(self.card)
        self.licence_edit.setText(preset_licence)
        grid.addRow(_field_label("Licence number", self.card), self.licence_edit)
        self.expiry_edit = QDateEdit(self.card)
        self.expiry_edit.setCalendarPopup(True)
        self.expiry_edit.setDisplayFormat("dd MMM yyyy")
        self.expiry_edit.setDate(QDate.currentDate().addYears(3))
        grid.addRow(_field_label("Licence expiry", self.card), self.expiry_edit)
        self.source_combo = QComboBox(self.card)
        self.source_combo.addItems(["walk_in", "online"])
        grid.addRow(_field_label("Source", self.card), self.source_combo)
        self._body.addLayout(grid)

        doc_row = QHBoxLayout()
        doc_row.setContentsMargins(0, 0, 0, 0)
        doc_row.setSpacing(8)
        self.front_button = QPushButton("Licence front…", self.card)
        self.front_button.setObjectName("secondaryButton")
        self.front_button.clicked.connect(lambda: self._pick_doc("front"))
        self.back_button = QPushButton("Licence back…", self.card)
        self.back_button.setObjectName("secondaryButton")
        self.back_button.clicked.connect(lambda: self._pick_doc("back"))
        doc_row.addWidget(self.front_button, 1)
        doc_row.addWidget(self.back_button, 1)
        self._body.addLayout(doc_row)
        self._doc_paths: dict[str, str] = {}

        for widget in (self.phone_edit, self.licence_edit):
            widget.textChanged.connect(lambda _t: self._check_duplicates())

        back, go = self._buttons("Create & continue" if user_id is None and preset_licence else "Save customer")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self._go = go

        if user_id is not None:
            self._prefill()

    def _prefill(self) -> None:
        with self._context.reading() as session:
            customer = session.get(Users, self._user_id)
            if customer is None:
                return
            snap = {
                "full_name": customer.full_name,
                "email": customer.email,
                "phone": customer.phone,
                "address": customer.address,
                "birthdate": customer.birthdate,
                "emergency_contact": customer.emergency_contact,
                "license_number": customer.license_number,
                "license_expiry": customer.license_expiry,
                "source": customer.source or "walk_in",
                "front": customer.licence_front_path,
                "back": customer.licence_back_path,
            }
        self.name_edit.setText(snap["full_name"] or "")
        self.email_edit.setText(snap["email"] or "")
        self.phone_edit.setText(snap["phone"] or "")
        self.address_edit.setText(snap["address"] or "")
        if snap["birthdate"] is not None:
            birth = snap["birthdate"]
            self.birth_edit.setDate(QDate(birth.year, birth.month, birth.day))
        self.emergency_edit.setText(snap["emergency_contact"] or "")
        self.licence_edit.setText(snap["license_number"] or "")
        if snap["license_expiry"] is not None:
            expiry = snap["license_expiry"]
            self.expiry_edit.setDate(QDate(expiry.year, expiry.month, expiry.day))
        self.source_combo.setCurrentText(snap["source"])
        if snap["front"]:
            self._doc_paths["front"] = snap["front"]
            self.front_button.setText("Front ✓")
        if snap["back"]:
            self._doc_paths["back"] = snap["back"]
            self.back_button.setText("Back ✓")

    def _pick_doc(self, side: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"Licence {side}", "", "Images (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self._doc_paths[side] = path
            button = self.front_button if side == "front" else self.back_button
            button.setText(f"{side.title()} ✓")

    def _check_duplicates(self) -> None:
        with self._context.reading() as session:
            hits = customer_service.find_duplicates(
                session,
                phone=self.phone_edit.text(),
                license_number=self.licence_edit.text(),
                exclude_id=self._user_id,
            )
            names = [c.full_name for c in hits]
        if names:
            self._dup_label.setText(
                f"Possible duplicate: {', '.join(names)}. Select them instead of creating a new record."
            )
        else:
            self._dup_label.setText("")

    def _fail(self, message: str) -> None:
        self._error.setText(message)
        self._error.setVisible(True)

    def _on_go(self) -> None:
        import re as _re

        if not self.name_edit.text().strip():
            self._fail("Customer name is required.")
            return
        if not _re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", self.email_edit.text().strip()):
            self._fail("Enter a valid email address.")
            return
        phone = self.phone_edit.text().strip()
        if phone:
            digits = _re.sub(r"\D", "", phone)
            if len(digits) < 7 or len(digits) > 15:
                self._fail("Contact number must be 7–15 digits.")
                return
        if not self.address_edit.text().strip():
            self._fail("Customer address is required.")
            return
        if not self.licence_edit.text().strip():
            self._fail("No licence, no rental: the licence number is required.")
            return
        if self.birth_edit.date().toPython() > date.today():
            self._fail("Birthdate cannot be in the future.")
            return
        self._error.setVisible(False)
        self.submitted.emit(
            {
                "full_name": self.name_edit.text().strip(),
                "email": self.email_edit.text().strip(),
                "phone": self.phone_edit.text().strip() or None,
                "address": self.address_edit.text().strip(),
                "birthdate": self.birth_edit.date().toPython(),
                "emergency_contact": self.emergency_edit.text().strip() or None,
                "license_number": self.licence_edit.text().strip(),
                "license_expiry": self.expiry_edit.date().toPython(),
                "source": self.source_combo.currentText(),
                "front_src": self._doc_paths.get("front"),
                "back_src": self._doc_paths.get("back"),
            }
        )


class SettlePanel(_StepBase):
    """Pick the booking, type the handover: remainder cascades oldest-first."""

    submitted = Signal(dict)
    cancelled = Signal()

    def __init__(self, context, customer_id: int, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._customer_id = customer_id
        self._head("Settle balance", "One handover, split across bookings.")
        self._error = self._error_label()
        with self._context.reading() as session:
            customer = session.get(Users, customer_id)
            self._unpaid = customer_service.unpaid_bookings(session, customer)
        if not self._unpaid:
            self._body.addWidget(Caption("Nothing owed.", parent=self.card))
            self._legs: list = []
        else:
            self._group = QButtonGroup(self)
            self._buttons_map: dict[int, QPushButton] = {}
            for booking_id, owed in self._unpaid:
                button = make_choice_button(
                    f"#{booking_id} — owes {owed:,.2f}", self.card
                )
                button.setCheckable(True)
                self._group.addButton(button)
                self._body.addWidget(button)
                self._buttons_map[booking_id] = button
            first = self._unpaid[0][0]
            self._buttons_map[first].setChecked(True)
            for button in self._buttons_map.values():
                button.clicked.connect(self._refresh_preview)
        grid = QFormLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(6)
        self.amount_edit = QLineEdit(self.card)
        self.method_combo = QComboBox(self.card)
        self.method_combo.addItems(list(payment_service.METHODS))
        self.ref_edit = QLineEdit(self.card)
        self.ref_edit.setPlaceholderText("GCash ref (required for GCash)")
        grid.addRow(_field_label("Amount (PHP)", self.card), self.amount_edit)
        grid.addRow(_field_label("Method", self.card), self.method_combo)
        grid.addRow(_field_label("Reference no.", self.card), self.ref_edit)
        self._body.addLayout(grid)
        self.preview = Caption("", parent=self.card)
        self._body.addWidget(self.preview)
        self.amount_edit.textChanged.connect(lambda _t: self._refresh_preview())
        back, go = self._buttons("Record payment")
        back.clicked.connect(self.cancelled.emit)
        go.clicked.connect(self._on_go)
        self._refresh_preview()

    def _picked(self) -> int | None:
        for booking_id, button in getattr(self, "_buttons_map", {}).items():
            if button.isChecked():
                return booking_id
        return self._unpaid[0][0] if self._unpaid else None

    def _refresh_preview(self) -> None:
        picked = self._picked()
        if picked is None:
            self.amount_edit.setText("")
            self.preview.setText("")
            return
        owed_here = next(owed for bid, owed in self._unpaid if bid == picked)
        if not self.amount_edit.text().strip():
            self.amount_edit.setText(f"{owed_here:,.2f}")
        try:
            legs = customer_service.allocate_settlement(
                self._unpaid, picked, self.amount_edit.text() or "0"
            )
        except Exception as exc:  # noqa: BLE001 - advisory preview only
            self.preview.setText(_friendly(exc))
            return
        self.preview.setText(
            " + ".join(f"#{bid} {owed:,.2f}" for bid, owed in legs)
        )

    def _on_go(self) -> None:
        picked = self._picked()
        if picked is None:
            self._error.setText("Nothing owed.")
            self._error.setVisible(True)
            return
        if self.method_combo.currentText() == "gcash" and not self.ref_edit.text().strip():
            self._error.setText("A GCash reference number is required.")
            self._error.setVisible(True)
            return
        try:
            legs = customer_service.allocate_settlement(
                self._unpaid, picked, self.amount_edit.text()
            )
        except Exception as exc:  # noqa: BLE001
            self._error.setText(_friendly(exc))
            self._error.setVisible(True)
            return
        self._error.setVisible(False)
        self.submitted.emit(
            {
                "legs": [(bid, str(owed)) for bid, owed in legs],
                "method": self.method_combo.currentText(),
                "reference_no": self.ref_edit.text().strip(),
            }
        )


class CustomerDetailsPanel(_StepBase):
    closed = Signal()
    edit_requested = Signal(int)
    rental_requested = Signal(int)

    def __init__(self, context, user_id: int, parent=None) -> None:
        super().__init__(parent)
        self._context = context
        self._user_id = user_id
        self._head("Customer", f"Customer #{user_id}")
        self._error = self._error_label()
        self._load()
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(8)
        action_row.addStretch(1)
        self.rental_button = QPushButton("New rental", self.card)
        self.rental_button.setObjectName("primaryButton")
        self.rental_button.clicked.connect(lambda: self.rental_requested.emit(self._user_id))
        self.edit_button = QPushButton("Edit", self.card)
        self.edit_button.setObjectName("secondaryButton")
        self.edit_button.clicked.connect(lambda: self.edit_requested.emit(self._user_id))
        self.close_button = QPushButton("Close", self.card)
        self.close_button.setObjectName("secondaryButton")
        self.close_button.clicked.connect(self.closed.emit)
        action_row.addWidget(self.rental_button)
        action_row.addWidget(self.edit_button)
        action_row.addWidget(self.close_button)
        self._body.addLayout(action_row)

    # -- data -----------------------------------------------------------

    def _read(self) -> dict | None:
        with self._context.reading() as session:
            customer = session.get(Users, self._user_id)
            if customer is None or customer.role != "customer":
                return None
            bookings = (
                session.query(Booking)
                .filter(Booking.user_id == customer.user_id)
                .order_by(Booking.start_date.desc())
                .all()
            )
            booking_rows = [
                {
                    "ref": b.booking_id,
                    "vehicle": f"{b.vehicle.plate_number} {b.vehicle.make} {b.vehicle.model}" if b.vehicle else DASH,
                    "category": b.vehicle.category.category_name if b.vehicle and b.vehicle.category else DASH,
                    "start": b.start_date,
                    "end": b.end_date,
                    "status": b.status,
                    "total": b.total_cost,
                    "paid": payment_service.booking_balance(session, b).amount_paid,
                    "owed": payment_service.booking_balance(session, b).balance,
                }
                for b in bookings
            ]
            payments = []
            for b in bookings:
                for p in b.payments:
                    payments.append(
                        {
                            "ref": b.booking_id,
                            "amount": p.amount,
                            "method": p.method,
                            "kind": p.kind,
                            "status": p.status,
                            "when": p.paid_at,
                        }
                    )
            penalties = []
            for b in bookings:
                for penalty in b.penalties:
                    inspections = "; ".join(
                        f"{r.inspection_type} {r.mileage_reading:,}km"
                        + (f" ({r.damage_notes})" if r.damage_notes else "")
                        for r in b.inspections
                    ) or "no inspection"
                    penalties.append(
                        {
                            "ref": b.booking_id,
                            "type": penalty.penalty_type,
                            "amount": penalty.amount,
                            "description": penalty.description,
                            "inspections": inspections,
                        }
                    )
            notes = [
                {
                    "note": n.note,
                    "author_id": n.author_id,
                    "when": n.created_at,
                }
                for n in customer_service.list_notes(session, customer)
            ]
            return {
                "name": customer.full_name,
                "email": customer.email,
                "phone": customer.phone or DASH,
                "address": customer.address,
                "birthdate": customer.birthdate,
                "emergency": customer.emergency_contact or DASH,
                "licence_number": customer.license_number,
                "licence_expiry": customer.license_expiry,
                "source": (customer.source or "walk_in").replace("_", " ").title(),
                "is_active": customer.is_active,
                "flag_reason": (customer.flag_reason or "").strip() or None,
                "front": customer.licence_front_path,
                "back": customer.licence_back_path,
                "bookings": booking_rows,
                "payments": payments,
                "penalties": penalties,
                "notes": notes,
            }

    def _section(self, title: str, lines: list[str]) -> None:
        self._body.addWidget(SectionLabel(title, parent=self.card))
        for line in lines:
            self._body.addWidget(Caption(line, parent=self.card))

    # -- layout ---------------------------------------------------------

    def _load(self) -> None:
        from app.staff.cards import SectionLabel

        data = self._read()
        if data is None:
            self._section("Missing", ["That customer no longer exists."])
            return
        self._data = data
        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)
        head.addWidget(SectionLabel(data["name"], parent=self.card), 1)
        if not data["is_active"]:
            head.addWidget(make_status_box("Deactivated", theme.MUTED, self.card))
        if data["flag_reason"]:
            head.addWidget(make_status_box(f"Flagged: {data['flag_reason']}", theme.DANGER, self.card))
        self._body.addLayout(head)
        contact_row = QHBoxLayout()
        contact_row.setContentsMargins(0, 0, 0, 0)
        contact_row.setSpacing(8)
        contact_row.addWidget(Caption(f"{data['phone']} · {data['email']}", parent=self.card), 1)
        copy_phone = QPushButton("Copy phone", self.card)
        copy_phone.setObjectName("secondaryButton")
        copy_phone.clicked.connect(
            lambda: QApplication.clipboard().setText(data["phone"] if data["phone"] != DASH else "")
        )
        copy_email = QPushButton("Copy email", self.card)
        copy_email.setObjectName("secondaryButton")
        copy_email.clicked.connect(
            lambda: QApplication.clipboard().setText(data["email"])
        )
        mailto = QPushButton("Email", self.card)
        mailto.setObjectName("secondaryButton")
        mailto.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl(f"mailto:{data['email']}"))
        )
        contact_row.addWidget(copy_phone)
        contact_row.addWidget(copy_email)
        contact_row.addWidget(mailto)
        self._body.addLayout(contact_row)

        self._section(
            "Profile",
            [
                f"Address: {data['address']}",
                f"Birthdate: {data['birthdate']:%d %b %Y}" if data["birthdate"] else "Birthdate: —",
                f"Emergency: {data['emergency']}",
                f"Source: {data['source']}",
            ],
        )
        self._section(
            "Licence",
            [
                f"{data['licence_number']} · "
                + (f"valid to {data['licence_expiry']:%d %b %Y}" if data["licence_expiry"] else "no expiry on file"),
            ],
        )
        doc_row = QHBoxLayout()
        doc_row.setContentsMargins(0, 0, 0, 0)
        doc_row.setSpacing(8)
        shown_docs = False
        for label, path in (("Front", data["front"]), ("Back", data["back"])):
            thumb = _thumb(path)
            doc = QLabel(self.card)
            if thumb is not None:
                doc.setPixmap(thumb)
                doc.setCursor(Qt.CursorShape.PointingHandCursor)
                doc.mousePressEvent = lambda _e, _p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(_p))
                shown_docs = True
            else:
                doc.setText(f"{label}: —")
            doc_row.addWidget(doc)
        doc_row.addStretch(1)
        if not shown_docs:
            doc_row.addWidget(Caption("No licence photos on file.", parent=self.card))
        self._body.addLayout(doc_row)

        actives = [b for b in data["bookings"] if b["status"] in ("pending", "confirmed", "ongoing")]
        if actives:
            current = actives[0]
            self._body.addWidget(
                make_status_box(
                    f"Out now: #{current['ref']} · {current['vehicle']} · "
                    f"{current['start']:%d %b} → {current['end']:%d %b}",
                    theme.WARN,
                    self.card,
                )
            )
            for b in actives[1:2]:
                self._body.addWidget(
                    Caption(
                        f"Next: #{b['ref']} · {b['vehicle']} · {b['start']:%d %b} → {b['end']:%d %b}",
                        parent=self.card,
                    )
                )
        history = [b for b in data["bookings"] if b["status"] in ("completed", "cancelled")][:5]
        self._section(
            "Booking history",
            [
                f"#{b['ref']} · {b['vehicle']} · {b['start']:%d %b %Y} → {b['end']:%d %b %Y} · {b['status']}"
                for b in history
            ]
            or ["No past rentals."],
        )
        owed_total = sum((b["owed"] for b in data["bookings"]), ZERO)
        money_lines = [
            f"Owes {owed_total:,.2f} across "
            f"{sum(1 for b in data['bookings'] if b['owed'] > ZERO)} rental(s)."
        ]
        for p in data["payments"][:10]:
            when = p["when"].strftime("%d %b %Y") if p["when"] else "pending"
            money_lines.append(
                f"#{p['ref']} · {p['amount']:,.2f} via {p['method']} ({p['kind']}/{p['status']}) · {when}"
            )
        self._section("Payments", money_lines)
        self._section(
            "Penalties & incidents",
            [
                f"#{p['ref']} {p['type']}: {p['amount']:,.2f} · {p['description']} · {p['inspections']}"
                for p in data["penalties"]
            ]
            or ["No penalties or incidents."],
        )

        self._body.addWidget(SectionLabel("Staff notes", parent=self.card))
        self._notes_host = QVBoxLayout()
        self._notes_host.setContentsMargins(0, 0, 0, 0)
        self._notes_host.setSpacing(8)
        notes_box = QWidget(self.card)
        notes_box.setLayout(self._notes_host)
        self._body.addWidget(notes_box)
        for n in data["notes"]:
            when = n["when"].strftime("%d %b %Y %H:%M") if n["when"] else DASH
            author = f"staff #{n['author_id']}" if n["author_id"] else "staff"
            self._notes_host.addWidget(Caption(f"{n['note']} ({author}, {when})", parent=self.card))
        note_row = QHBoxLayout()
        note_row.setContentsMargins(0, 0, 0, 0)
        note_row.setSpacing(8)
        self.note_edit = QLineEdit(self.card)
        self.note_edit.setPlaceholderText("Add a note…")
        note_add = QPushButton("Add", self.card)
        note_add.setObjectName("secondaryButton")
        note_add.clicked.connect(self._on_add_note)
        self.note_edit.returnPressed.connect(self._on_add_note)
        note_row.addWidget(self.note_edit, 1)
        note_row.addWidget(note_add)
        self._body.addLayout(note_row)

        done = [b for b in data["bookings"] if b["status"] == "completed"]
        spent = sum((b["paid"] for b in data["bookings"]), ZERO)
        lengths = [(b["end"] - b["start"]).days for b in done]
        avg = sum(lengths) / len(lengths) if lengths else 0
        categories: dict[str, int] = {}
        for b in done:
            categories[b["category"]] = categories.get(b["category"], 0) + 1
        top = max(categories, key=categories.get) if categories else DASH
        rentals = len(done)
        tier = "Frequent" if rentals >= 10 else "Regular"
        self._section(
            "Summary",
            [
                f"{rentals} rentals · spent {spent:,.2f} · "
                f"avg {avg:.1f} days · mostly {top} · {tier}.",
            ],
        )

    def _on_add_note(self) -> None:
        text = self.note_edit.text().strip()
        if not text:
            self._error.setText("Note cannot be empty.")
            self._error.setVisible(True)
            return
        try:
            with self._context.session() as session:
                customer = session.get(Users, self._user_id)
                customer_service.add_note(
                    session, customer, text,
                    author_id=self._context.user.user_id,
                )
        except Exception as exc:  # noqa: BLE001
            self._error.setText(_friendly(exc))
            self._error.setVisible(True)
            return
        self.note_edit.clear()
        self._error.setVisible(False)
        self._notes_host.addWidget(
            Caption(
                f"{text} (staff #{self._context.user.user_id})",
                parent=self.card,
            )
        )
