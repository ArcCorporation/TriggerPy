from Services import polygon_service, tws_service


class AppModel:
    def __init__(self):
        self.symbol = None
        self.price = None
        self.stop_loss = None
        self.take_profit = None
        self.expiry = None
        self.strike = None
        self.right = None
        self.orders = []

        self.tws = tws_service.TWSService()
        self.polygon = polygon_service.PolygonService()

    def set_symbol(self, symbol: str):
        """Sembolü seç ve son fiyatı güncelle."""
        self.symbol = symbol.upper()
        self.price = self.polygon.get_last_trade(self.symbol)
        return self.price

    def set_risk(self, stop_loss: float, take_profit: float):
        """Risk parametrelerini ayarla."""
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        return self.stop_loss, self.take_profit

    def set_option(self, expiry: str, strike: float, right: str):
        """Opsiyon kontrat detaylarını ayarla (örn. 20250926, 430.0, 'C')."""
        self.expiry = expiry
        self.strike = strike
        self.right = right.upper()
        return self.expiry, self.strike, self.right

    def place_order(self, action="BUY", quantity=1):
        """Bracket order gönder ve model state’ine ekle."""
        if not all([self.symbol, self.price, self.expiry, self.strike, self.right]):
            raise ValueError("Symbol, option params, or price not set")

        order = self.tws.place_bracket_order(
            symbol=self.symbol,
            expiry=self.expiry,
            strike=self.strike,
            right=self.right,
            action=action,
            quantity=quantity,
            entry_price=self.price,
            take_profit_price=self.take_profit,
            stop_loss_price=self.stop_loss
        )
        self.orders.append(order)
        return order

    def get_state(self):
        """Şu anki app state’i döndür."""
        return {
            "symbol": self.symbol,
            "price": self.price,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "expiry": self.expiry,
            "strike": self.strike,
            "right": self.right,
            "orders": self.orders,
        }
