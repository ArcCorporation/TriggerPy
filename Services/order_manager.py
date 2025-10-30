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
        logging.info("="*80)
        logging.info(f"[OrderManager] >>> BEGIN issue_sell_order(base_order_id={base_order_id}, sell_qty={sell_qty})")
        logging.info(f"[OrderManager] ExitOrder ref={getattr(exit_order, 'previous_id', 'N/A')} symbol={exit_order.symbol}")

        try:
            # 1️⃣ Lookup live position (must exist for sell_position_by_order_id)
            pos = self.tws_service.get_position_by_order_id(base_order_id)
            logging.info(f"[OrderManager] Position lookup result: {pos}")
            if not pos or pos.get("qty", 0) <= 0:
                logging.error(f"[OrderManager] issue_sell_order: no live position for {base_order_id}")
                logging.info("="*80)
                return None

            live_qty = pos.get("qty")
            logging.info(f"[OrderManager] Live qty={live_qty}, requested sell_qty={sell_qty}")

            # 2️⃣ Resolve correct option contract (same as WaitService)
            try:
                contract = self.tws_service.create_option_contract(
                    pos["symbol"], pos["expiry"], pos["strike"], pos["right"]
                )
                logging.info(f"[OrderManager] Contract resolved: {contract}")
            except Exception as e:
                logging.exception(f"[OrderManager] Failed to create option contract for {pos}: {e}")
                logging.info("="*80)
                exit_order.mark_failed(f"Contract creation failed: {e}")
                return None

            # 3️⃣ Log for clarity before sending
            logging.info(
                f"[OrderManager] Submitting MKT exit order → "
                f"Symbol={pos['symbol']} Qty={sell_qty} "
                f"({pos['expiry']} {pos['strike']}{pos['right']})"
            )

            # 4️⃣ Execute via TWS service — exact same function as WaitService
            logging.info("[OrderManager] Calling TWSService.sell_position_by_order_id() ...")
            success = self.tws_service.sell_position_by_order_id(
                base_order_id,
                contract=contract,
                qty=sell_qty,
                limit_price=None,      # Market order, same as _finalize_exit_order
                ex_order=exit_order
            )
            logging.info(f"[OrderManager] TWSService.sell_position_by_order_id() returned: {success}")

            # 5️⃣ Mirror the same handling logic
            if success:
                logging.info(
                    f"[OrderManager] ✅ Sold {sell_qty} {pos['symbol']} via TWS position map – MKT Exit successful."
                )
                exit_order.mark_finalized(f"Manual sell triggered for {pos['symbol']}")
                logging.info(f"[OrderManager] Exit order {exit_order.previous_id} marked finalized.")
                logging.info("="*80)
                return base_order_id
            else:
                logging.error(f"[OrderManager] ❌ TWS refused sell for {base_order_id}")
                exit_order.mark_failed("TWS refused sell order")
                logging.info("="*80)
                return None

        except Exception as e:
            logging.exception(f"[OrderManager] Exception in manual sell for {base_order_id}: {e}")
            exit_order.mark_failed(str(e))
            logging.info("="*80)
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
        logging.info("="*80)
        logging.info(f"[BREAKEVEN] BEGIN for order_id={order_id}")
        logging.info(f"[BREAKEVEN] Finalized orders tracked: {len(self.finalized_orders)}")

        base = self.finalized_orders.get(order_id)
        if not base:
            logging.error(f"[BREAKEVEN] No finalized order found for {order_id}")
            logging.info("="*80)
            return None
        if base.action != "BUY":
            logging.warning(f"[BREAKEVEN] Order {order_id} not a BUY (action={base.action})")
            logging.info("="*80)
            return None

        logging.info(f"[BREAKEVEN] Base order located: {base.symbol} {base.expiry} {base.strike}{base.right}")
        logging.info(f"[BREAKEVEN] Entry price: {base.entry_price}, SL: {base.sl_price}, TP: {base.tp_price}")

        pos = self.tws_service.get_position_by_order_id(order_id)
        if not pos:
            logging.error(f"[BREAKEVEN] No position found for {order_id}")
            logging.info("="*80)
            return None

        qty = pos.get("qty", 0)
        logging.info(f"[BREAKEVEN] Live position: {pos}")
        if qty <= 0:
            logging.warning(f"[BREAKEVEN] Position already closed or qty=0 for {order_id}")
            logging.info("="*80)
            return None

        sell_qty = qty
        logging.info(f"[BREAKEVEN] Triggering MKT exit for full qty={sell_qty}")

        # Create and log exit order details
        exit_order = self._create_exit_order(base, sell_qty)
        logging.info(f"[BREAKEVEN] Exit order created with previous_id={exit_order.previous_id}")

        try:
            # Execute
            logging.info(f"[BREAKEVEN] Calling issue_sell_order() → base_id={order_id}")
            result = self.issue_sell_order(order_id, sell_qty, exit_order=exit_order)
            logging.info(f"[BREAKEVEN] issue_sell_order() returned: {result}")
            if not result:
                logging.error(f"[BREAKEVEN] issue_sell_order() returned None — possible TWS rejection or missing position map.")
            else:
                logging.info(f"[BREAKEVEN] SUCCESSFUL exit for {order_id}")
        except Exception as e:
            logging.exception(f"[BREAKEVEN] Exception during breakeven execution: {e}")

        logging.info("="*80)
        return result


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