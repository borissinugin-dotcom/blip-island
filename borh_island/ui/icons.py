"""Bespoke vector QPainter icon factory for Dynamic Island — Borh-edition."""

from __future__ import annotations

import math
from typing import Dict, Tuple

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QIcon,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
)

ICON_NAMES: tuple[str, ...] = (
    "app",
    "home",
    "clipboard",
    "character",
    "shelf",
    "text",
    "chat",
    "notes",
    "stats",
    "weather",
    "controls",
    "collapse",
    "prev",
    "play",
    "pause",
    "next",
    "spotify",
    "music",
    "timer",
    "keyboard",
    "volume",
    "mute",
    "screenshot",
    "fullscreen",
    "ocr",
    "color",
    "qr",
    "record",
    "dictate",
    "command",
    "settings",
    "discord",
    "mic",
    "mic_mute",
    "sun",
    "theme",
    "lock",
    "sleep",
    "restart",
    "power",
    "rocket",
    "mixer",
    "refresh",
    "save",
    "plus",
    "close",
    "shield",
    "headphones",
    "bell",
    "trash",
    "copy",
    "attach",
    "camera",
    "globe",
    "folder",
    "bolt",
    "tools",
)

_PIXMAP_CACHE: Dict[Tuple[str, str, str, int], QPixmap] = {}


def _pen(color: QColor, width: float = 1.8) -> QPen:
    p = QPen(color, width)
    p.setCapStyle(Qt.PenCapStyle.RoundCap)
    p.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return p


def _draw_app_icon(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    pad = s * 0.06
    body = QRectF(pad, pad, s - 2 * pad, s - 2 * pad)
    grad = QLinearGradient(body.topLeft(), body.bottomRight())
    grad.setColorAt(0.0, QColor("#171923"))
    grad.setColorAt(1.0, QColor("#090B10"))
    ring = QLinearGradient(body.topLeft(), body.bottomRight())
    ring.setColorAt(0.0, acc)
    ring.setColorAt(0.55, QColor("#60A5FA"))
    ring.setColorAt(1.0, QColor("#F8FAFC"))
    p.setPen(QPen(QBrush(ring), max(1.4, s * 0.06)))
    p.setBrush(grad)
    p.drawRoundedRect(body, s * 0.28, s * 0.28)

    # Top Dynamic Island pill silhouette
    pill = QRectF(s * 0.19, s * 0.27, s * 0.62, s * 0.24)
    p.setPen(QPen(QColor(255, 255, 255, 58), max(1.0, s * 0.025)))
    p.setBrush(QColor("#020307"))
    p.drawRoundedRect(pill, pill.height() * 0.5, pill.height() * 0.5)

    # Glowing camera orb + circadian aura inside pill
    glow = QRadialGradient(QPointF(s * 0.34, s * 0.39), s * 0.13)
    glow.setColorAt(0.0, acc)
    glow.setColorAt(1.0, QColor(0, 0, 0, 0))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(glow)
    p.drawEllipse(QPointF(s * 0.34, s * 0.39), s * 0.12, s * 0.12)
    p.setBrush(QColor("#BAE6FD"))
    p.drawEllipse(QPointF(s * 0.34, s * 0.39), s * 0.045, s * 0.045)

    # Mini spectrum equalizer along bottom half
    bar_x = [0.27, 0.37, 0.47, 0.57, 0.67]
    bar_h = [0.14, 0.24, 0.30, 0.20, 0.15]
    bar_grad = QLinearGradient(QPointF(s * 0.25, 0), QPointF(s * 0.75, 0))
    bar_grad.setColorAt(0.0, QColor("#3B82F6"))
    bar_grad.setColorAt(0.5, QColor("#60A5FA"))
    bar_grad.setColorAt(1.0, QColor("#F8FAFC"))
    p.setBrush(bar_grad)
    bw = max(1.8, s * 0.055)
    base_y = s * 0.80
    for bx, bh in zip(bar_x, bar_h):
        h = s * bh * 0.72
        p.drawRoundedRect(QRectF(s * bx - bw * 0.5, base_y - h, bw, h), bw * 0.5, bw * 0.5)


def _draw_home(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.16, s * 0.47)
    path.lineTo(s * 0.50, s * 0.17)
    path.lineTo(s * 0.84, s * 0.47)
    p.setPen(_pen(fg, s * 0.095))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)

    house = QPainterPath()
    house.moveTo(s * 0.25, s * 0.43)
    house.lineTo(s * 0.25, s * 0.78)
    house.quadTo(s * 0.25, s * 0.83, s * 0.30, s * 0.83)
    house.lineTo(s * 0.70, s * 0.83)
    house.quadTo(s * 0.75, s * 0.83, s * 0.75, s * 0.78)
    house.lineTo(s * 0.75, s * 0.43)
    p.drawPath(house)

    # Accent core pill inside home
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawRoundedRect(QRectF(s * 0.41, s * 0.58, s * 0.18, s * 0.14), s * 0.06, s * 0.06)


def _draw_clipboard(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 20))
    p.drawRoundedRect(QRectF(s * 0.22, s * 0.22, s * 0.56, s * 0.64), s * 0.10, s * 0.10)

    p.setPen(_pen(acc, s * 0.08))
    p.setBrush(QColor("#111622"))
    p.drawRoundedRect(QRectF(s * 0.36, s * 0.13, s * 0.28, s * 0.14), s * 0.06, s * 0.06)

    p.setPen(_pen(fg, s * 0.075))
    p.drawLine(QPointF(s * 0.35, s * 0.45), QPointF(s * 0.65, s * 0.45))
    p.drawLine(QPointF(s * 0.35, s * 0.59), QPointF(s * 0.65, s * 0.59))
    p.setPen(_pen(acc, s * 0.075))
    p.drawLine(QPointF(s * 0.35, s * 0.73), QPointF(s * 0.54, s * 0.73))


