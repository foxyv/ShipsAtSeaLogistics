"""The map tab: the game's coastline from the depth grid, the ports, the boat and the planned route.

North is up. The game's world X axis runs north and its Y axis east, so a world point (x, y) is drawn
at screen (y, -x). Wheel zooms about the cursor, dragging pans, double-click fits the map again.
"""

from __future__ import annotations

from collections import defaultdict

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from .model import AT_SEA, BOAT
from .searoutes import NAVIGABLE_M, DepthGrid

LAND, SHALLOW, WATER, DEEP, OFF_MAP = "#55664a", "#a9cdd8", "#2f7396", "#1c4d69", "#163c52"
NO_DATA_M = -99  # what the mod writes beyond the edge of the game's height map (and for land over 99 m)
DEEP_M = 50
ROUTE, STOP, PORT, BOAT_COLOUR, INK, PAPER = "#ffd45a", "#ff7a3d", "#f4f6f8", "#ff4f6d", "#10202a", "#f4f6f8"


class MapView(QWidget):
    def __init__(self):
        super().__init__()
        self.grid: DepthGrid | None = None
        self.image: QImage | None = None
        self._pixels = None  # the image draws straight from this buffer, so it has to outlive the image
        self.snap = None
        self.plan = None
        self.routes: dict = {}
        self.scale = 1.0
        self.offset = QPointF(0, 0)
        self.fitted = True  # keep fitting the window until the user zooms or pans
        self._drag = None
        self.setMinimumSize(300, 240)
        self.setMouseTracking(False)

    # ------------------------------------------------------------------------------------------
    # Data

    def set_grid(self, grid: DepthGrid | None):
        if grid is self.grid:
            return
        self.grid = grid
        self.image = None
        if grid:
            # One byte per cell, north at the top: image x runs along world y, image y against world x.
            width, height = grid.rows, grid.cols
            stride = (width + 3) // 4 * 4  # 8-bit image rows are padded to a multiple of four bytes
            classes = bytearray(0 if d <= 0 else 1 if d < NAVIGABLE_M else 2 if d < DEEP_M else 3 for d in grid.depth_m)
            self._mark_off_map(grid, classes)
            pixels = bytearray(stride * height)
            for image_y in range(height):
                column = grid.cols - 1 - image_y
                pixels[image_y * stride:image_y * stride + width] = classes[column::grid.cols]
            self._pixels = bytes(pixels)
            self.image = QImage(self._pixels, width, height, stride, QImage.Format_Indexed8)
            self.image.setColorTable([QColor(c).rgb() for c in (LAND, SHALLOW, WATER, DEEP, OFF_MAP)])
        self.fitted = True
        self.update()

    @staticmethod
    def _mark_off_map(grid: DepthGrid, classes: bytearray):
        """Recolours the strips beyond the game's height map, which would otherwise show as slabs of land.

        Only whole rows and columns of no-data at the very edges are taken, so mountains are left alone.
        """
        depth, cols, rows = grid.depth_m, grid.cols, grid.rows

        def empty_row(row):
            return all(d == NO_DATA_M for d in depth[row * cols:(row + 1) * cols])

        def empty_col(col):
            return all(d == NO_DATA_M for d in depth[col::cols])

        for order, empty, paint in (
            (range(rows), empty_row, lambda row: classes.__setitem__(slice(row * cols, (row + 1) * cols), bytes([4]) * cols)),
            (range(rows - 1, -1, -1), empty_row, lambda row: classes.__setitem__(slice(row * cols, (row + 1) * cols), bytes([4]) * cols)),
            (range(cols), empty_col, lambda col: classes.__setitem__(slice(col, None, cols), bytes([4]) * rows)),
            (range(cols - 1, -1, -1), empty_col, lambda col: classes.__setitem__(slice(col, None, cols), bytes([4]) * rows)),
        ):
            for line in order:
                if not empty(line):
                    break
                paint(line)

    def set_state(self, snap, plan, routes):
        self.snap, self.plan, self.routes = snap, plan, routes or {}
        self.update()

    # ------------------------------------------------------------------------------------------
    # Geometry

    def to_map(self, x: float, y: float) -> QPointF:
        """A world position in map pixels (one pixel per grid cell before zooming)."""
        grid = self.grid
        return QPointF((y - grid.origin_y) / grid.cell_cm, (grid.cols - 1) - (x - grid.origin_x) / grid.cell_cm)

    def to_screen(self, x: float, y: float) -> QPointF:
        point = self.to_map(x, y)
        return QPointF(self.offset.x() + point.x() * self.scale, self.offset.y() + point.y() * self.scale)

    def fit(self):
        if not self.image:
            return
        self.scale = min(self.width() / self.image.width(), self.height() / self.image.height())
        self.offset = QPointF((self.width() - self.image.width() * self.scale) / 2,
                              (self.height() - self.image.height() * self.scale) / 2)

    def leg_points(self, a: str, b: str) -> list[QPointF]:
        """The sea route between two ports as screen points; a straight line when no route is known."""
        first, second = sorted((a, b))
        route = self.routes.get(f"{first}|{second}")
        if route:
            points = route["points"] if a == first else route["points"][::-1]
            return [self.to_screen(x, y) for x, y in points]
        ends = []
        for place in (a, b):
            at = self.snap.boat_location() if place == AT_SEA else getattr(self.snap.ports.get(place), "location", None)
            if at:
                ends.append(at)
        return [self.to_screen(e[0], e[1]) for e in ends] if len(ends) == 2 else []

    # ------------------------------------------------------------------------------------------
    # Interaction

    def resizeEvent(self, event):
        if self.fitted:
            self.fit()

    def wheelEvent(self, event):
        if not self.image:
            return
        factor = 1.25 if event.angleDelta().y() > 0 else 0.8
        new_scale = max(0.2, min(40.0, self.scale * factor))
        cursor = event.position()
        # Keep the point under the cursor where it is.
        self.offset = cursor - (cursor - self.offset) * (new_scale / self.scale)
        self.scale = new_scale
        self.fitted = False
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag = event.position()
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.offset += event.position() - self._drag
            self._drag = event.position()
            self.fitted = False
            self.update()

    def mouseReleaseEvent(self, event):
        self._drag = None
        self.unsetCursor()

    def mouseDoubleClickEvent(self, event):
        self.fitted = True
        self.fit()
        self.update()

    # ------------------------------------------------------------------------------------------
    # Drawing

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(OFF_MAP))
        if not self.image:
            painter.setPen(QColor(PAPER))
            painter.drawText(self.rect(), Qt.AlignCenter | Qt.TextWordWrap,
                             "No map yet.\nThe mod writes the depth map the first time a save is loaded in game.")
            return
        if self.fitted:
            self.fit()
        painter.setRenderHint(QPainter.SmoothPixmapTransform, self.scale < 1.0)
        painter.drawImage(QRectF(self.offset.x(), self.offset.y(), self.image.width() * self.scale,
                                 self.image.height() * self.scale), self.image)
        painter.setRenderHint(QPainter.Antialiasing)
        if self.snap:
            self._draw_route(painter)
            self._draw_ports(painter)
            self._draw_boat(painter)
        self._draw_key(painter)

    def _label(self, painter, anchor: QPointF, text: str, taken: list, must: bool, bold: bool = False):
        """Text on a dark plate, so it reads over land and water alike.

        `taken` holds the plates already drawn. A label that would overlap one is left out unless it
        `must` be shown; zooming in makes room for the rest.
        """
        dx, dy = 9, 4
        font = QFont(painter.font())
        font.setBold(bold)
        painter.setFont(font)
        size = painter.fontMetrics().boundingRect(text)
        plate = QRectF(anchor.x() + dx - 3, anchor.y() + dy - size.height() + 1, size.width() + 7, size.height() + 3)
        if not must and any(plate.intersects(other) for other in taken):
            return
        taken.append(plate)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 32, 42, 200))
        painter.drawRoundedRect(plate, 3, 3)
        painter.setPen(QColor(PAPER))
        painter.drawText(QPointF(anchor.x() + dx, anchor.y() + dy), text)

    def _draw_route(self, painter):
        stops = self.plan.stops if self.plan else []
        painter.setBrush(Qt.NoBrush)
        for index in range(len(stops) - 1):
            points = self.leg_points(stops[index].port, stops[index + 1].port)
            if len(points) < 2:
                continue
            painter.setPen(QPen(QColor(16, 32, 42, 170), 5.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(QPolygonF(points))
            painter.setPen(QPen(QColor(ROUTE), 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.drawPolyline(QPolygonF(points))

    def _draw_ports(self, painter):
        snap = self.snap
        visits = defaultdict(list)
        for number, stop in enumerate(self.plan.stops if self.plan else [], 1):
            visits[stop.port].append(str(number))
        waiting = defaultdict(int)
        for item in snap.undelivered():
            if item.location not in (None, BOAT):
                waiting[item.location] += 1
        placed = [p for p in snap.ports.values() if p.location and any(p.location[:2])]
        for port in sorted(placed, key=lambda p: p.id in visits):  # route stops drawn last, on top
            at = self.to_screen(port.location[0], port.location[1])
            on_route = port.id in visits
            painter.setPen(QPen(QColor(INK), 1.5))
            painter.setBrush(QColor(STOP if on_route else PORT))
            painter.drawEllipse(at, 6.5 if on_route else 4.5, 6.5 if on_route else 4.5)
        taken: list = []
        # Labels: stops on the route first, then the ports with the most cargo waiting.
        for port in sorted(placed, key=lambda p: (p.id not in visits, -waiting[p.id], p.name)):
            at = self.to_screen(port.location[0], port.location[1])
            on_route = port.id in visits
            text = port.name
            if on_route:
                text = f"{', '.join(visits[port.id])}  {port.name}"
            if waiting[port.id]:
                text += f"  ({waiting[port.id]})"
            self._label(painter, at, text, taken, must=on_route, bold=on_route)

    def _draw_boat(self, painter):
        here = self.snap.boat_location()
        if not here:
            return
        at = self.to_screen(here[0], here[1])
        painter.setPen(QPen(QColor(PAPER), 1.5))
        painter.setBrush(QColor(BOAT_COLOUR))
        marker = QPainterPath()
        marker.moveTo(at.x(), at.y() - 9)
        marker.lineTo(at.x() + 7, at.y() + 7)
        marker.lineTo(at.x() - 7, at.y() + 7)
        marker.closeSubpath()
        painter.drawPath(marker)

    def _draw_key(self, painter):
        entries = [(LAND, "Land"), (SHALLOW, f"Under {NAVIGABLE_M} m"), (WATER, "Navigable"), (DEEP, f"Over {DEEP_M} m"),
                   (ROUTE, "Planned route"), (BOAT_COLOUR, "Your boat")]
        font = QFont(painter.font())
        font.setBold(False)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        row = metrics.height() + 4
        width = max(metrics.horizontalAdvance(label) for _, label in entries) + 34
        box = QRectF(10, self.height() - 10 - row * len(entries) - 8, width, row * len(entries) + 8)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 32, 42, 210))
        painter.drawRoundedRect(box, 5, 5)
        for index, (colour, label) in enumerate(entries):
            y = box.top() + 6 + index * row
            painter.setPen(QPen(QColor(PAPER), 0.6))
            painter.setBrush(QColor(colour))
            painter.drawRect(QRectF(box.left() + 8, y + 1, 14, metrics.height() - 4))
            painter.setPen(QColor(PAPER))
            painter.drawText(QPointF(box.left() + 28, y + metrics.ascent()), label)
        # North arrow, top right.
        tip = QPointF(self.width() - 26, 14)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(16, 32, 42, 210))
        painter.drawRoundedRect(QRectF(tip.x() - 14, 6, 28, 46), 5, 5)
        painter.setBrush(QColor(PAPER))
        painter.drawPolygon(QPolygonF([tip, QPointF(tip.x() + 6, tip.y() + 14), QPointF(tip.x() - 6, tip.y() + 14)]))
        painter.setPen(QColor(PAPER))
        painter.drawText(QRectF(tip.x() - 14, tip.y() + 16, 28, 18), Qt.AlignCenter, "N")
