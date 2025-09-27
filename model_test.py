from model import AppModel

if __name__ == "__main__":
    model = AppModel()
    print("=== AppModel CLI Test ===")
    print("⚠️  Please run IBKR TWS (Trader Workstation) on port 7497 before placing orders.")
    print("Commands:")
    print("  symbol <SYM>                 - set the trading symbol (e.g. AAPL, TSLA)")
    print("  risk <SL> <TP>               - set stop loss and take profit prices")
    print("  option <EXPIRY> <STRIKE> <C/P> - set option contract (expiry YYYYMMDD, strike, CALL/PUT)")
    print("  order <BUY/SELL> <QTY> [TRIGGER] - place order, optionally with a trigger price")
    print("  show                         - display current state")
    print("  quit                         - exit the test")

    while True:
        cmd = input("> ").strip().split()
        if not cmd:
            continue
        if cmd[0] == "quit":
            break
        elif cmd[0] == "symbol" and len(cmd) == 2:
            price = model.set_symbol(cmd[1])
            print(f"Symbol set: {model.symbol}, price={price}")
        elif cmd[0] == "risk" and len(cmd) == 3:
            sl, tp = float(cmd[1]), float(cmd[2])
            model.set_risk(sl, tp)
            print(f"StopLoss={sl}, TakeProfit={tp}")
        elif cmd[0] == "option" and len(cmd) == 4:
            expiry, strike, right = cmd[1], float(cmd[2]), cmd[3]
            model.set_option(expiry, strike, right)
            print(f"Option set: expiry={expiry}, strike={strike}, right={right}")
        elif cmd[0] == "order" and (len(cmd) == 3 or len(cmd) == 4):
            action, quantity = cmd[1].upper(), int(cmd[2])
            trigger = float(cmd[3]) if len(cmd) == 4 else None
            order = model.place_order(action=action, quantity=quantity, trigger=trigger)
            print(f"Order placed: id={order['order_id']}, state={order['state']}, trigger={order['trigger']}")
        elif cmd[0] == "show":
            print("State:", model.get_state())
        else:
            print("Invalid command")
