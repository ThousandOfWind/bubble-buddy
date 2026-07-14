"""Generate packaging/bb.ico + packaging/bb.icns — the BB app icon.

Self-contained: renders the vector icon with an offscreen Qt painter and
assembles PNG-compressed multi-resolution .ico/.icns files by hand (no Pillow needed).

Usage (PowerShell)::

    $env:QT_QPA_PLATFORM = "offscreen"
    .\\.venv\\Scripts\\python.exe tools\\generate_bb_icon.py [--preview preview.png]

The design: a smiling BB face (blue glossy orb) on a blue->violet squircle tile,
with a small white speech-bubble at the top-right holding a 3-bar voice waveform.
"""
from __future__ import annotations

import argparse
import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QBuffer, QIODevice, QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath,
    QPen,
)

INK = "#20304F"
TILE_C1 = "#5B8DEF"
TILE_C2 = "#6E5BEF"
ORB = "#6E97F0"      # solid orb fill (no gradient, per design feedback)
ORB_RIM = "#4E77D8"  # thin darker rim so the orb reads on the tile
ICO_SIZES = [16, 32, 48, 64, 128, 256]
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]


# ---------------------------------------------------------------- primitives
def squircle(x: float, y: float, w: float, h: float, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(x, y, w, h), r, r)
    return path


def bg_tile(p: QPainter, S: float, c1: str, c2: str) -> None:
    grad = QLinearGradient(0, 0, S, S)
    grad.setColorAt(0.0, QColor(c1))
    grad.setColorAt(1.0, QColor(c2))
    p.setBrush(grad)
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(squircle(0, 0, S, S, S * 0.235))


def draw_orb_body(p: QPainter, R: float) -> None:
    """Flat solid-blue sphere centred at the current origin, radius R (no gradient)."""
    p.setPen(QPen(QColor(ORB_RIM), R * 0.055))
    p.setBrush(QColor(ORB))
    p.drawEllipse(QPointF(0, 0), R, R)


def draw_face(p: QPainter, R: float, mouth: float = 0.6, gaze: float = 0.0) -> None:
    """Two dot eyes + a quadratic smile. mouth>0 = smile; gaze shifts eyes.
    Kept small relative to the orb so the features don't dominate the tile
    (smaller 五官 = cleaner, more recognisable mark)."""
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(INK))
    ex, ey, er = R * 0.30, -R * 0.04, R * 0.072
    dx = gaze * R * 0.08
    p.drawEllipse(QPointF(-ex + dx, ey), er, er)
    p.drawEllipse(QPointF(ex + dx, ey), er, er)
    pen = QPen(QColor(INK), R * 0.06, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.BrushStyle.NoBrush)
    # Small, cute V-shaped mouth: two short strokes meeting at a soft point.
    mw, my = R * 0.15, R * 0.13
    path = QPainterPath()
    path.moveTo(-mw, my)
    path.lineTo(0, my + mouth * R * 0.18)
    path.lineTo(mw, my)
    p.drawPath(path)


def small_bubble(p: QPainter, cx, cy, w, h, R, tail_to=None) -> None:
    """A little white speech bubble (centred) holding a 3-bar voice waveform."""
    p.setBrush(QColor("#FFFFFF"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawPath(squircle(cx - w / 2, cy - h / 2, w, h, min(w, h) * 0.34))
    if tail_to is not None:
        tx, ty = tail_to
        tail = QPainterPath()
        tail.moveTo(QPointF(cx - w * 0.12, cy + h * 0.42))
        tail.lineTo(tx, ty)
        tail.lineTo(QPointF(cx + w * 0.16, cy + h * 0.42))
        tail.closeSubpath()
        p.drawPath(tail)
    p.setPen(QPen(QColor(TILE_C1), w * 0.09, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    for k, hh in zip((-1, 0, 1), (0.5, 0.85, 0.5)):
        x = cx + k * w * 0.24
        p.drawLine(QPointF(x, cy - h * 0.22 * hh), QPointF(x, cy + h * 0.22 * hh))


def icon_bb(p: QPainter, S: float) -> None:
    """The final BB app icon: big face + small top-right waveform bubble."""
    bg_tile(p, S, TILE_C1, TILE_C2)
    p.save()
    p.translate(S * 0.47, S * 0.55)
    R = S * 0.31
    draw_orb_body(p, R)
    draw_face(p, R, mouth=0.6, gaze=0.3)
    p.restore()
    small_bubble(p, S * 0.79, S * 0.24, S * 0.24, S * 0.19, S, tail_to=(S * 0.68, S * 0.36))


# ---------------------------------------------------------------- rendering
def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    icon_bb(p, size)
    p.end()
    return img


def png_bytes(size: int) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    render(size).save(buf, "PNG")
    buf.close()
    return bytes(ba)


def build_ico(sizes) -> bytes:
    images = [(s, png_bytes(s)) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, data = b"", b""
    for s, png in images:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        data += png
        offset += len(png)
    return header + entries + data


def build_icns(sizes) -> bytes:
    """Build a modern PNG-backed .icns. macOS accepts the icp4/icp5/icp6/ic07/ic08
    ic09/ic10 chunks produced here."""
    type_for_size = {
        16: b"icp4",
        32: b"icp5",
        64: b"icp6",
        128: b"ic07",
        256: b"ic08",
        512: b"ic09",
        1024: b"ic10",
    }
    chunks = []
    for size in sizes:
        png = png_bytes(size)
        kind = type_for_size[size]
        chunks.append(kind + struct.pack(">I", len(png) + 8) + png)
    body = b"".join(chunks)
    return b"icns" + struct.pack(">I", len(body) + 8) + body


def write_preview(path: str) -> None:
    big, smalls = 220, [64, 40, 28]
    sheet = QImage(520, 300, QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#F2F4F8"))
    p = QPainter(sheet)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.drawImage(30, 40, render(big))
    sx, sy = 30 + big + 40, 46
    for s in smalls:
        p.drawImage(sx, sy, render(s))
        sy += s + 30
    p.end()
    sheet.save(path)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate packaging/bb.ico")
    ap.add_argument("--preview", metavar="PNG", help="also write a preview sheet")
    args = ap.parse_args()

    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    root = Path(__file__).resolve().parents[1]
    packaging = root / "packaging"
    ico = packaging / "bb.ico"
    icns = packaging / "bb.icns"
    ico.write_bytes(build_ico(ICO_SIZES))
    icns.write_bytes(build_icns(ICNS_SIZES))
    print(f"wrote {ico} ({ico.stat().st_size} bytes) sizes={ICO_SIZES}")
    print(f"wrote {icns} ({icns.stat().st_size} bytes) sizes={ICNS_SIZES}")
    # README / docs logo (displayed at 128px; rendered at 2x for crispness).
    logo = root / "assets" / "bb-logo.png"
    logo.parent.mkdir(parents=True, exist_ok=True)
    render(256).save(str(logo))
    print(f"wrote {logo} ({logo.stat().st_size} bytes)")
    if args.preview:
        write_preview(args.preview)
        print(f"wrote {args.preview}")


if __name__ == "__main__":
    main()
