"""Dependency-light native OHLC chart, with hover, zoom, pan and indicators."""

from datetime import UTC, datetime

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .chart_data import bollinger_bands, moving_average, relative_strength
from .models import CandleSeries


class PriceChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(300, 240)
        self.setMouseTracking(True)
        self.setAccessibleName("Broker price chart. Scroll to zoom; drag to view earlier candles.")
        self.series: CandleSeries | None = None
        self.message = "Select a symbol to load its price chart."
        self.chart_type = "Candles"
        self.sma = False
        self.bands = False
        self.rsi = False
        self.visible_count = 80
        self.offset = 0
        self.hover_index: int | None = None
        self.expiration: int | None = None
        self.cutoff: int | None = None
        self._plot = QRectF()
        self._shown = ()
        self._drag_x: float | None = None
        self._drag_offset = 0

    def clear(self, message: str) -> None:
        self.series = None
        self.message = message
        self.offset = 0
        self.hover_index = None
        self.expiration = self.cutoff = None
        self.update()

    def set_series(self, series: CandleSeries) -> None:
        if self.series is None or (self.series.active_id, self.series.timeframe) != (
            series.active_id,
            series.timeframe,
        ):
            self.offset = 0
            self.hover_index = None
        self.series = series
        self.offset = min(self.offset, max(0, len(series.candles) - self.visible_count))
        self.message = "No price history returned for this symbol and timeframe."
        self.update()

    def zoom(self, factor: float) -> None:
        self.visible_count = max(20, min(120, round(self.visible_count * factor)))
        if self.series:
            self.offset = min(self.offset, max(0, len(self.series.candles) - self.visible_count))
        self.hover_index = None
        self.update()

    def reset_view(self) -> None:
        self.offset = 0
        self.visible_count = 80
        self.hover_index = None
        self.update()

    def wheelEvent(self, event) -> None:
        self.zoom(0.8 if event.angleDelta().y() > 0 else 1.25)
        event.accept()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_x = event.position().x()
            self._drag_offset = self.offset

    def mouseReleaseEvent(self, event) -> None:
        self._drag_x = None

    def mouseMoveEvent(self, event) -> None:
        x = event.position().x()
        if self.series and self._drag_x is not None and self._plot.width() > 0:
            delta = round((x - self._drag_x) / self._plot.width() * (len(self._shown) + 6))
            self.offset = max(
                0,
                min(
                    max(0, len(self.series.candles) - self.visible_count), self._drag_offset + delta
                ),
            )
        self.hover_index = None
        if self._shown and self._plot.contains(event.position()):
            step = self._plot.width() / (len(self._shown) + 6)
            index = int((x - self._plot.left()) / step)
            if 0 <= index < len(self._shown):
                self.hover_index = index
        self.update()

    def leaveEvent(self, event) -> None:
        self.hover_index = None
        self._drag_x = None
        self.update()

    def _price(self, value: float) -> str:
        digits = max(2, min(8, max(-c.close.as_tuple().exponent for c in self.series.candles)))
        return f"{value:.{digits}f}" if abs(value) < 10000 else f"{value:,.2f}"

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#14272e"))
        painter.setPen(QColor("#99b3bc"))
        if self.series is None or not self.series.candles:
            painter.drawText(
                QRectF(self.rect()).adjusted(24, 24, -24, -24),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self.message,
            )
            painter.end()
            return
        data = self.series.candles
        end = len(data) - self.offset
        start = max(0, end - self.visible_count)
        shown = data[start:end]
        self._shown = shown
        rsi_height = 84 if self.rsi else 0
        plot = QRectF(12, 42, self.width() - 105, self.height() - 83 - rsi_height)
        self._plot = plot
        step = plot.width() / (len(shown) + 6)
        x = lambda i: plot.left() + step * (i + 0.5)
        closes = [float(c.close) for c in data]
        sma = moving_average(closes) if self.sma else []
        upper, lower = bollinger_bands(closes) if self.bands else ([], [])
        prices = [float(c.low) for c in shown] + [float(c.high) for c in shown]
        if self.bands:
            prices.extend(v for v in upper[start:end] + lower[start:end] if v is not None)
        low, high = min(prices), max(prices)
        padding = max((high - low) * 0.12, abs(high) * 0.0001, 0.000001)
        low, high = low - padding, high + padding
        y = lambda price: plot.bottom() - (price - low) / (high - low) * plot.height()
        painter.setPen(QColor("#29414a"))
        for index in range(5):
            price = low + (high - low) * index / 4
            yy = y(price)
            painter.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
            painter.setPen(QColor("#94afb8"))
            painter.drawText(QRectF(plot.right() + 8, yy - 9, 88, 18), self._price(price))
            painter.setPen(QColor("#29414a"))

        def line(values, color, mapper=y, width=1.5):
            path = QPainterPath()
            connected = False
            for i, value in enumerate(values[start:end]):
                if value is None:
                    connected = False
                    continue
                point = QPointF(x(i), mapper(value))
                if connected:
                    path.lineTo(point)
                else:
                    path.moveTo(point)
                connected = True
            painter.setPen(QPen(QColor(color), width))
            painter.drawPath(path)

        painter.save()
        painter.setClipRect(plot)
        if self.chart_type == "Line":
            line(closes, "#65c6d7", width=2)
        else:
            for index, candle in enumerate(shown):
                color = QColor("#3ac995" if candle.close >= candle.open else "#ed786a")
                painter.setPen(QPen(color, 1))
                painter.drawLine(
                    QPointF(x(index), y(float(candle.high))),
                    QPointF(x(index), y(float(candle.low))),
                )
                opening, closing = y(float(candle.open)), y(float(candle.close))
                painter.fillRect(
                    QRectF(
                        x(index) - max(1, step * 0.32),
                        min(opening, closing),
                        max(2, step * 0.64),
                        max(1.4, abs(opening - closing)),
                    ),
                    color,
                )
        if self.sma:
            line(sma, "#f4c46c")
        if self.bands:
            line(upper, "#859fe5")
            line(lower, "#859fe5")
        painter.restore()
        if self.offset == 0:
            close_y = y(float(shown[-1].close))
            painter.setPen(QPen(QColor("#65c6d7"), 1, Qt.PenStyle.DotLine))
            painter.drawLine(QPointF(plot.left(), close_y), QPointF(plot.right(), close_y))
            painter.fillRect(QRectF(plot.right() + 3, close_y - 10, 96, 21), QColor("#246078"))
            painter.setPen(QColor("#ffffff"))
            painter.drawText(
                QRectF(plot.right() + 8, close_y - 9, 90, 20), self._price(float(shown[-1].close))
            )
            for timestamp, color, text, label_y in (
                (self.cutoff, "#b7c8ce", "Buy cutoff", 6),
                (self.expiration, "#ed786a", "Expiry", 24),
            ):
                if timestamp is None or timestamp < shown[-1].start:
                    continue
                xx = x(len(shown) - 1 + (timestamp - shown[-1].start) / self.series.timeframe)
                if xx <= plot.right():
                    painter.setPen(QPen(QColor(color), 1, Qt.PenStyle.DashLine))
                    painter.drawLine(QPointF(xx, plot.top()), QPointF(xx, plot.bottom()))
                    painter.drawText(
                        QRectF(min(xx + 3, plot.right() - 65), plot.top() + label_y, 70, 16), text
                    )
        selected = (
            shown[self.hover_index]
            if self.hover_index is not None and self.hover_index < len(shown)
            else shown[-1]
        )
        painter.setPen(QColor("#d9e8eb"))
        painter.drawText(
            QRectF(12, 10, self.width() - 24, 23),
            f"O {selected.open}   H {selected.high}   L {selected.low}   C {selected.close}",
        )
        if self.hover_index is not None and self.hover_index < len(shown):
            painter.setPen(QPen(QColor("#6f8a94"), 1, Qt.PenStyle.DashLine))
            xx = x(self.hover_index)
            painter.drawLine(QPointF(xx, plot.top()), QPointF(xx, plot.bottom()))
        for index in sorted({0, len(shown) // 2, len(shown) - 1}):
            time = datetime.fromtimestamp(shown[index].start, UTC).astimezone()
            text = time.strftime("%d %b" if self.series.timeframe >= 86400 else "%H:%M")
            painter.setPen(QColor("#94afb8"))
            painter.drawText(QRectF(x(index) - 22, plot.bottom() + 5, 55, 20), text)
        if self.rsi:
            rsi_top = plot.bottom() + 32
            mapper = lambda value: rsi_top + (100 - value) / 100 * 55
            painter.setPen(QColor("#94afb8"))
            painter.drawText(QRectF(12, rsi_top - 1, 60, 17), "RSI 14")
            for level in (30, 70):
                yy = mapper(level)
                painter.setPen(QPen(QColor("#425565"), 1, Qt.PenStyle.DotLine))
                painter.drawLine(QPointF(plot.left(), yy), QPointF(plot.right(), yy))
                painter.drawText(QRectF(plot.right() + 8, yy - 8, 35, 16), str(level))
            line(relative_strength(closes), "#b6a0ef", mapper)
        painter.setPen(QColor("#94afb8"))
        time = datetime.fromtimestamp(selected.start, UTC).astimezone().strftime("%d %b %H:%M %Z")
        painter.drawText(
            QRectF(12, self.height() - 24, self.width() - 24, 20),
            f"{time}  ·  {'Historical view' if self.offset else 'Latest candles'}",
        )
        painter.end()
