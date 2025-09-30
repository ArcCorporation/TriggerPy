import uuid
import enum
from ibapi.order import Order as IBOrder  # import IB's order class


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
        if self.right == "CALL" or self.right == "C":
            return market_price > self.trigger
        elif self.right == "PUT"or self.right == "P":
            return market_price < self.trigger
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

    def to_ib_order(self, order_type="LMT", limit_price=None, stop_price=None,
                    parent_id=None, transmit=True) -> IBOrder:
        """
        Convert this custom Order into an Interactive Brokers IBOrder.
        - order_type: "MKT", "LMT", or "STP"
        - limit_price: used if order_type == "LMT"
        - stop_price: used if order_type == "STP"
        - parent_id: used if this order is part of a bracket
        - transmit: whether to transmit immediately
        """
        ib_order = IBOrder()
        ib_order.action = self.action
        ib_order.totalQuantity = self.qty
        ib_order.orderType = order_type

        if order_type == "LMT" and limit_price is not None:
            ib_order.lmtPrice = limit_price
        if order_type == "STP" and stop_price is not None:
            ib_order.auxPrice = stop_price

        if parent_id is not None:
            ib_order.parentId = parent_id

        ib_order.transmit = transmit
        ib_order.eTradeOnly = False
        ib_order.firmQuoteOnly = False

        return ib_order
