import logging
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order
from ibapi.common import TickerId
import threading
import time

logging.basicConfig(level=logging.INFO, format="%(message)s")


class IBWrapper(EWrapper):
    def __init__(self):
        super().__init__()

    def nextValidId(self, orderId: int):
        logging.info(f"[IB] NextValidId = {orderId}")
        self.nextOrderId = orderId

    def error(self, reqId: TickerId, errorCode: int, errorString: str):
        logging.error(f"[Error] reqId={reqId}, code={errorCode}, msg={errorString}")

    def orderStatus(
        self,
        orderId: Order,
        status: str,
        filled: float,
        remaining: float,
        avgFillPrice: float,
        permId: int = 0,
        parentId: int = 0,
        lastFillPrice: float = 0.0,
        clientId: int = 0,
        whyHeld: str = "",
        mktCapPrice: float = 0.0,
    ):
        logging.info(
            f"[OrderStatus] id={orderId}, status={status}, filled={filled}, remaining={remaining}, avgFillPrice={avgFillPrice}"
        )

    def openOrder(self, orderId: Order, contract: Contract, order: Order, orderState):
        logging.info(
            f"[OpenOrder] id={orderId}, {contract.symbol} {contract.secType} {contract.lastTradeDateOrContractMonth} {contract.strike}{contract.right} "
            f"orderType={order.orderType}, action={order.action}, qty={order.totalQuantity}"
        )


class IBClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)


class TWSService:
    def __init__(self, host="127.0.0.1", port=7497, client_id=1):
        self.wrapper = IBWrapper()
        self.client = IBClient(self.wrapper)
        self.client.connect(host, port, client_id)

        # Start the network thread
        self.thread = threading.Thread(target=self.client.run, daemon=True)
        self.thread.start()

        # Wait a moment to ensure connection
        time.sleep(1)

    def place_bracket_order(
        self,
        symbol,
        expiry,
        strike,
        right,
        action,
        quantity,
        entry_price,
        take_profit_price,
        stop_loss_price,
    ):
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "OPT"
        contract.exchange = "SMART"
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = expiry
        contract.strike = strike
        contract.right = right

        # Parent order (entry)
        parent = Order()
        parent.orderId = self.wrapper.nextOrderId
        parent.action = action
        parent.orderType = "LMT"
        parent.totalQuantity = quantity
        parent.lmtPrice = entry_price
        parent.transmit = False

        # Take profit
        take_profit = Order()
        take_profit.orderId = parent.orderId + 1
        take_profit.action = "SELL" if action == "BUY" else "BUY"
        take_profit.orderType = "LMT"
        take_profit.totalQuantity = quantity
        take_profit.lmtPrice = take_profit_price
        take_profit.parentId = parent.orderId
        take_profit.transmit = False

        # Stop loss
        stop_loss = Order()
        stop_loss.orderId = parent.orderId + 2
        stop_loss.action = "SELL" if action == "BUY" else "BUY"
        stop_loss.orderType = "STP"
        stop_loss.totalQuantity = quantity
        stop_loss.auxPrice = stop_loss_price
        stop_loss.parentId = parent.orderId
        stop_loss.transmit = True

        self.client.placeOrder(parent.orderId, contract, parent)
        logging.info(f"Placed parent option order id={parent.orderId}")
        self.client.placeOrder(take_profit.orderId, contract, take_profit)
        logging.info(f"Placed takeProfit order id={take_profit.orderId}")
        self.client.placeOrder(stop_loss.orderId, contract, stop_loss)
        logging.info(f"Placed stopLoss order id={stop_loss.orderId}")

        self.wrapper.nextOrderId += 3

        return {
            "parentId": parent.orderId,
            "takeProfitId": take_profit.orderId,
            "stopLossId": stop_loss.orderId,
        }
