from Services import polygon_service, tws_service
from Services.order_wait_service import OrderWaitService
from Helpers.Order import Order, OrderState


class AppModel:
    def __init__(self):
        # state
        self.symbol = None
        self.price = None
        self.expiry = None
        self.strike = None
        self.right = None
        self.stop_loss = None
        self.take_profit = None
        self.orders = []

        # services
        self.tws = tws_service.TWSService()
        self.tws.connect_and_run()
        self.polygon = polygon_service.PolygonService()
        self.waiter = OrderWaitService(self.polygon, self.tws)

    def reconnect_broker(self):
        """Reconnect broker"""
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
        """Sembolü seç ve son fiyatı güncelle."""
        self.symbol = symbol.upper()
        self.price = self.get_market_price()
        return self.price

    def get_market_price(self):
        """Polygon’dan anlık fiyat çek."""
        if not self.symbol:
            return None
        self.price = self.polygon.get_last_trade(self.symbol)
        return self.price

    # ---------------- Option & Risk ----------------

    def set_option(self, expiry: str, strike: float, right: str):
        """Opsiyon kontrat parametrelerini ayarla."""
        self.expiry = expiry
        self.strike = strike
        self.right = right.upper()  # "C" veya "P"
        return self.expiry, self.strike, self.right

    def set_risk(self, stop_loss: float, take_profit: float):
        """Risk parametrelerini ayarla."""
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        return self.stop_loss, self.take_profit

    def set_stop_loss(self, value: float):
        """UI stop loss preset butonları için (örn: 0.20, 0.50, 1.00)."""
        self.stop_loss = value
        return self.stop_loss

    def set_profit_taking(self, percent: float):
        """Profit taking yüzdesine göre TP fiyatını hesapla."""
        if not self.price:
            self.get_market_price()

        if self.right == "CALL":
            self.take_profit = round(self.price * (1 + percent / 100), 2)
        elif self.right == "PUT":
            self.take_profit = round(self.price * (1 - percent / 100), 2)
        return self.take_profit

    def set_breakeven(self):
        """Stop loss’u entry price’a eşitle (breakeven)."""
        if self.price:
            self.stop_loss = self.price
        return self.stop_loss

    def calculate_quantity(self, position_size: float, price: float = None):
        """Pozisyon büyüklüğüne göre lot hesabı yap (UI: 5K/10K/25K butonları)."""
        if price is None:
            price = self.get_market_price()
        if not price or price <= 0:
            return 0
        qty = int(position_size // price)
        return qty

    # ---------------- Orders ----------------

    def place_order(self, action="BUY", quantity=1, trigger=None):
        """Order yarat, trigger varsa pending’e at, yoksa anında TWS’e gönder."""
        if not self.symbol or not self.expiry or not self.strike or not self.right:
            raise ValueError("Option parameters (symbol/expiry/strike/right) not set")

        # yeni order nesnesi
        order = Order(
            symbol=self.symbol,
            expiry=self.expiry,
            strike=self.strike,
            right=self.right,
            qty=quantity,
            entry_price=self.price,
            tp_price=self.take_profit,
            sl_price=self.stop_loss,
            action=action,
            trigger=trigger
        )

        # trigger check
        if order.trigger is None or order.is_triggered(self.price):
            # direkt TWS’e gönder
            result = self.tws.place_bracket_order(order)
            order.mark_active(result)
        else:
            # pending’e at
            self.waiter.add_order(order)
            order.state = OrderState.PENDING

        self.orders.append(order)
        return order.to_dict()

    def cancel_order(self, order_id: str):
        """Order’ı iptal et (pending ise queue’dan siler)."""
        for o in self.orders:
            if o.order_id == order_id and o.state == OrderState.PENDING:
                self.waiter.cancel_order(order_id)
                o.mark_cancelled()
                return True
        return False

    def invalidate(self):
        """Model’deki tüm geçerli parametreleri temizle (UI: Invalidate)."""
        self.symbol = None
        self.price = None
        self.expiry = None
        self.strike = None
        self.right = None
        self.stop_loss = None
        self.take_profit = None
        return True

    def list_orders(self, state: str = None):
        """Order listesini getir (opsiyonel state filtresiyle)."""
        if state:
            return [o.to_dict() for o in self.orders if o.state.value == state]
        return [o.to_dict() for o in self.orders]

    def update_order(self, order_id: str, tp_price=None, sl_price=None):
        """Mevcut order üzerinde TP/SL güncelle."""
        for o in self.orders:
            if o.order_id == order_id:
                if tp_price is not None:
                    o.tp_price = tp_price
                if sl_price is not None:
                    o.sl_price = sl_price
                return o.to_dict()
        return None

    # ---------------- State ----------------

    def get_state(self):
        """Şu anki app state’i döndür."""
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
