# model.py
import logging
from Services.polygon_service import PolygonService
from Services.tws_service import create_tws_service
from Services.order_wait_service import OrderWaitService
from Helpers.Order import Order, OrderState
from typing import List, Dict, Optional, Tuple

class AppModel:
    def __init__(self):
        self._symbol: Optional[str] = None
        self._underlying_price: Optional[float] = None
        self._expiry: Optional[str] = None
        self._strike: Optional[float] = None
        self._right: Optional[str] = None
        self._stop_loss: Optional[float] = None
        self._take_profit: Optional[float] = None
        self._orders: List[Order] = []
        
        self._tws_service = create_tws_service()
        self._polygon_service = PolygonService()
        self._order_wait_service = OrderWaitService(self._polygon_service, self._tws_service)
        self._connected = False

    def connect_services(self) -> bool:
        try:
            if self._tws_service.connect_and_start():
                self._connected = True
                logging.info("AppModel: Services connected")
                return True
            else:
                logging.error("AppModel: Failed to connect to TWS")
                return False
        except Exception as e:
            logging.error(f"AppModel: Connection failed: {e}")
            return False

    def disconnect_services(self):
        try:
            self._tws_service.disconnect_gracefully()
            self._connected = False
            logging.info("AppModel: Services disconnected")
        except Exception as e:
            logging.error(f"AppModel: Disconnection error: {e}")

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def symbol(self) -> Optional[str]:
        return self._symbol

    @symbol.setter
    def symbol(self, value: str):
        self._symbol = value.upper() if value else None

    def refresh_market_price(self) -> Optional[float]:
        if not self._symbol:
            return None
        try:
            self._underlying_price = self._polygon_service.get_last_trade(self._symbol)
            logging.info(f"AppModel: Market price for {self._symbol}: {self._underlying_price}")
            return self._underlying_price
        except Exception as e:
            logging.error(f"AppModel: Failed to get market price: {e}")
            return None

    def set_option_contract(self, expiry: str, strike: float, right: str) -> Tuple[str, float, str]:
        right = right.upper()
        if right in ("CALL", "C"):
            self._right = "C"
        elif right in ("PUT", "P"):
            self._right = "P"
        else:
            raise ValueError("Right must be CALL/PUT or C/P")

        if not self._validate_option_contract(expiry, strike, self._right):
            raise ValueError(f"Invalid option contract: {expiry} {strike} {self._right}")

        self._expiry = expiry
        self._strike = strike
        logging.info(f"AppModel: Option contract set: {self._symbol} {expiry} {strike}{self._right}")
        return self._expiry, self._strike, self._right

    def _validate_option_contract(self, expiry: str, strike: float, right: str) -> bool:
        try:
            maturities = self._tws_service.get_maturities(self._symbol)
            if not maturities:
                return False
            expirations = list(maturities['expirations'])
            if expiry not in expirations:
                return False
            strikes = list(maturities['strikes'])
            return strike in strikes
        except Exception as e:
            logging.error(f"AppModel: Contract validation failed: {e}")
            return False

    def get_available_maturities(self) -> List[str]:
        if not self._symbol:
            return []
        try:
            maturities = self._tws_service.get_maturities(self._symbol)
            if maturities:
                return sorted(list(maturities['expirations']))
            return []
        except Exception as e:
            logging.error(f"AppModel: Failed to get maturities: {e}")
            return []

    def _validate_breakout_trigger(self, trigger_price: Optional[float], current_price: float) -> bool:
        """Breakout-only validation: trigger must be above current price for calls"""
        if trigger_price is None:
            return True
        
        if trigger_price <= current_price:
            logging.error(f"AppModel: Breakout violation - trigger {trigger_price} <= current {current_price}")
            return False
        
        return True

    def place_option_order(self, action: str = "BUY", quantity: int = 1, 
                          trigger_price: Optional[float] = None) -> Dict:
        if not all([self._symbol, self._expiry, self._strike, self._right]):
            raise ValueError("Option parameters not set")

        current_price = self.refresh_market_price()
        if not current_price:
            raise ValueError("Could not get current market price")

        # BREAKOUT VALIDATION
        if not self._validate_breakout_trigger(trigger_price, current_price):
            raise ValueError(f"Trigger {trigger_price} must be above current price {current_price} for breakout")

        order = Order(
            symbol=self._symbol,
            expiry=self._expiry,
            strike=self._strike,
            right=self._right,
            qty=quantity,
            entry_price=0.10,
            tp_price=self._take_profit,
            sl_price=self._stop_loss,
            action=action.upper(),
            trigger=trigger_price
        )

        if not trigger_price or order.is_triggered(current_price):
            success = self._tws_service.place_custom_order(order)
            if success:
                order.mark_active(result=f"IB Order ID: {getattr(order, '_ib_order_id', 'Unknown')}")
                logging.info(f"AppModel: Order executed: {order.order_id}")
            else:
                order.mark_failed("Failed to place order with TWS")
                logging.error(f"AppModel: Order failed: {order.order_id}")
        else:
            self._order_wait_service.add_order(order)
            logging.info(f"AppModel: Order waiting for breakout: {order.order_id}")

        self._orders.append(order)
        return order.to_dict()
    
    def get_available_strikes(self, expiry: str) -> List[float]:
        """Get available strikes for a given expiration date"""
        # CHANGE THIS LINE - use _maturities_data instead of option_chains
        if not self._tws_service or not self._tws_service._maturities_data:
            return []
        
        # Get strikes from your existing _maturities_data structure
        for req_id, chain_data in self._tws_service._maturities_data.items():
            if expiry in chain_data.get('expirations', []):
                return chain_data.get('strikes', [])
        
        return []

    def cancel_pending_order(self, order_id: str) -> bool:
        for order in self._orders:
            if order.order_id == order_id and order.state == OrderState.PENDING:
                self._order_wait_service.cancel_order(order_id)
                order.mark_cancelled()
                logging.info(f"AppModel: Order cancelled: {order_id}")
                return True
        return False

    def get_orders(self, state_filter: Optional[str] = None) -> List[Dict]:
        if state_filter:
            return [order.to_dict() for order in self._orders 
                    if order.state.value == state_filter]
        return [order.to_dict() for order in self._orders]

    def reset(self):
        self._symbol = None
        self._underlying_price = None
        self._expiry = None
        self._strike = None
        self._right = None
        self._stop_loss = None
        self._take_profit = None
        logging.info("AppModel: Model state reset")

app_model = AppModel()