# tws_service.py
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order as IBOrder
import threading
import time
import logging
import random
from typing import List, Dict, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
)

logger = logging.getLogger('TWSService')

class TWSService(EWrapper, EClient):
    """
    TWS Service that integrates with Helpers.Order system
    """
    def __init__(self):
        EClient.__init__(self, self)
        self.next_valid_order_id = None
        self.connection_ready = threading.Event()
        self.client_id = random.randint(1, 999999)
        
        # For data requests
        self._maturities_data = {}
        self._maturities_req_id = None
        self._maturities_event = threading.Event()
        
        self._contract_details = {}
        self._contract_details_req_id = None
        self._contract_details_event = threading.Event()
        
        self._request_counter = 1
        
        # Track custom orders from Helpers.Order
        self._pending_orders = {}  # custom_order_id -> Helpers.Order object

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.next_valid_order_id = orderId
        logger.info(f"NextValidId: {orderId} (Client ID: {self.client_id})")
        self.connection_ready.set()

    def error(self, reqId, errorCode, errorString, *args):
        """Error callback - handles both regular and protobuf errors"""
        actual_error_code = errorCode
        if isinstance(errorCode, int) and errorCode > 10000:
            if "errorCode:" in str(errorString):
                try:
                    parts = str(errorString).split("errorCode:")
                    if len(parts) > 1:
                        actual_error_code = int(parts[1].split()[0])
                except:
                    actual_error_code = errorCode
        
        # Handle specific error codes
        if actual_error_code in [2104, 2106, 2158]:
            logger.info(f"TWS Info. Code: {actual_error_code}, Msg: {errorString}")
        elif actual_error_code == 502:
            logger.error("Connection failed - check TWS/IB Gateway")
            self.connection_ready.clear()
        elif actual_error_code == 504:
            logger.error(f"Not connected to TWS: {errorString}")
            self.connection_ready.clear()
        elif actual_error_code == 200:
            logger.warning(f"No security definition for reqId {reqId}")
            if reqId == self._maturities_req_id:
                self._maturities_event.set()
            elif reqId == self._contract_details_req_id:
                self._contract_details_event.set()
        elif actual_error_code == 321:
            logger.error(f"Contract validation error for reqId {reqId}: {errorString}")
            if reqId == self._maturities_req_id:
                self._maturities_event.set()
        else:
            logger.error(f"API Error. reqId: {reqId}, Code: {actual_error_code}, Msg: {errorString}")

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
        """Update custom order status based on IB callbacks"""
        logger.info(f"Order status - ID: {orderId}, Status: {status}, Filled: {filled}")
        
        # Find and update the corresponding custom order
        for custom_order_id, custom_order in self._pending_orders.items():
            if hasattr(custom_order, '_ib_order_id') and custom_order._ib_order_id == orderId:
                if status == "Filled":
                    custom_order.mark_active(result=orderId)
                    logger.info(f"Custom order {custom_order_id} filled")
                elif status in ["Cancelled", "ApiCancelled"]:
                    custom_order.mark_cancelled()
                    logger.info(f"Custom order {custom_order_id} cancelled")

    def openOrder(self, orderId, contract, order: IBOrder, orderState):
        logger.info(f"Order opened - ID: {orderId}, Symbol: {contract.symbol}")

    def execDetails(self, reqId, contract, execution):
        logger.info(f"Order executed - ID: {execution.orderId}, Price: {execution.price}")

    def securityDefinitionOptionParameter(self, reqId: int, exchange: str,
                                        underlyingConId: int, tradingClass: str,
                                        multiplier: str, expirations: List[str],
                                        strikes: List[float]):
        """Callback for option chain data"""
        logger.info(f"Option chain data: {len(expirations)} expirations, {len(strikes)} strikes")
        self._maturities_data[reqId] = {
            'exchange': exchange,
            'underlyingConId': underlyingConId,
            'tradingClass': tradingClass,
            'multiplier': multiplier,
            'expirations': expirations,
            'strikes': strikes
        }
        self._maturities_event.set()

    def securityDefinitionOptionParameterEnd(self, reqId: int):
        self._maturities_event.set()

    def contractDetails(self, reqId: int, contractDetails):
        self._contract_details[reqId] = contractDetails
        self._contract_details_event.set()

    def contractDetailsEnd(self, reqId: int):
        self._contract_details_event.set()

    def connectionClosed(self):
        logger.warning("Connection to TWS closed")
        self.connection_ready.clear()

    def connect_and_start(self, host='127.0.0.1', port=7497, timeout=10):
        """Connect to TWS/IB Gateway"""
        try:
            logger.info(f"Connecting to TWS on {host}:{port} with Client ID: {self.client_id}")
            self.connect(host, port, self.client_id)
            
            api_thread = threading.Thread(
                target=self.run, 
                daemon=True, 
                name=f"TWS-API-Thread-{self.client_id}"
            )
            api_thread.start()
            
            if self.connection_ready.wait(timeout=timeout):
                logger.info("Successfully connected to TWS")
                return True
            else:
                logger.error("Connection timeout")
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to TWS: {str(e)}")
            return False

    def is_connected(self):
        return self.connection_ready.is_set() and self.next_valid_order_id is not None

    def _get_next_req_id(self):
        req_id = self._request_counter
        self._request_counter += 1
        return req_id

    def get_maturities(self, symbol: str, exchange: str = "SMART", currency: str = "USD", 
                      timeout: int = 10) -> Optional[Dict]:
        """Get option expirations and strikes for a symbol"""
        if not self.is_connected():
            logger.error("Not connected to TWS")
            return None

        # Resolve underlying contract first
        underlying_contract = self.create_stock_contract(symbol, exchange, currency)
        underlying_conid = self.resolve_conid(underlying_contract)
        
        if not underlying_conid:
            logger.error(f"Failed to resolve conId for {symbol}")
            return None

        req_id = self._get_next_req_id()
        self._maturities_req_id = req_id
        self._maturities_data[req_id] = None
        self._maturities_event.clear()

        try:
            logger.info(f"Requesting option chain for {symbol}")
            self.reqSecDefOptParams(
                reqId=req_id, 
                underlyingSymbol=symbol,
                futFopExchange="", 
                underlyingSecType="STK",
                underlyingConId=underlying_conid
            )

            if self._maturities_event.wait(timeout=timeout):
                data = self._maturities_data.get(req_id)
                if data:
                    logger.info(f"Retrieved {len(data['expirations'])} expirations for {symbol}")
                    return data
                else:
                    logger.warning(f"No option chain data for {symbol}")
                    return None
            else:
                logger.error(f"Timeout getting option chain for {symbol}")
                return None

        except Exception as e:
            logger.error(f"Error getting maturities for {symbol}: {str(e)}")
            return None
        finally:
            if req_id in self._maturities_data:
                del self._maturities_data[req_id]

    def resolve_conid(self, contract: Contract, timeout: int = 10) -> Optional[int]:
        """Resolve contract to conId"""
        if not self.is_connected():
            return None

        req_id = self._get_next_req_id()
        self._contract_details_req_id = req_id
        self._contract_details[req_id] = None
        self._contract_details_event.clear()

        try:
            self.reqContractDetails(req_id, contract)

            if self._contract_details_event.wait(timeout=timeout):
                details = self._contract_details.get(req_id)
                if details:
                    conid = details.contract.conId
                    logger.info(f"Resolved conId {conid} for {contract.symbol}")
                    return conid
                else:
                    return None
            else:
                return None

        except Exception as e:
            logger.error(f"Error resolving conId: {str(e)}")
            return None
        finally:
            if req_id in self._contract_details:
                del self._contract_details[req_id]

    def create_option_contract(self, symbol: str, last_trade_date: str, strike: float, right: str, 
                             exchange: str = "SMART", currency: str = "USD") -> Contract:
        """Create IB option contract - converts CALL/PUT to C/P"""
        ib_right = "C" if right.upper() in ["C", "CALL"] else "P"
        
        contract = Contract()
        contract.symbol = symbol.upper()
        contract.secType = "OPT"
        contract.exchange = exchange
        contract.currency = currency
        contract.lastTradeDateOrContractMonth = last_trade_date
        contract.strike = float(strike)
        contract.right = ib_right
        contract.multiplier = "100"
        
        return contract

    def create_stock_contract(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> Contract:
        contract = Contract()
        contract.symbol = symbol.upper()
        contract.secType = "STK"
        contract.exchange = exchange
        contract.currency = currency
        return contract

    def place_custom_order(self, custom_order, account: str = "") -> bool:
        """
        Place an order using your custom Order object from Helpers.Order.
        """
        if not self.is_connected():
            logger.error(f"Cannot place order: Not connected to TWS")
            return False

        try:
            # Convert your custom order to IB contract
            ib_right = "C" if custom_order.right.upper() in ["C", "CALL"] else "P"
            
            contract = self.create_option_contract(
                symbol=custom_order.symbol,
                last_trade_date=custom_order.expiry,
                strike=custom_order.strike,
                right=ib_right,
                exchange="SMART",
                currency="USD"
            )

            # ✅ RESOLVE CONTRACT FIRST to avoid error 200
            conid = self.resolve_conid(contract)
            if not conid:
                logger.error(f"Could not resolve contract for {custom_order.symbol} {custom_order.expiry} {custom_order.strike}{ib_right}")
                custom_order.mark_failed("Contract resolution failed")
                return False

            # Use resolved contract
            contract.conId = conid

            # Convert your custom order to IB order
            ib_order = custom_order.to_ib_order(
                order_type="LMT",
                limit_price=custom_order.entry_price,
                transmit=True
            )
            ib_order.account = account

            # Store the custom order for tracking
            self._pending_orders[custom_order.order_id] = custom_order
            
            # Place the order with IB
            order_id = self.next_valid_order_id
            custom_order._ib_order_id = order_id
            
            self.placeOrder(order_id, contract, ib_order)
            
            logger.info(f"Placed custom order: {custom_order.order_id} -> IB ID: {order_id}")
            
            # Increment order ID for next use
            self.next_valid_order_id += 1
            return True
            
        except Exception as e:
            logger.error(f"Failed to place custom order {custom_order.order_id}: {str(e)}")
            custom_order.mark_failed(reason=str(e))
            return False

    def cancel_custom_order(self, custom_order_id: str) -> bool:
        """Cancel a custom order"""
        if custom_order_id in self._pending_orders:
            order = self._pending_orders[custom_order_id]
            if hasattr(order, '_ib_order_id'):
                self.cancelOrder(order._ib_order_id)
                order.mark_cancelled()
                logger.info(f"Cancelled order {custom_order_id}")
                return True
        return False

    def get_order_status(self, custom_order_id: str) -> Optional[Dict]:
        """
        Get the status of a custom order.
        """
        if custom_order_id in self._pending_orders:
            order = self._pending_orders[custom_order_id]
            return order.to_dict()
        return None
    def disconnect_gracefully(self):
        logger.info("Disconnecting from TWS...")
        self.connection_ready.clear()
        self.disconnect()


def create_tws_service(host: str = '127.0.0.1', port: int = 7497, client_id: Optional[int] = None) -> TWSService:
    service = TWSService()
    if client_id is not None:
        service.client_id = client_id
    return service


# Test the service
if __name__ == "__main__":
    print("Testing TWSService with Helpers.Order integration")
    
    # Import YOUR Order class
    from Order import Order
    
    service = create_tws_service()
    
    if service.connect_and_start(port=7497):
        print("✓ Connected to TWS")
        
        try:
            # Test data retrieval
            print("Testing option chain data...")
            maturities = service.get_maturities("SPY")
            if maturities:
                print(f"✓ Got {len(maturities['expirations'])} expirations")
            
            # Test with YOUR Order class
            print("Testing with Helpers.Order...")
            my_order = Order(
                symbol="SPY",
                expiry="20241220",
                strike=450.0,
                right="CALL",
                qty=1,
                entry_price=2.50,
                tp_price=5.00,
                sl_price=1.00
            )
            
            print(f"✓ Created Helpers.Order: {my_order.order_id}")
            print(f"  {my_order.symbol} {my_order.expiry} {my_order.strike}{my_order.right}")
            
            # Order placement ready (commented for safety)
            print("✓ Order system integrated and ready")
            print("Uncomment to test order placement:")
            print("# service.place_custom_order(my_order)")
            
        except Exception as e:
            print(f"Error: {e}")
        
        service.disconnect_gracefully()
        print("✓ Disconnected")