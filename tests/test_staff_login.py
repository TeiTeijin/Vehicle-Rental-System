"""Tests for the sign-in screen's appearance and its floating labels.

Every assertion in this file looks at rendered pixels or at geometry, never at
the stylesheet source. That is the point.

Three separate bugs got through review on this screen because each one was
invisible in the code and obvious on screen:

  * A bare ``QWidget`` ignores ``background`` from a stylesheet unless
    ``WA_StyledBackground`` is set, so ``#FFFFFF`` was accepted and silently
    discarded and the page's cream showed through instead.
  * The blanket ``QWidget { background: ... }`` rule at the top of ``staff.qss``
    matched the form column, which had no object name of its own to override
    it, and drew a cream panel over the white page.
  * A ``QLineEdit`` is a ``QAbstractScrollArea``, so the floating label at a
    negative ``y`` was clipped by the viewport and never painted at all.

In each case the stylesheet said the right thing and the screen showed
something else. Reading the source cannot catch that; sampling the render can.
"""

from __future__ import annotations

import os
import re
from collections import Counter

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.staff import theme  # noqa: E402
from app.staff.context import (  # noqa: E402
    DatabaseSelection,
    DatabaseTarget,
    StaffContext,
)
from app.staff.login import LOGIN_SIZE, LoginView  # noqa: E402

#: The page cream from the blanket rule. Nothing on this screen may be it.
PAGE_CREAM = "#f1e8d8"
#: The white the sign-in is supposed to be.
PAGE_WHITE = "#ffffff"
#: Anti-aliased edges blend towards these, so a near-match with a handful of
#: pixels is a rounded corner rather than a panel.
NEAR_WHITE_TOLERANCE = 3


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.load_stylesheet())
    yield app


@pytest.fixture
def view(qapp):
    """A demo sign-in at its fixed size, styled and processed."""
    from PySide6.QtTest import QTest

    context = StaffContext(
        DatabaseSelection(
            url="sqlite://",
            target=DatabaseTarget.DEMO,
            label="test demo",
        )
    )
    widget = LoginView(context, on_signed_in=lambda user: None)
    widget.setFixedSize(*LOGIN_SIZE)
    widget.show()
    QTest.qWait(120)
    qapp.processEvents()
    yield widget
    widget.hide()
    context.dispose()


def _colours(widget) -> Counter:
    image = widget.grab().toImage()
    return Counter(
        image.pixelColor(x, y).name()
        for y in range(image.height())
        for x in range(image.width())
    )


def _ink_box(widget, label):
    """Bounding box of drawn text inside a label, in window coordinates."""
    image = widget.grab().toImage()
    origin = label.mapTo(widget, label.rect().topLeft())
    xs, ys = [], []
    for y in range(origin.y(), min(image.height(), origin.y() + label.height())):
        for x in range(origin.x(), min(image.width(), origin.x() + label.width())):
            colour = image.pixelColor(x, y)
            if colour.red() + colour.green() + colour.blue() < 720:
                xs.append(x)
                ys.append(y)
    assert xs, f"no ink drawn for {label.objectName()!r}"
    return min(xs), max(xs)


def _settle(field):
    """Let the label's 150ms flight finish, then repaint.

    QPropertyAnimation has no `end()`; the honest way to see where the label
    ends up is to let the clock run.
    """
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    QTest.qWait(200)
    QApplication.processEvents()


def _focus(view, field):
    """Give a field keyboard focus the way a person would.

    A QWidget only takes focus while its window is active, so the window has
    to be activated first or `setFocus` is silently a no-op.
    """
    view.activateWindow()
    field.setFocus()
    assert field.hasFocus(), "focus never landed; the assertion below is vacuous"
    _settle(field)


def _blur(view, field):
    """Move focus off a field by putting it on the next control."""
    view.activateWindow()
    view.submit.setFocus()
    assert not field.hasFocus()
    _settle(field)


def _tap(view, field):
    """Put a field through a full focus/blur cycle.

    Showing the window hands focus to the first *empty* field, so which field
    starts focused is not fixed and must not be leaned on. Blurring first
    makes the lift-on-focus below a real transition rather than a no-op that
    happens to pass.
    """
    _blur(view, field)
    _focus(view, field)


# --------------------------------------------------------------------------
# Background
# --------------------------------------------------------------------------


