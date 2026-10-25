"""Branches: where a rental is picked up and dropped off, and when they open.

Times are local wall-clock times on one day -- no overnight counters -- so the
slot list a branch offers is simply ``open_time .. close_time``.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Branch
from app.services.errors import ValidationError

#: Granularity of the pickup/return time combos.
SLOT_MINUTES = 30

#: Last slot of the day must still leave time to hand the keys over.
LAST_SLOT_GRACE_MINUTES = 0


def active_branches(session: Session) -> list[Branch]:
    return list(
        session.execute(
            select(Branch)
            .where(Branch.is_active.is_(True))
            .order_by(Branch.name)
        ).scalars()
    )


def by_id(session: Session, branch_id: int | None) -> Branch | None:
    if branch_id is None:
        return None
    return session.get(Branch, branch_id)


def time_slots(branch: Branch, step_minutes: int = SLOT_MINUTES) -> list[time]:
    """Every clock time the branch can hand a vehicle over or take one back."""
    step = timedelta(minutes=step_minutes)
    moment = datetime.combine(date(2000, 1, 1), branch.open_time)
    last = datetime.combine(date(2000, 1, 1), branch.close_time)
    slots: list[time] = []
    while moment <= last:
        slots.append(moment.time())
        moment += step
    return slots


def within_hours(branch: Branch, moment: datetime) -> bool:
    """True when `moment`'s clock time sits inside the branch's day."""
    clock = moment.time()
    return branch.open_time <= clock <= branch.close_time


def validate_window(
    pickup_dt: datetime,
    return_dt: datetime,
    pickup_branch: Branch,
    return_branch: Branch | None = None,
) -> None:
    """Raise the first thing wrong with a requested window, in field order.

    `return_branch` defaults to the pickup branch (the "Same as pickup" answer).
    """
    drop_branch = return_branch or pickup_branch
    if pickup_branch is None or drop_branch is None:
        raise ValidationError(
            "Choose the branch you will pick up from.", field="pickup_branch"
        )
    if return_dt <= pickup_dt:
        raise ValidationError(
            "The return date and time must be after the pickup.",
            field="return_dt",
        )
    if not within_hours(pickup_branch, pickup_dt):
        raise ValidationError(
            f"{pickup_branch.name} is open "
            f"{pickup_branch.hours_label}: pick a pickup time inside that.",
            field="pickup_time",
        )
    if not within_hours(drop_branch, return_dt):
        raise ValidationError(
            f"{drop_branch.name} is open "
            f"{drop_branch.hours_label}: pick a return time inside that.",
            field="return_time",
        )
