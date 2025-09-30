import logging
from typing import List, Dict, Optional, Tuple

from Services.tws_service import create_tws_service
from Services.polygon_service import PolygonService
from Services.order_wait_service import OrderWaitService
from Helpers.Order import Order, OrderState


# --- Singleton: GeneralApp ---
class GeneralApp:
    def __init__(self):
        self._tws = None
        self._polygon = None
        self._order_wait = None
        self._connected = False

    def connect(self) -> bool:
        """Connect global services once for all models."""
        try:
            self._tws = create_tws_service()
            self._polygon = PolygonService()
            self._order_wait = OrderWaitService(self._polygon, self._tws)
            if self._tws.connect_and_start():
                self._connected = True
                logging.info("GeneralApp: Services connected")
                return True
            logging.error("GeneralApp: Failed to connect to TWS")
            return False
        except Exception as e:
            logging.error(f"GeneralApp: Connection failed: {e}")
            return False

    def disconnect(self):
        """Disconnect global services once for all models."""
        try:
            if self._tws:
                self._tws.disconnect_gracefully()
            self._tws = None
            self._polygon = None
            self._order_wait = None
            self._connected = False
            logging.info("GeneralApp: Services disconnected")
        except Exception as e:
            logging.error(f"GeneralApp: Disconnection error: {e}")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # --- Wrappers around services ---
    def search_symbol(self, query: str):
        if not self._tws:
            raise RuntimeError("GeneralApp: TWS not connected")
        return self._tws.search_symbol(query)

    def get_snapshot(self, symbol: str):
        if not self._polygon:
            raise RuntimeError("GeneralApp: Polygon not connected")
        return self._polygon.get_snapshot(symbol)

    def get_maturity(self, symbol: str) -> Optional[str]:
        if not self._tws:
            return None
        try:
            maturities = self._tws.get_maturities(symbol)
            return max(maturities['expirations']) if maturities else None
        except Exception as e:
            logging.error(f"GeneralApp: Failed to get maturity for {symbol}: {e}")
            return None

    # expose internal services for AppModel
    @property
    def tws(self):
        return self._tws

    @property
    def polygon(self):
        return self._polygon

    @property
    def order_wait(self):
        return self._order_wait


# Global singleton instance
general_app = GeneralApp()


# --- Per-symbol model ---
class AppModel:
    def __init__(self, symbol: str):
        self._symbol = symbol.upper()
        self._underlying_price: Optional[float] = None
        self._expiry: Optional[str] = None
        self._strike: Optional[float] = None
        self._right: Optional[str] = None
        self._stop_loss: Optional[float] = None
        self._take_profit: Optional[float] = None
        self._orders: List[Order] = []

    @property
    def symbol(self) -> str:
        return self._symbol

    def refresh_market_price(self) -> Optional[float]:
        try:
            self._underlying_price = general_app.polygon.get_last_trade(self._symbol)
            logging.info(f"AppModel[{self._symbol}]: Market price {self._underlying_price}")
            return self._underlying_price
        except Exception as e:
            logging.error(f"AppModel[{self._symbol}]: Failed to get market price: {e}")
            return None

    def set_option_contract(self, expiry: str, strike: float, right: str) -> Tuple[str, float, str]:
        right = right.upper()
        if right in ("CALL", "C"):
            self._right = "C"
        elif right in ("PUT", "P"):
            self._right = "P"
        else:
            raise ValueError("Right must be CALL/PUT or C/P")

        if not self._validate_option_contract(expiry, strike, self._right):
            raise ValueError(f"Invalid option contract: {expiry} {strike} {self._right}")

        self._expiry = expiry
        self._strike = strike
        logging.info(f"AppModel[{self._symbol}]: Contract set {expiry} {strike}{self._right}")
        return self._expiry, self._strike, self._right

    def _validate_option_contract(self, expiry: str, strike: float, right: str) -> bool:
        try:
            maturities = general_app.tws.get_maturities(self._symbol)
            if not maturities:
                return False
            return expiry in maturities['expirations'] and strike in maturities['strikes']
        except Exception as e:
            logging.error(f"AppModel[{self._symbol}]: Contract validation failed: {e}")
            return False

    def get_available_maturities(self) -> List[str]:
        try:
            maturities = general_app.tws.get_maturities(self._symbol)
            return sorted(maturities['expirations']) if maturities else []
        except Exception as e:
            logging.error(f"AppModel[{self._symbol}]: Failed to get maturities: {e}")
            return []

    def _validate_breakout_trigger(self, trigger_price: Optional[float], current_price: float) -> bool:
        if trigger_price is None:
            return True
        if trigger_price <= current_price:
            logging.error(f"AppModel[{self._symbol}]: Breakout violation trigger {trigger_price} <= {current_price}")
            return False
        return True

    def place_option_order(self, action: str = "BUY", quantity: int = 1,
                           trigger_price: Optional[float] = None) -> Dict:
        if not all([self._symbol, self._expiry, self._strike, self._right]):
            raise ValueError("Option parameters not set")

        current_price = self.refresh_market_price()
        if not current_price:
            raise ValueError("Could not get current market price")

        if not self._validate_breakout_trigger(trigger_price, current_price):
            raise ValueError(f"Trigger {trigger_price} invalid for current price {current_price}")

        order = Order(
            symbol=self._symbol,
            expiry=self._expiry,
            strike=self._strike,
            right=self._right,
            qty=quantity,
            entry_price=0.10,
            tp_price=self._take_profit,
            sl_price=self._stop_loss,
            action=action.upper(),
            trigger=trigger_price
        )

        if not trigger_price or order.is_triggered(current_price):
            success = general_app.tws.place_custom_order(order)
            if success:
                order.mark_active(result=f"IB Order ID: {getattr(order, '_ib_order_id', 'Unknown')}")
                logging.info(f"AppModel[{self._symbol}]: Order executed {order.order_id}")
            else:
                order.mark_failed("Failed to place order")
                logging.error(f"AppModel[{self._symbol}]: Order failed {order.order_id}")
        else:
            general_app.order_wait.add_order(order)
            logging.info(f"AppModel[{self._symbol}]: Order waiting breakout {order.order_id}")

        self._orders.append(order)
        return order.to_dict()

    def get_available_strikes(self, expiry: str) -> List[float]:
        try:
            maturities = general_app.tws.get_maturities(self._symbol)
            return maturities['strikes'] if maturities and expiry in maturities['expirations'] else []
        except Exception as e:
            logging.error(f"AppModel[{self._symbol}]: Failed to get strikes: {e}")
            return []

    def cancel_pending_order(self, order_id: str) -> bool:
        for order in self._orders:
            if order.order_id == order_id and order.state == OrderState.PENDING:
                general_app.order_wait.cancel_order(order_id)
                order.mark_cancelled()
                logging.info(f"AppModel[{self._symbol}]: Order cancelled {order_id}")
                return True
        return False

    def get_orders(self, state_filter: Optional[str] = None) -> List[Dict]:
        if state_filter:
            return [o.to_dict() for o in self._orders if o.state.value == state_filter]
        return [o.to_dict() for o in self._orders]

    def reset(self):
        self._underlying_price = None
        self._expiry = None
        self._strike = None
        self._right = None
        self._stop_loss = None
        self._take_profit = None
        self._orders.clear()
        logging.info(f"AppModel[{self._symbol}]: State reset")


# --- Per-symbol model registry ---
_models: Dict[str, AppModel] = {}

def get_model(symbol: str) -> AppModel:
    s = symbol.upper()
    if s not in _models:
        _models[s] = AppModel(s)
    return _models[s]