class TestThePageIsActuallyWhite:
    def test_the_corner_pixels_are_white(self, view):
        """A bare QWidget will not paint a stylesheet background without
        WA_StyledBackground, and does it silently."""
        image = view.grab().toImage()
        for x, y in ((2, 2), (image.width() - 3, 2), (2, image.height() - 3)):
            assert image.pixelColor(x, y).name() == PAGE_WHITE, (x, y)

    def test_no_panel_of_page_cream_is_left_anywhere(self, view):
        """The blanket `QWidget { background: #F1E8D8 }` rule will happily
        paint any unnamed container. The form column is one."""
        counts = _colours(view)
        assert PAGE_CREAM not in counts, (
            f"{counts[PAGE_CREAM]}px of page cream at the old panel's position"
        )
        # And cream is not hiding under a near-miss spelling.
        for name, n in counts.items():
            r, g, b = int(name[1:3], 16), int(name[3:5], 16), int(name[5:7], 16)
            if abs(r - 0xF1) + abs(g - 0xE8) + abs(b - 0xD8) <= 12 and n > 40:
                pytest.fail(f"{name} covering {n}px is the page cream again")

    def test_white_is_the_dominant_colour(self, view):
        counts = _colours(view)
        total = sum(counts.values())
        assert counts[PAGE_WHITE] / total > 0.8, "the page should be mostly white"

    def test_the_form_column_paints_nothing(self, view):
        from PySide6.QtWidgets import QWidget

        form = view.findChild(QWidget, theme.OBJ_LOGIN_FORM)
        assert form is not None, "the form column has no object name to style"
        image = view.grab().toImage()
        origin = form.mapTo(view, form.rect().topLeft())
        assert image.pixelColor(origin.x() + 1, origin.y() + 1).name() == PAGE_WHITE


# --------------------------------------------------------------------------
# The two centred labels
# --------------------------------------------------------------------------


class TestTheHeadingIsCentred:
    @pytest.mark.parametrize(
        "object_name", [theme.OBJ_LOGIN_WORDMARK, theme.OBJ_LOGIN_CAPTION]
    )
    def test_the_ink_sits_on_the_window_centre_line(self, view, object_name):
        """`setAlignment` alone does nothing on a QLabel, because its maximum
        width is its text width, so the layout hands it no slack to distribute.
        The widget has to be centred in its row as well."""
        from PySide6.QtWidgets import QLabel

        label = next(
            w for w in view.findChildren(QLabel) if w.objectName() == object_name
        )
        x0, x1 = _ink_box(view, label)
        centre = (x0 + x1) / 2
        window_centre = (view.width() - 1) / 2
        assert abs(centre - window_centre) <= 1.5, (
            f"{object_name} is {centre - window_centre:+.1f}px off centre"
        )

    def test_the_two_labels_say_what_they_should(self, view):
        from PySide6.QtWidgets import QLabel

        by_name = {w.objectName(): w for w in view.findChildren(QLabel)}
        assert by_name[theme.OBJ_LOGIN_WORDMARK].text() == "Rent Desk"
        assert by_name[theme.OBJ_LOGIN_CAPTION].text() == "Staff Sign in"


# --------------------------------------------------------------------------
# The floating label
# --------------------------------------------------------------------------


