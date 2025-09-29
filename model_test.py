import logging
import time
from model import AppModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

logging.info("=== Automated AppModel Test (TSLA) ===")

model = AppModel()

# 1. Sembol ayarla
price = model.set_symbol("TSLA")
logging.info(f"Symbol set: TSLA, market price={price}")

# 2. Expiry listesi çek (retry ile)
expiries = []
for attempt in range(3):
    expiries = model.get_maturities("TSLA")
    if expiries:
        break
    logging.warning(f"No expiries yet (attempt {attempt+1}/3), retrying...")
    time.sleep(2)

if expiries:
    expiries = sorted(expiries)  # garanti sıralı
    expiry = expiries[0]
    logging.info(f"Available expiries: {expiries[:5]} ... total={len(expiries)}")

    # 3. İlk expiry için chain’den strike seç
    chain = model.get_option_chain("TSLA", expiry)
    if chain:
        strike = chain[0]["strike"]
        right = chain[0]["right"]

        logging.info(f"Testing option: expiry={expiry}, strike={strike}, right={right}")
        model.set_option(expiry, strike, right)

        # 4. Risk parametrelerini ayarla
        model.set_risk(stop_loss=4.0, take_profit=7.0)

        # 5. Bracket order simülasyonu
        try:
            order = model.place_order(action="BUY", quantity=1)
            logging.info(f"Order placed: {order}")
        except Exception as e:
            logging.error(f"Order placement failed: {e}")
    else:
        logging.warning(f"No option chain data for expiry {expiry}")
else:
    logging.error("No expiries available after retries.")
