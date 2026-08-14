"""常驻进程：定时采集 + 提供 HTTP。"""
from __future__ import annotations

import logging
import threading
import time

from ccdesk import config
from ccdesk.api import AppState, make_server
from ccdesk.collector import Collector
from ccdesk.ledger import Ledger

POLL_SECONDS = 3.0


def _setup_logging() -> None:
    config.LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(config.LOG_PATH), level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s")


def _collect_forever(collector: Collector) -> None:
    while True:
        try:
            stats = collector.run_once()
            if stats["requests"] or stats["outcomes"] or stats["skipped"] or stats.get("orphans"):
                logging.info("collect %s", stats)
        except Exception:                          # noqa: BLE001 — 采集不得拖垮 daemon
            logging.exception("collector failed")
        time.sleep(POLL_SECONDS)


def main() -> None:
    _setup_logging()
    config.CCDESK_HOME.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(config.LEDGER_PATH, config.LEDGER_BAD_PATH)
    collector = Collector(config.EVENTS_PATH, ledger,
                          config.CCDESK_HOME / "collector.state.json")
    threading.Thread(target=_collect_forever, args=(collector,), daemon=True).start()
    server = make_server(config.API_HOST, config.API_PORT, AppState(ledger, collector))
    logging.info("ccdesk daemon listening on %s:%s", config.API_HOST, config.API_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