def _draw_shelf(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    # Magnet U-arc + floating file card
    p.setPen(_pen(acc, s * 0.095))
    p.setBrush(Qt.BrushStyle.NoBrush)
    arc = QPainterPath()
    arc.moveTo(s * 0.28, s * 0.30)
    arc.lineTo(s * 0.28, s * 0.53)
    arc.arcTo(QRectF(s * 0.28, s * 0.31, s * 0.44, s * 0.44), 180, 180)
    arc.lineTo(s * 0.72, s * 0.30)
    p.drawPath(arc)

    # Magnet polar tips
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawRoundedRect(QRectF(s * 0.23, s * 0.21, s * 0.10, s * 0.12), s * 0.03, s * 0.03)
    p.drawRoundedRect(QRectF(s * 0.67, s * 0.21, s * 0.10, s * 0.12), s * 0.03, s * 0.03)

    # Tray baseline
    p.setPen(_pen(fg, s * 0.09))
    p.drawLine(QPointF(s * 0.18, s * 0.84), QPointF(s * 0.82, s * 0.84))


def _draw_text(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    # Diagonal stylus + sparkle line
    p.setPen(_pen(fg, s * 0.09))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 26))
    pen_path = QPainterPath()
    pen_path.moveTo(s * 0.22, s * 0.78)
    pen_path.lineTo(s * 0.28, s * 0.59)
    pen_path.lineTo(s * 0.68, s * 0.19)
    pen_path.lineTo(s * 0.81, s * 0.32)
    pen_path.lineTo(s * 0.41, s * 0.72)
    pen_path.closeSubpath()
    p.drawPath(pen_path)
    p.setPen(_pen(acc, s * 0.085))
    p.drawLine(QPointF(s * 0.52, s * 0.79), QPointF(s * 0.82, s * 0.79))


def _draw_chat(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    bubble = QPainterPath()
    bubble.addRoundedRect(QRectF(s * 0.16, s * 0.20, s * 0.68, s * 0.50), s * 0.14, s * 0.14)
    tail = QPainterPath()
    tail.moveTo(s * 0.30, s * 0.68)
    tail.lineTo(s * 0.22, s * 0.83)
    tail.lineTo(s * 0.44, s * 0.68)
    bubble = bubble.united(tail)
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 18))
    p.drawPath(bubble)

    # AI 4-point star spark inside bubble
    cx, cy, r = s * 0.50, s * 0.45, s * 0.15
    star = QPainterPath()
    star.moveTo(cx, cy - r)
    star.quadTo(cx, cy, cx + r, cy)
    star.quadTo(cx, cy, cx, cy + r)
    star.quadTo(cx, cy, cx - r, cy)
    star.quadTo(cx, cy, cx, cy - r)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawPath(star)


def _draw_notes(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    page = QPainterPath()
    page.moveTo(s * 0.24, s * 0.16)
    page.lineTo(s * 0.62, s * 0.16)
    page.lineTo(s * 0.78, s * 0.32)
    page.lineTo(s * 0.78, s * 0.84)
    page.lineTo(s * 0.24, s * 0.84)
    page.closeSubpath()
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 18))
    p.drawPath(page)
    p.drawLine(QPointF(s * 0.62, s * 0.16), QPointF(s * 0.62, s * 0.32))
    p.drawLine(QPointF(s * 0.62, s * 0.32), QPointF(s * 0.78, s * 0.32))

    p.setPen(_pen(acc, s * 0.08))
    p.drawLine(QPointF(s * 0.35, s * 0.47), QPointF(s * 0.65, s * 0.47))
    p.setPen(_pen(fg, s * 0.075))
    p.drawLine(QPointF(s * 0.35, s * 0.60), QPointF(s * 0.66, s * 0.60))
    p.drawLine(QPointF(s * 0.35, s * 0.72), QPointF(s * 0.56, s * 0.72))


