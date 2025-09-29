# model.py
from Services import polygon_service, tws_service
from Services.order_wait_service import OrderWaitService
from Helpers.Order import Order, OrderState


class AppModel:
    def __init__(self):
        # state
        self.symbol = None
        self.price = None           # underlying stock price
        self.expiry = None
        self.strike = None
        self.right = None           # always "C" or "P"
        self.stop_loss = None
        self.take_profit = None
        self.orders = []

        # services
        self.tws = tws_service.TWSService()
        self.tws.connect_and_run()
        self.polygon = polygon_service.PolygonService()
        self.waiter = OrderWaitService(self.polygon, self.tws)

    # ---------------- Connection ----------------
    def reconnect_broker(self):
        try:
            if hasattr(self.tws, "disconnect"):
                self.tws.disconnect()
            self.tws.connect_and_run()
            return True
        except Exception as e:
            print(f"[AppModel] Reconnect failed: {e}")
            return False

    # ---------------- Symbol & Market ----------------
    def set_symbol(self, symbol: str):
        self.symbol = symbol.upper()
        self.price = self.get_market_price()
        return self.price

    def get_market_price(self):
        if not self.symbol:
            return None
        self.price = self.polygon.get_last_trade(self.symbol)
        return self.price

    # ---------------- Option & Risk ----------------
    def set_option(self, expiry: str, strike: float, right: str):
    # normalize right
        right = right.upper()
        if right in ("CALL", "C"):
            self.right = "C"
        elif right in ("PUT", "P"):
            self.right = "P"
        else:
            raise ValueError("Right must be CALL/PUT or C/P")

        # wait until chain is available
        chain = []
        for _ in range(5):  # retry up to 5 times
            chain = self.get_option_chain(self.symbol, expiry)
            if chain:
                break
            import time; time.sleep(1)

        if not chain:
            raise ValueError(f"No option chain data available for {expiry}")

        if not any(c["strike"] == strike and c["right"] == self.right for c in chain):
            raise ValueError(f"Invalid option: {expiry} {strike} {self.right}")

        self.expiry = expiry
        self.strike = strike
        return self.expiry, self.strike, self.right


    def set_risk(self, stop_loss: float, take_profit: float):
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        return self.stop_loss, self.take_profit

    def set_stop_loss(self, value: float):
        self.stop_loss = value
        return self.stop_loss

    def set_profit_taking(self, percent: float):
        entry_price = self.get_option_price(self.expiry, self.strike, self.right)
        if not entry_price:
            return None
        if self.right == "C":
            self.take_profit = round(entry_price * (1 + percent / 100), 2)
        elif self.right == "P":
            self.take_profit = round(entry_price * (1 + percent / 100), 2)
        return self.take_profit

    def set_breakeven(self):
        entry_price = self.get_option_price(self.expiry, self.strike, self.right)
        if entry_price:
            self.stop_loss = entry_price
        return self.stop_loss

    def calculate_quantity(self, position_size: float, price: float = None):
        if price is None:
            price = self.get_option_price(self.expiry, self.strike, self.right)
        if not price or price <= 0:
            return 0
        return int(position_size // price)

    # ---------------- Options Data via TWS ----------------
    def get_maturities(self, symbol: str):
        expiries = self.tws.get_maturities(symbol)
        return sorted(expiries) if expiries else []

    def get_option_chain(self, symbol: str, expiry: str):
        return self.tws.get_option_chain(symbol, expiry=expiry) or []

    def get_option_price(self, expiry: str, strike: float, right: str):
        """
        Try to find the option premium from TWS chain data.
        """
        chain = self.get_option_chain(self.symbol, expiry)
        for c in chain:
            if c["strike"] == strike and c["right"] == right:
                # IBKR contractDetails doesn’t give bid/ask directly
                # so fall back to strike/right for now
                return c.get("marketPrice") or c.get("bid") or c.get("ask") or 1.0
        return None

    # ---------------- Orders ----------------
    def place_order(self, action="BUY", quantity=1, trigger=None):
        if not self.symbol or not self.expiry or not self.strike or not self.right:
            raise ValueError("Option parameters (symbol/expiry/strike/right) not set")

        entry_price = self.get_option_price(self.expiry, self.strike, self.right)
        if not entry_price:
            raise ValueError("Option contract not found in chain")

        order = Order(
            symbol=self.symbol,
            expiry=self.expiry,
            strike=self.strike,
            right=self.right,
            qty=quantity,
            entry_price=entry_price,
            tp_price=self.take_profit,
            sl_price=self.stop_loss,
            action=action,
            trigger=trigger
        )

        if order.trigger is None or order.is_triggered(self.price):
            result = self.tws.place_bracket_order(order)
            order.mark_active(result)
        else:
            self.waiter.add_order(order)
            order.state = OrderState.PENDING

        self.orders.append(order)
        return order.to_dict()

    def cancel_order(self, order_id: str):
        for o in self.orders:
            if o.order_id == order_id and o.state == OrderState.PENDING:
                self.waiter.cancel_order(order_id)
                o.mark_cancelled()
                return True
        return False

    def invalidate(self):
        self.symbol = None
        self.price = None
        self.expiry = None
        self.strike = None
        self.right = None
        self.stop_loss = None
        self.take_profit = None
        return True

    def list_orders(self, state: str = None):
        if state:
            return [o.to_dict() for o in self.orders if o.state.value == state]
        return [o.to_dict() for o in self.orders]

    def update_order(self, order_id: str, tp_price=None, sl_price=None):
        for o in self.orders:
            if o.order_id == order_id:
                if tp_price is not None:
                    o.tp_price = tp_price
                if sl_price is not None:
                    o.sl_price = sl_price
                return o.to_dict()
        return None

    def get_state(self):
        return {
            "symbol": self.symbol,
            "price": self.price,
            "expiry": self.expiry,
            "strike": self.strike,
            "right": self.right,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "orders": [o.to_dict() for o in self.orders],
        }
