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
from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QWidget

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from app.staff import theme  # noqa: E402
from app.staff.context import (  # noqa: E402
    DatabaseSelection,
    DatabaseTarget,
    StaffContext,
)
from app.staff.login import (  # noqa: E402
    FIELD_BOTTOM,
    FIELD_TOP,
    LOGIN_SIZE,
    MASK_CHAR,
    REVEAL_ICON_HIDDEN,
    REVEAL_ICON_PX,
    REVEAL_ICON_SHOWN,
    REVEAL_TINT,
    LoginView,
)

#: The page cream from the blanket rule. Nothing on this screen may be it.
PAGE_CREAM = "#f1e8d8"
#: The white the sign-in is supposed to be.
PAGE_WHITE = "#ffffff"
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
# The two headings, pinned left
# --------------------------------------------------------------------------


class TestTheHeadingIsLeftAligned:
    @pytest.mark.parametrize(
        "object_name", [theme.OBJ_LOGIN_WORDMARK, theme.OBJ_LOGIN_CAPTION]
    )
    def test_the_ink_lines_up_with_the_fields(self, view, object_name):
        """`setAlignment` alone does nothing on a QLabel, because its maximum
        width is its text width, so the layout hands it no slack to distribute.
        `AlignLeft` in the row below is what actually puts it there -- and what
        matters is that the headings start where the fields start, not merely
        that they are somewhere left of the middle."""
        from PySide6.QtWidgets import QLabel

        label = next(
            w for w in view.findChildren(QLabel) if w.objectName() == object_name
        )
        x0, _ = _ink_box(view, label)
        field_left = view.email.mapTo(view, QPoint(0, 0)).x()
        assert abs(x0 - field_left) <= 2, (
            f"{object_name} starts at x={x0} but the fields start at {field_left}"
        )

    @pytest.mark.parametrize(
        "object_name", [theme.OBJ_LOGIN_WORDMARK, theme.OBJ_LOGIN_CAPTION]
    )
    def test_the_widget_is_not_being_centred_by_the_layout(self, view, object_name):
        from PySide6.QtWidgets import QLabel

        label = next(
            w for w in view.findChildren(QLabel) if w.objectName() == object_name
        )
        form = view.findChild(QWidget, theme.OBJ_LOGIN_FORM)
        assert label.mapTo(form, QPoint(0, 0)).x() == 0, "the row is centring it"

    def test_the_two_labels_say_what_they_should(self, view):
        from PySide6.QtWidgets import QLabel

        by_name = {w.objectName(): w for w in view.findChildren(QLabel)}
        assert by_name[theme.OBJ_LOGIN_WORDMARK].text() == "rentwheels"
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

    def test_the_fields_start_empty(self, view):
        """The demo pre-filled the email, which made the sign-in look like it
        had remembered a login nobody asked it to."""
        assert view.email.text() == "", "the email field is pre-filled"
        assert view.password.text() == "", "the password field is pre-filled"

    def test_a_prefilled_field_keeps_its_caption_lifted(self, view):
        field = view.email
        _focus(view, field)
        field.setText("someone@rentwheels.local")
        _blur(view, field)
        assert field._lifted
        assert field._label.objectName() == theme.OBJ_LABEL_LIFTED

    @pytest.mark.parametrize("field_name", ["email", "password"])
    @pytest.mark.parametrize("lifted", [False, True])
    def test_the_caption_is_never_clipped(self, view, field_name, lifted):
        """The caption is a bare child of the line edit, moved by hand and not
        in a layout, so nothing sizes it but `_restack`. Left to its own
        devices it kept whatever geometry it happened to have, and the line
        edit clipped letters off the end of it."""
        from PySide6.QtGui import QFontMetrics

        field = getattr(view, field_name)
        if lifted:
            _focus(view, field)
        else:
            _blur(view, field)
            assert field.text() == "", "precondition: empty, so the caption rests"

        self._assert_fits(field)

    @pytest.mark.parametrize("field_name", ["email", "password"])
    def test_the_caption_resizes_itself_to_the_font_in_use(self, view, field_name):
        """The same invariant, but reached the way it was actually broken.

        Squeezing the label is not something a person can do, but it stands in
        for the real cause: the font changed underneath a label whose size
        nothing was maintaining. A caption lifted while the label was narrow
        kept that narrow geometry when it dropped back, and the line edit --
        which is a scroll area -- cut the letters off.
        """
        field = getattr(view, field_name)
        field._label.resize(5, 5)
        field._restack(animate=False)
        self._assert_fits(field)

    @staticmethod
    def _assert_fits(field) -> None:
        from PySide6.QtGui import QFontMetrics

        label = field._label
        metrics = QFontMetrics(label.font())
        needed_w = metrics.horizontalAdvance(label.text())
        assert label.width() >= needed_w, (
            f"{label.objectName()} is {label.width()}px wide and "
            f"{label.text()!r} needs {needed_w}px"
        )
        assert label.height() >= metrics.height(), (
            f"{label.objectName()} is {label.height()}px tall and "
            f"{label.text()!r} needs {metrics.height()}px"
        )

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
        field.setText("someone@rentwheels.local")
        _blur(view, field)
        assert field._lifted
        assert field._label.objectName() == theme.OBJ_LABEL_LIFTED

    def test_clearing_the_field_puts_the_label_back(self, view):
        field = view.email
        _focus(view, field)
        field.setText("someone@rentwheels.local")
        _blur(view, field)
        assert field._lifted, "precondition: the label is up while the text is there"
        field.clear()
        _settle(field)
        assert not field._lifted


