import logging
from Services.tws_service import create_tws_service, TWSService
from Helpers.Order import Order
from typing import Optional, Dict


class OrderManager:
    def __init__(self, tws_service: TWSService):
        self.tws_service = tws_service
        self.finalized_orders: Dict[str, Order] = {}  # Dictionary to hold finalized orders

    def add_finalized_order(self, order_id, order):
        """
        Add a finalized order to the collection for further management.
        """
        self.finalized_orders[order_id] = order
        logging.info(f"Added finalized order {order_id} to management.")
    
    # ------------------ NEW HELPER FOR EXIT ORDER CREATION ------------------
    def _create_exit_order(self, base_order: Order, sell_qty: int) -> Order:
        """
        Creates a new Market SELL Order object for exiting a position.
        Crucially sets action='SELL', type='MKT', and links to the base_order's ID.
        """
        exit_order = Order(
            symbol=base_order.symbol,
            expiry=base_order.expiry,
            strike=base_order.strike,
            right=base_order.right,
            qty=sell_qty,
            # For MKT orders, entry_price is not used for execution, but we'll use base.entry_price for reference
            entry_price=base_order.entry_price, 
            tp_price=None,
            sl_price=None,
            action="SELL",
            type="MKT", # ⚡ FORCE MARKET ORDER FOR GUARANTEED FILL ⚡
            trigger=None
        )
        # This is CRUCIAL for position tracking in TWSService
        exit_order.previous_id = base_order.order_id
        
        # Copy position size for internal consistency
        if getattr(base_order, "_position_size", None):
            exit_order.set_position_size(base_order._position_size)
        
        return exit_order
    # ------------------------------------------------------------------------

    def issue_sell_order(self,
                 base_order_id: str,
                 sell_qty: int,
                 exit_order: Order) -> Optional[str]:
        """
        Mirror of WaitService stop-loss sell flow:
        Executes a guaranteed MKT SELL via tws.sell_position_by_order_id(),
        identical logic path to _finalize_exit_order().
        """
        logging.info(f"[OrderManager] attempting MKT sale of {base_order_id} x{sell_qty}")

        try:
            # 1️⃣ Lookup live position (must exist for sell_position_by_order_id)
            pos = self.tws_service.get_position_by_order_id(base_order_id)
            if not pos or pos.get("qty", 0) <= 0:
                logging.error(f"[OrderManager] issue_sell_order: no live position for {base_order_id}")
                return None

            # 2️⃣ Resolve correct option contract (same as WaitService)
            contract = self.tws_service.create_option_contract(
                pos["symbol"], pos["expiry"], pos["strike"], pos["right"]
            )

            # 3️⃣ Log for clarity
            logging.info(
                f"[OrderManager] Submitting MKT exit order for {pos['symbol']} "
                f"qty={sell_qty} ({pos['expiry']} {pos['strike']}{pos['right']})"
            )

            # 4️⃣ Execute via TWS service — exact same function as WaitService
            success = self.tws_service.sell_position_by_order_id(
                base_order_id,
                contract=contract,
                qty=sell_qty,
                limit_price=None,      # Market order, same as _finalize_exit_order
                ex_order=exit_order
            )

            # 5️⃣ Mirror the same handling logic
            if success:
                logging.info(
                    f"[OrderManager] Sold {sell_qty} {pos['symbol']} via TWS position map – MKT Exit successful."
                )
                exit_order.mark_finalized(f"Manual sell triggered for {pos['symbol']}")
                return base_order_id
            else:
                logging.error(f"[OrderManager] TWS refused sell for {base_order_id}")
                exit_order.mark_failed("TWS refused sell order")
                return None

        except Exception as e:
            logging.exception(f"[OrderManager] Exception in manual sell for {base_order_id}: {e}")
            exit_order.mark_failed(str(e))
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
        Sell a portion (sell_pct) of the contracts using a Market Order to capture profit.
        """
        base = self.finalized_orders.get(order_id)
        if not base or base.action != "BUY":
            logging.info("[OrderManager] take_profit: no buy-order %s", order_id)
            return None

        pos = self.tws_service.get_position_by_order_id(order_id)
        if not pos or pos.get("qty", 0) <= 0:
            logging.warning(f"[OrderManager] take_profit: position already closed for {order_id}")
            return None
        
        # Calculate quantity based on *live* position quantity
        live_qty = pos["qty"]
        sell_qty = max(1, int(live_qty * sell_pct))

        logging.info(f"[OrderManager] TAKE PROFIT triggered for {order_id}: "
                    f"Selling {sell_qty} contracts (MKT Exit).")
        
        # Create a MKT exit order
        exit_order = self._create_exit_order(base, sell_qty)

        # Execute sale
        return self.issue_sell_order(order_id, sell_qty, exit_order=exit_order)
    
    def breakeven(self, order_id: str) -> Optional[str]:
        """
        Sell 100% of the remaining contracts using a Market Order.
        (Original Breakeven logic of selling at entry price is replaced by MKT for guaranteed exit.)
        """
        base = self.finalized_orders.get(order_id)
        if not base or base.action != "BUY":
            logging.info("[OrderManager] breakeven: no buy-order %s", order_id)
            return None

        pos = self.tws_service.get_position_by_order_id(order_id)
        if not pos or pos.get("qty", 0) <= 0:
            logging.warning(f"[OrderManager] breakeven: position already closed for {order_id}")
            return None
            
        sell_qty = pos["qty"] # Sell 100% of remaining

        logging.info(f"[OrderManager] BREAKEVEN triggered for {order_id}: "
             f"Selling {sell_qty} contracts (MKT Exit).")

        # Create a MKT exit order
        exit_order = self._create_exit_order(base, sell_qty)

        # Execute sale
        return self.issue_sell_order(order_id, sell_qty, exit_order=exit_order)

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