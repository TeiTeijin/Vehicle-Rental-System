"""Tests for the staff app's feedback channels and the load-state table.

The load-state distinction is the one worth the most coverage: a failed query
that renders as an empty table is a silent, expensive bug, and the test is what
keeps it from coming back.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.services.errors import (  # noqa: E402
    ConflictError,
    NotFoundError,
    ServiceError,
    StateError,
    ValidationError,
)
from app.staff.context import AccessDenied, NotSignedIn  # noqa: E402
from app.staff.feedback import (  # noqa: E402
    GENERIC_FAILURE,
    describe,
    kind_colour,
    show_field_error,
)
from app.staff.tables import (  # noqa: E402
    ALIGN_RIGHT,
    Column,
    LoadState,
    LoadStateTable,
)
from app.staff.theme import DANGER, OK, WARN  # noqa: E402


class OperationalError(Exception):
    """Stand-in for a driver error.

    `feedback._looks_like_a_connection_problem` matches on the class *name*, so
    a local class with the right name exercises the same path without importing
    a DBAPI.
    """


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


# --------------------------------------------------------------------------
# Feedback
# --------------------------------------------------------------------------


class TestDescribe:
    def test_a_validation_error_keeps_its_own_text_and_field(self):
        message, field, kind = describe(ValidationError("Start date is in the past.", field="start_date"))
        assert message == "Start date is in the past."
        assert field == "start_date"
        assert kind == "error"

    def test_a_refusal_is_a_warning_not_a_failure(self):
        """Cancelling a completed booking is a normal part of the domain.
        Colouring it red makes an ordinary refusal look like a bug."""
        for error in (
            ConflictError("That car is already out."),
            StateError("This booking is already cancelled."),
            NotFoundError("No such booking."),
        ):
            _message, _field, kind = describe(error)
            assert kind == "warn", error

    def test_being_signed_out_says_what_to_do(self):
        assert describe(NotSignedIn())[0] == "Sign in to continue."

    def test_being_refused_keeps_its_reason(self):
        assert "Administrators only." in describe(AccessDenied("Administrators only."))[0]

    def test_a_service_error_keeps_its_text(self):
        assert describe(ServiceError("Payment exceeds the balance."))[0].startswith(
            "Payment"
        )

    def test_a_dropped_connection_is_called_out_because_retry_fixes_it(self):
        """A generic 'something went wrong' invites the user to give up; this
        one names the thing they can actually do about it."""
        driver = type("OperationalError", (Exception,), {})("lost")
        message, _field, kind = describe(driver)
        assert "connection" in message.lower()
        assert kind == "error"

    def test_an_unknown_failure_stays_generic(self):
        """A driver message or traceback in a dialog hides what matters."""
        message, field, kind = describe(RuntimeError("0x7fff segfault at ..."))
        assert message == GENERIC_FAILURE
        assert field is None
        assert kind == "error"

    def test_a_wrapped_connection_error_is_still_recognised(self):
        inner = type("OperationalError", (Exception,), {})("gone")
        outer = RuntimeError("query failed")
        outer.__cause__ = inner
        assert "connection" in describe(outer)[0].lower()

    def test_colours_match_the_kind(self):
        assert kind_colour("ok") == OK
        assert kind_colour("warn") == WARN
        assert kind_colour("error") == DANGER


class TestFieldErrors:
    def test_a_message_appears_and_clears(self, qapp):
        from PySide6.QtWidgets import QLabel

        label = QLabel()
        show_field_error(label, "Enter an amount.")
        # Not in a shown window, so isVisible() is False; check hidden state.
        assert label.isHidden() is False
        assert label.text() == "Enter an amount."
        assert label.objectName() == "fieldError"

        show_field_error(label, None)
        assert label.isHidden() is True
        assert label.text() == ""

    def test_a_non_label_is_ignored_rather_than_crashing(self):
        show_field_error("not a widget", "boom")  # must not raise


# --------------------------------------------------------------------------
# LoadStateTable
# --------------------------------------------------------------------------


COLUMNS = [Column("Name", stretch=True), Column("Amount", ALIGN_RIGHT)]


def make_table(qapp, loader, **kwargs):
    return LoadStateTable(COLUMNS, loader, **kwargs)


class TestLoadStates:
    def test_rows_land_in_loaded(self, qapp):
        table = make_table(qapp, lambda: [("One", "1.00"), ("Two", "2.00")])
        assert table.state is LoadState.LOADED
        assert table.row_count() == 2
        assert table.cell_text(0, 0) == "One"

    def test_a_successful_empty_result_is_empty_not_failed(self, qapp):
        """These are different facts and the user has to be able to tell them
        apart: one means 'nothing yet', the other means 'we could not ask'."""
        table = make_table(qapp, lambda: [])
        assert table.state is LoadState.EMPTY
        assert table.row_count() == 0
        assert table.last_error is None

    def test_a_failing_query_is_never_shown_as_empty(self, qapp):
        calls = {"n": 0}

        def loader():
            calls["n"] += 1
            raise OperationalError("server has gone away")

        table = make_table(qapp, loader)
        assert table.state is LoadState.FAILED
        assert table.row_count() == 0
        assert isinstance(table.last_error, OperationalError)
        assert "Could not load" in table._error.text()
        assert calls["n"] == 1

    def test_the_error_never_escapes_into_a_qt_slot(self, qapp):
        """An exception out of a slot prints to stderr and leaves the widget in
        whatever state it happened to be in."""

        def loader():
            raise ValueError("boom")

        table = make_table(qapp, loader)  # must not raise
        assert table.state is LoadState.FAILED

    def test_retry_reruns_the_loader(self, qapp):
        attempt = {"n": 0}

        def loader():
            attempt["n"] += 1
            if attempt["n"] == 1:
                raise OperationalError("transient")
            return [("Recovered", "9.00")]

        table = make_table(qapp, loader)
        assert table.state is LoadState.FAILED
        assert table.retry() is LoadState.LOADED
        assert table.retry_count == 1
        assert table.cell_text(0, 0) == "Recovered"
        assert table.last_error is None

    def test_the_retry_button_reloads(self, qapp):
        table = make_table(qapp, lambda: (_ for _ in ()).throw(OperationalError("x")))
        table._retry.click()
        assert table.retry_count == 1
        assert table.state is LoadState.FAILED

    def test_a_reload_that_still_fails_can_say_so_out_loud(self, qapp):
        table = make_table(qapp, lambda: (_ for _ in ()).throw(OperationalError("x")))
        # No parent, so the dialog path is not taken.
        assert table.reload_or_report(None) is LoadState.FAILED
        assert table.state is LoadState.FAILED

    def test_a_reload_that_recovers_does_not_need_a_parent(self, qapp):
        ok = {"v": True}

        def loader():
            if not ok["v"]:
                raise OperationalError("x")
            return [("Fine", "1.00")]

        table = make_table(qapp, loader)
        ok["v"] = False
        assert table.reload_or_report(None) is LoadState.FAILED
        ok["v"] = True
        assert table.reload_or_report(None) is LoadState.LOADED

    def test_the_empty_message_can_be_reworded(self, qapp):
        table = make_table(qapp, lambda: [], empty_message="Nothing here yet.")
        assert table._placeholder.text() == "Nothing here yet."
        table.set_placeholder("No bookings yet.")
        assert table._placeholder.text() == "No bookings yet."

    def test_a_none_cell_renders_empty_not_none(self, qapp):
        table = make_table(qapp, lambda: [("One", None)])
        assert table.cell_text(0, 1) == ""


class TestStatusPills:
    def test_a_status_column_becomes_a_pill(self, qapp):
        from app.staff.theme import OBJ_STATUS

        table = LoadStateTable(
            [Column("Status")], lambda: [("ongoing",), ("completed",)]
        )
        pill = table.table.cellWidget(0, 0)
        assert pill is not None
        assert pill.objectName() == OBJ_STATUS
        assert table.table.item(0, 0) is not None

    def test_the_pill_text_is_humanised(self, qapp):
        table = LoadStateTable([Column("Status")], lambda: [("in_progress",)])
        assert table.cell_text(0, 0) == "In Progress"

    def test_cell_text_falls_back_to_the_pill(self, qapp):
        table = LoadStateTable([Column("State")], lambda: [("maintenance",)])
        assert table.cell_text(0, 0) == "Maintenance"
