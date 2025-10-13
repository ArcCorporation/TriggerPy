from typing import Optional
import uuid
import enum
from ibapi.order import Order as IBOrder  # import IB's order class
import logging

class OrderState(enum.Enum):
    PENDING = "pending"
    ACTIVE = "active"
    CANCELLED = "cancelled"
    FAILED = "failed"
    FINALIZED = "finalized"  # ← add this

    @classmethod
    def deserialize(cls, value: str) -> "OrderState":
        """
        Reconstruct OrderState from a string value.
        Defaults to ACTIVE if invalid or unknown.
        """
        try:
            return cls(value)
        except Exception:
            logging.warning(f"Unknown OrderState '{value}', defaulting to ACTIVE")
            return cls.ACTIVE

    def serialize(self) -> str:
        """
        Return the enum’s value as string for compact persistence.
        """
        return self.value



class Order:
    def __init__(self, symbol, expiry, strike, right,
                 qty, entry_price, tp_price, sl_price,
                 action="BUY", type="LMT", trigger=None):
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

    # ----------------------------------------------------------------------
    # Status Callback Handling (added)
    # ----------------------------------------------------------------------
    def set_status_callback(self, fn):
        """
        Attach a direct GUI status updater (e.g. OrderFrame._set_status).
        Expected signature: fn(text: str, color: str)
        """
        if not callable(fn):
            raise ValueError("Callback must be callable")
        self._status_callback = fn
        return self

    def _notify(self, text: str, color: str):
        """Safely invoke attached status callback if present."""
        cb = getattr(self, "_status_callback", None)
        if cb:
            try:
                cb(text, color)
            except Exception as e:
                import logging
                logging.error(f"Order[{self.order_id}] UI callback failed: {e}")

    # ----------------------------------------------------------------------

    def serialize(self) -> str:
        """
        Serialize the order object into a single string with attributes separated by underscores.
        Includes state and result at the end for persistence.
        """
        position_size = self._position_size if self._position_size is not None else "None"
        state_val = self.state.serialize() if self.state else "None"
        result_val = self.result if self.result is not None else "None"

        return (
            f"{self.order_id}_{self.symbol}_{self.expiry}_{self.strike}_{self.right}_"
            f"{self.qty}_{self.entry_price}_{self.tp_price}_{self.sl_price}_"
            f"{self.action}_{self.type}_{self.trigger}_{position_size}_{state_val}_{result_val}"
        )

    @classmethod
    def deserialize(cls, serialized_str: str) -> "Order":
        """
        Deserialize the string back into an Order object.
        Supports both legacy (13-field) and new (15-field) formats.
        """
        parts = serialized_str.split('_')
        if len(parts) not in (13, 15):
            raise ValueError(f"Invalid serialized string format ({len(parts)} parts)")

        # --- unpack common fields ---
        order_id, symbol, expiry, strike, right, qty, entry_price, tp_price, sl_price, \
        action, type, trigger, position_size, *rest = parts

        # --- convert base types ---
        qty = int(qty)
        entry_price = float(entry_price)
        tp_price = float(tp_price)
        sl_price = None if sl_price == "None" else float(sl_price)
        trigger = None if trigger == "None" else float(trigger)
        position_size = None if position_size == "None" else float(position_size)

        # --- create order ---
        order = cls(symbol, expiry, strike, right, qty, entry_price, tp_price,
                    sl_price, action, type, trigger)
        order.order_id = order_id
        if position_size is not None:
            order.set_position_size(position_size)

        # --- handle extended fields (state, result) ---
        if len(rest) == 2:
            state_val, result_val = rest
            order.state = OrderState.deserialize(state_val)
            order.result = None if result_val == "None" else result_val
        else:
            # backward compatibility
            order.state = OrderState.PENDING if trigger else OrderState.ACTIVE
            order.result = None

        return order

    def is_triggered(self, market_price: float) -> bool:
        """
        Trigger koşulu sağlandı mı?
        """
        if self.trigger is None:
            return True
        if self.right == "CALL" or self.right == "C":
            return market_price > self.trigger
        elif self.right == "PUT" or self.right == "P":
            return market_price < self.trigger
        return False

    def calc_contracts_from_premium(self, premium: float) -> int:
        """Compute integer contracts that fit budget at *live* premium."""
        if self._position_size is None:
            raise RuntimeError("Position size not set")
        if premium <= 0:
            raise ValueError("Premium must be > 0")
        contracts = int(self._position_size // (premium * 100))
        return max(1, contracts)  # at least 1 contract

    def set_position_size(self, dollars: float) -> "Order":
        """Attach dollar budget and return self for chaining."""
        if dollars <= 0:
            raise ValueError("Position size must be > 0")
        self._position_size = dollars
        return self

    def mark_active(self, result=None):
        self.state = OrderState.ACTIVE
        self.result = result
        msg = f"Order {self.order_id} Active"
        if result:
            msg += f" – {result}"
        self._notify(msg, "green")

    def mark_cancelled(self):
        self.state = OrderState.CANCELLED
        msg = f"Order {self.order_id} Cancelled"
        self._notify(msg, "gray")

    def mark_failed(self, reason=None):
        self.state = OrderState.FAILED
        self.result = reason
        msg = f"Order {self.order_id} Failed"
        if reason:
            msg += f" – {reason}"
        self._notify(msg, "red")
    
    def mark_finalized(self, result=None):
        self.state = OrderState.FINALIZED
        self.result = result
        msg =f"Order {self.order_id} finalized with result: {result}"
        self._notify(msg, "green")


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
                    parent_id=None, transmit=True,closing=False) -> IBOrder:
        """
        Convert this custom Order into an Interactive Brokers IBOrder.
        - order_type: "MKT", "LMT", or "STP"
        - limit_price: used if order_type == "LMT"
        - stop_price: used if order_type == "STP"
        - parent_id: used if this order is part of a bracket
        - transmit: whether to transmit immediately
        """
        ib_order = IBOrder()
        ib_order.action = ("SELL_TO_CLOSE" if closing and self.action == "BUY" else
                       "BUY_TO_CLOSE"  if closing and self.action == "SELL" else
                       self.action)
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
