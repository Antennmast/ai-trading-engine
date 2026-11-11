import threading
import time
from typing import Optional

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.order import Order


class IBKRApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.ready = threading.Event()
        self.account_done = threading.Event()
        self.contract_done = threading.Event()
        self.history_done = threading.Event()
        self.order_done = threading.Event()
        self.next_order_id: Optional[int] = None
        self.account_values = []
        self.contract_details = []
        self.bars = []
        self.errors = []
        self.order_statuses = {}

    def nextValidId(self, orderId: int):
        self.next_order_id = orderId
        self.ready.set()
        print(f"[IBKR] Connected. Next valid order ID: {orderId}")

    def error(self, reqId, *args):
        # ibapi changed this signature between releases (10.33+ inserts errorTime before
        # the code), so pick out the first int code and first string message.
        errorCode = next((a for a in args if isinstance(a, int) and a < 100000), -1)
        errorString = next((a for a in args if isinstance(a, str) and a), "")
        # Informational farm-status messages are still logged for diagnosis.
        message = f"reqId={reqId} code={errorCode}: {errorString}"
        self.errors.append((errorCode, message))
        print(f"[IBKR] {message}")

    def accountSummary(self, reqId, account, tag, value, currency):
        self.account_values.append({
            "account": account, "tag": tag, "value": value, "currency": currency
        })

    def accountSummaryEnd(self, reqId: int):
        self.account_done.set()

    def contractDetails(self, reqId, contractDetails):
        c = contractDetails.contract
        self.contract_details.append({
            "conId": c.conId,
            "symbol": c.symbol,
            "localSymbol": c.localSymbol,
            "exchange": c.exchange,
            "primaryExchange": c.primaryExchange,
            "currency": c.currency,
            "minTick": contractDetails.minTick,
            "longName": contractDetails.longName,
        })

    def contractDetailsEnd(self, reqId: int):
        self.contract_done.set()

    def historicalData(self, reqId, bar):
        self.bars.append({
            "date": bar.date, "open": bar.open, "high": bar.high,
            "low": bar.low, "close": bar.close, "volume": bar.volume
        })

    def historicalDataEnd(self, reqId, start, end):
        self.history_done.set()

    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld,
                    mktCapPrice=0.0):
        self.order_statuses[orderId] = {
            "status": status, "filled": filled, "remaining": remaining,
            "avgFillPrice": avgFillPrice
        }
        print(f"[ORDER {orderId}] {status}: filled={filled}, remaining={remaining}, avg={avgFillPrice}")
        if status in ("Filled", "Cancelled", "Inactive"):
            self.order_done.set()

    def openOrder(self, orderId, contract, order, orderState):
        print(f"[OPEN ORDER {orderId}] {contract.symbol} {order.action} "
              f"{order.totalQuantity} @ {order.lmtPrice}; state={orderState.status}")

    def connect_and_start(self, host: str, port: int, client_id: int, timeout: int = 15):
        self.connect(host, port, client_id)
        thread = threading.Thread(target=self.run, daemon=True)
        thread.start()
        if not self.ready.wait(timeout):
            self.disconnect()
            raise TimeoutError("IBKR connection handshake timed out.")
        return thread

    def request_account_summary(self, timeout: int = 15):
        self.account_values.clear()
        self.account_done.clear()
        self.reqAccountSummary(9001, "All", "NetLiquidation,TotalCashValue,BuyingPower")
        if not self.account_done.wait(timeout):
            raise TimeoutError("Timed out waiting for account summary.")
        self.cancelAccountSummary(9001)
        return self.account_values

    def qualify_stock(self, symbol: str, currency: str, primary_exchange: str,
                      timeout: int = 15):
        contract = Contract()
        contract.symbol = symbol.upper()
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = currency.upper()
        contract.primaryExchange = primary_exchange.upper()

        self.contract_details.clear()
        self.contract_done.clear()
        self.reqContractDetails(9002, contract)
        if not self.contract_done.wait(timeout):
            raise TimeoutError("Timed out waiting for contract details.")
        if len(self.contract_details) != 1:
            raise RuntimeError(
                f"Expected exactly one contract match, got {len(self.contract_details)}. "
                "Check symbol, currency and primary exchange in TWS Contract Details."
            )
        return self.contract_details[0], contract

    def request_daily_history(self, contract, duration="30 D", bar_size="1 day",
                              timeout: int = 30):
        self.bars.clear()
        self.history_done.clear()
        self.reqHistoricalData(
            9003, contract, "", duration, bar_size, "TRADES",
            1, 1, False, []
        )
        if not self.history_done.wait(timeout):
            self.cancelHistoricalData(9003)
            raise TimeoutError("Timed out waiting for historical bars.")
        return self.bars

    def submit_paper_limit_order(self, contract, quantity: int, limit_price: float):
        if self.next_order_id is None:
            raise RuntimeError("No valid IBKR order ID.")
        order = Order()
        order.action = "BUY"
        order.orderType = "LMT"
        order.totalQuantity = quantity
        order.lmtPrice = limit_price
        order.tif = "DAY"
        order.transmit = True

        order_id = self.next_order_id
        self.next_order_id += 1
        self.placeOrder(order_id, contract, order)
        return order_id