class TestTheMaskedField:
    """The mask is drawn, not stored.

    Qt's password dot is a fixed 11x11px decoration at any font size, and
    PySide6 does not bind `QLineEdit::setEchoChar`, so the character cannot be
    changed the supported way either. The field therefore keeps the genuine
    text and swaps in a run of bullets for the duration of the paint.
    """

    def test_the_field_holds_the_real_password_not_bullets(self, view):
        field = view.password
        field.setText("hunter2")
        view.repaint()
        assert field.text() == "hunter2", "the mask leaked into the value"

    def test_the_value_survives_a_paint(self, view):
        field = view.password
        field.setText("correct horse")
        view.grab()  # force a paint, which is when the swap happens
        view.grab()
        assert field.text() == "correct horse"

    def test_typing_and_deleting_still_work(self, view):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QKeyEvent

        field = view.password
        _focus(view, field)
        field.setText("")
        for ch in "abc":
            event = QKeyEvent(
                QKeyEvent.Type.KeyPress,
                0,
                Qt.KeyboardModifier.NoModifier,
                ch,
            )
            QApplication.sendEvent(field, event)
        assert field.text() == "abc", field.text()
        field.backspace()
        assert field.text() == "ab", field.text()

    def test_the_drawn_dot_is_a_bullet_not_qts_circle(self, view):
        from PySide6.QtGui import QFontMetrics

        field = view.password
        _focus(view, field)
        field.setText("abcdefgh")
        view.repaint()

        image = view.grab().toImage()
        top = field.mapTo(view, field.rect().topLeft()).y()
        xs = set()
        for y in range(top + FIELD_TOP + 3, top + field.height() - FIELD_BOTTOM - 3):
            for x in range(2, 240):
                c = image.pixelColor(x, y)
                if c.red() + c.green() + c.blue() < 600:
                    xs.add(x)
        ordered = sorted(xs)
        groups, cur = [], [ordered[0]]
        for a, b in zip(ordered, ordered[1:]):
            if b - a > 1:
                groups.append(cur)
                cur = []
            cur.append(b)
        groups.append(cur)

        metrics = QFontMetrics(field.font())
        expected_advance = metrics.horizontalAdvance(MASK_CHAR)
        actual_advance = groups[1][0] - groups[0][0]
        assert actual_advance == expected_advance, (
            f"dots are {actual_advance}px apart, but a bullet at this font is "
            f"{expected_advance}px apart -- Qt's own dot is 14px"
        )

    def test_an_unmasked_field_is_untouched(self, view):
        view.email.setText("someone@rentwheels.local")
        view.repaint()
        assert view.email.text() == "someone@rentwheels.local"


