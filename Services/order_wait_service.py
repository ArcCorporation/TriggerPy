import threading
import logging
import Helpers.printer as p
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
        """
        Add a new Order to the wait service.
        Subscribes to Polygon live feed for the symbol.
        """
        order_id = order.order_id
        with self.lock:
            self.pending_orders[order_id] = order

        # Subscribe to Polygon ticks for this symbol
        # Every tick triggers _on_tick(order, price)
        self.polygon.subscribe(
            order.symbol,
            lambda price, oid=order_id: self._on_tick(oid, price)
        )

        msg = f"[WaitService] Order added {order_id} (waiting for trigger {order.trigger})"
        logging.info(msg)
        p.PRINT(msg)
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
                p.PRINT(msg)

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
        Sends the order to TWS once trigger condition is met.
        """
        try:
            result = self.tws.place_bracket_order(order)
            order.mark_active(result)
            msg = f"[WaitService] Order finalized {order_id} → {result}"
            logging.info(msg)
            p.PRINT(msg)
        except Exception as e:
            order.mark_failed(str(e))
            msg = f"[WaitService] Finalize failed {order_id}: {e}"
            logging.error(msg)
            p.PRINT(msg)
