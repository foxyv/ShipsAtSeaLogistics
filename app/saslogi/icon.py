"""The application icon, drawn in code so there is no image file to ship."""

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap, QPolygonF


def make_icon() -> QIcon:
    """A cargo boat carrying crates on a sea-blue tile."""
    size = 256
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)

    tile = QPainterPath()
    tile.addRoundedRect(QRectF(8, 8, 240, 240), 48, 48)
    painter.setClipPath(tile)
    painter.fillRect(0, 0, size, size, QColor("#12456b"))
    painter.fillRect(0, 178, size, 78, QColor("#1f77a8"))  # sea

    for x, y, colour in ((66, 98, "#f2a33a"), (112, 98, "#e8743b"), (158, 98, "#f2a33a"), (89, 54, "#e8743b"), (135, 54, "#f2a33a")):
        painter.setBrush(QColor(colour))
        painter.drawRoundedRect(QRectF(x, y, 40, 40), 5, 5)

    painter.setBrush(QColor("#f4f6f8"))
    painter.drawPolygon(QPolygonF([QPointF(30, 142), QPointF(226, 142), QPointF(196, 196), QPointF(60, 196)]))  # hull
    painter.setBrush(QColor("#c8d3dc"))
    painter.drawRect(QRectF(30, 142, 196, 12))  # deck line

    painter.setBrush(QColor("#1f77a8"))
    for x in range(-10, size, 44):  # waves lapping over the hull
        painter.drawEllipse(QRectF(x, 186, 52, 34))
    painter.end()
    return QIcon(pixmap)
