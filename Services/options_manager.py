import threading
import time
import logging
from typing import Dict, Optional, Callable
from Services.tws_service import create_tws_service, TWSService
from Services.polygon_service import polygon_service
from Services.order_manager import order_manager
from Services.runtime_manager import runtime_man
from Helpers.Order import Order, OrderState
from Services.nasdaq_info import is_market_closed_or_pre_market


class OptionPosition:
    """
    Represents a live option position for unified monitoring.
    Arc-Tier: includes greeks, pnl, exposure, risk metrics.
    """
    def __init__(self, uuid, data):
        self.uuid = uuid
        self.symbol = data["symbol"]
        self.expiry = data["expiry"]
        self.strike = data["strike"]
        self.right = data["right"]
        self.qty = data["qty"]
        self.avg_price = data["avg_price"]
        self.ib_id = data.get("ib_id")

        # dynamic fields (populated every refresh cycle)
        self.bid = None
        self.ask = None
        self.mid = None
        self.last = None

        self.delta = None
        self.gamma = None
        self.theta = None
        self.vega = None

        self.unrealized_pnl = None
        self.realized_pnl = 0
        self.exposure = None
        self.timestamp = 0

        self.status = "OPEN"
        self.last_update = 0
        self.stale = False

        self._tp_triggered = False
        self._sl_triggered = False

    def to_dict(self):
        return {
            "uuid": self.uuid,
            "symbol": self.symbol,
            "expiry": self.expiry,
            "strike": self.strike,
            "right": self.right,
            "qty": self.qty,
            "avg_price": self.avg_price,
            "mid": self.mid,
            "bid": self.bid,
            "ask": self.ask,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "unrealized_pnl": self.unrealized_pnl,
            "realized_pnl": self.realized_pnl,
            "exposure": self.exposure,
            "timestamp": self.timestamp,
            "status": self.status,
            "stale": self.stale
        }


