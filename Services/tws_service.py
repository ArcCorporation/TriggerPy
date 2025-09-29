# Services/tws_service.py
import time
import logging
from threading import Thread
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")


class TWSService(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)

        self.nextOrderId = None
        self.req_id = 9000

        # storage
        self.search_results = {}
        self.contract_details = {}
        self.contract_maturities = {}
        self.resolved_conids = {}

    # -------------------- Connection --------------------
    def connect_and_run(self, host="127.0.0.1", port=7497, client_id=9102):
        self.connect(host, port, clientId=client_id)
        thread = Thread(target=self.run, daemon=True)
        thread.start()
        time.sleep(1)
        logging.info("Connected to TWS")

    def nextValidId(self, orderId: int):
        super().nextValidId(orderId)
        self.nextOrderId = orderId
        logging.info(f"NextValidId set: {orderId}")

    # -------------------- Symbol Search --------------------
    def symbolSamples(self, reqId: int, contractDescriptions):
        self.search_results[reqId] = []
        for desc in contractDescriptions:
            c = desc.contract
            self.search_results[reqId].append({
                "symbol": c.symbol,
                "secType": c.secType,
                "currency": c.currency,
                "exchange": c.exchange,
                "primaryExchange": c.primaryExchange,
                "description": desc.derivativeSecTypes
            })

    def search_symbol(self, pattern: str):
        self.req_id += 1
        req_id = self.req_id
        self.reqMatchingSymbols(req_id, pattern)
        for _ in range(50):  # ~5s
            if req_id in self.search_results:
                return self.search_results[req_id]
            time.sleep(0.1)
        return []

    # -------------------- Contract Details --------------------
    def contractDetails(self, reqId, contractDetails):
        c = contractDetails.contract
        expiry = c.lastTradeDateOrContractMonth
        symbol = c.symbol

        # store contract detail
        if reqId not in self.contract_details:
            self.contract_details[reqId] = []
        self.contract_details[reqId].append({
            "symbol": c.symbol,
            "expiry": expiry,
            "strike": c.strike,
            "right": c.right,
            "exchange": c.exchange,
            "currency": c.currency,
            "conId": c.conId,
        })

        # store maturities
        if symbol not in self.contract_maturities:
            self.contract_maturities[symbol] = []
        if expiry and expiry not in self.contract_maturities[symbol]:
            self.contract_maturities[symbol].append(expiry)

    def contractDetailsEnd(self, reqId: int):
        logging.info(f"contractDetailsEnd for reqId {reqId}")

    # -------------------- Resolution Helpers --------------------
    def resolve_conid(self, symbol: str):
        """Resolve a stock symbol to conId (for option chains)."""
        if symbol in self.resolved_conids:
            return self.resolved_conids[symbol]

        self.req_id += 1
        req_id = self.req_id

        c = Contract()
        c.symbol = symbol
        c.secType = "STK"
        c.exchange = "SMART"
        c.currency = "USD"

        self.contract_details[req_id] = []
        self.reqContractDetails(req_id, c)

        for _ in range(50):
            if self.contract_details[req_id]:
                conid = self.contract_details[req_id][0]["conId"]
                self.resolved_conids[symbol] = conid
                return conid
            time.sleep(0.1)
        return None

    def get_maturities(self, symbol: str):
        """Fetch option expiries for a given stock symbol."""
        conid = self.resolve_conid(symbol)
        if not conid:
            logging.error(f"Could not resolve conId for {symbol}")
            return []

        self.req_id += 1
        req_id = self.req_id

        c = Contract()
        c.symbol = symbol
        c.secType = "OPT"
        c.exchange = "SMART"
        c.currency = "USD"
        c.underConId = conid  # critical for IBKR option chain

        self.contract_details[req_id] = []
        self.reqContractDetails(req_id, c)

        for _ in range(50):  # ~5s
            if symbol in self.contract_maturities and self.contract_maturities[symbol]:
                return sorted(self.contract_maturities[symbol])
            time.sleep(0.1)

        return []

    def get_option_chain(self, symbol: str, expiry: str):
        """Fetch strikes/rights for a given expiry."""
        conid = self.resolve_conid(symbol)
        if not conid:
            logging.error(f"Could not resolve conId for {symbol}")
            return []

        self.req_id += 1
        req_id = self.req_id

        c = Contract()
        c.symbol = symbol
        c.secType = "OPT"
        c.exchange = "SMART"
        c.currency = "USD"
        c.underConId = conid
        c.lastTradeDateOrContractMonth = expiry

        self.contract_details[req_id] = []
        self.reqContractDetails(req_id, c)

        for _ in range(50):
            if self.contract_details[req_id]:
                return self.contract_details[req_id]
            time.sleep(0.1)
        return []


# -------------------- Test Main --------------------
if __name__ == "__main__":
    tws = TWSService()
    tws.connect_and_run()

    syms = tws.search_symbol("TSLA")
    print("Search result:", syms[:3])

    conid = tws.resolve_conid("TSLA")
    print("Resolved conId:", conid)

    maturities = tws.get_maturities("TSLA")
    print("Available expiries:", maturities[:5])

    if maturities:
        chain = tws.get_option_chain("TSLA", expiry=maturities[0])
        print("Chain sample:", chain[:3])
