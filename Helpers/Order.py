# Helpers/order.py
import uuid
import enum


class OrderState(enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    CANCELLED = "cancelled"
    FAILED = "failed"


class Order:
    def __init__(self, symbol, expiry, strike, right,
                 qty, entry_price, tp_price, sl_price,
                 action="BUY", trigger=None):
        """
        Temel Order nesnesi.
        """
        self.order_id = str(uuid.uuid4())
        self.symbol = symbol
        self.expiry = expiry
        self.strike = strike
        self.right = right.upper()  # "C" ya da "P" → CALL/PUT
        self.qty = qty
        self.entry_price = entry_price
        self.tp_price = tp_price
        self.sl_price = sl_price
        self.action = action.upper()
        self.trigger = trigger  # float ya da None

        self.state = OrderState.PENDING if trigger else OrderState.ACTIVE
        self.result = None  # finalize edildiğinde TWS’ten dönen order id seti

    def is_triggered(self, market_price: float) -> bool:
        """
        Trigger koşulu sağlandı mı?
        """
        if self.trigger is None:
            return True
        if self.right == "CALL":
            return market_price >= self.trigger
        elif self.right == "PUT":
            return market_price <= self.trigger
        return False

    def mark_active(self, result=None):
        self.state = OrderState.ACTIVE
        self.result = result

    def mark_cancelled(self):
        self.state = OrderState.CANCELLED

    def mark_failed(self, reason=None):
        self.state = OrderState.FAILED
        self.result = reason

    def to_dict(self):
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "expiry": self.expiry,
            "strike": self.strike,
            "right": self.right,
            "qty": self.qty,
            "entry_price": self.entry_price,
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
            "action": self.action,
            "trigger": self.trigger,
            "state": self.state.value,
            "result": self.result,
        }