class OptionsManager:
    """
    ARC TIER PORTFOLIO ENGINE
    • Unifies all live option positions
    • Fetches prices + Greeks from IBKR + Polygon
    • Computes P&L, exposure, risk
    • Auto TP/SL execution with configurable thresholds
    • Serves as the SINGLE source of truth for UI + AI agents
    """

    REFRESH_INTERVAL = 0.5     # 500ms
    STALE_TIMEOUT = 10         # seconds

    # ARC TP/SL AUTOMATION THRESHOLDS (%)
    TP_MEDIUM = 0.40
    TP_AGGRESSIVE = 0.70
    SL_FORCE = None            # You can override dynamic SL from order metadata

    def __init__(self, tws: TWSService):
        self.tws = tws
        self.positions: Dict[str, OptionPosition] = {}
        self.lock = threading.Lock()

        self._stop = False
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

        logging.info("[OptionsManager] Started Arc-Tier monitoring engine.")

    # ---------------------------------------------------------------------
    # INTERNAL LOOP
    # ---------------------------------------------------------------------

    def _loop(self):
        while runtime_man.is_run() and not self._stop:
            try:
                self.refresh_positions()
            except Exception as e:
                logging.exception(f"[OptionsManager] refresh loop crashed: {e}")
            time.sleep(self.REFRESH_INTERVAL)

    # ---------------------------------------------------------------------
    # SYNCHRONIZE WITH TWS POSITION MAP
    # ---------------------------------------------------------------------

    def refresh_positions(self):
        tws_map = self.tws._positions_by_order_id.copy()

        with self.lock:
            # --- ADD OR UPDATE POSITIONS ---
            for uuid, data in tws_map.items():
                if uuid not in self.positions:
                    self.positions[uuid] = OptionPosition(uuid, data)
                    logging.info(f"[OptionsManager] Tracking new position {uuid}")
                else:
                    self.positions[uuid].qty = data["qty"]
                    self.positions[uuid].avg_price = data["avg_price"]

            # --- REMOVE CLOSED POSITIONS ---
            closed = [uuid for uuid, pos in self.positions.items()
                      if uuid not in tws_map]

            for uuid in closed:
                self.positions[uuid].status = "CLOSED"
                logging.info(f"[OptionsManager] Position closed {uuid}")

            # --- REFRESH MARKET DATA + GREEKS ---
            for pos in self.positions.values():
                if pos.status == "CLOSED":
                    continue
                self._refresh_market_snapshot(pos)
                self._compute_pnl(pos)
                self._risk_check(pos)

    # ---------------------------------------------------------------------
    # MARKET SNAPSHOT WITH FALLBACK LOGIC
    # ---------------------------------------------------------------------

    def _refresh_market_snapshot(self, pos: OptionPosition):
        """
        Hybrid logic:
        • RTH → try TWS first
        • If failure or outside RTH → Polygon snapshot
        • If both fail → stale flag
        """

        now = time.time()
        pos.last_update = now

        use_polygon = is_market_closed_or_pre_market()
        tws_ok = self.tws.is_connected()

        snap = None

        if tws_ok and not use_polygon:
            try:
                tsnap = self.tws.get_option_snapshot(
                    pos.symbol, pos.expiry, pos.strike, pos.right, timeout=1
                )
                if tsnap:
                    snap = tsnap
            except Exception:
                pass

        if not snap:
            try:
                snap = polygon_service.get_option_snapshot(
                    pos.symbol, pos.expiry, pos.strike, pos.right
                )
            except Exception:
                snap = None

        if not snap:
            pos.stale = True
            return

        pos.stale = False
        pos.bid = snap.get("bid")
        pos.ask = snap.get("ask")
        pos.last = snap.get("last")
        pos.mid = snap.get("mid") or (pos.bid + pos.ask) / 2 if pos.bid and pos.ask else pos.last

        # Greeks if Polygon supports it
        greeks = snap.get("greeks")
        if greeks:
            pos.delta = greeks.get("delta")
            pos.gamma = greeks.get("gamma")
            pos.theta = greeks.get("theta")
            pos.vega = greeks.get("vega")

    # ---------------------------------------------------------------------
    # PNL ENGINE
    # ---------------------------------------------------------------------

    def _compute_pnl(self, pos: OptionPosition):
        if pos.stale:
            return

        if not pos.mid:
            return

        pos.unrealized_pnl = (pos.mid - pos.avg_price) * pos.qty * 100
        pos.exposure = pos.delta * pos.qty * 100 if pos.delta else None

    # ---------------------------------------------------------------------
    # RISK / TP / SL CHECKS
    # ---------------------------------------------------------------------

    def _risk_check(self, pos: OptionPosition):
        """
        ARC MODE:
        • SL → immediate forced sell
        • TP > 70% → automatic sell
        • TP > 40% → warning signal
        """

        if pos.stale or pos.unrealized_pnl is None:
            return

        # pct return on premium
        pct = (pos.mid - pos.avg_price) / pos.avg_price

        # STOP LOSS
        if self.SL_FORCE is not None and pct <= -abs(self.SL_FORCE):
            if not pos._sl_triggered:
                pos._sl_triggered = True
                logging.warning(f"[OptionsManager] SL AUTO-SELL {pos.uuid}")
                self._auto_close_position(pos)

        # TAKE PROFITS
        if pct >= self.TP_AGGRESSIVE and not pos._tp_triggered:
            pos._tp_triggered = True
            logging.warning(f"[OptionsManager] AGGRESSIVE TP AUTO-SELL {pos.uuid}")
            self._auto_close_position(pos)
        elif pct >= self.TP_MEDIUM and not pos._tp_triggered:
            logging.info(f"[OptionsManager] Medium TP signal for {pos.uuid}")

    # ---------------------------------------------------------------------
    # AUTO EXECUTION
    # ---------------------------------------------------------------------

    def _auto_close_position(self, pos: OptionPosition):
        """
        Uses OrderManager logic to initiate a full close.
        """
        base = order_manager.finalized_orders.get(pos.uuid)
        if not base:
            logging.error(f"[OptionsManager] auto_close_position: cannot find finalized base order for {pos.uuid}")
            return

        exit_order = order_manager._create_exit_order(base, sell_qty=pos.qty)
        order_manager.issue_sell_order(pos.uuid, pos.qty, exit_order)

    # ---------------------------------------------------------------------
    # PUBLIC API
    # ---------------------------------------------------------------------

    def list_positions(self):
        with self.lock:
            return [p.to_dict() for p in self.positions.values()]

    def get_position(self, uuid):
        with self.lock:
            pos = self.positions.get(uuid)
            return pos.to_dict() if pos else None

    def close_position(self, uuid):
        with self.lock:
            pos = self.positions.get(uuid)
            if not pos:
                return False
        return self._auto_close_position(pos)

    def stop(self):
        self._stop = True


options_manager = OptionsManager(create_tws_service())