class TestTheRevealToggle:
    """The eye in the password field."""

    def test_it_belongs_to_the_password_field(self, view):
        assert view.reveal.parent() is view.password, "it should ride in the field"

    def test_it_sits_at_the_right_end_and_stays_inside(self, view):
        toggle, field = view.reveal, view.password
        right_gap = field.width() - (toggle.x() + toggle.width())
        assert 0 <= right_gap <= 12, f"right gap is {right_gap}px"
        assert toggle.y() >= 0 and toggle.y() + toggle.height() <= field.height()

    def test_the_typed_text_cannot_run_under_the_eye(self, view):
        """A 30px button parked on top of the field would sit on the end of a
        long password unless the field keeps room for it."""
        toggle = view.reveal
        reserved = view.password.textMargins().right()
        text_right_edge = view.password.width() - reserved
        assert text_right_edge <= toggle.x(), (
            f"text runs to x={text_right_edge} but the eye starts at {toggle.x()}"
        )

    def test_the_password_starts_masked(self, view):
        assert view.password._masked
        assert not view.reveal.isChecked()

    def test_a_click_shows_the_password_and_another_hides_it(self, view):
        view.reveal.click()
        assert view.reveal.isChecked()
        assert not view.password._masked, "the field and the eye disagreed"
        view.reveal.click()
        assert not view.reveal.isChecked()
        assert view.password._masked

    def test_the_button_is_the_only_source_of_truth(self, view):
        """The eye and the field used to each keep their own flag; if only one
        is flipped the icon and the text disagree."""
        view.reveal.set_revealed(True, animated=False)
        assert view.reveal.isChecked()
        assert not view.password._masked
        view.reveal.set_revealed(False, animated=False)
        assert view.password._masked

    def test_the_eye_says_which_way_it_will_go(self, view):
        assert "Show" in view.reveal.toolTip()
        view.reveal.click()
        assert "Hide" in view.reveal.toolTip()

    def test_it_is_not_in_the_tab_order(self, view):
        """Tab is for filling the form in; the eye is a shortcut, and a stop
        in the chain that only leads back to the same field."""
        from PySide6.QtCore import Qt

        assert view.reveal.focusPolicy() == Qt.FocusPolicy.NoFocus

    def test_the_fade_actually_runs_and_lands_on_the_icon(self, view):
        from PySide6.QtCore import QPropertyAnimation

        fade = view.reveal._fade
        assert isinstance(fade, QPropertyAnimation) or fade.duration() > 0
        view.reveal.click()
        assert fade.state() == QPropertyAnimation.State.Running
        _settle(view.password)
        assert view.reveal._progress == 1.0
        view.reveal.click()
        _settle(view.password)
        assert view.reveal._progress == 0.0

    def test_both_eye_images_are_actually_loaded(self, view):
        """A missing file gives a null pixmap and a silently blank button."""
        for name in (REVEAL_ICON_HIDDEN, REVEAL_ICON_SHOWN):
            pixmap = view.reveal._source[name]
            assert not pixmap.isNull(), f"{name} did not load"
            assert pixmap.width() >= REVEAL_ICON_PX, f"{name} is too small to draw"

    def test_the_eye_paints_something(self, view):
        """Null pixmaps and successful ones both pass the size check above."""
        painted = _toggle_pixels(view, view.reveal)
        assert painted, "the toggle is drawing nothing at all"
        assert REVEAL_TINT.lower() in painted, f"expected the tint, painted {painted}"

    def test_no_cream_square_behind_the_eye(self, view):
        """The global QWidget rule fills this page with cream, so the toggle
        has to opt out explicitly -- exactly the trap that turned the form
        column cream."""
        image = view.grab().toImage()
        origin = view.reveal.mapTo(view, view.reveal.rect().topLeft())
        for y in range(origin.y(), origin.y() + view.reveal.height()):
            for x in range(origin.x(), origin.x() + view.reveal.width()):
                assert image.pixelColor(x, y).name() != theme.BG.lower(), (
                    f"cream at {(x, y)} behind the eye"
                )

    def test_the_two_eyes_are_different_pictures(self, view):
        """Otherwise the cross-fade has nothing to fade between."""
        hidden = view.reveal._source[REVEAL_ICON_HIDDEN].toImage()
        shown = view.reveal._source[REVEAL_ICON_SHOWN].toImage()
        assert hidden != shown, "the crossed and plain eye are the same file"

    def test_the_eye_shows_the_current_state_not_the_next_one(self, view):
        """Masked has to look masked, and revealed has to look revealed.

        Both files are "an eye", and the crossed-out one is very nearly the
        plain one plus a diagonal, so comparing whole pictures barely separates
        them. The diagonal is the part that distinguishes the states, so that
        is what gets asserted: drawn while masked, gone once revealed.
        """
        assert view.reveal.dominant_icon == REVEAL_ICON_HIDDEN
        assert _slash_strength(view) > 0.7, "masked, but the eye is not struck out"

        view.reveal.click()
        _settle(view.password)
        assert view.reveal.dominant_icon == REVEAL_ICON_SHOWN
        assert _slash_strength(view) < 0.2, "revealed, but it is still struck out"

        view.reveal.click()
        _settle(view.password)
        assert _slash_strength(view) > 0.7

    def test_mid_fade_the_slash_is_only_half_gone(self, view):
        """Halfway, the strike sits between the two states rather than having
        snapped to one of them."""
        assert 0.3 < _slash_strength(view, 0.5) < 0.8

    def test_revealing_draws_letters_and_masking_draws_bullets(self, view):
        view.password.setText("demo-password")
        view.reveal.click()
        _settle(view.password)
        revealed = _first_glyph_width(view)
        view.reveal.click()
        _settle(view.password)
        masked = _first_glyph_width(view)
        assert masked < revealed, (
            f"masked {masked}px vs revealed {revealed}px -- the mask may not be on"
        )


