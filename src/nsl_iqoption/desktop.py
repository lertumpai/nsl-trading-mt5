"""Native desktop workspace; GUI callbacks never access the broker client."""

from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

from PySide6.QtCore import QSignalBlocker, Qt, Slot
from PySide6.QtGui import QAction, QCloseEvent, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .errors import BrokerError
from .gui_chart import PriceChart
from .gui_state import DesktopSnapshot, expiration_choices, ticket_input
from .gui_widgets import (
    STYLE,
    ReviewDialog,
    StatCard,
    button,
    card,
    divider,
    label,
    local_time,
    table,
)
from .gui_worker import BrokerWorker
from .models import CHART_TIMEFRAMES, AuthChallenge, Order, OrderState, SessionState, Symbol
from .protocol import expiration_for


class TradingWindow(QMainWindow):
    def __init__(self, worker: BrokerWorker):
        super().__init__()
        self.worker = worker
        self.snapshot = DesktopSnapshot()
        self._ready = False
        self._busy = False
        self._closing = False
        self._closed = False
        self._market_key: tuple[str, str] | None = None
        self._market_signature = None
        self._orders_signature = None
        self._balance_signature = None
        self._unknown_signature = None
        self._pending_ack = False
        self._review: ReviewDialog | None = None
        self._expiry_choices: dict[int, Symbol] = {}
        self._chart_key = None
        self._timeframe = 60
        self.setWindowTitle("NSL IQ Option")
        self.resize(1400, 860)
        self.setMinimumSize(1280, 820)
        font = QFont()
        font.setFamilies(["Inter", "SF Pro Text", "Segoe UI", "DejaVu Sans"])
        font.setPointSize(10)
        self.setFont(font)
        self.setStyleSheet(STYLE)
        self._build()
        self.worker.ready.connect(self._worker_ready)
        self.worker.snapshot.connect(self._on_snapshot)
        self.worker.completed.connect(self._completed)
        self.worker.chart_updated.connect(self._chart_updated)
        self.worker.finished.connect(self._worker_finished)
        self._controls()
        quit_action = QAction("Quit", self)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(self.close)
        self.addAction(quit_action)

    def _build(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(148)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(18, 30, 18, 24)
        side.setSpacing(7)
        side.addWidget(label("NSL", "brand"))
        side.addWidget(label("IQ OPTION", "brandCaption"))
        side.addSpacing(35)
        self.navigation = []
        nav_group = QButtonGroup(self)
        for index, title in enumerate(("Workspace", "Orders", "Account")):
            nav = button(title)
            nav.setObjectName("nav")
            nav.setCheckable(True)
            nav.clicked.connect(lambda checked=False, page=index: self._navigate(page))
            nav_group.addButton(nav, index)
            side.addWidget(nav)
            self.navigation.append(nav)
        self.navigation[0].setChecked(True)
        side.addStretch()
        side.addWidget(label("Manual trading", "sideNote"))
        side.addWidget(label("Binary + turbo", "brandCaption"))
        outer.addWidget(sidebar)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(20, 22, 20, 16)
        body.setSpacing(14)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(5)
        self.heading = label("Trading workspace", "heading")
        self.subtitle = label("Select a market. Review every order.", "muted")
        titles.addWidget(self.heading)
        titles.addWidget(self.subtitle)
        header.addLayout(titles)
        header.addStretch()
        self.connection_badge = label("●  Disconnected")
        self.connect_button = button("Connect account", primary=True)
        self.connect_button.clicked.connect(lambda: self._navigate(2))
        header.addWidget(self.connection_badge)
        header.addSpacing(8)
        header.addWidget(self.connect_button)
        body.addLayout(header)
        self.notice = label(
            "Connect your IQ Option account to load markets and balances.", wrap=True
        )
        self.notice.setMinimumHeight(28)
        body.addWidget(self.notice)
        self.pages = QStackedWidget()
        self.pages.addWidget(self._workspace())
        self.pages.addWidget(self._orders_page())
        self.pages.addWidget(self._account_page())
        body.addWidget(self.pages, 1)
        footer = QHBoxLayout()
        self.activity = label("Ready when you are.", "small")
        footer.addWidget(self.activity)
        footer.addStretch()
        footer.addWidget(label("NSL TRADING", "small"))
        body.addLayout(footer)
        outer.addWidget(content, 1)

    def _workspace(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        stats = QHBoxLayout()
        stats.setSpacing(12)
        self.account_stat = StatCard("Selected account", "Connect an account")
        self.balance_stat = StatCard("Available balance", "Account currency")
        self.markets_stat = StatCard("Markets", "Binary and turbo products")
        self.orders_stat = StatCard("Active orders", "Awaiting final status")
        for widget in (self.account_stat, self.balance_stat, self.markets_stat, self.orders_stat):
            stats.addWidget(widget, 1)
        layout.addLayout(stats)
        panels = QHBoxLayout()
        panels.setSpacing(12)
        market_card, market = card()
        market_card.setFixedWidth(286)
        market.setContentsMargins(12, 16, 12, 16)
        heading = QHBoxLayout()
        heading.addWidget(label("Markets", "cardTitle"))
        heading.addStretch()
        self.refresh_markets = button("Refresh")
        self.refresh_markets.clicked.connect(lambda: self._request("symbols"))
        heading.addWidget(self.refresh_markets)
        market.addLayout(heading)
        filters = QHBoxLayout()
        filters.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search symbols")
        self.search.setAccessibleName("Search markets")
        self.search.textChanged.connect(self._render_markets)
        self.product_filter = QComboBox()
        self.product_filter.addItems(["All products", "Binary", "Turbo"])
        self.product_filter.currentIndexChanged.connect(self._render_markets)
        self.otc_filter = QComboBox()
        self.otc_filter.addItems(["All markets", "OTC only", "Regular only"])
        self.otc_filter.currentIndexChanged.connect(self._render_markets)
        market.addWidget(self.search)
        filters.addWidget(self.product_filter)
        filters.addWidget(self.otc_filter)
        market.addLayout(filters)
        self.open_filter = QCheckBox("Open markets only")
        self.open_filter.toggled.connect(self._render_markets)
        market.addWidget(self.open_filter)
        self.market_stack = QStackedWidget()
        self.market_table = table(["SYMBOL", "PRODUCT", "STATUS", "PAYOUT"])
        self.market_table.setStyleSheet(
            "QHeaderView::section { padding: 9px 4px; } QTableWidget::item { padding: 6px 4px; }"
        )
        self.market_table.setColumnHidden(1, True)
        self.market_table.setColumnWidth(2, 70)
        self.market_table.setColumnWidth(3, 70)
        self.market_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.market_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.market_table.horizontalHeader().setStretchLastSection(False)
        self.market_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.market_table.itemSelectionChanged.connect(self._select_market)
        self.market_stack.addWidget(self.market_table)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = label("◇", "emptyIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.market_empty_title = label("Your markets will appear here", "emptyTitle", wrap=True)
        self.market_empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.market_empty_text = label(
            "Connect your account to browse available instruments.", "muted", wrap=True
        )
        self.market_empty_text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(icon)
        empty_layout.addSpacing(10)
        empty_layout.addWidget(self.market_empty_title)
        empty_layout.addWidget(self.market_empty_text)
        self.market_stack.addWidget(empty)
        self.market_stack.setCurrentIndex(1)
        market.addWidget(self.market_stack, 1)
        self.catalog_note = label("No catalog loaded", "small")
        market.addWidget(self.catalog_note)
        panels.addWidget(market_card)
        panels.addWidget(self._chart_panel(), 1)
        ticket_card, ticket = card()
        ticket_card.setMinimumWidth(244)
        ticket.setContentsMargins(16, 16, 16, 16)
        ticket.setSpacing(7)
        ticket.addWidget(label("Binary options", "cardTitle"))
        self.market_name = label("Select a market", "marketName", wrap=True)
        self.market_detail = label("Choose a binary or turbo instrument.", "muted", wrap=True)
        ticket.addWidget(self.market_name)
        ticket.addWidget(self.market_detail)
        ticket.addWidget(divider())
        ticket.addWidget(label("Account", "formLabel"))
        self.balance_combo = QComboBox()
        self.balance_combo.setPlaceholderText("Connect account first")
        self.balance_combo.setAccessibleName("Trading balance")
        self.balance_combo.currentIndexChanged.connect(self._select_balance)
        ticket.addWidget(self.balance_combo)
        ticket.addWidget(label("Direction", "formLabel"))
        directions = QHBoxLayout()
        self.call_button = button("Higher  ↑")
        self.call_button.setToolTip("CALL: predict a closing price above the opening price")
        self.call_button.setObjectName("call")
        self.put_button = button("Lower  ↓")
        self.put_button.setToolTip("PUT: predict a closing price below the opening price")
        self.put_button.setObjectName("put")
        self.direction_group = QButtonGroup(self)
        for widget in (self.call_button, self.put_button):
            widget.setCheckable(True)
            widget.setMinimumHeight(36)
            widget.toggled.connect(self._controls)
            self.direction_group.addButton(widget)
            directions.addWidget(widget)
        ticket.addLayout(directions)
        self.stake_label = label("Stake", "formLabel")
        ticket.addWidget(self.stake_label)
        self.amount = QLineEdit()
        self.amount.setPlaceholderText("0.00")
        self.amount.setAccessibleName("Stake amount")
        self.amount.textChanged.connect(self._controls)
        ticket.addWidget(self.amount)
        amount_actions = QHBoxLayout()
        for title, action in (("−", "subtract"), ("+", "add"), ("½", "half"), ("2×", "double")):
            control = button(title)
            control.clicked.connect(lambda checked=False, a=action: self._adjust_amount(a))
            amount_actions.addWidget(control)
        ticket.addLayout(amount_actions)
        ticket.addWidget(label("Trade expiry", "formLabel"))
        self.duration = QComboBox()
        self.duration.setPlaceholderText("Select a market first")
        self.duration.setAccessibleName("Expiration duration")
        self.duration.currentIndexChanged.connect(self._controls)
        ticket.addWidget(self.duration)
        self.expiry_hint = label("Choose an expiry for this symbol.", "small", wrap=True)
        ticket.addWidget(self.expiry_hint)
        self.purchase_hint = label("", "small", wrap=True)
        ticket.addWidget(self.purchase_hint)
        self.profit_hint = label("Profit if successful: —", "cardTitle", wrap=True)
        ticket.addWidget(self.profit_hint)
        self.return_hint = label("Payout: unknown", "small", wrap=True)
        ticket.addWidget(self.return_hint)
        self.limits_hint = label("Exact expiration is shown before submission.", "small", wrap=True)
        ticket.addWidget(self.limits_hint)
        self.ack_unknown = QCheckBox("Reviewed unresolved orders")
        self.ack_unknown.setToolTip(
            "Review unresolved orders on the Orders page before allowing a new intent on this account."
        )
        self.ack_unknown.toggled.connect(self._controls)
        self.ack_unknown.hide()
        ticket.addWidget(self.ack_unknown)
        ticket.addStretch()
        self.review_button = button("Review order", primary=True)
        self.review_button.setMinimumHeight(42)
        self.review_button.clicked.connect(self._prepare)
        ticket.addWidget(self.review_button)
        ticket_scroll = QScrollArea()
        ticket_scroll.setFrameShape(QFrame.Shape.NoFrame)
        ticket_scroll.setWidgetResizable(True)
        ticket_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        ticket_scroll.setFixedWidth(266)
        ticket_scroll.setWidget(ticket_card)
        ticket.removeWidget(self.review_button)
        ticket_column = QVBoxLayout()
        ticket_column.setSpacing(8)
        ticket_column.addWidget(ticket_scroll, 1)
        ticket_column.addWidget(self.review_button)
        panels.addLayout(ticket_column)
        layout.addLayout(panels, 1)
        return page

    def _chart_panel(self) -> QWidget:
        frame, layout = card()
        layout.setContentsMargins(14, 16, 14, 14)
        layout.setSpacing(10)
        self.chart_title = label("Price chart", "cardTitle")
        layout.addWidget(self.chart_title)
        timeframes = QHBoxLayout()
        timeframes.setSpacing(4)
        self.timeframe_buttons = {}
        self.timeframe_group = QButtonGroup(self)
        for title, seconds in CHART_TIMEFRAMES:
            control = button(title)
            control.setCheckable(True)
            control.setObjectName("timeframe")
            control.setAccessibleName(f"Chart timeframe {title}")
            control.clicked.connect(lambda checked=False, value=seconds: self._set_timeframe(value))
            self.timeframe_group.addButton(control)
            self.timeframe_buttons[seconds] = control
            timeframes.addWidget(control)
        self.timeframe_buttons[60].setChecked(True)
        timeframes.addStretch()
        layout.addLayout(timeframes)
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.chart_style = QComboBox()
        self.chart_style.addItems(["Candles", "Line"])
        self.chart_style.setAccessibleName("Chart type")
        self.chart_style.currentTextChanged.connect(self._chart_options)
        tools.addWidget(self.chart_style)
        for title, attribute in (("SMA 20", "sma"), ("BB 20", "bands"), ("RSI 14", "rsi")):
            control = QCheckBox(title)
            control.toggled.connect(self._chart_options)
            setattr(self, f"{attribute}_toggle", control)
            tools.addWidget(control)
        tools.addStretch()
        layout.addLayout(tools)
        self.chart = PriceChart()
        layout.addWidget(self.chart, 1)
        view = QHBoxLayout()
        for title, action in (
            ("−", lambda: self.chart.zoom(1.25)),
            ("+", lambda: self.chart.zoom(0.8)),
            ("Latest", self.chart.reset_view),
        ):
            control = button(title)
            control.clicked.connect(action)
            view.addWidget(control)
        view.addStretch()
        positions = button("Positions")
        positions.clicked.connect(lambda: self._navigate(1))
        view.addWidget(positions)
        layout.addLayout(view)
        self.chart_status = label(
            "Broker candles · chart timeframe is independent of trade expiry", "small", wrap=True
        )
        layout.addWidget(self.chart_status)
        self.positions_hint = label("No open positions for this symbol.", "small", wrap=True)
        layout.addWidget(self.positions_hint)
        return frame

    def _set_timeframe(self, seconds: int) -> None:
        self._timeframe = seconds
        self._sync_chart()

    def _chart_options(self) -> None:
        if not hasattr(self, "chart"):
            return
        self.chart.chart_type = self.chart_style.currentText()
        self.chart.sma = self.sma_toggle.isChecked()
        self.chart.bands = self.bands_toggle.isChecked()
        self.chart.rsi = self.rsi_toggle.isChecked()
        self.chart.update()

    def _sync_chart(self) -> None:
        symbol = self._selected_symbol()
        title = next(title for title, value in CHART_TIMEFRAMES if value == self._timeframe)
        self.chart_title.setText(f"{symbol.symbol} · {title}" if symbol else "Price chart")
        key = (
            (self.snapshot.user_id, symbol.active_id, self._timeframe)
            if self._ready and symbol and self.snapshot.state is SessionState.CONNECTED
            else None
        )
        if key == self._chart_key:
            return
        self._chart_key = key
        if key is None:
            self.chart.clear("Connect and select a symbol to load broker price history.")
            self.chart_status.setText("Price history paused · no connected symbol")
            if self._ready:
                self.worker.request("chart", active_id=None)
        else:
            self.chart.clear(f"Loading {symbol.symbol} · {title} candles…")
            self.chart_status.setText("Loading broker price history…")
            self.worker.request("chart", active_id=symbol.active_id, timeframe=self._timeframe)

    @Slot(str, int, object, object)
    def _chart_updated(self, active_id: str, timeframe: int, series: object, error: object) -> None:
        if self._closing or self._chart_key != (self.snapshot.user_id, active_id, timeframe):
            return
        if error:
            self.chart_status.setText(f"Chart unavailable · {error[1]} · retrying")
            if self.chart.series is None:
                self.chart.clear("Broker price history is unavailable. Retrying…")
        else:
            self.chart.set_series(series)
            self.chart_status.setText(
                f"{len(series.candles)} broker candles · sampled {local_time(series.server_time)} · refresh every 3s"
            )

    def _adjust_amount(self, action: str) -> None:
        if not self.amount.isEnabled():
            return
        symbol = self._ticket_symbol()
        balance = self.snapshot.selected_balance()
        if symbol is None or balance is None or balance.currency_digits is None:
            return
        try:
            current = Decimal(self.amount.text().strip() or str(symbol.min_stake or 1))
            if not current.is_finite() or current <= 0:
                return
            values = {
                "add": current + 1,
                "subtract": current - 1,
                "half": current / 2,
                "double": current * 2,
            }
            maximum = min(
                balance.amount, symbol.max_stake if symbol.max_stake is not None else balance.amount
            )
            minimum = (
                symbol.min_stake
                if symbol.min_stake is not None
                else Decimal(1).scaleb(-balance.currency_digits)
            )
            unit = Decimal(1).scaleb(-balance.currency_digits)
            minimum = minimum.quantize(unit, rounding=ROUND_CEILING)
            maximum = maximum.quantize(unit, rounding=ROUND_FLOOR)
            if minimum > maximum:
                return
            value = max(minimum, min(maximum, values[action]))
            value = value.quantize(unit)
            self.amount.setText(f"{value:.{balance.currency_digits}f}")
        except (InvalidOperation, ValueError):
            return

    def _orders_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        frame, content = card()
        heading = QHBoxLayout()
        heading.addWidget(label("Order history", "cardTitle"))
        heading.addStretch()
        self.reconcile_button = button("Refresh selected status")
        self.reconcile_button.clicked.connect(self._reconcile)
        self.review_saved_button = button("Review prepared order")
        self.review_saved_button.clicked.connect(self._review_saved)
        heading.addWidget(self.reconcile_button)
        heading.addWidget(self.review_saved_button)
        content.addLayout(heading)
        self.orders_hint = label(
            "Connect your account to see its recorded orders.", "muted", wrap=True
        )
        content.addWidget(self.orders_hint)
        self.order_table = table(
            ["MARKET", "SIDE", "ACCOUNT", "STAKE", "EXPIRATION", "STATUS", "NET P/L"]
        )
        self.order_table.itemSelectionChanged.connect(self._order_selected)
        content.addWidget(self.order_table, 1)
        self.order_detail = QPlainTextEdit()
        self.order_detail.setReadOnly(True)
        self.order_detail.setPlaceholderText(
            "Select an order to inspect its local ID, broker ID, and status."
        )
        self.order_detail.setMaximumHeight(140)
        content.addWidget(self.order_detail)
        layout.addWidget(frame)
        return page

    def _account_page(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(30)
        intro = QVBoxLayout()
        intro.setSpacing(14)
        intro.addStretch()
        intro.addWidget(label("Your account.\nYour trading workspace.", "heading", wrap=True))
        intro.addWidget(
            label(
                "Connect to view your available markets and account balances. Every trade starts with an order review.",
                "muted",
                wrap=True,
            )
        )
        intro.addSpacing(12)
        intro.addWidget(label("Practice balance selected by default", "cardTitle", wrap=True))
        intro.addWidget(label("Real balances require an explicit selection.", "muted", wrap=True))
        intro.addStretch()
        layout.addLayout(intro, 1)
        frame, form = card()
        frame.setFixedWidth(405)
        frame.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        form.setContentsMargins(28, 28, 28, 28)
        form.setSpacing(16)
        form.addWidget(label("Connect to IQ Option", "cardTitle"))
        form.addWidget(label("Sign in with your account credentials.", "muted", wrap=True))
        form.addWidget(label("Email or identifier", "formLabel"))
        self.identifier = QLineEdit()
        self.identifier.setPlaceholderText("you@example.com")
        self.identifier.setAccessibleName("IQ Option identifier")
        form.addWidget(self.identifier)
        form.addWidget(label("Password", "formLabel"))
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.password.setAccessibleName("IQ Option password")
        self.password.returnPressed.connect(self._login)
        form.addWidget(self.password)
        self.show_password = QCheckBox("Show password")
        self.show_password.toggled.connect(
            lambda show: self.password.setEchoMode(
                QLineEdit.EchoMode.Normal if show else QLineEdit.EchoMode.Password
            )
        )
        form.addWidget(self.show_password)
        self.login_button = button("Connect account", primary=True)
        self.login_button.clicked.connect(self._login)
        self.logout_button = button("Disconnect")
        self.logout_button.clicked.connect(lambda: self._request("logout"))
        form.addWidget(self.login_button)
        form.addWidget(self.logout_button)
        form.addWidget(divider())
        form.addWidget(
            label(
                "Your password is kept in memory for sign-in and isn’t saved to disk.",
                "small",
                wrap=True,
            )
        )
        form.addWidget(
            label("Additional verification codes aren’t supported yet.", "small", wrap=True)
        )
        layout.addWidget(frame, alignment=Qt.AlignmentFlag.AlignVCenter)
        return page

    def _navigate(self, page: int) -> None:
        self.pages.setCurrentIndex(page)
        self.navigation[page].setChecked(True)
        titles = (
            ("Trading workspace", "Select a market. Review every order."),
            ("Orders", "Broker status and recorded outcomes."),
            ("Account", "Manage your IQ Option connection."),
        )
        self.heading.setText(titles[page][0])
        self.subtitle.setText(titles[page][1])

    def _notify(self, text: str, *, error: bool = False) -> None:
        self.notice.setText(text)
        self.notice.setStyleSheet("color: #a8483c;" if error else "color: #3d6960;")

    def _request(self, operation: str, **payload: object) -> bool:
        if self._busy or self._closing or not self._ready:
            return False
        self._busy = True
        self._controls()
        messages = {
            "login": "Connecting your account…",
            "symbols": "Refreshing broker markets…",
            "balance": "Selecting account…",
            "prepare": "Preparing your order…",
            "submit": "Submitting once; waiting for broker acknowledgement…",
            "reconcile": "Checking recorded broker status…",
            "logout": "Disconnecting…",
        }
        self.activity.setText(messages.get(operation, "Working…"))
        if not self.worker.request(operation, **payload):
            self._busy = False
            self._controls()
            self._notify("The broker worker is unavailable. Restart the application.", error=True)
            return False
        return True

    @Slot()
    def _worker_ready(self) -> None:
        self._ready = True
        self._controls()
        self._sync_chart()

    @Slot(object)
    def _on_snapshot(self, snapshot: DesktopSnapshot) -> None:
        self.snapshot = snapshot
        balance = snapshot.selected_balance()
        self.account_stat.value.setText(balance.mode.title() if balance else "—")
        self.account_stat.note.setText(
            f"Balance {balance.id}" if balance else "Connect or select an account"
        )
        digits = balance.currency_digits if balance and balance.currency_digits is not None else 2
        self.balance_stat.value.setText(f"{balance.amount:,.{digits}f}" if balance else "—")
        self.balance_stat.note.setText(balance.currency if balance else "Account currency")
        self.markets_stat.value.setText(str(len(snapshot.symbols)) if snapshot.catalog_at else "—")
        active = sum(
            o.state in (OrderState.ACCEPTED, OrderState.UNKNOWN, OrderState.SUBMITTING)
            for o in snapshot.orders
        )
        self.orders_stat.value.setText(str(active) if snapshot.user_id else "—")
        state = snapshot.state.value.replace("_", " ").title()
        self.connection_badge.setText(f"●  {state}")
        connected = snapshot.state is SessionState.CONNECTED
        self.connection_badge.setStyleSheet(
            f"background: {'#e2f1e9' if connected else '#e7edef'}; color: {'#247354' if connected else '#6b8087'}; border-radius: 13px; padding: 7px 11px;"
        )
        self.connect_button.setText("Manage account" if snapshot.user_id else "Connect account")
        signature = (snapshot.balances, snapshot.selected_balance_id)
        if signature != self._balance_signature:
            self._balance_signature = signature
            with QSignalBlocker(self.balance_combo):
                self.balance_combo.clear()
                for item in snapshot.balances:
                    self.balance_combo.addItem(
                        f"{item.mode.title()} · {item.currency} · {item.id}", item.id
                    )
                self.balance_combo.setCurrentIndex(
                    self.balance_combo.findData(snapshot.selected_balance_id)
                )
                if not snapshot.balances:
                    self.balance_combo.addItem("Connect account first", None)
        unknown = tuple(
            o.local_id
            for o in snapshot.orders
            if o.balance_id == snapshot.selected_balance_id
            and o.state in (OrderState.UNKNOWN, OrderState.SUBMITTING)
        )
        signature = (snapshot.selected_balance_id, unknown)
        if signature != self._unknown_signature:
            self._unknown_signature = signature
            self.ack_unknown.setChecked(False)
        self.ack_unknown.setVisible(bool(unknown))
        self.stake_label.setText(f"Stake · {balance.currency}" if balance else "Stake")
        self._render_markets()
        self._render_orders()
        self._sync_ticket()
        self._controls()
        self._sync_chart()

    def _filtered_symbols(self) -> list[Symbol]:
        search = self.search.text().strip().casefold()
        product = self.product_filter.currentText().casefold()
        otc = self.otc_filter.currentIndex()
        return [
            s
            for s in self.snapshot.symbols
            if (not search or search in f"{s.symbol} {s.display_name} {s.active_id}".casefold())
            and (product == "all products" or s.product.value == product)
            and (otc == 0 or (otc == 1 and s.otc) or (otc == 2 and not s.otc))
            and (not self.open_filter.isChecked() or s.market_status(self.snapshot.now()) == "open")
        ]

    def _render_markets(self) -> None:
        symbols = self._filtered_symbols()
        now = self.snapshot.now()
        signature = tuple((s, s.market_status(now)) for s in symbols)
        if signature != self._market_signature:
            self._market_signature = signature
            with QSignalBlocker(self.market_table):
                self.market_table.setRowCount(len(symbols))
                for row, symbol in enumerate(symbols):
                    status = symbol.market_status(now)
                    values = (
                        f"{symbol.symbol}\n{symbol.product.title()}",
                        symbol.product.title(),
                        status.title(),
                        f"{symbol.payout_percent}%" if symbol.payout_percent is not None else "—",
                    )
                    for column, value in enumerate(values):
                        item = QTableWidgetItem(value)
                        item.setData(
                            Qt.ItemDataRole.UserRole, (symbol.active_id, symbol.product.value)
                        )
                        item.setToolTip(
                            f"{symbol.display_name} · {symbol.product.title()} · {status.title()}"
                        )
                        if column == 2:
                            item.setForeground(QColor("#22835b" if status == "open" else "#8b989b"))
                        self.market_table.setItem(row, column, item)
                    if (symbol.active_id, symbol.product.value) == self._market_key:
                        self.market_table.selectRow(row)
        self.market_stack.setCurrentIndex(0 if symbols else 1)
        if not symbols:
            loaded = self.snapshot.catalog_at is not None
            self.market_empty_title.setText(
                "No matching markets" if loaded else "Your markets will appear here"
            )
            self.market_empty_text.setText(
                "Try another filter or refresh the catalog."
                if loaded
                else "Connect your account to browse available instruments."
            )
        if self.snapshot.catalog_at:
            self.catalog_note.setText(
                f"{len(symbols)} products · refreshed {local_time(self.snapshot.catalog_at)}"
                + (" · stale" if self.snapshot.catalog_stale else "")
            )
        else:
            self.catalog_note.setText("No catalog loaded")

    def _selected_symbol(self) -> Symbol | None:
        return next(
            (
                s
                for s in self.snapshot.symbols
                if (s.active_id, s.product.value) == self._market_key
            ),
            None,
        )

    def _select_market(self) -> None:
        row = self.market_table.currentRow()
        if row >= 0 and self.market_table.item(row, 0):
            self._market_key = tuple(self.market_table.item(row, 0).data(Qt.ItemDataRole.UserRole))
        self._sync_ticket()
        self._controls()
        self._sync_chart()

    def _ticket_symbol(self) -> Symbol | None:
        return self._expiry_choices.get(self.duration.currentData())

    def _sync_ticket(self) -> None:
        symbol = self._selected_symbol()
        self.market_name.setText(symbol.symbol if symbol else "Select a market")
        choices = expiration_choices(symbol, self.snapshot.symbols) if symbol else ()
        self._expiry_choices = dict(choices)
        durations = tuple(self._expiry_choices)
        existing = tuple(self.duration.itemData(index) for index in range(self.duration.count()))
        if durations != existing:
            previous = self.duration.currentData()
            with QSignalBlocker(self.duration):
                self.duration.clear()
                for minutes in durations:
                    self.duration.addItem(f"{minutes}m", minutes)
                if previous in durations:
                    self.duration.setCurrentIndex(self.duration.findData(previous))
                elif durations:
                    available = next(
                        (m for m, s in choices if s.market_status(self.snapshot.now()) == "open"),
                        durations[0],
                    )
                    self.duration.setCurrentIndex(self.duration.findData(available))
        self.market_detail.setText(
            f"{'OTC' if symbol.otc else 'Regular'} · {symbol.display_name}"
            if symbol
            else "Choose a binary options symbol."
        )
        symbol = self._ticket_symbol()
        balance = self.snapshot.selected_balance()
        if symbol and (symbol.min_stake is not None or symbol.max_stake is not None):
            self.limits_hint.setText(
                f"Stake limits: {symbol.min_stake or 'unknown'} – {symbol.max_stake or 'unknown'} {balance.currency if balance else ''}\nExact expiration shown on review."
            )
        else:
            self.limits_hint.setText("Exact expiration is shown before submission.")

    def _update_ticket_preview(self) -> None:
        symbol = self._ticket_symbol()
        balance = self.snapshot.selected_balance()
        now = self.snapshot.now()
        self.chart.expiration = self.chart.cutoff = None
        self.expiry_hint.setText(
            "No supported expiries for this symbol."
            if self._selected_symbol()
            else "Choose a symbol."
        )
        self.purchase_hint.setText("")
        if symbol:
            self.expiry_hint.setText(
                f"{self.duration.currentData()}m expiry · {symbol.market_status(now).title()}"
            )
            if now is None:
                self.purchase_hint.setText("Waiting for fresh broker time.")
            else:
                try:
                    expiration = expiration_for(symbol, self.duration.currentData(), now)
                    self.expiry_hint.setText(
                        f"Expires {local_time(expiration)} · {max(0, int(expiration - now))}s left"
                    )
                    self.purchase_hint.setText(
                        f"Purchase window: {max(0, int(expiration - symbol.purchase_cutoff - now))}s"
                    )
                    self.chart.expiration = expiration
                    self.chart.cutoff = expiration - symbol.purchase_cutoff
                except BrokerError as error:
                    self.purchase_hint.setText(error.message)
        self.chart.update()
        self.profit_hint.setText("Profit if successful: —")
        self.return_hint.setText(
            "Payout: unknown"
            if not self.snapshot.catalog_stale
            else "Payout: waiting for fresh catalog"
        )
        if symbol and symbol.payout_percent is not None and not self.snapshot.catalog_stale:
            self.return_hint.setText(f"Payout: {symbol.payout_percent}%")
            if balance:
                try:
                    stake = Decimal(self.amount.text().strip())
                    if stake.is_finite() and stake > 0:
                        profit = stake * symbol.payout_percent / 100
                        digits = (
                            balance.currency_digits if balance.currency_digits is not None else 2
                        )
                        self.profit_hint.setText(
                            f"Profit if successful:\n≈ {profit:.{digits}f} {balance.currency}"
                        )
                        self.return_hint.setText(
                            f"Payout: {symbol.payout_percent}% · total return ≈ {stake + profit:.{digits}f} {balance.currency}"
                        )
                except InvalidOperation:
                    pass
        selected = self._selected_symbol()
        positions = [
            o
            for o in self.snapshot.orders
            if selected
            and o.active_id == selected.active_id
            and o.balance_id == self.snapshot.selected_balance_id
            and o.state in (OrderState.ACCEPTED, OrderState.SUBMITTING, OrderState.UNKNOWN)
        ]
        lines = []
        for order in positions[:3]:
            remaining = max(0, int(order.expiration - now)) if now is not None else None
            timing = (
                f"{remaining}s left"
                if remaining
                else "Awaiting settlement"
                if remaining == 0
                else "Time unavailable"
            )
            lines.append(
                f"{order.direction.upper()} · {order.amount} {order.currency} · {timing}"
                + (f" · {order.state}" if order.state is not OrderState.ACCEPTED else "")
            )
        self.positions_hint.setText(
            "\n".join(lines) if lines else "No open positions for this symbol."
        )

    def _select_balance(self) -> None:
        balance_id = self.balance_combo.currentData()
        if not balance_id or balance_id == self.snapshot.selected_balance_id or self._busy:
            return
        balance = next((b for b in self.snapshot.balances if b.id == balance_id), None)
        if balance is None:
            return
        real = balance.mode == "real"
        if (
            real
            and QMessageBox.question(
                self,
                "Select real account",
                f"Enable real trading on balance {balance.id} ({balance.currency})?\n\nOrder reviews will show REAL mode.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            with QSignalBlocker(self.balance_combo):
                self.balance_combo.setCurrentIndex(
                    self.balance_combo.findData(self.snapshot.selected_balance_id)
                )
            return
        self._request("balance", balance_id=balance_id, enable_real=real)

    def _login(self) -> None:
        if self._busy or not self._ready:
            return
        identifier = self.identifier.text().strip()
        secret = self.password.text()
        if not identifier or not secret:
            self._notify("Enter your account identifier and password.", error=True)
            return
        self.password.clear()
        self.show_password.setChecked(False)
        self._request("login", identifier=identifier, secret=secret)

    def _prepare(self) -> None:
        symbol = self._ticket_symbol()
        if symbol is None:
            return
        direction = (
            "call" if self.call_button.isChecked() else "put" if self.put_button.isChecked() else ""
        )
        try:
            order_input = ticket_input(
                symbol, self.amount.text(), self.duration.currentData(), direction
            )
        except BrokerError as error:
            self._notify(error.message, error=True)
            return
        self._pending_ack = self.ack_unknown.isChecked()
        self._request("prepare", order_input=order_input, acknowledge_unknown=self._pending_ack)

    def _show_review(self, order: Order, *, acknowledge_unknown: bool = False) -> None:
        acknowledge_unknown = (
            acknowledge_unknown and order.balance_id == self.snapshot.selected_balance_id
        )
        symbol = next(
            (
                s
                for s in self.snapshot.symbols
                if s.active_id == order.active_id and s.product is order.product
            ),
            None,
        )
        if symbol is None:
            self._notify("Refresh the market catalog before reviewing this intent.", error=True)
            return
        dialog = ReviewDialog(order, symbol.purchase_cutoff, lambda: self.snapshot, self)
        self._review = dialog
        result = dialog.exec()
        self._review = None
        if result == QDialog.DialogCode.Accepted:
            self._request(
                "submit", local_id=order.local_id, acknowledge_unknown=acknowledge_unknown
            )

    def _selected_order(self) -> Order | None:
        row = self.order_table.currentRow()
        item = self.order_table.item(row, 0) if row >= 0 else None
        return next(
            (
                o
                for o in self.snapshot.orders
                if item and o.local_id == item.data(Qt.ItemDataRole.UserRole)
            ),
            None,
        )

    def _render_orders(self) -> None:
        if self._orders_signature == self.snapshot.orders:
            return
        self._orders_signature = self.snapshot.orders
        selected = self._selected_order()
        with QSignalBlocker(self.order_table):
            self.order_table.setRowCount(len(self.snapshot.orders))
            for row, order in enumerate(self.snapshot.orders):
                values = (
                    f"{order.symbol} · {order.product.title()}",
                    order.direction.upper(),
                    order.mode.title(),
                    f"{order.amount} {order.currency}",
                    local_time(order.expiration, date=True),
                    order.state.value,
                    f"{order.net_profit:+} {order.currency}"
                    if order.net_profit is not None
                    else "—",
                )
                for column, value in enumerate(values):
                    item = QTableWidgetItem(value)
                    item.setData(Qt.ItemDataRole.UserRole, order.local_id)
                    if column == 5 and order.state is OrderState.UNKNOWN:
                        item.setForeground(QColor("#ad783b"))
                    if column == 6 and order.net_profit is not None:
                        item.setForeground(
                            QColor("#22835b" if order.net_profit >= Decimal(0) else "#ba5947")
                        )
                    self.order_table.setItem(row, column, item)
                if selected and selected.local_id == order.local_id:
                    self.order_table.selectRow(row)
        unknown = sum(o.state is OrderState.UNKNOWN for o in self.snapshot.orders)
        self.orders_hint.setText(
            f"{unknown} unresolved order(s). Check their broker status before placing a replacement."
            if unknown
            else f"{len(self.snapshot.orders)} recorded order(s). Select an order to inspect its details."
            if self.snapshot.user_id
            else "Connect your account to see its recorded orders."
        )
        self._order_selected()

    def _order_selected(self) -> None:
        order = self._selected_order()
        if order:
            self.order_detail.setPlainText(
                f"Local ID: {order.local_id}\nBroker ID: {order.broker_id or 'Unknown'}\nAccount: {order.mode.upper()} · balance {order.balance_id} · {order.currency}\nStatus: {order.state}"
                + (f" · {order.error_code}" if order.error_code else "")
                + f"\nGross return: {order.gross_return if order.gross_return is not None else 'Unknown'} · Net profit: {order.net_profit if order.net_profit is not None else 'Unknown'} {order.currency}"
            )
        else:
            self.order_detail.clear()
        self._controls()

    def _reconcile(self) -> None:
        order = self._selected_order()
        if order:
            self._request("reconcile", local_id=order.local_id)

    def _review_saved(self) -> None:
        order = self._selected_order()
        if order and order.state is OrderState.PREPARED:
            self._show_review(order, acknowledge_unknown=self.ack_unknown.isChecked())

    def _controls(self) -> None:
        if not hasattr(self, "login_button"):
            return
        ready = self._ready and not self._busy and not self._closing
        connected = self.snapshot.state is SessionState.CONNECTED
        for widget in (self.identifier, self.password, self.show_password, self.login_button):
            widget.setEnabled(ready and not connected)
        self.logout_button.setEnabled(ready and self.snapshot.user_id is not None)
        self.refresh_markets.setEnabled(ready and connected)
        self.balance_combo.setEnabled(ready and connected)
        symbol = self._ticket_symbol()
        tradable = bool(
            ready and connected and symbol and symbol.market_status(self.snapshot.now()) == "open"
        )
        for widget in (self.call_button, self.put_button, self.amount):
            widget.setEnabled(tradable)
        self.duration.setEnabled(bool(ready and connected and self._expiry_choices))
        direction = self.call_button.isChecked() or self.put_button.isChecked()
        unresolved = any(
            o.balance_id == self.snapshot.selected_balance_id
            and o.state in (OrderState.UNKNOWN, OrderState.SUBMITTING)
            for o in self.snapshot.orders
        )
        unknown_checked = not unresolved or self.ack_unknown.isChecked()
        self.review_button.setEnabled(
            bool(
                tradable
                and self.snapshot.selected_balance()
                and direction
                and self.amount.text().strip()
                and self.duration.currentData()
                and unknown_checked
            )
        )
        order = self._selected_order()
        self.reconcile_button.setEnabled(
            bool(
                ready
                and connected
                and order
                and order.state in (OrderState.UNKNOWN, OrderState.ACCEPTED)
            )
        )
        self.review_saved_button.setEnabled(
            bool(ready and connected and order and order.state is OrderState.PREPARED)
        )
        self._update_ticket_preview()

    @Slot(str, object, object)
    def _completed(self, operation: str, value: object, error: object) -> None:
        self._busy = False
        self.activity.setText("Ready.")
        self._controls()
        if self._closing:
            return
        if error:
            if operation == "balance":
                with QSignalBlocker(self.balance_combo):
                    self.balance_combo.setCurrentIndex(
                        self.balance_combo.findData(self.snapshot.selected_balance_id)
                    )
            self._notify(f"{error[1]} ({error[0]})", error=True)
            return
        if operation == "login":
            if isinstance(value, AuthChallenge):
                self._notify(value.message, error=True)
            else:
                self._notify("Account connected. Loading your markets…")
                self._navigate(0)
                self._request("symbols")
        elif operation == "prepare":
            self._show_review(value, acknowledge_unknown=self._pending_ack)
        elif operation == "submit":
            self._navigate(1)
            if value.state is OrderState.UNKNOWN:
                self._notify(
                    "Order status is unknown. It will not be resubmitted automatically. Check the broker status here.",
                    error=True,
                )
            elif value.state is OrderState.REJECTED:
                self._notify(f"Broker rejected the order. {value.error_code or ''}", error=True)
            else:
                self._notify(
                    f"Order {value.state.value.lower()} · broker ID {value.broker_id or 'unknown'}."
                )
            for row, order in enumerate(self.snapshot.orders):
                if order.local_id == value.local_id:
                    self.order_table.selectRow(row)
                    break
        elif operation == "symbols":
            self._notify(f"Loaded {len(value.symbols)} binary and turbo products.")
            if self._selected_symbol() is None:
                symbols = self._filtered_symbols()
                row = next(
                    (
                        i
                        for i, s in enumerate(symbols)
                        if s.market_status(self.snapshot.now()) == "open"
                    ),
                    0,
                )
                if symbols:
                    self.market_table.selectRow(row)
        elif operation == "logout":
            self._notify(
                "Disconnected. Remote logout confirmed."
                if value
                else "Disconnected locally; remote logout was not confirmed."
            )
            self._market_key = None
            self._sync_ticket()
            self._sync_chart()
        elif operation == "balance":
            self._notify(f"Selected {value.mode} balance {value.id} · {value.currency}.")
        elif operation == "reconcile":
            self._notify(f"Recorded status: {value.state}.")

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._closed or not self.worker.isRunning():
            event.accept()
            return
        event.ignore()
        if not self._closing:
            self._closing = True
            self.password.clear()
            if self._review is not None:
                self._review.reject()
            self._notify("Closing the broker connection and preserving order status…")
            self.activity.setText("Closing…")
            self._controls()
            self.worker.shutdown()

    @Slot()
    def _worker_finished(self) -> None:
        self._ready = False
        if self._closing:
            self._closed = True
            self.close()
        else:
            self._notify(
                "The broker worker stopped. Restart the application to reconnect.", error=True
            )
            self._controls()
