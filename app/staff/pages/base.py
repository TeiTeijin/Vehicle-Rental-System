"""Base class shared by every staff page.

A page is a title, a description, and a body. The body is filled by the
subclass, and :meth:`refresh` is the single reload path -- which matters
because the shell's 30-second timer calls it, and a page with a second,
slightly different reload path will show stale data after a refresh and
nobody will know why.

The role check happens in :meth:`refresh` rather than only at construction, so
a page that outlives a sign-out cannot render for the wrong user. Combined
with the shell clearing its page registry on sign-out, that is belt and
braces on purpose: the cost of getting it wrong is showing one member of
staff another's view of the branch.
"""

from __future__ import annotations

from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from app.staff.context import StaffContext
from app.staff.feedback import describe
from app.staff.widgets import toast


class StaffPage(QWidget):
    """Title, subtitle, and a body area that subclasses fill in."""

    #: Overridden by subclasses that restrict themselves to admins.
    admin_only = False

    def __init__(self, shell, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("page")
        self.shell = shell
        self.context: StaffContext = shell.context

        self.title_label = QLabel(title, self)
        self.title_label.setObjectName("pageTitle")

        self.subtitle_label = QLabel(subtitle, self)
        self.subtitle_label.setObjectName("pageSubtitle")
        self.subtitle_label.setVisible(bool(subtitle))

        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(14)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 22, 26, 22)
        layout.setSpacing(6)
        layout.addWidget(self.title_label)
        layout.addWidget(self.subtitle_label)
        layout.addSpacing(10)
        layout.addLayout(self.body)
        layout.addStretch(1)

        self._loaded = False

    # -- reload -----------------------------------------------------------

    def refresh(self) -> None:
        """Re-read this page's data.

        The base implementation enforces the role gate and nothing else;
        subclasses call ``super().refresh()`` and then do their loading.
        """
        if self.admin_only:
            self.context.require_admin()
        else:
            self.context.require_staff()
        self._loaded = True

    def reload(self) -> None:
        """Refresh, and if it fails, say why without a dialog.

        Used by the background timer and by the page's own Refresh button.
        A page-level failure is almost always one table failing, and that table
        shows its own error with its own retry -- a modal on top of that would
        be two reports of one problem.
        """
        try:
            self.refresh()
        except Exception as exc:  # noqa: BLE001
            message, _field, _kind = describe(exc)
            toast(self.window(), f"{self.title_label.text()}: {message}", "error")
