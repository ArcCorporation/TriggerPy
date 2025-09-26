# model.py
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
        self.polygon = polygon_service.PolygonService()
        self.waiter = OrderWaitService(self.polygon, self.tws)

    def set_symbol(self, symbol: str):
        """Sembolü seç ve son fiyatı güncelle."""
        self.symbol = symbol.upper()
        self.price = self.polygon.get_last_trade(self.symbol)
        return self.price

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
            entry_price=self.price,   # şimdilik current price üzerinden
            tp_price=self.take_profit,
            sl_price=self.stop_loss,
            action=action,
            trigger=trigger
        )

        # trigger check
        if order.trigger is None or order.is_triggered(self.price):
            # direkt TWS’e gönder
            result = self.tws.place_bracket_order(
                symbol=order.symbol,
                expiry=order.expiry,
                strike=order.strike,
                right=order.right,
                action=order.action,
                quantity=order.qty,
                entry_price=order.entry_price,
                take_profit_price=order.tp_price,
                stop_loss_price=order.sl_price
            )
            order.mark_active(result)
        else:
            # pending’e at
            self.waiter.add_order(order.to_dict())  # waiter dict format bekliyor
            order.state = OrderState.PENDING

        self.orders.append(order)
        return order.to_dict()

    def get_state(self):
        """Şu anki app state’i döndür."""
        return {
            "symbol": self.symbol,
            "price": self.price,
            "expiry": self.expiry,
            "strike": self.strike,
            "right": self.right,