def _draw_stats(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    bw = s * 0.15
    p.drawRoundedRect(QRectF(s * 0.19, s * 0.52, bw, s * 0.30), bw * 0.4, bw * 0.4)
    p.drawRoundedRect(QRectF(s * 0.66, s * 0.36, bw, s * 0.46), bw * 0.4, bw * 0.4)
    p.setBrush(acc)
    p.drawRoundedRect(QRectF(s * 0.425, s * 0.20, bw, s * 0.62), bw * 0.4, bw * 0.4)


def _draw_weather(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    sun = QColor("#FBBF24")
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(sun)
    p.drawEllipse(QPointF(s * 0.36, s * 0.36), s * 0.16, s * 0.16)

    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(QColor("#E2E8F0") if fg.lightness() > 140 else fg)
    cloud = QPainterPath()
    cloud.addRoundedRect(QRectF(s * 0.24, s * 0.50, s * 0.56, s * 0.25), s * 0.125, s * 0.125)
    cloud.addEllipse(QPointF(s * 0.46, s * 0.47), s * 0.16, s * 0.15)
    cloud.addEllipse(QPointF(s * 0.62, s * 0.51), s * 0.12, s * 0.12)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawPath(cloud)

    p.setPen(_pen(acc, s * 0.08))
    p.drawLine(QPointF(s * 0.38, s * 0.82), QPointF(s * 0.34, s * 0.89))
    p.drawLine(QPointF(s * 0.58, s * 0.82), QPointF(s * 0.54, s * 0.89))


def _draw_controls(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    ys = [0.28, 0.50, 0.72]
    kx = [0.35, 0.66, 0.44]
    for i, (y, x) in enumerate(zip(ys, kx)):
        p.setPen(_pen(fg, s * 0.085))
        p.drawLine(QPointF(s * 0.18, s * y), QPointF(s * 0.82, s * y))
        p.setPen(_pen(fg, s * 0.07))
        p.setBrush(acc if i == 1 else fg)
        p.drawEllipse(QPointF(s * x, s * y), s * 0.085, s * 0.085)


def _draw_collapse(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.11))
    p.setBrush(Qt.BrushStyle.NoBrush)
    up1 = QPainterPath()
    up1.moveTo(s * 0.24, s * 0.56)
    up1.lineTo(s * 0.50, s * 0.30)
    up1.lineTo(s * 0.76, s * 0.56)
    p.drawPath(up1)
    p.setPen(_pen(acc, s * 0.09))
    p.drawLine(QPointF(s * 0.34, s * 0.73), QPointF(s * 0.66, s * 0.73))


def _draw_prev(p: QPainter, s: float, fg: QColor, _acc: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawRoundedRect(QRectF(s * 0.21, s * 0.24, s * 0.11, s * 0.52), s * 0.04, s * 0.04)
    tri = QPainterPath()
    tri.moveTo(s * 0.76, s * 0.24)
    tri.lineTo(s * 0.37, s * 0.50)
    tri.lineTo(s * 0.76, s * 0.76)
    tri.closeSubpath()
    p.drawPath(tri)


def _draw_next(p: QPainter, s: float, fg: QColor, _acc: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawRoundedRect(QRectF(s * 0.68, s * 0.24, s * 0.11, s * 0.52), s * 0.04, s * 0.04)
    tri = QPainterPath()
    tri.moveTo(s * 0.24, s * 0.24)
    tri.lineTo(s * 0.63, s * 0.50)
    tri.lineTo(s * 0.24, s * 0.76)
    tri.closeSubpath()
    p.drawPath(tri)


def _draw_play(p: QPainter, s: float, fg: QColor, _acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.06))
    p.setBrush(fg)
    tri = QPainterPath()
    tri.moveTo(s * 0.31, s * 0.22)
    tri.lineTo(s * 0.78, s * 0.50)
    tri.lineTo(s * 0.31, s * 0.78)
    tri.closeSubpath()
    p.drawPath(tri)


def _draw_pause(p: QPainter, s: float, fg: QColor, _acc: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawRoundedRect(QRectF(s * 0.26, s * 0.22, s * 0.16, s * 0.56), s * 0.06, s * 0.06)
    p.drawRoundedRect(QRectF(s * 0.58, s * 0.22, s * 0.16, s * 0.56), s * 0.06, s * 0.06)


def _draw_spotify(p: QPainter, s: float, _fg: QColor, _acc: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1DB954"))
    p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.42, s * 0.42)
    p.setPen(_pen(QColor("#090D12"), s * 0.08))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for y_off, span, amp in [(0.38, 0.25, 0.06), (0.51, 0.21, 0.05), (0.64, 0.16, 0.04)]:
        wave = QPainterPath()
        wave.moveTo(s * (0.50 - span), s * y_off)
        wave.quadTo(s * 0.50, s * (y_off - amp), s * (0.50 + span), s * (y_off + amp * 0.3))
        p.drawPath(wave)


def _draw_music(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(acc, s * 0.095))
    p.drawLine(QPointF(s * 0.42, s * 0.66), QPointF(s * 0.42, s * 0.24))
    p.drawLine(QPointF(s * 0.74, s * 0.59), QPointF(s * 0.74, s * 0.19))
    p.drawLine(QPointF(s * 0.42, s * 0.24), QPointF(s * 0.74, s * 0.19))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawEllipse(QPointF(s * 0.34, s * 0.70), s * 0.12, s * 0.09)
    p.drawEllipse(QPointF(s * 0.66, s * 0.63), s * 0.12, s * 0.09)


def _draw_timer(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.09))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(s * 0.50, s * 0.54), s * 0.30, s * 0.30)
    p.drawLine(QPointF(s * 0.41, s * 0.15), QPointF(s * 0.59, s * 0.15))
    p.setPen(_pen(acc, s * 0.09))
    p.drawLine(QPointF(s * 0.50, s * 0.54), QPointF(s * 0.50, s * 0.36))
    p.drawLine(QPointF(s * 0.50, s * 0.54), QPointF(s * 0.64, s * 0.54))


def _draw_screenshot(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.09))
    # 4 crop corner brackets
    c = [(0.18, 0.18, 1, 1), (0.82, 0.18, -1, 1), (0.18, 0.82, 1, -1), (0.82, 0.82, -1, -1)]
    arm = s * 0.19
    for cx, cy, sx, sy in c:
        x, y = s * cx, s * cy
        p.drawLine(QPointF(x, y), QPointF(x + sx * arm, y))
        p.drawLine(QPointF(x, y), QPointF(x, y + sy * arm))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.11, s * 0.11)


def _draw_fullscreen(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 18))
    p.drawRoundedRect(QRectF(s * 0.16, s * 0.22, s * 0.68, s * 0.56), s * 0.10, s * 0.10)
    p.setPen(_pen(acc, s * 0.085))
    p.drawLine(QPointF(s * 0.30, s * 0.64), QPointF(s * 0.70, s * 0.36))
    p.drawLine(QPointF(s * 0.57, s * 0.36), QPointF(s * 0.70, s * 0.36))
    p.drawLine(QPointF(s * 0.70, s * 0.36), QPointF(s * 0.70, s * 0.49))


def _draw_ocr(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.09))
    p.drawLine(QPointF(s * 0.33, s * 0.29), QPointF(s * 0.67, s * 0.29))
    p.drawLine(QPointF(s * 0.50, s * 0.29), QPointF(s * 0.50, s * 0.71))
    p.setPen(_pen(acc, s * 0.085))
    p.drawLine(QPointF(s * 0.16, s * 0.53), QPointF(s * 0.84, s * 0.53))


def _draw_color(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.09))
    p.drawLine(QPointF(s * 0.26, s * 0.74), QPointF(s * 0.66, s * 0.34))
    p.drawLine(QPointF(s * 0.54, s * 0.24), QPointF(s * 0.76, s * 0.46))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    drop = QPainterPath()
    drop.moveTo(s * 0.22, s * 0.66)
    drop.quadTo(s * 0.13, s * 0.82, s * 0.22, s * 0.86)
    drop.quadTo(s * 0.31, s * 0.82, s * 0.22, s * 0.66)
    p.drawPath(drop)


def _draw_qr(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.18, s * 0.18, s * 0.25, s * 0.25), s * 0.04, s * 0.04)
    p.drawRoundedRect(QRectF(s * 0.57, s * 0.18, s * 0.25, s * 0.25), s * 0.04, s * 0.04)
    p.drawRoundedRect(QRectF(s * 0.18, s * 0.57, s * 0.25, s * 0.25), s * 0.04, s * 0.04)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawRoundedRect(QRectF(s * 0.58, s * 0.58, s * 0.11, s * 0.11), s * 0.02, s * 0.02)
    p.drawRoundedRect(QRectF(s * 0.72, s * 0.72, s * 0.11, s * 0.11), s * 0.02, s * 0.02)


def _draw_record(p: QPainter, s: float, fg: QColor, _acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.33, s * 0.33)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#EF4444"))
    p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.18, s * 0.18)


