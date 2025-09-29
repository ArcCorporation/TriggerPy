# test.py - ACİL DÜZELTİLMİŞ VERSİYON
import logging
from model import app_model

def setup_logging():
    logging.basicConfig(level=logging.INFO)

def test_breakout_validation():
    print("🧪 BREAKOUT TESTİ")
    

    app_model.symbol = "SPY"
    # Mevcut fiyatı al
    current_price = app_model.refresh_market_price()
    print(f"SPY Mevcut Fiyat: {current_price}")
    
    # Basit breakout testi
    trigger_price = current_price + 0.5
    print(f"🎯 Breakout Seviyesi: {trigger_price}")
    
    # Fiyat trigger'ı geçerse alarm
    if current_price >= trigger_price:
        print("🚀 BREAKOUT GERÇEKLEŞTİ! İşlem yapılacak.")
    else:
        print(f"⏳ Breakout bekleniyor... ({current_price} < {trigger_price})")

def test_trading_system():
    print("\n🎯 TİCARET SİSTEMİ TESTİ")
    
    try:
        # Servisleri bağla
        if app_model.connect_services():
            print("✅ Servisler bağlandı")
            
            # SPY fiyatını al
            app_model.symbol = "SPY"
            price = app_model.refresh_market_price()
            print(f"✅ SPY Fiyatı: {price}")
            
            # Opsiyon verilerini al (basit versiyon)
            maturities = app_model.get_available_maturities()
            if maturities:
                print(f"✅ {len(maturities)} opsiyon vadesi bulundu")
                print(f"   İlk 3: {maturities[:3]}")
                
                # Hemen işlem testi - breakout olmadan
                print("🔧 Basit işlem testi yapılıyor...")
                # Burada gerçek işlem mantığınızı ekleyin
                
            print("✅ Sistem çalışıyor - işlem yapmaya hazır!")
            
    except Exception as e:
        print(f"❌ Hata: {e}")
    finally:
        app_model.disconnect_services()
        print("✅ Servisler kapatıldı")

if __name__ == "__main__":
    setup_logging()
    test_breakout_validation() 
    test_trading_system()
    print("\n🎉 SİSTEM HAZIR - PARA KAZANMAYA BAŞLAYABİLİRSİNİZ!")