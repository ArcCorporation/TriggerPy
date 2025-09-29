import threading
import logging
from Helpers.Order import Order


class OrderWaitService:
    def __init__(self, polygon_service, tws_service):
        self.polygon = polygon_service
        self.tws = tws_service

        # Active pending orders, keyed by order_id
        self.pending_orders = {}
        # Cancelled order IDs
        self.cancelled_orders = set()
        # Lock for thread-safety
        self.lock = threading.Lock()

    def add_order(self, order: Order) -> str:
        order_id = order.order_id
        with self.lock:
            self.pending_orders[order_id] = order

        # ✅ IMMEDIATE TRIGGER CHECK - Execute immediately if condition already met
        current_price = self.polygon.get_last_trade(order.symbol)
        if current_price and order.is_triggered(current_price):
            logging.info(f"[WaitService] 🚨 TRIGGER ALREADY MET! Executing immediately. Current: {current_price}, Trigger: {order.trigger}")
            self._finalize_order(order_id, order)
            return order_id

        # Only subscribe if trigger not already met
        self.polygon.subscribe(
            order.symbol,
            lambda price, oid=order_id: self._on_tick(oid, price)
        )

        msg = f"[WaitService] Order added {order_id} (waiting for trigger {order.trigger}, current: {current_price})"
        logging.info(msg)
        return order_id

    def cancel_order(self, order_id: str):
        """
        Cancel an order. Removes it from pending set and unsubscribes from Polygon.
        """
        with self.lock:
            if order_id in self.pending_orders:
                order = self.pending_orders[order_id]
                order.mark_cancelled()
                self.cancelled_orders.add(order_id)
                del self.pending_orders[order_id]

                # Unsubscribe from Polygon feed for this symbol
                self.polygon.unsubscribe(order.symbol)

                msg = f"[WaitService] Order cancelled {order_id}"
                logging.info(msg)

    def list_pending_orders(self):
        """Return all pending orders as dicts."""
        with self.lock:
            return [o.to_dict() for o in self.pending_orders.values()]

    def _on_tick(self, order_id: str, price: float):
        """
        Callback from PolygonService for live ticks.
        Checks if trigger condition is met and finalizes the order.
        """
        with self.lock:
            # Make sure order is still pending and not cancelled
            order = self.pending_orders.get(order_id)
            if not order or order_id in self.cancelled_orders:
                return

        # Debug log
        msg = f"[WaitService] Tick received for {order.symbol} @ {price}, trigger={order.trigger}"
        logging.debug(msg)

        # Check if trigger is satisfied
        if order.is_triggered(price):
            self._finalize_order(order_id, order)

            # After finalizing, unsubscribe to stop receiving ticks
            self.polygon.unsubscribe(order.symbol)

            with self.lock:
                if order_id in self.pending_orders:
                    del self.pending_orders[order_id]

    def _finalize_order(self, order_id: str, order: Order):
        """
        Sends the order to TWS using the new TWSService.
        """
        try:
            # Use the new TWSService method
            success = self.tws.place_custom_order(order)
            
            if success:
                order.mark_active(result=f"IB Order ID: {order._ib_order_id}")
                msg = f"[WaitService] Order finalized {order_id} → IB ID: {order._ib_order_id}"
                logging.info(msg)
            else:
                order.mark_failed("Failed to place order with TWS")
                msg = f"[WaitService] Order placement failed {order_id}"
                logging.error(msg)
                
        except Exception as e:
            order.mark_failed(str(e))
            msg = f"[WaitService] Finalize failed {order_id}: {e}"
            logging.error(msg)

    def get_order_status(self, order_id: str):
        """
        Get the current status of an order from TWSService.
        """
        return self.tws.get_order_status(order_id)

    def cancel_active_order(self, order_id: str) -> bool:
        """
        Cancel an order that has already been sent to TWS.
        """
        try:
            # First try to cancel via TWS if it's an active order
            if self.tws.cancel_custom_order(order_id):
                # Also remove from our tracking
                self.cancel_order(order_id)
                return True
            return False
        except Exception as e:
            logging.error(f"[WaitService] Cancel active order failed {order_id}: {e}")
            return False

    def get_all_orders_status(self):
        """
        Get status for all orders (pending and active).
        """
        result = {
            'pending': self.list_pending_orders(),
            'active': {}
        }
        
        # Get status for orders that have been sent to TWS
        for order_id in list(self.pending_orders.keys()):
            status = self.get_order_status(order_id)
            if status:
                result['active'][order_id] = status
                
        return result