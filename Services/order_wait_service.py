import threading
import time
import logging
import time
from Helpers.Order import Order, OrderState
from Services.order_manager import order_manager
from Services.watcher_info import (
    ThreadInfo, watcher_info,
    STATUS_PENDING, STATUS_RUNNING, STATUS_FINALIZED, STATUS_CANCELLED, STATUS_FAILED
)
from Services.runtime_manager import runtime_man
from Services.tws_service import create_tws_service, TWSService
from Services.polygon_service import polygon_service, PolygonService


class OrderWaitService:
    def __init__(self, polygon_service: PolygonService, tws_service: TWSService, poll_interval=0.1):
        self.polygon = polygon_service
        self.tws = tws_service
        self.trigger_lock = threading.Lock()
        self.trigger_status = set()
        # Active pending orders, keyed by order_id
        self.pending_orders = {}
        # 💡 NEW: Storage for active stop-loss orders being monitored by WS
        self.active_stop_losses = {}
        # Cancelled order IDs
        self.cancelled_orders = set()
        # Lock for thread-safety
        self.lock = threading.Lock()

        # Storage for WS callbacks to allow proper unsubscription
        self._ws_callbacks = {} # Dictionary to store {order_id: callback_function}

        # Polling interval for alternate mode (seconds), e.g. 0.1s = 100ms
        self.poll_interval = poll_interval

    def _poll_snapshot_thread(self, order_id: str, order: Order, tinfo: ThreadInfo):
        """
        Inner polling thread logic for trigger watcher (formerly _poll_snapshot).
        Monitors a stock snapshot until the trigger condition is met or the order is cancelled.
        """
        delay = 2
        last = 0
        try:
            while runtime_man.is_run() and order.state == OrderState.PENDING:
                with self.lock:
                    if order_id not in self.pending_orders or order_id in self.cancelled_orders:
                        watcher_info.remove(order_id)
                        return
                
                snap = self.polygon.get_snapshot(order.symbol)
                if not snap:
                    time.sleep(self.poll_interval)
                    continue

                last_price = snap.get("last")
                msg = f"[WaitService] Poll {order.symbol} → {last_price}"
                now = time.time()
                
                if now - last > delay:
                    logging.info(msg)
                    print(msg)
                    last = now

                if last_price:
                    tinfo.update_status(STATUS_RUNNING, last_price=last_price)

                if last_price and order.is_triggered(last_price):
                    self._finalize_order(order_id, order, tinfo, last_price)
                    with self.lock:
                        if order_id in self.pending_orders:
                            del self.pending_orders[order_id]
                    return

                time.sleep(self.poll_interval)
        except Exception as e:
            tinfo.update_status(STATUS_FAILED, info={"error": str(e)})

    def start_trigger_watcher(self, order: Order, mode: str = "ws") -> threading.Thread:
        """
        Start a dedicated thread (or ws subscription) to watch trigger price for an order.
        When trigger condition is met, finalize order via TWS.
        """
        order_id = order.order_id

        # Register ThreadInfo
        tinfo = ThreadInfo(order_id, order.symbol, watcher_type="trigger", stop_loss=order.sl_price, mode=mode,order=order)
        watcher_info.add_watcher(tinfo)
        tinfo.update_status(STATUS_RUNNING)

        if mode == "ws":
            # Define the callback function and store it for unsubscription
            callback_func = lambda price, oid=order_id: self._on_tick(oid, price)
            self._ws_callbacks[order_id] = callback_func # Store it

            self.polygon.subscribe(
                order.symbol,
                callback_func # Pass the stored function
            )
            logging.info(f"[TriggerWatcher] Started WS watcher for {order.symbol} (order {order_id}) - WS mode.")
            return None  # no thread object for ws

        elif mode == "poll":
            # Old-school polling thread path
            t = threading.Thread(
                target=self._poll_snapshot_thread, 
                args=(order_id, order, tinfo),
                daemon=True
            )
            t.start()
            logging.info(f"[TriggerWatcher] Started polling watcher for {order.symbol} (order {order_id}) - Poll mode.")
            return t

        else:
            logging.warning(f"[TriggerWatcher] Unknown mode '{mode}', defaulting to 'ws'")
            # Fallback path must also store the callback
            callback_func = lambda price, oid=order_id: self._on_tick(oid, price)
            self._ws_callbacks[order_id] = callback_func
            self.polygon.subscribe(
                order.symbol,
                callback_func
            )
            return None

    def _finalize_exit_order(self, exit_order: Order, tinfo: ThreadInfo, last_price: float, live_qty: int, contract):
        """
        Sends the stop-loss order to TWS and handles cleanup and status updates.
        This is the consolidated logic from the old _stop_loss_thread.
        """
        order_id = exit_order.order_id
        
        try:
            logging.info(f"[StopLoss] Submitting MKT exit order for {exit_order.symbol} at {last_price}...")
            
            success = self.tws.sell_position_by_order_id(
                exit_order.previous_id,
                contract,
                qty=live_qty,
                limit_price=None,      # market order
                ex_order=exit_order
            )

            if success:
                logging.info(
                    f"[StopLoss] Sold {live_qty} {exit_order.symbol} "
                    f"via TWS position map – MKT Exit. Watcher finalized."
                )
                # ✅ RE-ADDED CRITICAL FINALIZATION LOGIC
                exit_order.mark_finalized(f"Stop-loss triggered @ {last_price}") 
                if tinfo: # tinfo might be None if WS mode lookup failed
                    tinfo.update_status(STATUS_FINALIZED, last_price=last_price)
                return True
            else:
                logging.error(f"[StopLoss] TWS refused stop-loss sell for {order_id} – will retry/fail.")
                # We return False so the while loop in _stop_loss_thread can decide to retry or fail.
                return False 

        except Exception as e:
            logging.exception(f"[StopLoss] Exception in finalize exit for {order_id}: {e}")
            exit_order.mark_failed(str(e))
            if tinfo:
                tinfo.update_status(STATUS_FAILED, last_price=last_price, info={"error": str(e)})
            return False


    def start_stop_loss_watcher(self, order: Order, stop_loss_price: float, mode: str = "ws"):
        """
        💡 MODIFIED
        Start a dedicated watcher to monitor stop-loss for an active order.
        Supports "poll" (thread) and "ws" (event-driven) modes.
        """
        order_id = order.order_id

        tinfo = ThreadInfo(order_id, order.symbol,
                         watcher_type="stop_loss",
                         mode=mode, # 💡 Pass mode to info
                         stop_loss=stop_loss_price)
        watcher_info.add_watcher(tinfo)
        tinfo.update_status(STATUS_RUNNING)

        # 💡 Route to the correct handler based on mode
        if mode == "ws":
            # --- WebSocket Mode (Event-Driven) ---
            with self.lock:
                self.active_stop_losses[order_id] = order # Store the order for the callback
            
            # Define the callback, baking in the order_id and stop_loss_level
            callback_func = lambda price, oid=order_id, sl=stop_loss_price: \
                                 self._on_stop_loss_tick(oid, price, sl)
            
            self._ws_callbacks[order_id] = callback_func # Store for unsubscription
            
            self.polygon.subscribe(order.symbol, callback_func)
            logging.info(f"[StopLoss-WS] Started WS watcher for {order.symbol} (order {order_id})")
            return None # No thread object

        else:
            # --- Polling Mode (Thread-Based) ---
            if mode != "poll":
                logging.warning(f"[StopLoss] Unknown mode '{mode}' for {order_id}. Defaulting to 'poll'.")
            
            # 💡 Renamed target method
            t = threading.Thread(
                target=self._run_stop_loss_watcher_poll_thread, 
                args=(order, stop_loss_price, tinfo), # Pass necessary variables
                daemon=True,
                name=f"StopLoss-{order.symbol}-{order_id[:4]}-poll"
            )
            t.start()
            return t

    def _run_stop_loss_watcher_poll_thread(self, order: Order, stop_loss_price: float, tinfo: ThreadInfo):
        """
        💡 RENAMED (was _run_stop_loss_watcher_thread)
        THREAD TARGET (POLL): Monitors a single position's stop-loss using polling.
        """
        # --- This is the body of the old inner _stop_loss_thread ---
        last_print   = 0
        delay        = 5
        warn_times   = {"contract": 0, "premium": 0, "position": 0}

        logging.info(
            f"[StopLoss-POLL] Watching {order.symbol} stop-loss @ {stop_loss_price}  ({order.right})")
        try:

            while runtime_man.is_run():
                # 1. order still alive?
                if order.state not in (OrderState.ACTIVE, OrderState.PENDING):
                    logging.info(
                        f"[StopLoss-POLL] Order {order.order_id} no longer active – stopping watcher.")
                    tinfo.update_status(STATUS_CANCELLED)
                    return
                
                # 💡 Check for external cancellation
                with self.lock:
                    if order.order_id in self.cancelled_orders:
                        logging.info(f"[StopLoss-POLL] Watcher {order.order_id} cancelled by service.")
                        tinfo.update_status(STATUS_CANCELLED)
                        return

                # 2. market data
                snap = self.polygon.get_snapshot(order.symbol)
                if not snap:
                    time.sleep(self.poll_interval)
                    logging.warning(f"[StopLoss-POLL] BEWARE NO SNAP FOR {order.symbol}")
                    continue

                now = time.time()
                last_price = snap.get("last")
                if now - last_print >= delay:
                    logging.info(
                        f"[StopLoss-POLL] Poll {order.symbol} → {last_price}, stop={stop_loss_price}")
                    last_print = now
                if last_price:
                    tinfo.update_status(STATUS_RUNNING, last_price=last_price)

                # 3. trigger logic
                triggered = (last_price >= stop_loss_price) if order.right in ("P", "PUT") \
                        else (last_price <= stop_loss_price)

                # 4. contract resolution (throttled)
                contract = self.tws.create_option_contract(
                    order.symbol, order.expiry, order.strike, order.right)
                conid = self.tws.resolve_conid(contract)
                if not conid:
                    if now - warn_times["contract"] >= 30:
                        logging.warning(
                            f"[StopLoss-POLL] still no conId for {order.symbol} "
                            f"{order.expiry} {order.strike}{order.right} – will keep trying")
                        warn_times["contract"] = now
                    time.sleep(self.poll_interval)
                    continue
                contract.conId = conid
                warn_times["contract"] = 0

                # 5. premium fetch (throttled)
                premium = self.tws.get_option_premium(
                    order.symbol, order.expiry, order.strike, order.right)
                if premium is None or premium <= 0:
                    pos_fallback = self.tws.get_position_by_order_id(order.previous_id)
                    premium = pos_fallback and pos_fallback.get("avg_price") or None
                    if premium is None:
                        if now - warn_times["premium"] >= 30:
                            logging.warning(
                                f"[StopLoss-POLL] no premium for {order.symbol} "
                                f"{order.expiry} {order.strike}{order.right} – will keep trying")
                            warn_times["premium"] = now
                        time.sleep(self.poll_interval)
                        continue
                warn_times["premium"] = 0

                # 6. position check (throttled)
                pos = self.tws.get_position_by_order_id(order.previous_id)
                if not pos or pos.get("qty", 0) <= 0:
                    if now - warn_times["position"] >= 30:
                        logging.warning(
                            f"[StopLoss-POLL] no live position for {order.previous_id} – will keep watching")
                        warn_times["position"] = now
                    time.sleep(self.poll_interval)
                    continue
                warn_times["position"] = 0

                # 7. exit when triggered
                if triggered:
                    logging.info(
                        f"[StopLoss-POLL] TRIGGERED! {order.symbol} "
                        f"Price {last_price} vs Stop {stop_loss_price}"
                    )
                    live_qty = int(pos["qty"])
                    
                    success = self._finalize_exit_order(order, tinfo, last_price, live_qty, contract)

                    if success:
                        return
                
                time.sleep(self.poll_interval)

        except Exception as e:
            logging.exception(f"[StopLoss-POLL] Outer exception in stop-loss watcher: {e}")
            tinfo.update_status(STATUS_FAILED, info={"error": str(e)})
        finally:
            watcher_info.remove(order.order_id) # Cleanup watcher info on thread exit

    def add_order(self, order: Order, mode: str = "ws") -> str:
        """
        Add an order to be executed once its trigger is met.
        mode="ws"   -> subscribe to live ticks (original behavior)
        mode="poll" -> start a polling thread using snapshot
        """
        order_id = order.order_id
        with self.lock:
            self.pending_orders[order_id] = order

        # ✅ IMMEDIATE TRIGGER CHECK
        current_price = self.polygon.get_last_trade(order.symbol)
        if current_price and order.is_triggered(current_price):
            logging.info(
                f"[WaitService] 🚨 TRIGGER ALREADY MET! Executing immediately. "
                f"Current: {current_price}, Trigger: {order.trigger}"
            )
            self._finalize_order(order_id, order, tinfo=None, last_price=current_price)
            return order_id

        # Subscribe / start poller only if trigger not already met
        self.start_trigger_watcher(order, mode) # 💡 Simplified to use the router

        msg = (
            f"[WaitService] Order added {order_id} "
            f"(mode={mode}, waiting for trigger {order.trigger}, current: {current_price})"
        )
        logging.info(msg)
        return order_id

    def cancel_order(self, order_id: str):
        """
        💡 MODIFIED
        Cancel an order. 
        Removes it from pending trigger set OR active stop-loss set.
        Unsubscribes from Polygon.
        """
        order = None
        symbol = None
        
        with self.lock:
            if order_id in self.pending_orders:
                order = self.pending_orders.pop(order_id, None)
                if order:
                    logging.info(f"[WaitService] Cancelling PENDING order {order_id}")
                    symbol = order.symbol
            
            elif order_id in self.active_stop_losses: # 💡 NEW: Check active stop-losses
                order = self.active_stop_losses.pop(order_id, None)
                if order:
                    logging.info(f"[WaitService] Cancelling STOP-LOSS watcher {order_id}")
                    symbol = order.symbol

            if order:
                order.mark_cancelled()
                self.cancelled_orders.add(order_id)
                watcher_info.update_watcher(order_id, STATUS_CANCELLED)
            else:
                logging.warning(f"[WaitService] cancel_order: No active watcher found for {order_id}")
                return # Not found, nothing to do

        # Unsubscribe logic (outside lock)
        callback_func = self._ws_callbacks.pop(order_id, None)
        if callback_func and symbol:
            try:
                self.polygon.unsubscribe(symbol, callback_func)
            except Exception as e:
                logging.debug(f"[WaitService] Unsubscribe ignored for {symbol}: {e}")
        elif not callback_func:
            logging.debug(f"[WaitService] No WS callback found for order {order_id} (likely poll mode).")

    def list_pending_orders(self):
        with self.lock:
            return [o.to_dict() for o in self.pending_orders.values()]

    def _on_tick(self, order_id: str, price: float):
        """Callback from PolygonService for live ENTRY triggers."""
        with self.lock:
            order = self.pending_orders.get(order_id)
            if not order or order_id in self.cancelled_orders:
                return # Order was finalized or cancelled

        # Update watcher info with live price
        if tinfo := watcher_info.get_watcher(order_id):
            tinfo.update_status(STATUS_RUNNING, last_price=price)

        with self.trigger_lock:
            if order.is_triggered(price) and  order not in self.trigger_status:
                self.trigger_status.add(order)
                logging.info(f"[WaitService-WS] TRIGGERED! {order.symbol} @ {price}, trigger={order.trigger}")
                
                # --- Finalize ---
                # Note: tinfo is None, _finalize_order will find it
                self._finalize_order(order_id, order, tinfo=None, last_price=price)
                
                # --- Unsubscribe and cleanup ---
                callback_func = self._ws_callbacks.pop(order_id, None)
                if callback_func:
                    try:
                        self.polygon.unsubscribe(order.symbol, callback_func)
                    except Exception as e:
                        logging.debug(f"[WaitService-WS] Unsubscribe ignored for {order.symbol}: {e}")

                with self.lock:
                    self.pending_orders.pop(order_id, None) # Remove from pending
                    self.cancelled_orders.add(order_id) # Add to prevent race conditions

    def _on_stop_loss_tick(self, order_id: str, price: float, stop_loss_level: float):
        """💡 NEW: Callback from PolygonService for live STOP-LOSS triggers."""
        
        # 1. Get order from the active stop-loss dictionary
        with self.lock:
            order = self.active_stop_losses.get(order_id)
            if not order or order_id in self.cancelled_orders:
                return # Watcher was cancelled or already triggered
        
        tinfo = watcher_info.get_watcher(order_id) # Get tinfo for status updates

        # 2. Check trigger logic
        triggered = (price >= stop_loss_level) if order.right in ("P", "PUT") \
                    else (price <= stop_loss_level)
        
        if tinfo:
            tinfo.update_status(STATUS_RUNNING, last_price=price)

        if triggered:
            logging.info(
                f"[StopLoss-WS] TRIGGERED! {order.symbol} "
                f"Price {price} vs Stop {stop_loss_level}"
            )
            
            # --- Triggered: Execute TWS-heavy logic NOW ---
            contract = self.tws.create_option_contract(
                order.symbol, order.expiry, order.strike, order.right)
            conid = self.tws.resolve_conid(contract)
            
            if not conid:
                logging.error(f"[StopLoss-WS] Triggered, but FAILED to resolve conid for {order_id}. Retrying next tick.")
                if tinfo: tinfo.update_status(STATUS_FAILED, info={"error": "Failed to resolve conId"})
                return # Will retry on next tick if still triggered
            
            contract.conId = conid

            pos = self.tws.get_position_by_order_id(order.previous_id)
            if not pos or pos.get("qty", 0) <= 0:
                logging.warning(f"[StopLoss-WS] Triggered, but no position found for {order.previous_id}. Closing watcher.")
                self._cleanup_ws_watcher(order_id, order.symbol) # Position is gone, close watcher
                if tinfo: tinfo.update_status(STATUS_FINALIZED, info={"message": "Position not found"})
                return

            live_qty = int(pos["qty"])

            # 4. Finalize
            success = self._finalize_exit_order(order, tinfo, price, live_qty, contract)

            if success:
                # 5. Unsubscribe and cleanup
                self._cleanup_ws_watcher(order_id, order.symbol)
        
        # --- Not triggered: Do nothing, wait for next tick ---

    def _cleanup_ws_watcher(self, order_id: str, symbol: str):
        """💡 NEW: Helper to remove WS callback and order from active monitoring."""
        with self.lock:
            self.active_stop_losses.pop(order_id, None)
            self.cancelled_orders.add(order_id) # Add to prevent race conditions

        callback_func = self._ws_callbacks.pop(order_id, None)
        if callback_func:
            try:
                self.polygon.unsubscribe(symbol, callback_func)
            except Exception as e:
                logging.error(f"[WaitService] WS unsubscribe failed for {order_id}: {e}")

  
    def _finalize_order(self, order_id: str, order: Order, tinfo: ThreadInfo, last_price):
        """Sends the entry order to TWS and handles cleanup and status updates."""
        
        # Helper to update tinfo if it exists (for poll mode)
        def _update_tinfo_status(status, **kwargs):
            # 💡 MODIFIED: Find the watcher info, whether from poll thread (tinfo) or WS (lookup)
            active_tinfo = tinfo or watcher_info.get_watcher(order_id)
            if active_tinfo:
                active_tinfo.update_status(status, last_price=last_price, **kwargs)


        try:
            start_ts = time.time() * 1000
            logging.info(f"[TWS-LATENCY] {order.symbol} Trigger hit → sending ENTRY order "
                        f"({order.right}{order.strike}) at {start_ts:.0f} ms")
            success = self.tws.place_custom_order(order)
            if success:
                end_ts = time.time() * 1000
                latency = end_ts - start_ts
                logging.info(f"[TWS-LATENCY] {order.symbol} Order sent in {latency:.1f} ms "
                            f"(start {start_ts:.0f} → end {end_ts:.0f})")

                order.mark_active(result=f"IB Order ID: {order._ib_order_id}")
                if getattr(order, "_status_callback", None):
                    try:
                        order._status_callback(f"Finalized: {order.symbol} {order.order_id}", "green")
                    except Exception as e:
                        logging.error(f"[WaitService] UI callback failed for finalized order {order.order_id}: {e}")
                
                if getattr(order, "_fill_event", None):
                    filled = order._fill_event.wait(timeout=60)
                    if filled and order.state == OrderState.FINALIZED:
                        order_manager.add_finalized_order(order_id, order)
                        msg = f"[WaitService] Order finalized {order_id} → IB ID: {order._ib_order_id}"
                        logging.info(msg)
                        watcher_info.update_watcher(order_id, STATUS_FINALIZED)
                        _update_tinfo_status(STATUS_FINALIZED)
                    else:
                        logging.warning(f"[WaitService] Order {order_id} not filled within timeout window.")
                        # Even if not filled, we mark the *watcher* as finalized if the order was sent
                        _update_tinfo_status(STATUS_FAILED, info={"error": "Fill event timed out"}) 

                # ✅ if stop-loss configured, launch stop-loss watcher
                    if order.trigger and order.sl_price and order.state == OrderState.FINALIZED:
                        stop_loss_level = order.trigger - order.sl_price if order.right == 'C' or order.right == "CALL" else order.trigger + order.sl_price
                        exit_order = Order(
                            symbol=order.symbol,
                            expiry=order.expiry,
                            strike=order.strike,
                            right=order.right,
                            qty=order.qty,
                            entry_price=order.entry_price,   # keeps breakeven reference
                            tp_price=None,
                            sl_price=order.sl_price,
                            action="SELL",
                            type="MKT", # Use MKT for guaranteed stop-loss exit
                            trigger=None
                        )
                        ex_order = exit_order.set_position_size(order._position_size) 
                        ex_order.previous_id = order.order_id
                        ex_order.mark_active()
                        logging.info(f"[WAITSERVICE] Spawned EXIT watcher {ex_order.order_id} "
                                f"stop={stop_loss_level} ({order.right})")
                        
                        # 💡 MODIFIED: Spawn watcher in "ws" mode
                        self.start_stop_loss_watcher(ex_order, stop_loss_level, mode="poll")


            else:
                order.mark_failed("Failed to place order with TWS")
                msg = f"[WaitService] Order placement failed {order_id}"
                logging.error(msg)
                watcher_info.update_watcher(order_id, STATUS_FAILED)
                _update_tinfo_status(STATUS_FAILED, info={"error": "TWS place_custom_order failed"})

        except Exception as e:
            order.mark_failed(str(e))
            msg = f"[WaitService] Finalize failed {order_id}: {e}"
            logging.exception(msg) # 💡 Use exception logging
            _update_tinfo_status(STATUS_FAILED, info={"error": str(e)})

    def get_order_status(self, order_id: str):
        return self.tws.get_order_status(order_id)

    def cancel_active_order(self, order_id: str) -> bool:
        """
        Cancels an order that is live at TWS.
        This is different from cancel_order, which stops a local watcher.
        """
        try:
            if self.tws.cancel_custom_order(order_id):
                # Also cancel any local watcher associated with it
                self.cancel_order(order_id) 
                watcher_info.update_watcher(order_id, STATUS_CANCELLED)
                return True
            return False
        except Exception as e:
            logging.error(f"[WaitService] Cancel active order failed {order_id}: {e}")
            watcher_info.update_watcher(order_id, STATUS_FAILED, info={"error": str(e)})
            return False

    def get_all_orders_status(self):
        result = {
            'pending': self.list_pending_orders(),
            'active': {} # 💡 This seems to be legacy, TWS tracks active orders
        }
        # This logic is likely flawed as TWS is the source of truth for active orders
        # Re-kept as per original file, but recommend review.
        for order_id in list(self.pending_orders.keys()):
            status = self.get_order_status(order_id)
            if status:
                result['active'][order_id] = status
        return result
    


wait_service = OrderWaitService(polygon_service, create_tws_service())