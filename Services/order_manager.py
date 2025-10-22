import logging
from Services.tws_service import create_tws_service, TWSService
from Helpers.Order import Order
from typing import Optional, Dict


class OrderManager:
    def __init__(self, tws_service: TWSService):
        self.tws_service = tws_service
        self.finalized_orders: Dict[Order] = {}  # Dictionary to hold finalized orders

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
        Sell an existing TWS-tracked position by its order_id.
        Uses tws_service.sell_position_by_order_id() for consistency.
        """
        logging.info(f"[OrderManager] is attempting sale of options {base_order_id}:{sell_qty}:{limit_price}")
        pos = self.tws_service.get_position_by_order_id(base_order_id)
        if not pos or pos.get("qty", 0) <= 0:
            logging.info(f"[OrderManager] issue_sell_order: no live position for {base_order_id}")
            return None

        # use TWSService’s built-in position selling
        ok = self.tws_service.sell_position_by_order_id(
            base_order_id, qty=sell_qty, limit_price=limit_price
        )
        if ok:
            logging.info(f"[OrderManager] sell order placed -> base={base_order_id}, qty={sell_qty}, limit={limit_price}")
            return base_order_id  # keep same id for tracking continuity

        logging.error(f"[OrderManager] sell order failed for {base_order_id}")
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

    def take_profit(self, order_id: str,  sell_pct: float) -> Optional[str]:
        """
        Sell a portion (sell_pct) of the contracts once option gains profit_pct.
        """
        base = self.finalized_orders.get(order_id)
        if not base or base.action != "BUY":
            logging.info("[OrderManager] take_profit: no buy-order %s", order_id)
            return None

        sell_qty = max(1, int(base.qty * sell_pct))
        order = self.finalized_orders[order_id]
        snapshot = self.tws_service.get_option_snapshot(order.symbol, order.expiry, order.strike, order.right)
        if not snapshot or snapshot.get("ask") is None:
            logging.info("[StopLOrderManageross] Snapshot timeout – cannot compute premium")
            return

        mid_premium = snapshot["ask"] * 1.05
        tick = 0.01 if mid_premium < 3 else 0.05
        mid_premium = int(round(mid_premium / tick)) * tick
        mid_premium = round(mid_premium, 2)

        profit_price = mid_premium

        logging.info(f"[OrderManager] TAKE PROFIT triggered for {order_id}: "
                    f"{sell_pct*100:.0f}% qty @ {sell_pct *100}% profit "
                    f"(limit={profit_price}, entry={base.entry_price})")

        return self.issue_sell_order(order_id, sell_qty, limit_price=profit_price)
    
    def breakeven(self, order_id: str) -> Optional[str]:
        """
        Sell 100 % of the contracts at the original trigger price (breakeven).
        """
        base = self.finalized_orders.get(order_id)
        if not base or base.action != "BUY":
            logging.info("[OrderManager] breakeven: no buy-order %s", order_id)
            return None

        # use the exact qty that was finally bought
        sell_qty = base.qty
        # breakeven = trigger price of the original buy
        order = base
        snapshot = self.tws_service.get_option_snapshot(order.symbol, order.expiry, order.strike, order.right)
        if not snapshot or snapshot.get("ask") is None:
            logging.info("[StopLOrderManageross] Snapshot timeout – cannot compute premium")
            return

        mid_premium = snapshot["ask"] * 1.05
        tick = 0.01 if mid_premium < 3 else 0.05
        mid_premium = int(round(mid_premium / tick)) * tick
        mid_premium = round(mid_premium, 2)
        breakeven_price = mid_premium
        logging.info(f"[OrderManager] BREAKEVEN triggered for {order_id}: "
             f"symbol={base.symbol}, qty={base.qty}, price={base.entry_price}")


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