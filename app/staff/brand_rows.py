from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from app.staff.metrics import (
    VEHICLE_CARD_GUTTER,
    VEHICLE_CARD_H,
    VEHICLE_CARD_MIN_W,
)
from app.staff.vehicle_cards import RentalVehicle, VehicleSlot

#: Card width inside a rail: narrow enough that a few cards already overflow.
RAIL_CARD_W = VEHICLE_CARD_MIN_W

#: Reserved under the cards for the horizontal scrollbar, so it never clips one.
RAIL_SCROLLBAR_H = 16


class VehicleRail(QScrollArea):
    def __init__(
        self, pool, vehicles: list[RentalVehicle], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("vehicleRail")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setFixedHeight(VEHICLE_CARD_H + RAIL_SCROLLBAR_H)
        self.viewport().setObjectName("vehicleRailViewport")

        host = QWidget()
        host.setObjectName("vehicleRailHost")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(VEHICLE_CARD_GUTTER)

        self._slots: list[VehicleSlot] = []
        for vehicle in vehicles:
            slot = VehicleSlot(vehicle)
            slot.setFixedWidth(RAIL_CARD_W)
            slot.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            row.addWidget(slot)
            self._slots.append(slot)
        row.addStretch(1)

        self._pool = pool
        self._built = False
        self._build_queue: list[int] = list(range(len(self._slots)))
        self._building = False
        self.setWidget(host)

        self._alive = True
        self.destroyed.connect(self._stop_building)
        QTimer.singleShot(0, self._process_queue)

    def _stop_building(self) -> None:
        self._alive = False
        self._build_queue = []

    @property
    def built(self) -> bool:
        return self._built

    @property
    def slots(self) -> list[VehicleSlot]:
        return list(self._slots)

    def set_photo_urls(self, urls: dict[int, str]) -> None:
        for slot in self._slots:
            slot.set_photo_url(urls.get(slot.vehicle.vehicle_id))

    def build_now(self) -> None:
        self._build_queue = []
        self._built = True
        self._building = False
        for slot in self._slots:
            if not slot.built:
                slot.build(self._pool)

    def _process_queue(self) -> None:
        if not getattr(self, "_alive", False):
            return
        if self._building:
            return
        if not self._build_queue:
            self._built = True
            return
        self._building = True
        idx = self._build_queue.pop(0)
        if 0 <= idx < len(self._slots) and not self._slots[idx].built:
            self._slots[idx].build(self._pool)
        self._building = False
        QTimer.singleShot(0, self._process_queue)


class BrandSection(QWidget):
    def __init__(
        self,
        brand: str,
        vehicles: list[RentalVehicle],
        pool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("brandSection")
        self.brand = brand
        self.vehicles = list(vehicles)

        head = QHBoxLayout()
        head.setContentsMargins(0, 0, 0, 0)
        head.setSpacing(8)

        self.heading = QLabel(brand, self)
        self.heading.setObjectName("brandHeading")
        head.addWidget(self.heading)

        # The per-brand count used to sit here, so the heading read "Toyota 24".
        # Removed on request. The one count that remains is in the top bar, where
        # it describes the whole filtered fleet rather than one rail at a time.
        head.addStretch(1)

        self.rail = VehicleRail(pool, self.vehicles, self)

        body = QVBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(10)
        body.addLayout(head)
        body.addWidget(self.rail)

    @property
    def slots(self) -> list[VehicleSlot]:
        return self.rail.slots

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self.sizeHint()

    def set_photo_urls(self, urls: dict[int, str]) -> None:
        self.rail.set_photo_urls(urls)
