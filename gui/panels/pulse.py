"""Arc-reactor style HUD centerpiece that reacts to JARVIS's listening/speaking state.

Rendered entirely with QPainter (no image assets): concentric rotating rings with
tick-mark dials, a pulsing glow core, and — while speaking — animated radial spokes.
Inspired by the Iron Man arc-reactor / HUD look.
"""

from __future__ import annotations

import math

from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import QColor, QConicalGradient, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import QWidget

CYAN = QColor("#00d9ff")
AMBER = QColor("#ff9d2f")

# per-state animation speed and glow intensity
STATE_PARAMS = {
    "idle": {"speed": 0.010, "glow": 0.35, "spin": 0.3},
    "listening": {"speed": 0.030, "glow": 0.75, "spin": 1.0},
    "speaking": {"speed": 0.055, "glow": 1.0, "spin": 1.6},
}


def _rgba(color: QColor, alpha: int) -> QColor:
    return QColor(color.red(), color.green(), color.blue(), max(0, min(255, alpha)))


class PulseWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._phase = 0.0
        self._spin = 0.0
        self._state = "idle"
        self.setMinimumHeight(220)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)  # ~30 fps

    def set_state(self, state: str) -> None:
        if state in STATE_PARAMS:
            self._state = state

    def _tick(self) -> None:
        params = STATE_PARAMS[self._state]
        self._phase += params["speed"]
        self._spin += params["spin"]
        self.update()

    # ------------------------------------------------------------------ paint

    def paintEvent(self, event) -> None:  # noqa: N802 (Qt override)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        cx, cy = w / 2, h / 2
        radius = min(w, h) * 0.42
        params = STATE_PARAMS[self._state]
        glow = params["glow"]

        self._draw_glow_core(painter, cx, cy, radius, glow)
        self._draw_tick_ring(painter, cx, cy, radius, self._spin, count=60, length=0.08, color=CYAN)
        self._draw_tick_ring(painter, cx, cy, radius * 0.78, -self._spin * 0.6, count=36, length=0.06, color=AMBER)
        self._draw_rotating_arcs(painter, cx, cy, radius * 0.62, self._spin)
        self._draw_inner_core(painter, cx, cy, radius * 0.34, glow)

        if self._state == "speaking":
            self._draw_spokes(painter, cx, cy, radius * 0.34)

        painter.end()

    def _draw_glow_core(self, painter: QPainter, cx: float, cy: float, radius: float, glow: float) -> None:
        gradient = QRadialGradient(cx, cy, radius * 1.4)
        pulse = 0.5 + 0.5 * math.sin(self._phase * 3)
        gradient.setColorAt(0.0, _rgba(CYAN, int(120 * glow * (0.6 + 0.4 * pulse))))
        gradient.setColorAt(0.5, _rgba(CYAN, int(40 * glow)))
        gradient.setColorAt(1.0, _rgba(CYAN, 0))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), radius * 1.4, radius * 1.4)

    def _draw_tick_ring(
        self, painter: QPainter, cx: float, cy: float, radius: float, angle: float, count: int, length: float, color: QColor
    ) -> None:
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(angle)
        pen = QPen(_rgba(color, 200))
        pen.setWidthF(1.6)
        painter.setPen(pen)
        for i in range(count):
            theta = (2 * math.pi / count) * i
            # every 5th tick is longer/brighter, like a gauge
            major = i % 5 == 0
            r_in = radius * (1 - length * (2.0 if major else 1.0))
            pen.setColor(_rgba(color, 230 if major else 120))
            pen.setWidthF(2.2 if major else 1.4)
            painter.setPen(pen)
            painter.drawLine(
                QPointF(r_in * math.cos(theta), r_in * math.sin(theta)),
                QPointF(radius * math.cos(theta), radius * math.sin(theta)),
            )
        painter.restore()

    def _draw_rotating_arcs(self, painter: QPainter, cx: float, cy: float, radius: float, angle: float) -> None:
        painter.save()
        painter.translate(cx, cy)
        painter.rotate(angle * 0.8)
        pen = QPen(_rgba(CYAN, 180))
        pen.setWidthF(3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect_r = radius
        # three broken arc segments spaced around the circle
        for start_deg in (0, 120, 240):
            painter.drawArc(
                int(-rect_r), int(-rect_r), int(rect_r * 2), int(rect_r * 2),
                start_deg * 16, 70 * 16,
            )
        painter.restore()

    def _draw_inner_core(self, painter: QPainter, cx: float, cy: float, radius: float, glow: float) -> None:
        pulse = 0.5 + 0.5 * math.sin(self._phase * 3)
        core_r = radius * (0.85 + 0.15 * pulse)

        gradient = QRadialGradient(cx, cy, core_r)
        gradient.setColorAt(0.0, _rgba(QColor("#eaffff"), int(230 * glow)))
        gradient.setColorAt(0.4, _rgba(CYAN, int(200 * glow)))
        gradient.setColorAt(1.0, _rgba(CYAN, int(30 * glow)))
        painter.setBrush(gradient)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QPointF(cx, cy), core_r, core_r)

        # crisp outer rim
        pen = QPen(_rgba(CYAN, 220))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QPointF(cx, cy), radius, radius)

    def _draw_spokes(self, painter: QPainter, cx: float, cy: float, radius: float) -> None:
        painter.save()
        painter.translate(cx, cy)
        pen = QPen(_rgba(QColor("#eaffff"), 230))
        pen.setWidthF(3.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        spokes = 12
        for i in range(spokes):
            theta = (2 * math.pi / spokes) * i
            amp = 0.4 + 0.6 * abs(math.sin(self._phase * 4 + i * 0.7))
            r_out = radius * (1.0 + 0.5 * amp)
            painter.drawLine(
                QPointF(radius * 0.6 * math.cos(theta), radius * 0.6 * math.sin(theta)),
                QPointF(r_out * math.cos(theta), r_out * math.sin(theta)),
            )
        painter.restore()