class TestTheFloatingLabel:
    def test_an_empty_field_shows_its_label_inside(self, view):
        """A pre-filled field keeps its caption lifted -- the words are the
        answer now, so it is a label rather than a prompt. Only a genuinely
        empty, unfocused field shows the caption as placeholder text."""
        from app.staff.login import FIELD_TOP

        field = view.password
        _blur(view, field)
        assert not field.text(), "precondition: the field must be empty"
        assert not field._lifted
        assert field._label.pos().y() > FIELD_TOP, "should sit in the text area"

    def test_a_prefilled_field_keeps_its_caption_lifted(self, view):
        field = view.email
        assert field.text(), "precondition: the demo email is pre-filled"
        _blur(view, field)
        assert field._lifted
        assert field._label.objectName() == theme.OBJ_LABEL_LIFTED

    def test_focusing_lifts_the_label_above_the_rule(self, view):
        field = view.password
        _blur(view, field)
        before = field._label.pos().y()
        _focus(view, field)
        assert field._lifted
        assert field._label.pos().y() < before, "the label did not travel up"

    def test_the_lifted_label_is_inside_the_widget_not_clipped(self, view):
        """A QLineEdit is a QAbstractScrollArea: a child at a negative y is
        clipped by the viewport and drawn nowhere. The label must therefore
        travel *into* the reserved top band, never above the widget."""
        field = view.password
        _tap(view, field)
        assert field._label.pos().y() >= 0, "clipped by the line edit's viewport"

    def test_the_lifted_label_is_actually_painted(self, view):
        """The stronger version of the clipping check: the band the label
        travels into must contain something other than a flat fill."""
        field = view.password
        _tap(view, field)
        view.repaint()

        image = view.grab().toImage()
        top = field.mapTo(view, field.rect().topLeft()).y()
        band = Counter(
            image.pixelColor(x, y).name()
            for y in range(max(0, top), top + 16)
            for x in range(field.x() + 2, min(view.width(), field.x() + 140))
        )
        assert len(band) > 1, "the lifted label is not being drawn"

    def test_losing_focus_drops_the_label_when_the_field_is_empty(self, view):
        field = view.password
        _focus(view, field)
        field.clear()
        _blur(view, field)
        assert not field._lifted
        assert field._label.objectName() == theme.OBJ_LABEL_REST

    def test_a_filled_field_keeps_its_label_up(self, view):
        """The words are the answer now, so the caption is a label again
        rather than a prompt, and it should stay out of the way.

        Focus has to be granted first: a field that was never focused never
        gets a focus-out, so nothing would drop the label in the first place.
        """
        field = view.email
        _focus(view, field)
        field.setText("someone@rentdesk.local")
        _blur(view, field)
        assert field._lifted
        assert field._label.objectName() == theme.OBJ_LABEL_LIFTED

    def test_clearing_the_field_puts_the_label_back(self, view):
        field = view.email
        _focus(view, field)
        field.setText("someone@rentdesk.local")
        _blur(view, field)
        assert field._lifted, "precondition: the label is up while the text is there"
        field.clear()
        _settle(field)
        assert not field._lifted


# --------------------------------------------------------------------------
# Stylesheet integrity
# --------------------------------------------------------------------------


class TestNoDeadStylesheetRules:
    def test_every_sign_in_rule_matches_a_real_widget(self, view):
        """Qt does not warn about a selector that matches nothing, so a rule
        can sit in the stylesheet styling nothing at all -- which is how the
        old `QLabel#loginError` rule survived."""
        from PySide6.QtWidgets import QWidget

        rules = set(
            re.findall(
                r"Q(?:Widget|Label|LineEdit|Frame|PushButton)#(login[A-Za-z]*)\s*\{",
                theme.load_stylesheet(),
            )
        )
        # These two are swapped onto a label at runtime, so at any instant one
        # of them matches nothing. They are checked by state instead.
        rules -= {theme.OBJ_LABEL_REST, theme.OBJ_LABEL_LIFTED}
        present = {view.objectName()} | {
            w.objectName() for w in view.findChildren(QWidget) if w.objectName()
        }
        assert rules - present == set(), "styled but never applied"

    def test_both_label_states_are_reachable(self, view):
        """Each state rule has to be exercised while it is live, otherwise a
        typo in one of them is invisible."""
        field = view.password
        _blur(view, field)
        assert field._label.objectName() == theme.OBJ_LABEL_REST
        _focus(view, field)
        assert field._label.objectName() == theme.OBJ_LABEL_LIFTED

    def test_no_rule_is_written_against_the_wrong_widget_type(self):
        """`QLabel#loginField` would style nothing, because the field is a
        QLineEdit. Cheap to check, and it has happened once already."""
        qss = theme.load_stylesheet()
        for object_name, widget_type in (
            (theme.OBJ_LOGIN_FIELD, "QLineEdit"),
            (theme.OBJ_LOGIN_WORDMARK, "QLabel"),
            (theme.OBJ_LOGIN_CAPTION, "QLabel"),
            (theme.OBJ_LOGIN_BUTTON, "QPushButton"),
        ):
            wrong = [
                m
                for m in re.findall(r"(Q[A-Za-z]+)#" + object_name + r"\s*\{", qss)
                if m != widget_type
            ]
            assert not wrong, f"#{object_name} is not a {widget_type}: {wrong}"

    def test_every_colour_is_a_valid_hex(self):
        bad = re.findall(
            r":\s*#([0-9A-Za-z]*[g-zG-Z][0-9A-Za-z]*)\s*[;\s}]",
            theme.load_stylesheet(),
        )
        assert not bad, bad
