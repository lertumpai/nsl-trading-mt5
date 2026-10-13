from collections.abc import Callable
from datetime import UTC, datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .gui_state import DesktopSnapshot, review_status
from .models import Order

STYLE = """
QWidget { color: #1b3035; font-size: 13px; }
QMainWindow, QWidget#root { background: #f4f7f7; }
QFrame#sidebar { background: #162a30; border: none; }
QLabel#brand { color: #f6fbfb; font-size: 25px; font-weight: 700; }
QLabel#brandCaption { color: #9cb2b8; font-size: 11px; }
QLabel#sideNote { color: #a7bdc2; font-size: 12px; }
QPushButton#nav { background: transparent; color: #acbfc4; text-align: left; padding: 13px 16px; border: none; border-radius: 8px; font-weight: 500; }
QPushButton#nav:checked { background: #29434a; color: #ffffff; }
QPushButton#nav:hover { background: #233a41; color: #ffffff; }
QLabel#heading { font-size: 27px; font-weight: 600; color: #172d32; }
QLabel#muted { color: #74858a; }
QLabel#small { color: #7d8b90; font-size: 11px; }
QLabel#cardTitle { font-size: 16px; font-weight: 600; }
QLabel#statValue { font-size: 23px; font-weight: 600; }
QLabel#marketName { font-size: 21px; font-weight: 600; }
QLabel#emptyTitle { color: #314a50; font-size: 18px; font-weight: 600; }
QLabel#emptyIcon { font-size: 37px; color: #9db3b8; }
QLabel#formLabel { color: #61757b; font-size: 12px; font-weight: 500; }
QFrame#card { background: #ffffff; border: 1px solid #e0e8e9; border-radius: 12px; }
QFrame#divider { background: #e7eded; border: none; max-height: 1px; }
QPushButton { background: #ffffff; border: 1px solid #d9e3e5; border-radius: 7px; padding: 9px 14px; font-weight: 500; }
QPushButton:hover { background: #f0f5f4; border-color: #b4c8c9; }
QPushButton:pressed { background: #e4eeec; }
QPushButton:disabled { color: #9caeb2; background: #f3f6f6; border-color: #e5ecee; }
QPushButton#primary { background: #1a654f; color: #ffffff; border: 1px solid #1a654f; }
QPushButton#primary:hover { background: #14543f; }
QPushButton#primary:disabled { background: #d8e7e2; border-color: #d8e7e2; color: #8ca89e; }
QPushButton#call { background: #eaf5ef; color: #167b50; border-color: #deeee4; }
QPushButton#call:checked { background: #188354; color: #ffffff; border-color: #188354; }
QPushButton#put { background: #fcf0ed; color: #ba5947; border-color: #f2e4df; }
QPushButton#put:checked { background: #bc5847; color: #ffffff; border-color: #bc5847; }
QPushButton#timeframe { padding: 8px 10px; }
QPushButton#timeframe:checked { background: #1a654f; color: white; border-color: #1a654f; }
QPushButton#call:disabled, QPushButton#put:disabled { color: #9caeb2; background: #f3f6f6; border-color: #e5ecee; }
QLineEdit, QComboBox { background: #ffffff; border: 1px solid #d9e3e5; border-radius: 7px; padding: 9px 11px; min-height: 18px; selection-background-color: #c5e4d9; }
QLineEdit:focus, QComboBox:focus { border: 1px solid #3c9273; }
QLineEdit:disabled, QComboBox:disabled { background: #f5f7f7; color: #99a9ad; border-color: #e5ecee; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: white; color: #203b41; selection-background-color: #e5f1ec; selection-color: #18573f; }
QTableWidget { background: #ffffff; border: none; gridline-color: transparent; selection-background-color: #e9f3ee; selection-color: #184c3c; }
QTableWidget::item { padding: 6px 9px; border: none; }
QHeaderView::section { background: #f5f8f8; color: #798c91; border: none; border-bottom: 1px solid #e7eded; padding: 10px 9px; font-size: 11px; font-weight: 600; }
QCheckBox { color: #536c72; spacing: 7px; font-size: 12px; }
QScrollBar:vertical { background: transparent; width: 9px; margin: 0; }
QScrollBar::handle:vertical { background: #cfdbdd; border-radius: 4px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QPlainTextEdit { background: #f6f9f9; border: 1px solid #e0e8e9; border-radius: 8px; padding: 10px; color: #4a646b; }
QDialog { background: #ffffff; }
QToolTip { background: #213d43; color: white; padding: 6px; border: none; }
"""


