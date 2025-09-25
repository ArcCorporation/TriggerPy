from Services import polygon_service, tws_service


class AppModel:
    def __init__(self):
        self.symbol = None
        self.price = None
        self.stop_loss = None
        self.take_profit = None
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

    def place_order(self, action="BUY", quantity=1):
        """Bracket order gönder ve model state’ine ekle."""
        if not self.symbol or not self.price:
            raise ValueError("Symbol or price not set")

        order = self.tws.place_bracket_order(
            symbol=self.symbol,
            action=action,
            quantity=quantity,  # ✅ doğru parametre
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
            "orders": self.orders,
        }
