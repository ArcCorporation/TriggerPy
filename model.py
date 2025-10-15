import logging
from typing import List, Dict, Optional, Tuple
import uuid
from Services.price_watcher import PriceWatcher
from Services.tws_service import create_tws_service
from Services.polygon_service import polygon_service
from Services.order_wait_service import OrderWaitService
from Helpers.Order import Order, OrderState
import random

# --- Singleton: GeneralApp ---
class GeneralApp:
    def __init__(self):
        self._tws = None
        self._polygon = None
        self._order_wait = None
        self._connected = False
        self._models = set()

    def save(self, filename: Optional[str] = "ARCTRIGGER.DAT") -> str:
        """
        Save all models to ARCTRIGGER.DAT (or given filename) in this format:

            N
            AppModel:...
            [Order:...]
            AppModel:...
            ...

        Returns the filename used.
        """
        lines = [str(len(self._models))]
        for m in self._models:
            serialized = m.serialize()
            for ln in serialized.split("\n"):
                lines.append(ln)

        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            logging.info(f"[GeneralApp.save()] Saved {len(self._models)} models → {filename}")
        except Exception as e:
            logging.error(f"[GeneralApp.save()] Failed to save models: {e}")
            raise

        return filename

    def load(self, filename: Optional[str] = "ARCTRIGGER.DAT") -> None:
        """
        Load models from ARCTRIGGER.DAT (or given filename).
        Each model may consume 1 or 2 lines depending on whether it has an order.
        """
        try:
            with open(filename, "r", encoding="utf-8") as f:
                lines = [ln.strip() for ln in f if ln.strip()]
        except FileNotFoundError:
            logging.warning(f"[GeneralApp.load()] File not found: {filename}")
            return
        except Exception as e:
            logging.error(f"[GeneralApp.load()] Failed to read file: {e}")
            return

        if not lines:
            logging.warning(f"[GeneralApp.load()] File empty: {filename}")
            return

        try:
            count = int(lines[0])
        except Exception as e:
            logging.error(f"[GeneralApp.load()] Invalid header line: {e}")
            return

        self._models.clear()
        idx = 1
        loaded = 0

        while idx < len(lines):
            if not lines[idx].startswith("AppModel:"):
                idx += 1
                continue
            try:
                model, consumed = AppModel.deserialize(lines[idx: idx + 2])
                self._models.add(model)
                loaded += 1
                idx += consumed
            except Exception as e:
                logging.error(f"[GeneralApp.load()] Error parsing model at line {idx}: {e}")
                idx += 1  # skip to next line safely

        logging.info(f"[GeneralApp.load()] Restored {loaded}/{count} models from {filename}")

        # Reattach pending orders (safe)
        for model in self._models:
            if model.order and model.order.state == OrderState.PENDING:
                try:
                    self._order_wait.add_order(model.order, mode="poll")
                    logging.info(f"[GeneralApp.load()] Reattached pending order {model.order.order_id}")
                except Exception as e:
                    logging.error(f"[GeneralApp.load()] Failed to reattach order: {e}")

    def add_model(self, model: "AppModel"):
        self._models.add(model)

    def get_models(self):
        return list(self._models)

    def amount_of_models(self):
        return len(self._models)

    def cancel_order(self, order_id):
        self.order_wait.cancel_order(order_id)

    def get_option_chain(self, symbol: str, expiry: str):
        """
        Wrapper around TWSService.get_option_chain.
        Models call this, never touch TWSService directly.
        """
        if not self._tws:
            raise RuntimeError("GeneralApp: TWS not connected")
        return self._tws.get_option_chain(symbol, expiry)

    def place_custom_order(self, order) -> bool:
        """
        Proxy to TWS place_custom_order.
        Prevents models from touching self._tws directly.
        """
        if not self._tws:
            logging.info("TWS NOT CONNECTED ERR PLACING ORDER")
            return False
        return self._tws.place_custom_order(order)

    def get_option_snapshot(self, symbol: str, expiry: str, strike: float, right: str):
        """
        Wrapper around TWSService.get_option_snapshot.
        Returns {'bid', 'ask', 'last', 'mid'} or None.
        Used by AppModel and risk modules to get live option pricing.
        """
        if not self._tws:
            raise RuntimeError("GeneralApp: TWS not connected")
        return self._tws.get_option_snapshot(symbol, expiry, strike, right)

    def connect(self) -> bool:
        """Connect global services once for all models."""
        try:
            self._tws = create_tws_service()
            self._polygon = polygon_service
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

    def watch_price(self, symbol, update_fn):
        watcher = PriceWatcher(symbol, update_fn, polygon_service)
        return watcher

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
        self._id = str(uuid.uuid4())
        self._symbol = symbol.upper()
        self._underlying_price: Optional[float] = None
        self._expiry: Optional[str] = None
        self._strike: Optional[float] = None
        self._right: Optional[str] = None
        self._stop_loss: Optional[float] = None
        self._take_profit: Optional[float] = None
        self._order: Optional[Order] = None
        self._status_callback: Optional[callable] = None  # added

    # ------------------------------------------------------------------
    def set_status_callback(self, fn):
        """Allow UI (OrderFrame) to supply its _set_status method."""
        if callable(fn):
            self._status_callback = fn
        else:
            self._status_callback = None
    # ------------------------------------------------------------------

    

    @property
    def symbol(self) -> str:
        return self._symbol

    @property
    def order(self):
        return self._order

    def refresh_market_price(self) -> Optional[float]:
        try:
            self._underlying_price = general_app.polygon.get_last_trade(self._symbol)
            logging.info(f"AppModel[{self._symbol}]: Market price {self._underlying_price}")
            return self._underlying_price
        except Exception as e:
            logging.error(f"AppModel[{self._symbol}]: Failed to get market price: {e}")
            return None

    # ---------------- Option & Risk ----------------
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

    def set_risk(self, stop_loss: float, take_profit: float):
        self._stop_loss = stop_loss
        self._take_profit = take_profit
        return self._stop_loss, self._take_profit

    def set_stop_loss(self, value: float):
        self._stop_loss = value
        return self._stop_loss

    def set_profit_taking(self, percent: float):
        entry_price = self.get_option_price(self._expiry, self._strike, self._right)
        if not entry_price:
            return None
        self._take_profit = round(entry_price * (1 + percent / 100), 2)
        return self._take_profit

    def set_breakeven(self):
        entry_price = self.get_option_price(self._expiry, self._strike, self._right)
        if entry_price:
            self._stop_loss = entry_price
        return self._stop_loss

    def calculate_quantity(self, position_size: float, price: float = None):
        if price is None:
            price = self.get_option_price(self._expiry, self._strike, self._right)
        if not price or price <= 0:
            return 0
        return int(position_size // price)

    # ---------------- Options Data via TWS ----------------
    def get_available_maturities(self) -> List[str]:
        try:
            maturities = general_app.tws.get_maturities(self._symbol)
            return sorted(maturities['expirations']) if maturities else []
        except Exception as e:
            logging.error(f"AppModel[{self._symbol}]: Failed to get maturities: {e}")
            return []

    def get_option_chain(self, expiry: str):
        try:
            if not general_app.tws:
                raise RuntimeError("TWS not connected")
            return general_app.get_option_chain(self._symbol, expiry=expiry) or []
        except Exception as e:
            logging.error(f"AppModel[{self._symbol}]: Failed to get option chain: {e}")
            return []

    def get_option_price(self, expiry: str, strike: float, right: str):
        chain = self.get_option_chain(expiry)
        for c in chain:
            if c["strike"] == strike and c["right"] == right:
                price = c.get("marketPrice") or c.get("bid") or c.get("ask")
                if price and price > 0:
                    return price
                else:
                    raise ValueError(f"No valid price for {expiry} {strike} {right}")
        raise ValueError(f"Option {expiry} {strike} {right} not found")

    # ---------------- Orders ----------------
    def _validate_breakout_trigger(self, trigger_price: Optional[float], current_price: float) -> bool:
        if trigger_price is None:
            return True
        if (self._right == "C" and trigger_price <= current_price) or (self._right == "P" and trigger_price >= current_price):
            logging.error(f"AppModel[{self._symbol}]: Breakout violation trigger {trigger_price} vs {current_price}")
            return False
        return True

    def place_option_order(
        self,
        action: str = "BUY",
        position: int = 2000,
        quantity: int = 1,
        trigger_price: Optional[float] = None,
        status_callback=None,
        arcTick=1.07
    ) -> Dict:
        """
        Create & transmit an option order.  
        Fails gracefully if TWS snapshot times out.
        status_callback: optional fn(text, color) to attach directly to order.
        """
        # 1. basic sanity
        if not all([self._symbol, self._expiry, self._strike, self._right]):
            raise ValueError("Option parameters not set")

        current_price = self.refresh_market_price()
        if not current_price:
            raise ValueError("Could not get underlying market price")

        if not self._validate_breakout_trigger(trigger_price, current_price):
            raise ValueError(f"Trigger {trigger_price} invalid for current price {current_price}")

        # 2. live option premium (snapshot) – can time-out
        snapshot = general_app.get_option_snapshot(
            self._symbol, self._expiry, self._strike, self._right
        )
        if snapshot is None or snapshot.get("mid") is None:
            logging.error("place_option_order: TWS snapshot time-out – cannot set TP/SL")
            raise RuntimeError("No option premium available from TWS snapshot")

        mid_premium = snapshot["ask"] * arcTick

        if mid_premium < 3:
            tick = 0.01
        else:
            tick = 0.05

        mid_premium = int(round(mid_premium / tick)) * tick
        mid_premium = round(mid_premium, 2)

        if self._stop_loss is None:
            self._stop_loss = round(mid_premium * 0.8, 2)
        if self._take_profit is None:
            self._take_profit = round(mid_premium * 1.2, 2)

        # 4. build & route order
        order = Order(
            symbol=self._symbol,
            expiry=self._expiry,
            strike=self._strike,
            right=self._right,
            qty=quantity,
            entry_price=mid_premium,
            tp_price=self._take_profit,
            sl_price=self._stop_loss,
            action=action.upper(),
            trigger=trigger_price,
        )
        order.set_position_size(float(position))

        # attach callback if provided either explicitly or via model-level default
        cb = status_callback or self._status_callback
        if cb:
            try:
                order.set_status_callback(cb)
            except Exception as e:
                logging.error(f"Failed to attach status callback: {e}")

        # 5. immediate or waiting execution
        if not trigger_price or order.is_triggered(current_price):
            success = general_app.place_custom_order(order)
            if success:
                order.mark_active(
                    result=f"IB Order ID: {getattr(order, '_ib_order_id', 'Unknown')}"
                )
                logging.info(f"AppModel[{self._symbol}]: Order executed {order.order_id}")
            else:
                order.mark_failed("Failed to place order")
                logging.error(f"AppModel[{self._symbol}]: Order failed {order.order_id}")
        else:
            general_app.order_wait.add_order(order, mode="poll")
            logging.info(
                f"AppModel[{self._symbol}]: Order waiting breakout {order.order_id}"
            )

        self._order = order
        return order.to_dict()

    def get_available_strikes(self, expiry: str) -> List[float]:
        try:
            maturities = general_app.tws.get_maturities(self._symbol)
            return (
                maturities["strikes"]
                if maturities and expiry in maturities["expirations"]
                else []
            )
        except Exception as e:
            logging.error(f"AppModel[{self._symbol}]: Failed to get strikes: {e}")
            return []

    def cancel_pending_order(self, order_id: str) -> bool:
        order = self._order
        if order.order_id == order_id and order.state == OrderState.PENDING:
            general_app.cancel_order(order_id)
            order.mark_cancelled()
            logging.info(f"AppModel[{self._symbol}]: Order cancelled {order_id}")
            return True
        return False

    def get_order(self) -> Optional[Order]:
        return self._order

    def reset(self):
        self._underlying_price = None
        self._expiry = None
        self._strike = None
        self._right = None
        self._stop_loss = None
        self._take_profit = None
        self._order = None
        logging.info(f"AppModel[{self._symbol}]: State reset")

    def get_state(self):
        return {
            "symbol": self._symbol,
            "price": self._underlying_price,
            "expiry": self._expiry,
            "strike": self._strike,
            "right": self._right,
            "stop_loss": self._stop_loss,
            "take_profit": self._take_profit,
            "orders": [o.to_dict() for o in self._orders],
        }
    
    def serialize(self) -> str:
        """
        Serialize model in at most two lines:
        Line 1: AppModel:<id>:<symbol>:<expiry>:<strike>:<right>:<stop_loss>:<take_profit>:<has_order>
        Line 2 (optional): serialized order if present
        """
        expiry = self._expiry or "None"
        strike = self._strike or "None"
        right = self._right or "None"
        sl = self._stop_loss if self._stop_loss is not None else "None"
        tp = self._take_profit if self._take_profit is not None else "None"

        has_order = bool(self._order)
        base_line = (
            f"AppModel:{self._id}:{self._symbol}:{expiry}:{strike}:{right}:{sl}:{tp}:{has_order}"
        )

        if has_order:
            return base_line + "\n" + self._order.serialize()
        else:
            return base_line

    @classmethod
    def deserialize(cls, lines: list[str]) -> "AppModel":
        """
        Deserialize model from one or two lines.
        The first line must start with 'AppModel:'.
        If 'has_order' is True, second line must start with 'Order:'.
        Returns the new model and the number of lines consumed.
        """
        header = lines[0].split(":")
        if len(header) < 9 or header[0] != "AppModel":
            raise ValueError("Invalid AppModel serialization")

        _, mid, symbol, expiry, strike, right, sl, tp, has_order = header
        model = cls(symbol)
        model._id = mid
        model._expiry = None if expiry == "None" else expiry
        model._strike = None if strike == "None" else float(strike)
        model._right = None if right == "None" else right
        model._stop_loss = None if sl == "None" else float(sl)
        model._take_profit = None if tp == "None" else float(tp)

        has_order = has_order == "True"
        consumed = 1

        if has_order:
            if len(lines) < 2 or not lines[1].startswith("Order:"):
                raise ValueError("Missing Order line after AppModel")
            model._order = Order.deserialize(lines[1])
            consumed = 2

        return model, consumed



# --- Per-symbol model registry ---
_models: Dict[str, AppModel] = {}


def get_model(symbol: str) -> AppModel:
    s = symbol.upper()
    if s not in _models:
        _models[s] = AppModel(s)
        general_app.add_model(_models[s])
    return _models[s]
