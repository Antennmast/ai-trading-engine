import argparse
import json
import time

from app.config import Settings
from app.ibkr_client import IBKRApp


def parse_args():
    p = argparse.ArgumentParser(description="AI Trading Engine V1 - IBKR Paper connector")
    p.add_argument("--account", action="store_true", help="Read account summary")
    p.add_argument("--history", action="store_true", help="Resolve contract and fetch daily history")
    p.add_argument("--paper-limit-order", action="store_true",
                   help="Submit an explicitly enabled Paper Trading limit test order")
    p.add_argument("--symbol", default="EQNR")
    p.add_argument("--currency", default="NOK")
    p.add_argument("--primary-exchange", default="OSE")
    p.add_argument("--quantity", type=int, default=1)
    p.add_argument("--limit-price", type=float, default=1.00)
    return p.parse_args()


def main():
    args = parse_args()
    settings = Settings()
    settings.validate_paper_mode()

    if not (args.account or args.history or args.paper_limit_order):
        raise SystemExit("Choose --account, --history or --paper-limit-order.")

    if args.paper_limit_order:
        if not settings.allow_paper_test_orders:
            raise SystemExit("Order blocked. Set ALLOW_PAPER_TEST_ORDERS=true explicitly.")
        if args.currency.upper() != "NOK":
            raise SystemExit("Scaffold order guard: test orders are restricted to NOK until FX-aware sizing is implemented.")
        if args.quantity != 1 or args.limit_price <= 0:
            raise SystemExit("Scaffold order guard: use exactly 1 share and a positive limit price.")
        estimated_value = args.quantity * args.limit_price
        if estimated_value > settings.starting_capital_nok * settings.max_position_weight:
            raise SystemExit("Order blocked: exceeds configured 20% position cap (nominal NOK check).")

    app = IBKRApp()
    app.connect_and_start(settings.host, settings.port, settings.client_id)

    try:
        if args.account:
            rows = app.request_account_summary()
            print("\nACCOUNT SUMMARY")
            print(json.dumps(rows, indent=2))

        if args.history or args.paper_limit_order:
            details, contract = app.qualify_stock(
                args.symbol, args.currency, args.primary_exchange
            )
            print("\nQUALIFIED CONTRACT")
            print(json.dumps(details, indent=2))

        if args.history:
            bars = app.request_daily_history(
                contract, settings.historical_duration, settings.historical_bar_size
            )
            print(f"\nHISTORICAL BARS ({len(bars)})")
            print(json.dumps(bars, indent=2))

        if args.paper_limit_order:
            print("\nSubmitting PAPER limit test order. Verify Paper Trading login in IB Gateway.")
            order_id = app.submit_paper_limit_order(
                contract, args.quantity, args.limit_price
            )
            print(f"Submitted order ID {order_id}; monitoring status for up to 20 seconds.")
            deadline = time.time() + 20
            while time.time() < deadline and order_id not in app.order_statuses:
                time.sleep(0.25)
            print("Latest status:", app.order_statuses.get(order_id, "No status callback received yet"))
            print("Cancel manually in TWS/IB Gateway if the order remains open.")

    finally:
        time.sleep(1)
        app.disconnect()
        print("[IBKR] Disconnected.")


if __name__ == "__main__":
    main()
