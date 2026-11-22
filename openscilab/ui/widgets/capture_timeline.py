# Copyright (C) 2026 Julian Decker
#
# Part of openSciLab.
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""The capture of the setup on a time line: the samples before the trigger, the trigger and
the samples after it (per burst), with their counts and durations.

The trigger is dragged within the capture (its length stays), the end of the capture is dragged
or the wheel turned for its length (the trigger keeps its place in it). The line only asks for
counts; the setup keeps them to what the device captures.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from ...core.formatting import to_small_time, to_thousands
from ..theme import qcolor

#: Bursts drawn one by one; more are drawn as these many with their count
MAX_DRAWN_BURSTS = 8
BAR_HEIGHT = 14
#: Pixels around the trigger and the end that grab them
GRAB = 7
#: Dragging the end this far doubles (or halves) the capture
PIXELS_PER_DOUBLING = 60
#: Lengths a drag of the end snaps to (× 10ⁿ), and the steps of the wheel and the keys
DRAG_SERIES = (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8)
STEP_SERIES = (1, 2, 5)


def _series(value: float, series) -> list[float]:
    exponent = math.floor(math.log10(max(value, 1)))
    return sorted(m * 10 ** e for e in range(exponent - 1, exponent + 2) for m in series)


def nice_count(value: float, series=DRAG_SERIES) -> int:
    """The number of ``series`` × 10ⁿ nearest to ``value`` (at least 1)."""
    if value <= 1:
        return 1
    return max(int(round(min(_series(value, series), key=lambda c: abs(math.log(c / value))))), 1)


def step_count(value: int, steps: int, series=STEP_SERIES) -> int:
    """``value`` moved ``steps`` numbers of ``series`` × 10ⁿ up (or down, at least 1)."""
    for _ in range(abs(steps)):
        candidates = _series(value, series)
        if steps > 0:
            value = next(c for c in candidates if c > value)
        else:
            value = next((c for c in reversed(candidates) if c < value), 1)
    return max(int(round(value)), 1)


@dataclass(frozen=True)
class CapturePlan:
    pre: int = 0
    post: int = 0
    #: Samples per second (unused for ``state``)
    rate: float = 0.0
    bursts: int = 1
    #: What starts the capture: ``"trigger"``, ``"sequence"`` or ``"start"`` (no trigger)
    start: str = "trigger"
    #: The stream runs until stopped and keeps the last ``post`` samples
    continuous: bool = False
    #: A sample per edge of an external clock: no durations
    state: bool = False
    #: The device keeps samples before the trigger: it may be dragged
    movable: bool = False
    #: The length may be changed
    resizable: bool = False

    @property
    def total(self) -> int:
        return self.pre + self.post * max(self.bursts, 1)


