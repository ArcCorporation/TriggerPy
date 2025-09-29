import logging
import time
from model import AppModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

logging.info("=== Automated AppModel Test (TSLA) ===")

model = AppModel()

# 1. Sembol ayarla
price = model.set_symbol("TSLA")
logging.info(f"Symbol set: TSLA, market price={price}")
time.sleep(3)
# 2. Expiry listesi al
conid = model.get_conid("TSLA")
expiries = model.get_maturities("TSLA")
time.sleep(1)
if expiries:
    expiries = sorted(expiries)
    #logging.info(f"All expiries ({len(expiries)} total): {expiries[:15]} ...")  # ilk 15 tanesini yaz
    expiry = expiries[0]  # en yakın vade
    logging.info(f"Chosen expiry: {expiry}")

    # 3. Option chain çek
    chain = model.get_option_chain("TSLA", expiry)
    if chain:
        strike = chain[0]["strike"]
        right = chain[0]["right"]

        logging.info(f"Testing option: expiry={expiry}, strike={strike}, right={right}")
        model.set_option(expiry, strike, right)

        # 4. Risk parametreleri
        model.set_risk(stop_loss=4.0, take_profit=7.0)

        # 5. Order
        try:
            order = model.place_order(action="BUY", quantity=1)
            logging.info(f"Order placed: {order}")
        except Exception as e:
            logging.error(f"Order placement failed: {e}")
    else:
        logging.warning(f"No option chain data for expiry {expiry}")
else:
    logging.error("No expiries available.")
