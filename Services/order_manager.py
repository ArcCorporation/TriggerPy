import logging
from Services.tws_service import create_tws_service, TWSService
from Helpers.Order import Order
from typing import Optional


class OrderManager:
    def __init__(self, tws_service: TWSService):
        self.tws_service = tws_service
        self.finalized_orders = {}  # Dictionary to hold finalized orders

    def add_finalized_order(self, order_id, order):
        """
        Add a finalized order to the collection for further management.
        """
        self.finalized_orders[order_id] = order
        logging.info(f"Added finalized order {order_id} to management.")
        

    def issue_sell_order(self,
                     base_order_id: str,
                     sell_qty: int,
                     limit_price: Optional[float] = None) -> Optional[str]:
        """
        Create and transmit a **sell** order for an already-finalised long position.

        Parameters
        ----------
        base_order_id : str
            The key under which the original BUY order is stored in `self.finalized_orders`.
        sell_qty : int
            Number of option contracts to sell (must be ≤ original buy qty).
        limit_price : float | None
            Limit price per contract.  
            If **None** → sent as a **market** order (IB will treat it as LMT with no limit).

        Returns
        -------
        str | None
            The new **sell** order ID if TWS accepted the order, otherwise **None**.
        """
        base = self.finalized_orders.get(base_order_id)
        if not base or base.action != "BUY":
            logging.warning("[OrderManager] issue_sell_order: no buy-order %s", base_order_id)
            return None

        if sell_qty <= 0 or sell_qty > base.qty:
            logging.warning("[OrderManager] issue_sell_order: invalid sell qty %s for order %s",
                            sell_qty, base_order_id)
            return None

        sell_order = Order(
            symbol=base.symbol,
            expiry=base.expiry,
            strike=base.strike,
            right=base.right,
            qty=sell_qty,
            action="SELL",
            entry_price=0,          # not used for sell
            limit_price=limit_price
        )
        sell_order.set_position_size(base._position_size)

        ok = self.tws_service.place_custom_order(sell_order)
        if ok:
            logging.info("[OrderManager] sell order placed -> ID %s", sell_order.order_id)
            return sell_order.order_id

        logging.error("[OrderManager] sell order failed for %s", base_order_id)
        return None




    def remove_order(self, order_id):
        """
        Remove an order from management.
        """
        if order_id in self.finalized_orders:
            del self.finalized_orders[order_id]
            logging.info(f"Removed order {order_id} from management.")

    def update_order(self, order_id, **kwargs):
        """
        Update attributes of a finalized order.
        """
        if order_id in self.finalized_orders:
            for key, value in kwargs.items():
                setattr(self.finalized_orders[order_id], key, value)
            logging.info(f"Updated order {order_id} with new attributes.")

    def take_profit(self, order_id: str, percentage: float) -> Optional[str]:
        """
        Sell a percentage (0–1) of the contracts at trigger × (1 + percentage).
        """
        base = self.finalized_orders.get(order_id)
        if not base or base.action != "BUY":
            logging.warning("[OrderManager] take_profit: no buy-order %s", order_id)
            return None

        # percentage is given as 0.20, 0.30, 0.40
        sell_qty = max(1, int(base.qty * percentage))
        profit_price = round(base.trigger * (1 + percentage), 2)

        return self.issue_sell_order(order_id, sell_qty, limit_price=profit_price)

    def breakeven(self, order_id: str) -> Optional[str]:
        """
        Sell 100 % of the contracts at the original trigger price (breakeven).
        """
        base = self.finalized_orders.get(order_id)
        if not base or base.action != "BUY":
            logging.warning("[OrderManager] breakeven: no buy-order %s", order_id)
            return None

        # use the exact qty that was finally bought
        sell_qty = base.qty
        # breakeven = trigger price of the original buy
        breakeven_price = base.trigger

        return self.issue_sell_order(order_id, sell_qty, limit_price=breakeven_price)

    def get_order_status(self, order_id):
        """
        Get the status of a specific order.
        """
        return self.tws_service.get_order_status(order_id)

    def cancel_order(self, order_id):
        """
        Cancel a finalized order if possible.
        """
        order = self.finalized_orders.get(order_id)
        if order:
            # Logic to attempt cancellation
            pass  # Placeholder for actual implementation

    # Additional methods to interact with finalized orders can be added here


    # ---------- export singleton ----------

order_manager = OrderManager(create_tws_service())