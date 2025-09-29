from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.ticktype import TickTypeEnum
import threading
import time
import logging
import random
from typing import List, Dict, Optional, Callable

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('tws_service.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger('TWSService')

class TWSService(EWrapper, EClient):
    """
    A service class to interact with the Interactive Brokers TWS API for trading options.
    """
    def __init__(self):
        EClient.__init__(self, self)
        self.next_valid_order_id = None
        self.connection_ready = threading.Event()
        self.client_id = random.randint(1, 999999)  # Random client ID to avoid conflicts
        self._connection_timeout = 10
        
        # For option chain data
        self._maturities_data = {}
        self._maturities_req_id = None
        self._maturities_event = threading.Event()
        
        self._contract_details = {}
        self._contract_details_req_id = None
        self._contract_details_event = threading.Event()
        
        self._request_counter = 1

    def nextValidId(self, orderId: int):
        """
        Callback to confirm the connection and provide the next valid order ID.
        """
        super().nextValidId(orderId)
        self.next_valid_order_id = orderId
        logger.info(f"NextValidId received: {orderId} (Client ID: {self.client_id})")
        self.connection_ready.set()  # Signal that connection is ready

    def error(self, reqId, errorCode, errorString, *args):
        """
        Critical error handling callback.
        Future-proof with *args for different ibapi versions.
        """
        advancedOrderRejectJson = ""
        if args:
            advancedOrderRejectJson = args[0] if args else ""
        
        if errorCode in [2104, 2106]:  # Common info messages
            logger.info(f"TWS Message. Code: {errorCode}, Msg: {errorString}")
        elif errorCode == 502:  # Could not connect to TWS
            logger.error(f"Connection failed. Check if TWS/IB Gateway is running on the specified port.")
            self.connection_ready.clear()
        elif errorCode == 504:  # Not connected
            logger.error(f"Not connected to TWS: {errorString}")
            self.connection_ready.clear()
        elif errorCode == 200:  # No security definition found
            logger.warning(f"No security definition found for reqId {reqId}")
            if reqId == self._maturities_req_id:
                self._maturities_event.set()
            elif reqId == self._contract_details_req_id:
                self._contract_details_event.set()
        else:
            logger.error(f"API Error. reqId: {reqId}, Code: {errorCode}, Msg: {errorString}")
        
        if advancedOrderRejectJson:
            logger.error(f"Advanced Order Reject: {advancedOrderRejectJson}")

    def securityDefinitionOptionParameter(self, reqId: int, exchange: str,
                                        underlyingConId: int, tradingClass: str,
                                        multiplier: str, expirations: List[str],
                                        strikes: List[float]):
        """
        Callback for receiving option chain data (maturities and strikes).
        """
        logger.info(f"Received option chain data for reqId {reqId}: {len(expirations)} expirations, {len(strikes)} strikes")
        
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
        """
        Callback indicating all option chain data has been received.
        """
        logger.debug(f"Option chain data reception complete for reqId {reqId}")
        self._maturities_event.set()

    def contractDetails(self, reqId: int, contractDetails):
        """
        Callback for receiving contract details.
        """
        logger.debug(f"Received contract details for reqId {reqId}: {contractDetails.contract.conId}")
        self._contract_details[reqId] = contractDetails
        self._contract_details_event.set()

    def contractDetailsEnd(self, reqId: int):
        """
        Callback indicating all contract details have been received.
        """
        logger.debug(f"Contract details reception complete for reqId {reqId}")
        self._contract_details_event.set()

    def connectionClosed(self):
        """Callback when connection is closed"""
        logger.warning(f"Connection to TWS has been closed (Client ID: {self.client_id})")
        self.connection_ready.clear()

    def connect_and_start(self, host='127.0.0.1', port=7497, timeout=10):
        """
        Establishes connection to TWS/IB Gateway and starts the message processing thread.
        Returns True if connection successful, False otherwise.
        """
        self._connection_timeout = timeout
        
        try:
            logger.info(f"Connecting to TWS on {host}:{port} with Client ID: {self.client_id}")
            self.connect(host, port, self.client_id)
            
            # Start the message processing in a separate thread
            api_thread = threading.Thread(
                target=self.run, 
                daemon=True, 
                name=f"TWS-API-Thread-{self.client_id}"
            )
            api_thread.start()
            
            # Wait for connection to be established with timeout
            if self.connection_ready.wait(timeout=self._connection_timeout):
                logger.info(f"Successfully connected to TWS (Client ID: {self.client_id})")
                return True
            else:
                logger.error(f"Connection timeout: Failed to receive valid order ID from TWS (Client ID: {self.client_id})")
                self.disconnect()
                return False
                
        except Exception as e:
            logger.error(f"Failed to connect to TWS: {str(e)} (Client ID: {self.client_id})")
            return False

    def is_connected(self):
        """Check if the service is connected and ready to trade"""
        return self.connection_ready.is_set() and self.next_valid_order_id is not None

    def _get_next_req_id(self):
        """Get next request ID"""
        req_id = self._request_counter
        self._request_counter += 1
        return req_id

    def get_maturities(self, symbol: str, exchange: str = "SMART", currency: str = "USD", 
                      timeout: int = 10) -> Optional[Dict]:
        """
        Get available option expirations and strikes for a symbol.
        
        Args:
            symbol: Underlying symbol (e.g., 'SPY')
            exchange: Exchange where the underlying trades
            currency: Currency code
            timeout: Timeout in seconds
            
        Returns:
            Dictionary with expirations and strikes, or None if failed
        """
        if not self.is_connected():
            logger.error("Cannot get maturities: Not connected to TWS")
            return None

        req_id = self._get_next_req_id()
        self._maturities_req_id = req_id
        self._maturities_data[req_id] = None
        self._maturities_event.clear()

        try:
            # Create underlying contract
            underlying = Contract()
            underlying.symbol = symbol.upper()
            underlying.secType = "STK"
            underlying.exchange = exchange
            underlying.currency = currency

            logger.info(f"Requesting option chain for {symbol} (reqId: {req_id})")
            self.reqSecDefOptParams(reqId=req_id, underlyingSymbol=symbol,
                                  futFopExchange="", underlyingSecType="STK",
                                  underlyingConId=0)

            # Wait for data with timeout
            if self._maturities_event.wait(timeout=timeout):
                data = self._maturities_data.get(req_id)
                if data:
                    logger.info(f"Successfully retrieved {len(data['expirations'])} expirations for {symbol}")
                    return data
                else:
                    logger.warning(f"No option chain data received for {symbol}")
                    return None
            else:
                logger.error(f"Timeout waiting for option chain data for {symbol}")
                return None

        except Exception as e:
            logger.error(f"Error getting maturities for {symbol}: {str(e)}")
            return None
        finally:
            # Cleanup
            if req_id in self._maturities_data:
                del self._maturities_data[req_id]

    def resolve_conid(self, contract: Contract, timeout: int = 10) -> Optional[int]:
        """
        Resolve a contract to its conId.
        
        Args:
            contract: The contract to resolve
            timeout: Timeout in seconds
            
        Returns:
            conId if successful, None otherwise
        """
        if not self.is_connected():
            logger.error("Cannot resolve conId: Not connected to TWS")
            return None

        req_id = self._get_next_req_id()
        self._contract_details_req_id = req_id
        self._contract_details[req_id] = None
        self._contract_details_event.clear()

        try:
            logger.info(f"Resolving conId for {contract.symbol} {contract.secType} (reqId: {req_id})")
            self.reqContractDetails(req_id, contract)

            # Wait for data with timeout
            if self._contract_details_event.wait(timeout=timeout):
                details = self._contract_details.get(req_id)
                if details:
                    conid = details.contract.conId
                    logger.info(f"Resolved conId {conid} for {contract.symbol}")
                    return conid
                else:
                    logger.warning(f"No contract details received for {contract.symbol}")
                    return None
            else:
                logger.error(f"Timeout resolving conId for {contract.symbol}")
                return None

        except Exception as e:
            logger.error(f"Error resolving conId for {contract.symbol}: {str(e)}")
            return None
        finally:
            # Cleanup
            if req_id in self._contract_details:
                del self._contract_details[req_id]

    def create_option_contract(self, symbol: str, last_trade_date: str, strike: float, right: str, 
                             exchange: str = "SMART", currency: str = "USD", multiplier: str = "100") -> Contract:
        """
        Creates an option contract definition.
        """
        contract = Contract()
        contract.symbol = symbol.upper()
        contract.secType = "OPT"
        contract.exchange = exchange
        contract.currency = currency
        contract.lastTradeDateOrContractMonth = last_trade_date
        contract.strike = float(strike)
        contract.right = right.upper()
        contract.multiplier = multiplier
        
        logger.debug(f"Created option contract: {symbol} {last_trade_date} {strike} {right}")
        return contract

    def create_stock_contract(self, symbol: str, exchange: str = "SMART", currency: str = "USD") -> Contract:
        """
        Creates a stock contract definition for the underlying.
        """
        contract = Contract()
        contract.symbol = symbol.upper()
        contract.secType = "STK"
        contract.exchange = exchange
        contract.currency = currency
        return contract

    def place_option_order(self, contract: Contract, action: str, quantity: int, order_type: str, 
                          price: Optional[float] = None, account: str = "") -> bool:
        """
        Places an order for the specified option contract.
        """
        if not self.is_connected():
            logger.error(f"Cannot place order: Not connected to TWS (Client ID: {self.client_id})")
            return False

        try:
            order = Order()
            order.action = action.upper()
            order.totalQuantity = int(quantity)
            order.orderType = order_type.upper()
            order.account = account
            
            if order_type.upper() == "LMT":
                if price is None:
                    logger.error("Limit price required for LMT orders")
                    return False
                order.lmtPrice = float(price)
            
            order_id = self.next_valid_order_id
            self.placeOrder(order_id, contract, order)
            logger.info(f"Placed order: ID={order_id}, {action} {quantity} {contract.symbol} "
                       f"{contract.lastTradeDateOrContractMonth} {contract.strike}{contract.right} "
                       f"@{price if price else 'MKT'} (Client ID: {self.client_id})")
            
            # Increment order ID for next use
            self.next_valid_order_id += 1
            return True
            
        except Exception as e:
            logger.error(f"Failed to place order: {str(e)} (Client ID: {self.client_id})")
            return False

    def disconnect_gracefully(self):
        """Disconnect from TWS gracefully"""
        logger.info(f"Disconnecting from TWS (Client ID: {self.client_id})...")
        self.connection_ready.clear()
        self.disconnect()

    def get_client_id(self) -> int:
        """Get the current client ID"""
        return self.client_id


# Factory function to create multiple service instances
def create_tws_service(host: str = '127.0.0.1', port: int = 7497, client_id: Optional[int] = None) -> TWSService:
    """
    Factory function to create a new TWS service instance.
    """
    service = TWSService()
    if client_id is not None:
        service.client_id = client_id
    return service


# Example usage
if __name__ == "__main__":
    service = create_tws_service()
    
    if service.connect_and_start():
        logger.info("TWSService started successfully")
        
        # Example: Get option maturities for SPY
        try:
            maturities = service.get_maturities("SPY")
            if maturities:
                print(f"Available expirations: {maturities['expirations'][:5]}")  # First 5
                print(f"Available strikes count: {len(maturities['strikes'])}")
            
            # Example: Resolve conId for a stock
            stock_contract = service.create_stock_contract("AAPL")
            conid = service.resolve_conid(stock_contract)
            if conid:
                print(f"AAPL conId: {conid}")
            
        except Exception as e:
            logger.error(f"Example failed: {e}")
        
        # Keep the service running
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("Shutting down by user request...")
            service.disconnect_gracefully()
    else:
        logger.error("Failed to start TWSService")