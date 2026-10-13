"""Desktop entry point; the terminal installation does not require Qt."""

import argparse
import logging
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="NSL IQ Option desktop trading workspace")
    parser.add_argument("--journal", type=Path, default=Path(".runtime/orders.sqlite3"))
    parser.add_argument("--auth-style", choices=("modern", "legacy"), default="modern")
    parser.add_argument("--timeout", type=float, default=10)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("timeout must be positive")
    try:
        from PySide6.QtWidgets import QApplication

        from .desktop import TradingWindow
        from .gui_worker import BrokerWorker
    except ImportError:
        parser.exit(
            1,
            "The desktop runtime is missing. Install it with: python -m pip install -e '.[gui]'\n",
        )
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    app = QApplication([sys.argv[0]])
    app.setApplicationName("NSL IQ Option")
    app.setOrganizationName("NSL")
    app.setStyle("Fusion")
    worker = BrokerWorker(args.journal, auth_style=args.auth_style, timeout=args.timeout)
    window = TradingWindow(worker)
    window.show()
    worker.start()
    app.exec()
    worker.shutdown()
    worker.wait()


if __name__ == "__main__":
    main()