def _draw_dictate(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(acc.red(), acc.green(), acc.blue(), 190))
    p.drawRoundedRect(QRectF(s * 0.39, s * 0.16, s * 0.22, s * 0.42), s * 0.11, s * 0.11)
    p.setBrush(Qt.BrushStyle.NoBrush)
    arc = QPainterPath()
    arc.moveTo(s * 0.28, s * 0.47)
    arc.arcTo(QRectF(s * 0.28, s * 0.26, s * 0.44, s * 0.42), 180, 180)
    p.drawPath(arc)
    p.drawLine(QPointF(s * 0.50, s * 0.68), QPointF(s * 0.50, s * 0.84))
    p.drawLine(QPointF(s * 0.36, s * 0.84), QPointF(s * 0.64, s * 0.84))


def _draw_command(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 16))
    p.drawRoundedRect(QRectF(s * 0.15, s * 0.21, s * 0.70, s * 0.58), s * 0.14, s * 0.14)
    p.setPen(_pen(acc, s * 0.09))
    chev = QPainterPath()
    chev.moveTo(s * 0.29, s * 0.39)
    chev.lineTo(s * 0.44, s * 0.50)
    chev.lineTo(s * 0.29, s * 0.61)
    p.drawPath(chev)
    p.setPen(_pen(fg, s * 0.09))
    p.drawLine(QPointF(s * 0.51, s * 0.61), QPointF(s * 0.70, s * 0.61))


def _draw_settings(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    cx, cy = s * 0.50, s * 0.50
    p.setPen(_pen(fg, s * 0.09))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for i in range(8):
        ang = i * (math.pi / 4.0)
        p.drawLine(
            QPointF(cx + math.cos(ang) * s * 0.24, cy + math.sin(ang) * s * 0.24),
            QPointF(cx + math.cos(ang) * s * 0.36, cy + math.sin(ang) * s * 0.36),
        )
    p.setBrush(QColor("#111622"))
    p.drawEllipse(QPointF(cx, cy), s * 0.23, s * 0.23)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawEllipse(QPointF(cx, cy), s * 0.095, s * 0.095)


def _draw_keyboard(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 20))
    p.drawRoundedRect(QRectF(s * 0.14, s * 0.28, s * 0.72, s * 0.44), s * 0.09, s * 0.09)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    for x in (0.24, 0.38, 0.52, 0.66):
        p.drawRoundedRect(QRectF(s * x, s * 0.39, s * 0.09, s * 0.07), s * 0.02, s * 0.02)
    p.setBrush(fg)
    p.drawRoundedRect(QRectF(s * 0.31, s * 0.56, s * 0.38, s * 0.07), s * 0.03, s * 0.03)


def _draw_volume(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    speaker = QPainterPath()
    speaker.moveTo(s * 0.18, s * 0.41)
    speaker.lineTo(s * 0.34, s * 0.41)
    speaker.lineTo(s * 0.52, s * 0.24)
    speaker.lineTo(s * 0.52, s * 0.76)
    speaker.lineTo(s * 0.34, s * 0.59)
    speaker.lineTo(s * 0.18, s * 0.59)
    speaker.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawPath(speaker)
    p.setPen(_pen(acc, s * 0.085))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(s * 0.50, s * 0.35, s * 0.18, s * 0.30), -55 * 16, 110 * 16)
    p.drawArc(QRectF(s * 0.56, s * 0.24, s * 0.24, s * 0.52), -55 * 16, 110 * 16)


def _draw_mute(p: QPainter, s: float, fg: QColor, _acc: QColor) -> None:
    _draw_volume(p, s, fg, fg)
    p.setPen(_pen(QColor("#EF4444"), s * 0.10))
    p.drawLine(QPointF(s * 0.62, s * 0.36), QPointF(s * 0.84, s * 0.64))
    p.drawLine(QPointF(s * 0.84, s * 0.36), QPointF(s * 0.62, s * 0.64))


