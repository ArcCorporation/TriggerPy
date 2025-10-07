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

    def save(self, filename: Optional[str] = None) -> str:
        """
        Persist current _models to disk.
        If filename is None, generate Arc_N.txt with N in 0..1000.
        Returns the filename actually used.
        """
        if filename is None:
            filename = f"Arc_{random.randint(0, 1000)}.txt"
        with open(filename, "w") as f:
            f.write(f"{len(self._models)}\n")
            for m in self._models:
                f.write(m.serialize() + "\n")
        return filename

    def load(self, filename: Optional[str] =  None) -> None:
        """
        Replace current _models with contents of file.
        Clears existing models first.
        """
        if filename == None:
            raise ValueError(f"[GeneralModel.load()]filename is None")
        self._models.clear()
        with open(filename) as f:
            lines = [ln.rstrip("\n") for ln in f if ln.strip()]
        if not lines:
            raise ValueError("empty archive")
        n = int(lines[0])
        for raw in lines[1 : 1 + n]:
            if not raw.startswith("AppModel:"):
                continue
            _, mid, odata = raw.split(":", 2)
            m = AppModel("UNKNOWN")      # symbol will be fixed later if needed
            m._id = mid
            m._order = Order.deserialize(odata) if odata != "None" else None
            self._models.add(m)

        for model in self._models:
            if model.order() != None:
                self._order_wait.add_order(model.order(), "poll")
        

    def add_model(self,model: "AppModel"):
        self._models.add(model)

    def get_models(self):
        return list(self._models)
    
    def serialize(self):
        pass

    def cancel_order(self,order_id):
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


    
    def serialize(self):
        pass

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

    def place_option_order(self, action: str = "BUY", position: int = 2000,quantity: int = 1,
                           trigger_price: Optional[float] = None) -> Dict:
        if not all([self._symbol, self._expiry, self._strike, self._right]):
            raise ValueError("Option parameters not set")

        current_price = self.refresh_market_price()
        if not current_price:
            raise ValueError("Could not get current market price")

        if not self._validate_breakout_trigger(trigger_price, current_price):
            raise ValueError(f"Trigger {trigger_price} invalid for current price {current_price}")


        entry_price = general_app.get_option_snapshot(self._symbol,self._expiry,self._strike, self._right)
        try:
            entry_price = self.get_option_price(self._expiry, self._strike, self._right)
        except Exception:
            pass

        if self._stop_loss is None:
            self._stop_loss = round(entry_price * 0.8, 2)
        if self._take_profit is None:
            self._take_profit = round(entry_price * 1.2, 2)

        order = Order(
            symbol=self._symbol,
            expiry=self._expiry,
            strike=self._strike,
            right=self._right,
            qty=quantity,
            entry_price=entry_price,
            tp_price=self._take_profit,
            sl_price=self._stop_loss,
            action=action.upper(),
            trigger=trigger_price
        )

        order.set_position_size(float(position))

        if not trigger_price or order.is_triggered(current_price):
            success = general_app.place_custom_order(order)
            if success:
                order.mark_active(result=f"IB Order ID: {getattr(order, '_ib_order_id', 'Unknown')}")
                logging.info(f"AppModel[{self._symbol}]: Order executed {order.order_id}")
            else:
                order.mark_failed("Failed to place order")
                logging.error(f"AppModel[{self._symbol}]: Order failed {order.order_id}")
        else:
            general_app.order_wait.add_order(order, mode="poll")
            logging.info(f"AppModel[{self._symbol}]: Order waiting breakout {order.order_id}")

        self._order = order
        #save_ticket({**order.to_dict(), "id": order.order_id})
        return order.to_dict()

    def get_available_strikes(self, expiry: str) -> List[float]:
        try:
            maturities = general_app.tws.get_maturities(self._symbol)
            return maturities['strikes'] if maturities and expiry in maturities['expirations'] else []
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


# --- Per-symbol model registry ---
_models: Dict[str, AppModel] = {}

def get_model(symbol: str) -> AppModel:
    s = symbol.upper()
    if s not in _models:
        _models[s] = AppModel(s)
    return _models[s]