class CaptureTimeline(QWidget):
    """Pre-trigger samples, the trigger and the post-trigger samples, in proportion."""

    #: Samples before the trigger and in total the user asks for
    requested = Signal(int, int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.plan = CapturePlan()
        #: ``"trigger"`` or ``"end"`` while dragged, with the x, total and share before the trigger at the press
        self._drag: Optional[tuple[str, float, int, float]] = None
        self._wheel = 0
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("Capture timeline")
        self.setMinimumWidth(320)

    def sizeHint(self):  # noqa: D401 - Qt override
        hint = super().sizeHint()
        line = QFontMetricsF(self.font()).height()
        hint.setHeight(int(line * 2 + BAR_HEIGHT + 12))
        return hint

    def minimumSizeHint(self):  # noqa: D401 - Qt override
        return self.sizeHint()

    def set_plan(self, plan: CapturePlan) -> None:
        if plan == self.plan:
            return
        self.plan = plan
        self.setAccessibleDescription(self.description())
        hints = (["drag the trigger to move it in the capture"] if plan.movable else []) + (
            ["drag the end or turn the wheel for the length"] if plan.resizable else [])
        self.setToolTip(self.description() + (f"\n({'; '.join(hints)})" if hints else ""))
        self.update()

    # ------------------------------------------------------------------ text
    def _amount(self, samples: int) -> str:
        unit = "state" if self.plan.state else "sample"
        text = f"{to_thousands(samples)} {unit}{'s' if samples != 1 else ''}"
        if not self.plan.state and self.plan.rate > 0:
            text += f" · {to_small_time(samples / self.plan.rate)}"
        return text

    def start_name(self) -> str:
        return {"start": "Start", "sequence": "Trigger (sequence)"}.get(self.plan.start, "Trigger")

    def end_text(self, samples: int) -> str:
        """The distance of an end of the capture from the start (``-`` before it)."""
        sign = "−" if samples < 0 else "+"
        if self.plan.state or self.plan.rate <= 0:
            return f"{sign}{to_thousands(abs(samples))}"
        return f"{sign}{to_small_time(abs(samples) / self.plan.rate)}"

    def pre_text(self) -> str:
        return f"before: {self._amount(self.plan.pre)}"

    def post_text(self) -> str:
        plan = self.plan
        if plan.continuous:
            return f"until stopped, keeps the last {self._amount(plan.post)}"
        if plan.bursts > 1:
            return f"{plan.bursts} bursts × {self._amount(plan.post)}"
        return f"after: {self._amount(plan.post)}"

    def description(self) -> str:
        parts = [self.pre_text()] if self.plan.pre else []
        parts += [self.start_name(), self.post_text()]
        return ", ".join(parts)

    # ------------------------------------------------------------ geometry
    def _bar(self) -> QRectF:
        line = QFontMetricsF(self.font()).height()
        return QRectF(GRAB, line + 4, self.width() - 2 * GRAB, BAR_HEIGHT)

    def _start_x(self, bar: QRectF) -> float:
        total = self.plan.total
        return bar.left() + (bar.width() * self.plan.pre / total if total else 0.0)

    def _part_at(self, point: QPointF) -> Optional[str]:
        bar = self._bar()
        if point.y() > bar.bottom() + GRAB:
            return None
        if self.plan.movable and abs(point.x() - self._start_x(bar)) <= GRAB:
            return "trigger"
        if self.plan.resizable and abs(point.x() - bar.right()) <= GRAB:
            return "end"
        if self.plan.movable and bar.left() <= point.x() <= bar.right() and point.y() >= bar.top() - GRAB:
            return "bar"
        return None

    # --------------------------------------------------------------- input
    def _request(self, pre_share: float, total: int) -> None:
        total = max(int(total), 1)
        pre = int(round(min(max(pre_share, 0.0), 1.0) * total)) if self.plan.movable else self.plan.pre
        self.requested.emit(pre, total)

    def _share(self) -> float:
        return self.plan.pre / self.plan.total if self.plan.total else 0.0

    def _resize(self, total: int) -> None:
        """A new length; the trigger keeps its place in it."""
        if self.plan.resizable:
            self._request(self._share(), total)

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        part = self._part_at(event.position())
        if event.button() != Qt.LeftButton or part is None:
            return super().mousePressEvent(event)
        self._drag = ("end" if part == "end" else "trigger", event.position().x(), self.plan.total, self._share())
        if part == "bar":  # a click in the line puts the trigger there
            self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt naming
        x = event.position().x()
        if self._drag is None:
            self.setCursor(Qt.SizeHorCursor if self._part_at(event.position()) in ("trigger", "end") else
                           Qt.PointingHandCursor if self._part_at(event.position()) == "bar" else Qt.ArrowCursor)
            return
        kind, x0, total, share = self._drag
        bar = self._bar()
        if kind == "trigger":
            self._request((x - bar.left()) / max(bar.width(), 1.0), total)
        else:
            self._request(share, nice_count(total * 2 ** ((x - x0) / PIXELS_PER_DOUBLING)))

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._drag = None
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if not self.plan.resizable:
            return super().wheelEvent(event)
        event.accept()
        # a step per notch of a wheel; a touchpad sends many small turns
        self._wheel += event.angleDelta().y()
        steps = int(self._wheel / 120)
        if steps:
            self._wheel -= steps * 120
            self._resize(step_count(self.plan.total, steps))

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        key = event.key()
        if key in (Qt.Key_Left, Qt.Key_Right) and self.plan.movable:
            step = (0.1 if event.modifiers() & Qt.ShiftModifier else 0.01) * (1 if key == Qt.Key_Right else -1)
            self._request(self._share() + step, self.plan.total)
        elif key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Plus, Qt.Key_Minus) and self.plan.resizable:
            self._resize(step_count(self.plan.total, 1 if key in (Qt.Key_Up, Qt.Key_Plus) else -1))
        else:
            super().keyPressEvent(event)

    # ----------------------------------------------------------------- paint
    def paintEvent(self, _event) -> None:  # noqa: N802 - Qt naming
        plan = self.plan
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        metrics = QFontMetricsF(self.font())
        line = metrics.height()
        accent, trigger, muted, text = (qcolor("ACCENT"), qcolor("display.line.trigger"),
                                        qcolor("TEXT_MUTED"), qcolor("TEXT"))
        bar = self._bar()
        left, right = bar.left(), bar.right()
        start_x = self._start_x(bar)

        pre_fill = QColor(accent)
        pre_fill.setAlphaF(0.35)
        if plan.pre > 0:
            painter.fillRect(QRectF(bar.left(), bar.top(), start_x - bar.left(), bar.height()), pre_fill)
        drawn = min(max(plan.bursts, 1), MAX_DRAWN_BURSTS)
        if plan.post > 0 and start_x < bar.right():
            width = (bar.right() - start_x) / drawn
            for burst in range(drawn):
                x = start_x + burst * width
                gap = 2.0 if burst else 0.0
                painter.fillRect(QRectF(x + gap, bar.top(), width - gap, bar.height()), accent)
                if burst:  # the trigger of every further burst
                    painter.setPen(QPen(trigger, 1.5))
                    painter.drawLine(QPointF(x + 1, bar.top() - 3), QPointF(x + 1, bar.bottom() + 3))
            if plan.continuous:  # it goes on: an arrow at the end
                path = QPainterPath(QPointF(bar.right() - 8, bar.top() - 3))
                path.lineTo(bar.right() + GRAB - 1, bar.center().y())
                path.lineTo(bar.right() - 8, bar.bottom() + 3)
                path.closeSubpath()
                painter.fillPath(path, accent)
        painter.setPen(QPen(accent if self.hasFocus() else qcolor("BORDER"), 1))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(bar)
        if plan.resizable and not plan.continuous:  # the grip of the end
            painter.setPen(Qt.NoPen)
            painter.setBrush(text)
            painter.drawRoundedRect(QRectF(bar.right() - 2.5, bar.top() - 3, 5, bar.height() + 6), 2, 2)

        # the start: a line through the bar with a mark on top
        painter.setPen(QPen(trigger, 2))
        painter.drawLine(QPointF(start_x, bar.top() - 4), QPointF(start_x, bar.bottom() + 4))
        mark = QPainterPath(QPointF(start_x - 5, bar.top() - 9))
        mark.lineTo(start_x + 5, bar.top() - 9)
        mark.lineTo(start_x, bar.top() - 3)
        mark.closeSubpath()
        painter.fillPath(mark, trigger)

        # the start named next to its mark (left of it when there is no room), the ends with their times
        name = self.start_name()
        name_width = metrics.horizontalAdvance(name)
        name_x = start_x + 8 if start_x + 8 + name_width <= right else start_x - 8 - name_width
        painter.setPen(trigger)
        painter.drawText(QRectF(name_x, 0, name_width + 1, line), Qt.AlignLeft | Qt.AlignVCenter, name)
        painter.setPen(muted)
        before = self.end_text(-plan.pre)
        if plan.pre > 0 and left + metrics.horizontalAdvance(before) < min(start_x, name_x) - 8:
            painter.drawText(QRectF(left, 0, right - left, line), Qt.AlignLeft | Qt.AlignVCenter, before)
        end = self.end_text(plan.post * max(plan.bursts, 1))
        if plan.post > 0 and not plan.continuous and max(start_x, name_x + name_width) + 12 + metrics.horizontalAdvance(end) < right:
            painter.drawText(QRectF(left, 0, right - left, line), Qt.AlignRight | Qt.AlignVCenter, end)

        # counts and durations next to the trigger, pushed apart into the line where there is no room
        top = bar.bottom() + 4
        pre_text = self.pre_text() if plan.pre > 0 else ""
        post_text = self.post_text()
        pre_width = metrics.horizontalAdvance(pre_text) if pre_text else -12.0
        post_width = metrics.horizontalAdvance(post_text)
        pre_x = max(start_x - 6 - pre_width, left)
        post_x = max(start_x + 6, pre_x + pre_width + 12)
        if post_x + post_width > right:
            post_x = max(right - post_width, left)
            pre_x = max(min(pre_x, post_x - 12 - pre_width), left)
        if pre_text:
            room = max(post_x - 12 - pre_x, 0)
            painter.setPen(muted)
            painter.drawText(QRectF(pre_x, top, room, line), Qt.AlignLeft | Qt.AlignVCenter,
                             metrics.elidedText(pre_text, Qt.ElideRight, room))
        painter.setPen(text)
        painter.drawText(QRectF(post_x, top, right - post_x, line), Qt.AlignLeft | Qt.AlignVCenter,
                         metrics.elidedText(post_text, Qt.ElideRight, right - post_x))
        painter.end()
