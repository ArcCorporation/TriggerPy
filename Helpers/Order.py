from typing import Optional
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
                 action="BUY", type = "LMT",trigger=None):
        """
        Temel Order nesnesi.
        """
        self.order_id = str(uuid.uuid4())
        self._position_size: Optional[float] = None   # dollars
        self.type = type
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


    def serialize(self) -> str:
        """
        Serialize the order object into a single string with attributes separated by underscores.
        """
        position_size = self._position_size if self._position_size is not None else "None"
        return f"{self.order_id}_{self.symbol}_{self.expiry}_{self.strike}_{self.right}_{self.qty}_{self.entry_price}_{self.tp_price}_{self.sl_price}_{self.action}_{self.type}_{self.trigger}_{position_size}"

    @classmethod
    def deserialize(cls, serialized_str: str) -> 'Order':
        """
        Deserialize the string back into an Order object.
        """
        parts = serialized_str.split('_')
        if len(parts) != 13:
            raise ValueError("Invalid serialized string format")

        order_id, symbol, expiry, strike, right, qty, entry_price, tp_price, sl_price, action, type, trigger, position_size = parts

        # Convert numeric strings back to their appropriate types
        qty = int(qty)
        entry_price = float(entry_price)
        tp_price = float(tp_price)
        sl_price = float(sl_price) if sl_price != "None" else None
        trigger = float(trigger) if trigger != "None" else None
        position_size = float(position_size) if position_size != "None" else None

        # Create a new Order object with the deserialized values
        order = cls(symbol, expiry, strike, right, qty, entry_price, tp_price, sl_price, action, type, trigger)
        order.order_id = order_id  # Set the original order_id

        # Set the position size if it was provided
        if position_size is not None:
            order.set_position_size(position_size)

        return order

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
    
    def calc_contracts_from_premium(self, premium: float) -> int:
        """Compute integer contracts that fit budget at *live* premium."""
        if self._position_size is None:
            raise RuntimeError("Position size not set")
        if premium <= 0:
            raise ValueError("Premium must be > 0")
        contracts = int(self._position_size // (premium * 100))
        return max(1, contracts)          # at least 1 contract

    def set_position_size(self, dollars: float) -> "Order":
        """Attach dollar budget and return self for chaining."""
        if dollars <= 0:
            raise ValueError("Position size must be > 0")
        self._position_size = dollars
        return self

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
    
    def move_stop_to_breakeven(self) -> bool:
        """
        Set stop loss to entry_price (breakeven).
        Returns True if updated, False if already at breakeven or no SL.
        """
        if self.sl_price is None:
            return False
        if self.sl_price == self.entry_price:
            return False
        self.sl_price = self.entry_price
        return True

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