def _draw_discord(p: QPainter, s: float, _fg: QColor, _acc: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#5865F2"))
    p.drawRoundedRect(QRectF(s * 0.10, s * 0.16, s * 0.80, s * 0.68), s * 0.22, s * 0.22)
    p.setBrush(QColor("#FFFFFF"))
    p.drawEllipse(QPointF(s * 0.37, s * 0.48), s * 0.085, s * 0.10)
    p.drawEllipse(QPointF(s * 0.63, s * 0.48), s * 0.085, s * 0.10)
    p.setPen(_pen(QColor("#FFFFFF"), s * 0.075))
    p.setBrush(Qt.BrushStyle.NoBrush)
    smile = QPainterPath()
    smile.moveTo(s * 0.32, s * 0.66)
    smile.quadTo(s * 0.50, s * 0.74, s * 0.68, s * 0.66)
    p.drawPath(smile)


def _draw_character(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    """Sparkling friendly companion badge icon."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    blob = QPainterPath()
    blob.moveTo(s * 0.20, s * 0.62)
    blob.cubicTo(s * 0.16, s * 0.28, s * 0.34, s * 0.16, s * 0.50, s * 0.18)
    blob.cubicTo(s * 0.68, s * 0.16, s * 0.84, s * 0.28, s * 0.80, s * 0.62)
    blob.cubicTo(s * 0.78, s * 0.80, s * 0.64, s * 0.84, s * 0.50, s * 0.83)
    blob.cubicTo(s * 0.36, s * 0.84, s * 0.22, s * 0.80, s * 0.20, s * 0.62)
    blob.closeSubpath()
    p.drawPath(blob)
    # Eyes + cute smile
    p.setBrush(fg)
    p.drawEllipse(QPointF(s * 0.39, s * 0.48), s * 0.065, s * 0.08)
    p.drawEllipse(QPointF(s * 0.61, s * 0.48), s * 0.065, s * 0.08)
    p.setPen(_pen(fg, s * 0.07))
    p.setBrush(Qt.BrushStyle.NoBrush)
    smile = QPainterPath()
    smile.moveTo(s * 0.40, s * 0.64)
    smile.quadTo(s * 0.50, s * 0.72, s * 0.60, s * 0.64)
    p.drawPath(smile)


def _draw_mic(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    _draw_dictate(p, s, fg, acc)


def _draw_mic_mute(p: QPainter, s: float, fg: QColor, _acc: QColor) -> None:
    _draw_dictate(p, s, fg, fg)
    p.setPen(_pen(QColor("#EF4444"), s * 0.11))
    p.drawLine(QPointF(s * 0.22, s * 0.22), QPointF(s * 0.78, s * 0.78))


def _draw_sun(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    cx, cy = s * 0.50, s * 0.50
    p.setPen(_pen(acc, s * 0.085))
    for i in range(8):
        ang = i * (math.pi / 4.0)
        p.drawLine(
            QPointF(cx + math.cos(ang) * s * 0.28, cy + math.sin(ang) * s * 0.28),
            QPointF(cx + math.cos(ang) * s * 0.40, cy + math.sin(ang) * s * 0.40),
        )
    p.setPen(_pen(fg, s * 0.075))
    p.setBrush(QColor("#FBBF24"))
    p.drawEllipse(QPointF(cx, cy), s * 0.19, s * 0.19)


def _draw_theme(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    """Split half-contrast sphere badge (dark/light system theme switch)."""
    cx, cy, r = s * 0.50, s * 0.50, s * 0.34
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(cx, cy), r, r)
    half = QPainterPath()
    half.moveTo(cx, cy - r + s * 0.03)
    half.arcTo(QRectF(cx - r + s * 0.03, cy - r + s * 0.03, (r - s * 0.03) * 2, (r - s * 0.03) * 2), 90, 180)
    half.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawPath(half)


def _draw_lock(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    """Custom-drawn padlock badge with shackle, beveled body, and keyhole."""
    # Shackle
    p.setPen(_pen(fg, s * 0.105))
    p.setBrush(Qt.BrushStyle.NoBrush)
    arc = QPainterPath()
    arc.moveTo(s * 0.33, s * 0.44)
    arc.lineTo(s * 0.33, s * 0.32)
    arc.arcTo(QRectF(s * 0.33, s * 0.15, s * 0.34, s * 0.34), 180, -180)
    arc.lineTo(s * 0.67, s * 0.44)
    p.drawPath(arc)

    # Lock body with warm tinted fill + crisp border
    body = QRectF(s * 0.21, s * 0.43, s * 0.58, s * 0.42)
    fill = QColor(fg.red(), fg.green(), fg.blue(), 52)
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(fill)
    p.drawRoundedRect(body, s * 0.12, s * 0.12)

    # Subtle top inner highlight line
    p.setPen(_pen(QColor(255, 255, 255, 110), s * 0.045))
    p.drawLine(QPointF(s * 0.29, s * 0.49), QPointF(s * 0.71, s * 0.49))

    # Keyhole (circle + slot)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawEllipse(QPointF(s * 0.50, s * 0.60), s * 0.072, s * 0.072)
    slot = QPainterPath()
    slot.moveTo(s * 0.465, s * 0.63)
    slot.lineTo(s * 0.535, s * 0.63)
    slot.lineTo(s * 0.555, s * 0.75)
    slot.lineTo(s * 0.445, s * 0.75)
    slot.closeSubpath()
    p.drawPath(slot)


def _draw_sleep(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    """Clean geometric Zz + standby vector sticker."""
    p.setPen(_pen(fg, s * 0.095))
    z1 = QPainterPath()
    z1.moveTo(s * 0.22, s * 0.36)
    z1.lineTo(s * 0.48, s * 0.36)
    z1.lineTo(s * 0.22, s * 0.64)
    z1.lineTo(s * 0.48, s * 0.64)
    p.drawPath(z1)
    p.setPen(_pen(acc, s * 0.09))
    z2 = QPainterPath()
    z2.moveTo(s * 0.54, s * 0.22)
    z2.lineTo(s * 0.78, s * 0.22)
    z2.lineTo(s * 0.54, s * 0.44)
    z2.lineTo(s * 0.78, s * 0.44)
    p.drawPath(z2)
    p.setPen(_pen(acc, s * 0.08))
    p.drawLine(QPointF(s * 0.55, s * 0.72), QPointF(s * 0.76, s * 0.72))


def _draw_restart(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.095))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(s * 0.20, s * 0.20, s * 0.60, s * 0.60), 40 * 16, 285 * 16)
    p.setPen(_pen(acc, s * 0.095))
    head = QPainterPath()
    head.moveTo(s * 0.62, s * 0.16)
    head.lineTo(s * 0.79, s * 0.26)
    head.lineTo(s * 0.64, s * 0.38)
    p.drawPath(head)


def _draw_power(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.095))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(s * 0.21, s * 0.23, s * 0.58, s * 0.58), 125 * 16, 290 * 16)
    p.setPen(_pen(QColor("#FB7185"), s * 0.10))
    p.drawLine(QPointF(s * 0.50, s * 0.16), QPointF(s * 0.50, s * 0.48))


def _draw_rocket(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    body = QPainterPath()
    body.moveTo(s * 0.76, s * 0.22)
    body.quadTo(s * 0.44, s * 0.24, s * 0.30, s * 0.56)
    body.lineTo(s * 0.44, s * 0.70)
    body.quadTo(s * 0.76, s * 0.56, s * 0.76, s * 0.22)
    body.closeSubpath()
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(QColor(acc.red(), acc.green(), acc.blue(), 145))
    p.drawPath(body)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawEllipse(QPointF(s * 0.56, s * 0.44), s * 0.075, s * 0.075)
    p.setPen(_pen(QColor("#FBBF24"), s * 0.085))
    p.drawLine(QPointF(s * 0.22, s * 0.78), QPointF(s * 0.34, s * 0.66))


def _draw_mixer(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    xs = [0.26, 0.50, 0.74]
    ky = [0.36, 0.64, 0.44]
    for i, (x, y) in enumerate(zip(xs, ky)):
        p.setPen(_pen(fg, s * 0.085))
        p.drawLine(QPointF(s * x, s * 0.18), QPointF(s * x, s * 0.82))
        p.setPen(_pen(fg, s * 0.07))
        p.setBrush(acc if i != 1 else fg)
        p.drawEllipse(QPointF(s * x, s * y), s * 0.085, s * 0.085)


def _draw_refresh(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    _draw_restart(p, s, acc, fg)


def _draw_save(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    box = QPainterPath()
    box.moveTo(s * 0.20, s * 0.18)
    box.lineTo(s * 0.68, s * 0.18)
    box.lineTo(s * 0.82, s * 0.32)
    box.lineTo(s * 0.82, s * 0.82)
    box.lineTo(s * 0.20, s * 0.82)
    box.closeSubpath()
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 22))
    p.drawPath(box)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawRoundedRect(QRectF(s * 0.33, s * 0.22, s * 0.28, s * 0.19), s * 0.04, s * 0.04)
    p.setBrush(fg)
    p.drawRoundedRect(QRectF(s * 0.31, s * 0.55, s * 0.38, s * 0.21), s * 0.05, s * 0.05)


def _draw_plus(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(acc, s * 0.11))
    p.drawLine(QPointF(s * 0.50, s * 0.22), QPointF(s * 0.50, s * 0.78))
    p.setPen(_pen(fg, s * 0.11))
    p.drawLine(QPointF(s * 0.22, s * 0.50), QPointF(s * 0.78, s * 0.50))


def _draw_close(p: QPainter, s: float, fg: QColor, _acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.10))
    p.drawLine(QPointF(s * 0.27, s * 0.27), QPointF(s * 0.73, s * 0.73))
    p.drawLine(QPointF(s * 0.73, s * 0.27), QPointF(s * 0.27, s * 0.73))


def _draw_shield(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    sh = QPainterPath()
    sh.moveTo(s * 0.50, s * 0.14)
    sh.lineTo(s * 0.80, s * 0.28)
    sh.quadTo(s * 0.80, s * 0.64, s * 0.50, s * 0.86)
    sh.quadTo(s * 0.20, s * 0.64, s * 0.20, s * 0.28)
    sh.closeSubpath()
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(acc.red(), acc.green(), acc.blue(), 65))
    p.drawPath(sh)


def _draw_headphones(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.09))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(s * 0.20, s * 0.22, s * 0.60, s * 0.58), 0, 180 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawRoundedRect(QRectF(s * 0.16, s * 0.52, s * 0.16, s * 0.26), s * 0.06, s * 0.06)
    p.drawRoundedRect(QRectF(s * 0.68, s * 0.52, s * 0.16, s * 0.26), s * 0.06, s * 0.06)


def _draw_bell(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    bell = QPainterPath()
    bell.moveTo(s * 0.30, s * 0.62)
    bell.quadTo(s * 0.30, s * 0.26, s * 0.50, s * 0.24)
    bell.quadTo(s * 0.70, s * 0.26, s * 0.70, s * 0.62)
    bell.lineTo(s * 0.79, s * 0.72)
    bell.lineTo(s * 0.21, s * 0.72)
    bell.closeSubpath()
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(QColor(acc.red(), acc.green(), acc.blue(), 95))
    p.drawPath(bell)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawEllipse(QPointF(s * 0.50, s * 0.81), s * 0.075, s * 0.075)


def _draw_trash(p: QPainter, s: float, fg: QColor, _acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.drawLine(QPointF(s * 0.22, s * 0.28), QPointF(s * 0.78, s * 0.28))
    p.drawRoundedRect(QRectF(s * 0.38, s * 0.16, s * 0.24, s * 0.12), s * 0.04, s * 0.04)
    p.setBrush(QColor(239, 68, 68, 45))
    p.drawRoundedRect(QRectF(s * 0.28, s * 0.28, s * 0.44, s * 0.54), s * 0.08, s * 0.08)


def _draw_copy(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(acc, s * 0.08))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(QRectF(s * 0.34, s * 0.18, s * 0.46, s * 0.52), s * 0.08, s * 0.08)
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(QColor("#111622"))
    p.drawRoundedRect(QRectF(s * 0.20, s * 0.32, s * 0.46, s * 0.52), s * 0.08, s * 0.08)


def _draw_attach(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    clip = QPainterPath()
    clip.moveTo(s * 0.36, s * 0.64)
    clip.lineTo(s * 0.62, s * 0.30)
    clip.arcTo(QRectF(s * 0.46, s * 0.16, s * 0.30, s * 0.30), 135, -180)
    clip.lineTo(s * 0.38, s * 0.76)
    clip.arcTo(QRectF(s * 0.20, s * 0.54, s * 0.28, s * 0.28), 315, -180)
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(clip)
    p.setPen(_pen(acc, s * 0.085))
    p.drawLine(QPointF(s * 0.50, s * 0.45), QPointF(s * 0.36, s * 0.64))


def _draw_camera(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 22))
    p.drawRoundedRect(QRectF(s * 0.16, s * 0.28, s * 0.68, s * 0.50), s * 0.10, s * 0.10)
    p.drawRoundedRect(QRectF(s * 0.37, s * 0.19, s * 0.26, s * 0.11), s * 0.04, s * 0.04)
    p.setPen(_pen(acc, s * 0.08))
    p.drawEllipse(QPointF(s * 0.50, s * 0.53), s * 0.14, s * 0.14)


def _draw_globe(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.33, s * 0.33)
    p.setPen(_pen(acc, s * 0.075))
    p.drawEllipse(QPointF(s * 0.50, s * 0.50), s * 0.15, s * 0.33)
    p.drawLine(QPointF(s * 0.18, s * 0.50), QPointF(s * 0.82, s * 0.50))


def _draw_folder(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    fld = QPainterPath()
    fld.moveTo(s * 0.16, s * 0.26)
    fld.lineTo(s * 0.42, s * 0.26)
    fld.lineTo(s * 0.50, s * 0.35)
    fld.lineTo(s * 0.84, s * 0.35)
    fld.lineTo(s * 0.84, s * 0.78)
    fld.lineTo(s * 0.16, s * 0.78)
    fld.closeSubpath()
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(QColor(acc.red(), acc.green(), acc.blue(), 85))
    p.drawPath(fld)


def _draw_bolt(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    b = QPainterPath()
    b.moveTo(s * 0.56, s * 0.15)
    b.lineTo(s * 0.28, s * 0.54)
    b.lineTo(s * 0.49, s * 0.54)
    b.lineTo(s * 0.42, s * 0.85)
    b.lineTo(s * 0.72, s * 0.45)
    b.lineTo(s * 0.51, s * 0.45)
    b.closeSubpath()
    p.setPen(_pen(fg, s * 0.065))
    p.setBrush(acc)
    p.drawPath(b)


def _draw_tools(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.095))
    p.drawLine(QPointF(s * 0.24, s * 0.76), QPointF(s * 0.74, s * 0.26))
    p.setPen(_pen(acc, s * 0.095))
    p.drawLine(QPointF(s * 0.24, s * 0.26), QPointF(s * 0.74, s * 0.76))


def _draw_warning(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    tri = QPainterPath()
    tri.moveTo(s * 0.50, s * 0.16)
    tri.lineTo(s * 0.86, s * 0.82)
    tri.lineTo(s * 0.14, s * 0.82)
    tri.closeSubpath()
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(QColor(245, 158, 11, 80))
    p.drawPath(tri)
    p.setPen(_pen(acc, s * 0.09))
    p.drawLine(QPointF(s * 0.50, s * 0.40), QPointF(s * 0.50, s * 0.60))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawEllipse(QPointF(s * 0.50, s * 0.71), s * 0.045, s * 0.045)


def _draw_swap(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.drawLine(QPointF(s * 0.34, s * 0.76), QPointF(s * 0.34, s * 0.24))
    p.drawLine(QPointF(s * 0.22, s * 0.36), QPointF(s * 0.34, s * 0.24))
    p.drawLine(QPointF(s * 0.46, s * 0.36), QPointF(s * 0.34, s * 0.24))
    p.setPen(_pen(acc, s * 0.085))
    p.drawLine(QPointF(s * 0.66, s * 0.24), QPointF(s * 0.66, s * 0.76))
    p.drawLine(QPointF(s * 0.54, s * 0.64), QPointF(s * 0.66, s * 0.76))
    p.drawLine(QPointF(s * 0.78, s * 0.64), QPointF(s * 0.66, s * 0.76))


def _draw_undo(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.09))
    p.setBrush(Qt.BrushStyle.NoBrush)
    arc = QPainterPath()
    arc.moveTo(s * 0.24, s * 0.42)
    arc.cubicTo(s * 0.46, s * 0.20, s * 0.80, s * 0.30, s * 0.78, s * 0.62)
    arc.cubicTo(s * 0.76, s * 0.78, s * 0.56, s * 0.82, s * 0.36, s * 0.78)
    p.drawPath(arc)
    p.setPen(_pen(acc, s * 0.09))
    p.drawLine(QPointF(s * 0.22, s * 0.44), QPointF(s * 0.28, s * 0.22))
    p.drawLine(QPointF(s * 0.22, s * 0.44), QPointF(s * 0.44, s * 0.46))


def _draw_pen(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(acc.red(), acc.green(), acc.blue(), 60))
    pen_p = QPainterPath()
    pen_p.moveTo(s * 0.20, s * 0.80)
    pen_p.lineTo(s * 0.26, s * 0.60)
    pen_p.lineTo(s * 0.68, s * 0.18)
    pen_p.lineTo(s * 0.82, s * 0.32)
    pen_p.lineTo(s * 0.40, s * 0.74)
    pen_p.closeSubpath()
    p.drawPath(pen_p)


def _draw_cloud(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 36))
    cl = QPainterPath()
    cl.addRoundedRect(QRectF(s * 0.16, s * 0.48, s * 0.68, s * 0.28), s * 0.14, s * 0.14)
    p.drawPath(cl)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 190))
    p.drawEllipse(QPointF(s * 0.40, s * 0.44), s * 0.18, s * 0.16)
    p.drawEllipse(QPointF(s * 0.60, s * 0.48), s * 0.15, s * 0.13)


def _draw_wifi(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.095))
    p.setBrush(Qt.BrushStyle.NoBrush)
    cx, cy = s * 0.50, s * 0.74
    for r_k in (0.52, 0.34):
        r = s * r_k
        p.drawArc(QRectF(cx - r, cy - r, r * 2.0, r * 2.0), 42 * 16, 96 * 16)
    p.setPen(_pen(acc, s * 0.095))
    r1 = s * 0.17
    p.drawArc(QRectF(cx - r1, cy - r1, r1 * 2.0, r1 * 2.0), 42 * 16, 96 * 16)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    p.drawEllipse(QPointF(cx, cy), s * 0.075, s * 0.075)


def _draw_bluetooth(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    path = QPainterPath()
    path.moveTo(s * 0.34, s * 0.33)
    path.lineTo(s * 0.66, s * 0.65)
    path.lineTo(s * 0.49, s * 0.82)
    path.lineTo(s * 0.49, s * 0.18)
    path.lineTo(s * 0.66, s * 0.35)
    path.lineTo(s * 0.34, s * 0.67)
    p.setPen(_pen(fg, s * 0.09))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)


def _draw_plane(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    plane = QPainterPath()
    plane.moveTo(s * 0.84, s * 0.50)
    plane.lineTo(s * 0.62, s * 0.44)
    plane.lineTo(s * 0.38, s * 0.16)
    plane.lineTo(s * 0.30, s * 0.16)
    plane.lineTo(s * 0.42, s * 0.44)
    plane.lineTo(s * 0.22, s * 0.44)
    plane.lineTo(s * 0.14, s * 0.32)
    plane.lineTo(s * 0.10, s * 0.32)
    plane.lineTo(s * 0.16, s * 0.50)
    plane.lineTo(s * 0.10, s * 0.68)
    plane.lineTo(s * 0.14, s * 0.68)
    plane.lineTo(s * 0.22, s * 0.56)
    plane.lineTo(s * 0.42, s * 0.56)
    plane.lineTo(s * 0.30, s * 0.84)
    plane.lineTo(s * 0.38, s * 0.84)
    plane.lineTo(s * 0.62, s * 0.56)
    plane.closeSubpath()
    p.setPen(_pen(fg, s * 0.055))
    p.setBrush(acc)
    p.drawPath(plane)


def _draw_desktop(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.085))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 18))
    p.drawRoundedRect(QRectF(s * 0.16, s * 0.20, s * 0.68, s * 0.48), s * 0.08, s * 0.08)
    p.drawLine(QPointF(s * 0.38, s * 0.78), QPointF(s * 0.62, s * 0.78))
    p.drawLine(QPointF(s * 0.50, s * 0.68), QPointF(s * 0.50, s * 0.78))
    p.setPen(_pen(acc, s * 0.08))
    p.drawLine(QPointF(s * 0.28, s * 0.56), QPointF(s * 0.72, s * 0.56))


def _draw_download(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(acc, s * 0.10))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawLine(QPointF(s * 0.50, s * 0.18), QPointF(s * 0.50, s * 0.62))
    p.drawLine(QPointF(s * 0.34, s * 0.46), QPointF(s * 0.50, s * 0.64))
    p.drawLine(QPointF(s * 0.66, s * 0.46), QPointF(s * 0.50, s * 0.64))
    p.setPen(_pen(fg, s * 0.095))
    p.drawLine(QPointF(s * 0.22, s * 0.78), QPointF(s * 0.78, s * 0.78))


def _draw_dnd(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(acc)
    moon = QPainterPath()
    moon.addEllipse(QPointF(s * 0.48, s * 0.50), s * 0.30, s * 0.30)
    cut = QPainterPath()
    cut.addEllipse(QPointF(s * 0.61, s * 0.40), s * 0.24, s * 0.24)
    p.drawPath(moon.subtracted(cut))
    p.setPen(_pen(fg, s * 0.08))
    p.drawLine(QPointF(s * 0.60, s * 0.62), QPointF(s * 0.78, s * 0.62))


def _draw_monitor_reset(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    p.setPen(_pen(fg, s * 0.08))
    p.setBrush(QColor(fg.red(), fg.green(), fg.blue(), 16))
    p.drawRoundedRect(QRectF(s * 0.16, s * 0.20, s * 0.68, s * 0.48), s * 0.08, s * 0.08)
    p.drawLine(QPointF(s * 0.38, s * 0.78), QPointF(s * 0.62, s * 0.78))
    p.setPen(_pen(acc, s * 0.085))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawArc(QRectF(s * 0.36, s * 0.30, s * 0.28, s * 0.28), 35 * 16, 260 * 16)


def _draw_caps(p: QPainter, s: float, fg: QColor, acc: QColor) -> None:
    arrow = QPainterPath()
    arrow.moveTo(s * 0.50, s * 0.18)
    arrow.lineTo(s * 0.76, s * 0.46)
    arrow.lineTo(s * 0.60, s * 0.46)
    arrow.lineTo(s * 0.60, s * 0.66)
    arrow.lineTo(s * 0.40, s * 0.66)
    arrow.lineTo(s * 0.40, s * 0.46)
    arrow.lineTo(s * 0.24, s * 0.46)
    arrow.closeSubpath()
    p.setPen(_pen(fg, s * 0.07))
    p.setBrush(acc)
    p.drawPath(arrow)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(fg)
    p.drawRoundedRect(QRectF(s * 0.36, s * 0.74, s * 0.28, s * 0.09), s * 0.03, s * 0.03)


_PAINTERS = {
    "app": _draw_app_icon,
    "home": _draw_home,
    "clipboard": _draw_clipboard,
    "character": _draw_character,
    "shelf": _draw_shelf,
    "text": _draw_text,
    "chat": _draw_chat,
    "notes": _draw_notes,
    "stats": _draw_stats,
    "weather": _draw_weather,
    "controls": _draw_controls,
    "collapse": _draw_collapse,
    "prev": _draw_prev,
    "play": _draw_play,
    "pause": _draw_pause,
    "next": _draw_next,
    "spotify": _draw_spotify,
    "music": _draw_music,
    "timer": _draw_timer,
    "keyboard": _draw_keyboard,
    "volume": _draw_volume,
    "mute": _draw_mute,
    "screenshot": _draw_screenshot,
    "fullscreen": _draw_fullscreen,
    "ocr": _draw_ocr,
    "color": _draw_color,
    "qr": _draw_qr,
    "record": _draw_record,
    "dictate": _draw_dictate,
    "command": _draw_command,
    "settings": _draw_settings,
    "discord": _draw_discord,
    "mic": _draw_mic,
    "mic_mute": _draw_mic_mute,
    "sun": _draw_sun,
    "theme": _draw_theme,
    "lock": _draw_lock,
    "sleep": _draw_sleep,
    "restart": _draw_restart,
    "power": _draw_power,
    "rocket": _draw_rocket,
    "mixer": _draw_mixer,
    "refresh": _draw_refresh,
    "save": _draw_save,
    "plus": _draw_plus,
    "close": _draw_close,
    "shield": _draw_shield,
    "headphones": _draw_headphones,
    "bell": _draw_bell,
    "trash": _draw_trash,
    "copy": _draw_copy,
    "attach": _draw_attach,
    "camera": _draw_camera,
    "globe": _draw_globe,
    "folder": _draw_folder,
    "bolt": _draw_bolt,
    "tools": _draw_tools,
    "warning": _draw_warning,
    "swap": _draw_swap,
    "undo": _draw_undo,
    "pen": _draw_pen,
    "cloud": _draw_cloud,
    "wifi": _draw_wifi,
    "bluetooth": _draw_bluetooth,
    "plane": _draw_plane,
    "desktop": _draw_desktop,
    "download": _draw_download,
    "dnd": _draw_dnd,
    "monitor_reset": _draw_monitor_reset,
    "caps": _draw_caps,
}


def get_pixmap(
    name: str,
    size: int = 18,
    color: str = "#F2F5FA",
    accent: str = "#60A5FA",
) -> QPixmap:
    """Render and cache a crisp bespoke vector pixmap for the requested icon name."""
    key = (name, color, accent, size)
    cached = _PIXMAP_CACHE.get(key)
    if cached is not None and not cached.isNull():
        return cached

    scale = 2
    px = max(12, int(size))
    pix = QPixmap(px * scale, px * scale)
    pix.setDevicePixelRatio(float(scale))
    pix.fill(Qt.GlobalColor.transparent)

    fg = QColor(color)
    if not fg.isValid():
        fg = QColor("#F2F5FA")
    acc = QColor(accent)
    if not acc.isValid():
        acc = QColor("#60A5FA")

    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    drawer = _PAINTERS.get(name)
    if drawer is not None:
        drawer(p, float(px), fg, acc)
    else:
        p.setPen(_pen(fg, px * 0.1))
        p.setBrush(acc)
        p.drawEllipse(QPointF(px * 0.5, px * 0.5), px * 0.25, px * 0.25)
    p.end()

    _PIXMAP_CACHE[key] = pix
    return pix


def get_icon(
    name: str,
    size: int = 18,
    color: str = "#F2F5FA",
    accent: str = "#60A5FA",
) -> QIcon:
    """Return a bespoke vector QIcon suitable for buttons, tabs, and toolbars."""
    return QIcon(get_pixmap(name, size=size, color=color, accent=accent))


def app_pill_icon(accent: str = "#3B82F6") -> QIcon:
    """High-resolution application & tray icon for Dynamic Island — Borh-edition."""
    ic = QIcon()
    for sz in (16, 24, 32, 48, 64):
        ic.addPixmap(get_pixmap("app", size=sz, color="#F2F5FA", accent=accent))
    return ic