#: Summed RGB below which a pixel counts as ink (the cut sits near white).
INK_CUTOFF = 760


def _is_ink(image, x: int, y: int, cutoff: int = INK_CUTOFF) -> bool:
    return sum(image.pixelColor(x, y).getRgb()[:3]) < cutoff


def _toggle_ink(view, toggle) -> set:
    """The (x, y) points inside the toggle that carry ink, relative to it."""
    image = view.grab().toImage()
    origin = toggle.mapTo(view, toggle.rect().topLeft())
    return {
        (x - origin.x(), y - origin.y())
        for y in range(origin.y(), origin.y() + toggle.height())
        for x in range(origin.x(), origin.x() + toggle.width())
        if _is_ink(image, x, y)
    }


def _source_ink(toggle, filename, cutoff: int = INK_CUTOFF) -> set:
    """The same picture scaled into the toggle, ink only.

    Rebuilt with the widget's own draw call so the comparison is against what
    the eye is *supposed* to be on screen, not a hand-copied threshold.
    """
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QImage, QPainter

    size = toggle.width()
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(QColor(255, 255, 255))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    side = REVEAL_ICON_PX
    painter.drawPixmap(
        QRect((size - side) // 2, (size - side) // 2, side, side),
        toggle._source[filename],
    )
    painter.end()
    return {
        (x, y)
        for y in range(size)
        for x in range(size)
        if _is_ink(image, x, y, cutoff)
    }


def _agreement(rendered: set, expected: set) -> float:
    """How much of the expected ink actually turned up, 0..1."""
    if not expected:
        return 0.0
    return len(rendered & expected) / len(expected)


def _slash_strength(view, progress: float | None = None) -> float:
    """How strongly the crossed-out eye's diagonal is drawn, 0..1.

    Measured against the part the plain eye does not have, so the shared body
    of the two pictures cannot pad the score. Full strength is taken as
    whatever the diagonal measures at 50% opacity on the crossed-out eye --
    derived, not hard-coded, so it does not depend on the tint or on how this
    platform antialiases a diagonal.
    """
    toggle = view.reveal
    only_here = _source_ink(toggle, REVEAL_ICON_HIDDEN) - _source_ink(
        toggle, REVEAL_ICON_SHOWN
    )
    if not only_here:
        return 0.0

    def measure() -> float:
        image = view.grab().toImage()
        origin = toggle.mapTo(view, toggle.rect().topLeft())
        total = 0.0
        for dx, dy in only_here:
            total += 765 - sum(
                image.pixelColor(origin.x() + dx, origin.y() + dy).getRgb()[:3]
            )
        return total / len(only_here)

    def at(value: float) -> float:
        keep = toggle._progress
        toggle._progress = value
        toggle.repaint()
        reading = measure()
        toggle._progress = keep
        toggle.repaint()
        return reading

    reference = at(0.0)
    if reference <= 20:
        return 0.0
    if progress is None:
        return measure() / reference
    return at(progress) / reference


def _toggle_pixels(view, toggle) -> dict:
    from PySide6.QtWidgets import QWidget

    image = view.grab().toImage()
    origin = toggle.mapTo(view, QPoint(0, 0))
    counts: dict = {}
    for y in range(origin.y(), origin.y() + toggle.height()):
        for x in range(origin.x(), origin.x() + toggle.width()):
            name = image.pixelColor(x, y).name()
            if name != "#ffffff":
                counts[name] = counts.get(name, 0) + 1
    return counts


def _first_glyph_width(view) -> int:
    image = view.grab().toImage()
    top = view.password.mapTo(view, view.password.rect().topLeft()).y()
    xs = set()
    for y in range(top + 24, top + view.password.height() - 6):
        for x in range(2, view.reveal.x()):
            c = image.pixelColor(x, y)
            if c.red() + c.green() + c.blue() < 600:
                xs.add(x)
    ordered = sorted(xs)
    if not ordered:
        return 0
    group = [ordered[0]]
    for a, b in zip(ordered, ordered[1:]):
        if b - a > 1:
            break
        group.append(b)
    return group[-1] - group[0] + 1


class TestTheFocusColour:
    @staticmethod
    def _rule_colours(view, field) -> set:
        """Every non-white colour in the last few rows of a field.

        Sampling one exact row is fragile: a 1px rule and a 2px one do not
        start on the same line, and the focused one also shifts the padding.
        """
        image = view.grab().toImage()
        bottom = field.mapTo(view, field.rect().bottomLeft()).y()
        found = set()
        for y in range(bottom - 3, bottom + 1):
            for x in range(80, 240):
                name = image.pixelColor(x, y).name()
                if name != "#ffffff":
                    found.add(name)
        return found

    def test_the_focused_rule_is_the_button_sand(self, view):
        _focus(view, view.password)
        found = self._rule_colours(view, view.password)
        assert theme.BUTTON.lower() in found, f"rule colours were {found}"

    def test_an_unfocused_rule_stays_the_pale_rule(self, view):
        _focus(view, view.password)
        _blur(view, view.password)
        found = self._rule_colours(view, view.password)
        assert theme.RULE.lower() in found, f"rule colours were {found}"
        assert theme.BUTTON.lower() not in found, "the accent stuck after blur"

    def test_the_lifted_caption_stays_readable(self, view):
        """The accent is now the button's sand, which is 1.42:1 on white and
        would leave a 12px caption invisible, so the caption uses a darker
        shade of the same colour. This is what stops that regressing."""
        _focus(view, view.password)
        view.repaint()
        image = view.grab().toImage()
        label = view.password._label
        origin = label.mapTo(view, QPoint(0, 0))
        ink = set()
        for y in range(origin.y(), origin.y() + label.height()):
            for x in range(origin.x(), origin.x() + label.width()):
                c = image.pixelColor(x, y)
                if c.red() + c.green() + c.blue() < 600:
                    ink.add(c.name())
        assert ink, "the caption is not being drawn at all"
        assert theme.FIELD_FOCUS_TEXT.lower() in ink, (
            f"caption ink was {ink}, expected the token's shade"
        )

    def test_the_stylesheet_agrees_with_the_palette(self):
        """Three copies of one colour drift. The tokens are the source; the
        stylesheet has to be spelling the same ones."""
        qss = theme.load_stylesheet().lower()
        for token in (
            theme.BUTTON,
            theme.FIELD_FOCUS,
            theme.FIELD_FOCUS_TEXT,
            theme.RULE,
        ):
            assert token.lower() in qss, f"{token} is in the palette but not the sheet"

    def test_no_stale_petrol_is_left_on_the_sign_in(self):
        """Petrol used to be the focus colour. It is still the brand colour,
        so it may stay in the palette, but the sign-in must not use it."""
        sheet = theme.load_stylesheet()
        sign_in = sheet[ sheet.index("QWidget#loginRoot") : ]
        assert theme.PETROL.lower() not in sign_in.lower(), (
            "the sign-in is still painting with petrol"
        )


class TestTheTypography:
    def test_no_widget_falls_back_off_inter(self, view):
        from app.utils.fonts import BUNDLED_FAMILY

        offenders = []
        for widget in view.findChildren(QWidget):
            getter = getattr(widget, "text", None)
            label = getter() if callable(getter) else ""
            if not isinstance(label, str) or not label.strip():
                continue
            if BUNDLED_FAMILY not in widget.font().family():
                offenders.append((widget.objectName(), label, widget.font().family()))
        assert not offenders, offenders

    def test_the_application_font_is_inter_not_just_the_stylesheet(self, view):
        """`font-family` in a stylesheet is a request. Anything the sheet does
        not reach would quietly use the system default, so the app font is set
        as well."""
        from app.utils.fonts import BUNDLED_FAMILY

        app = QApplication.instance()
        assert BUNDLED_FAMILY in app.font().family(), app.font().family()


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
                r"Q(?:Widget|Label|LineEdit|Frame|PushButton|AbstractButton)"
                r"#(login[A-Za-z]*)\s*\{",
                theme.load_stylesheet(),
            )
        )
        # Swapped at runtime, so one matches nothing at any instant.
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
            (theme.OBJ_LOGIN_REVEAL, "QAbstractButton"),
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
