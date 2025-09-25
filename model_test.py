from model import AppModel

if __name__ == "__main__":
    model = AppModel()
    print("=== AppModel CLI Test ===")
    print("Komutlar: symbol <SYM>, risk <SL> <TP>, option <EXPIRY> <STRIKE> <C/P>, order <BUY/SELL> <QTY>, show, quit")

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
        elif cmd[0] == "order" and len(cmd) == 3:
            action, quantity = cmd[1].upper(), int(cmd[2])
            order = model.place_order(action=action, quantity=quantity)
            print(f"Order placed: {order}")
        elif cmd[0] == "show":
            print("State:", model.get_state())
        else:
            print("Geçersiz komut")