def label(text: str = "", role: str = "", *, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(wrap)
    if role:
        widget.setObjectName(role)
    return widget


def button(text: str, *, primary: bool = False) -> QPushButton:
    widget = QPushButton(text)
    widget.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        widget.setObjectName("primary")
    return widget


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 20, 20, 20)
    layout.setSpacing(14)
    return frame, layout


def divider() -> QFrame:
    frame = QFrame()
    frame.setObjectName("divider")
    frame.setFixedHeight(1)
    return frame


def table(headers: list[str]) -> QTableWidget:
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.verticalHeader().hide()
    widget.verticalHeader().setDefaultSectionSize(45)
    widget.setShowGrid(False)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    widget.horizontalHeader().setStretchLastSection(True)
    widget.setAlternatingRowColors(False)
    return widget


def local_time(timestamp: float, *, date: bool = False) -> str:
    value = datetime.fromtimestamp(timestamp, UTC).astimezone()
    return value.strftime("%d %b %Y · %H:%M:%S %Z" if date else "%H:%M:%S")


class StatCard(QFrame):
    def __init__(self, title: str, note: str):
        super().__init__()
        self.setObjectName("card")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(7)
        layout.addWidget(label(title, "muted"))
        self.value = label("—", "statValue")
        self.note = label(note, "small")
        layout.addWidget(self.value)
        layout.addWidget(self.note)


class ReviewDialog(QDialog):
    def __init__(
        self, order: Order, cutoff: int, snapshot: Callable[[], DesktopSnapshot], parent: QWidget
    ):
        super().__init__(parent)
        self.order = order
        self._cutoff = cutoff
        self._snapshot = snapshot
        self.setWindowTitle("Review order · NSL IQ Option")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 26, 28, 26)
        layout.setSpacing(16)
        layout.addWidget(label("Review your order", "heading"))
        layout.addWidget(
            label("Confirm the account, stake, and exact expiration.", "muted", wrap=True)
        )
        layout.addWidget(divider())
        for title, value in (
            ("Market", f"{order.symbol} · {order.product.upper()}"),
            ("Direction", order.direction.upper()),
            ("Account", f"{order.mode.upper()} · {order.balance_id}"),
            ("Stake", f"{order.amount} {order.currency}"),
            ("Expiration", local_time(order.expiration, date=True)),
        ):
            row = QHBoxLayout()
            row.addWidget(label(title, "muted"))
            row.addStretch()
            row.addWidget(label(value))
            layout.addLayout(row)
        layout.addWidget(divider())
        self.window_hint = label("", "muted", wrap=True)
        layout.addWidget(self.window_hint)
        actions = QHBoxLayout()
        self.cancel_button = button("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        self.submit_button = button(f"Place {order.mode} order", primary=True)
        self.submit_button.clicked.connect(self._confirm)
        actions.addWidget(self.cancel_button)
        actions.addWidget(self.submit_button)
        layout.addLayout(actions)
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self._tick()

    def _tick(self) -> None:
        allowed, explanation = review_status(self.order, self._snapshot(), self._cutoff)
        self.submit_button.setEnabled(allowed)
        self.window_hint.setText(explanation)

    def _confirm(self) -> None:
        allowed, _ = review_status(self.order, self._snapshot(), self._cutoff)
        if allowed:
            self.submit_button.setEnabled(False)
            self.timer.stop()
            self.accept()
