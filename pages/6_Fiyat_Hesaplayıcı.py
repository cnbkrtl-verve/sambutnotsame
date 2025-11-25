# pages/6_Fiyat_Hesaplayıcı.py

import streamlit as st
import sys
import os

# Projenin ana dizinini Python'un arama yoluna ekle
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)


# 🎨 GLOBAL CSS YÜKLEME
from utils.style_loader import load_global_css
load_global_css()
import pandas as pd
import math
import numpy as np
import json
from io import StringIO
import queue
import threading
import time
import logging
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

# Logging ayarları
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# gsheets_manager.py'den gerekli fonksiyonları içe aktar
from operations.price_sync import SmartRateLimiter, update_prices_for_single_product
from gsheets_manager import load_pricing_data_from_gsheets, save_pricing_data_to_gsheets
from connectors.shopify_api import ShopifyAPI
from connectors.sentos_api import SentosAPI
from data_manager import load_user_data
from config_manager import load_all_user_keys

class RateLimiter:
    def __init__(self, requests_per_second: float):
        self.min_interval = 1.0 / requests_per_second
        self.lock = threading.Lock()
        self.last_request_time = 0

    def wait(self):
        with self.lock:
            elapsed = time.time() - self.last_request_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_request_time = time.time()

# Threading ayarlarını güvenli hale getirin
def get_safe_thread_settings():
    """10 worker için optimize edilmiş ayarlar"""
    return {
        'worker_count': 10,          # Maksimum 10 worker
        'requests_per_second': 2.5,  # Daha hızlı
        'batch_size': 200,           # Daha büyük batch
        'retry_count': 5,
        'base_delay': 2
    }

# --- Sayfa Kurulumu ve Kontroller ---
def load_css():
    try:
        with open("style.css", encoding='utf-8') as f:
            st.markdown(f'<style>{f.read()}</style>', unsafe_allow_html=True)
    except FileNotFoundError:
        pass
    except UnicodeDecodeError:
        st.error("CSS dosyası encoding hatası.")
    

# YENİ: Oturum durumu için başlangıç değerlerini ayarlayan fonksiyon
def initialize_session_state_defaults():
    defaults = {
        'authentication_status': None, 'shopify_status': 'pending', 'sentos_status': 'pending',
        'shopify_data': {}, 'sentos_data': {}, 'user_data_loaded_for': None,
        'price_df': None, 'calculated_df': None,
        'shopify_store': None, 'shopify_token': None,
        'sentos_api_url': None, 'sentos_api_key': None, 'sentos_api_secret': None, 'sentos_cookie': None,
        'update_in_progress': False,
        'sync_progress_queue': queue.Queue()
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

# YENİ: Kullanıcıya özel sırları ve verileri yükler
def load_and_verify_user_data(username):
    if st.session_state.get('user_data_loaded_for') == username:
        return
    user_keys = load_all_user_keys(username)
    st.session_state.update(user_keys)
    user_price_data = load_user_data(username)
    try:
        if price_df_json := user_price_data.get('price_df_json'):
            st.session_state.price_df = pd.read_json(StringIO(price_df_json), orient='split')
        if calculated_df_json := user_price_data.get('calculated_df_json'):
            st.session_state.calculated_df = pd.read_json(StringIO(calculated_df_json), orient='split')
    except Exception:
        st.session_state.price_df, st.session_state.calculated_df = None, None
    st.session_state['user_data_loaded_for'] = username

# Ana sayfa mantığını sayfaya taşıma
if not st.session_state.get("authentication_status"):
    st.error("Lütfen bu sayfaya erişmek için giriş yapın.")
    st.stop()

# API bilgilerinin oturumda olduğundan emin ol
if st.session_state.get('user_data_loaded_for') != st.session_state.get('username'):
    load_and_verify_user_data(st.session_state.get('username'))    

if not st.session_state.get("authentication_status"):
    st.error("Lütfen bu sayfaya erişmek için giriş yapın.")
    st.stop()

load_css()

# --- YARDIMCI FONKSİYONLAR ---
def process_sentos_data(product_list):
    all_variants_rows = []
    main_products_rows = []
    for p in product_list:
        main_sku = p.get('sku')
        main_name = p.get('name')
        try:
            main_price_str = str(p.get('purchase_price') or p.get('AlisFiyati') or '0').replace(',', '.')
            main_purchase_price = float(main_price_str)
        except (ValueError, TypeError):
            main_purchase_price = 0.0
        main_products_rows.append({
            'MODEL KODU': main_sku, 'ÜRÜN ADI': main_name, 'ALIŞ FİYATI': main_purchase_price
        })
        variants = p.get('variants', [])
        if not variants:
            all_variants_rows.append({
                'base_sku': main_sku, 'MODEL KODU': main_sku,
                'ÜRÜN ADI': main_name, 'ALIŞ FİYATI': main_purchase_price
            })
        else:
            for v in variants:
                try:
                    variant_price_str = str(v.get('purchase_price') or v.get('AlisFiyati') or '0').replace(',', '.')
                    variant_purchase_price = float(variant_price_str)
                except (ValueError, TypeError):
                    variant_purchase_price = 0.0
                final_price = variant_purchase_price if variant_purchase_price > 0 else main_purchase_price
                color = v.get('color', '').strip()
                model_data = v.get('model', '')
                size = (model_data.get('value', '') if isinstance(model_data, dict) else str(model_data)).strip()
                attributes = [attr for attr in [color, size] if attr]
                suffix = " - " + " / ".join(attributes) if attributes else ""
                variant_name = f"{main_name}{suffix}".strip()
                all_variants_rows.append({
                    'base_sku': main_sku, 'MODEL KODU': v.get('sku'),
                    'ÜRÜN ADI': variant_name, 'ALIŞ FİYATI': final_price
                })
    df_variants = pd.DataFrame(all_variants_rows)
    df_main_products = pd.DataFrame(main_products_rows).drop_duplicates(subset=['MODEL KODU'])
    
    # --- YENİ EKLENEN KISIM BURASI ---
    # Veri tipi tutarsızlığını ve Arrow hatasını önlemek için SKU/Model Kodu sütunlarının
    # veri tipini her zaman metin (string) olarak zorunlu kılıyoruz.
    if not df_variants.empty:
        df_variants['MODEL KODU'] = df_variants['MODEL KODU'].astype(str)
        df_variants['base_sku'] = df_variants['base_sku'].astype(str)
    if not df_main_products.empty:
        df_main_products['MODEL KODU'] = df_main_products['MODEL KODU'].astype(str)
    # --- YENİ KISIM BİTİŞ ---

    return df_variants, df_main_products

def apply_rounding(price, method):
    if method == "Yukarı Yuvarla":
        if price % 10 != 9.99 and price % 10 != 9: 
            return math.floor(price / 10) * 10 + 9.99
        elif price % 1 == 0: 
            return price - 0.01
        return price
    elif method == "Aşağı Yuvarla":
        return math.floor(price / 10) * 10 - 0.01 if price > 10 else 9.99
    return price

# --- Session State Başlatma ---
st.session_state.setdefault('calculated_df', None)
st.session_state.setdefault('df_for_display', None)
st.session_state.setdefault('df_variants', None)
st.session_state.setdefault('retail_df', None)
st.session_state.setdefault('sync_progress_queue', queue.Queue())
st.session_state.setdefault('sync_log_list', [])
st.session_state.setdefault('update_in_progress', False)
st.session_state.setdefault('sync_results', None)
st.session_state.setdefault('last_failed_skus', [])
st.session_state.setdefault('last_update_results', {})

def _process_one_product_for_price_sync(shopify_api, product_base_sku, all_variants_df, price_data_df, price_col, compare_col, rate_limiter):
    """
    GraphQL ile optimize edilmiş tek ürün işleme fonksiyonu.
    Tek GraphQL call ile hem ürün hem varyantları al ve güncelle.
    """
    try:
        # İlk önce fiyat verisini kontrol et
        price_row = price_data_df.loc[price_data_df['MODEL KODU'] == product_base_sku]
        if price_row.empty:
            return {"status": "skipped", "reason": f"Fiyat bulunamadı: {product_base_sku}"}
        
        price_to_set = price_row.iloc[0][price_col]
        compare_price_to_set = price_row.iloc[0].get(compare_col)

        # GraphQL ile ürün ve tüm varyantlarını al
        query = """
        query getProductWithVariants($query: String!) {
            products(first: 1, query: $query) {
                edges {
                    node {
                        id
                        variants(first: 100) {
                            edges {
                                node {
                                    id
                                    sku
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        
        rate_limiter.wait()
        result = shopify_api.execute_graphql(query, {"query": f"sku:{product_base_sku}*"})
        
        product_edges = result.get("products", {}).get("edges", [])
        if not product_edges:
            return {"status": "failed", "reason": f"Shopify'da ürün bulunamadı: {product_base_sku}"}
        
        product = product_edges[0]['node']
        product_id = product['id']
        
        # Base SKU ile başlayan varyantları filtrele ve updates hazırla
        updates = []
        for v_edge in product.get('variants', {}).get('edges', []):
            variant = v_edge['node']
            variant_sku = variant.get('sku', '')
            
            if variant_sku.startswith(product_base_sku):
                payload = {
                    "id": variant['id'],
                    "price": f"{price_to_set:.2f}"
                }
                if compare_price_to_set is not None and pd.notna(compare_price_to_set):
                    payload["compareAtPrice"] = f"{compare_price_to_set:.2f}"
                updates.append(payload)

        if not updates:
            return {"status": "skipped", "reason": "Eşleşen varyant bulunamadı."}

        # GraphQL bulk mutation ile fiyatları güncelle
        from operations.price_sync import update_prices_for_single_product
        return update_prices_for_single_product(shopify_api, product_id, updates, rate_limiter)

    except Exception as e:
        return {"status": "failed", "reason": str(e)}


def _run_price_sync(
    shopify_store, shopify_token, 
    calculated_df, retail_df, variants_df, 
    update_choice, worker_count, queue, **kwargs
):
    """
    10 WORKER OPTİMİZE EDİLMİŞ: Bulk Fetch + In-Memory Diff + Threaded Update
    """
    try:
        safe_settings = get_safe_thread_settings()
        actual_worker_count = min(worker_count, 10)
        
        logging.info(f"10-Worker sistemi aktif: {actual_worker_count} worker")
        
        import pandas as pd
        shopify_api = ShopifyAPI(shopify_store, shopify_token)
        
        # 1. HEDEF FİYATLARI HAZIRLA
        price_data_df = retail_df if update_choice == "İndirimli Fiyatlar" else calculated_df
        price_col = 'İNDİRİMLİ SATIŞ FİYATI' if update_choice == "İndirimli Fiyatlar" else 'NIHAI_SATIS_FIYATI'
        compare_col = 'NIHAI_SATIS_FIYATI' if update_choice == "İndirimli Fiyatlar" else None

        if variants_df is None or price_data_df is None:
            raise ValueError("Güncelleme için veri bulunamadı.")
            
        # Fiyat haritası oluştur: SKU -> {price, compare_at}
        target_prices = {}
        for _, row in price_data_df.iterrows():
            sku = str(row['MODEL KODU']).strip()
            try:
                price = float(row[price_col])
                compare = float(row[compare_col]) if compare_col and pd.notna(row.get(compare_col)) else None
                target_prices[sku] = {'price': price, 'compare': compare}
            except (ValueError, TypeError):
                continue
                
        # Varyant tablosundan da ekle (Eğer ana tabloda yoksa)
        # Not: Genellikle ana tablo yeterlidir ama varyant bazlı fiyat varsa buraya eklenebilir.
        
        queue.put({'progress': 5, 'message': 'Mevcut Shopify fiyatları çekiliyor (Bulk)...'})
        
        # 2. MEVCUT SHOPIFY VERİLERİNİ ÇEK (BULK)
        def fetch_progress(msg):
            queue.put({'progress': 10, 'message': msg})
            
        try:
            current_shopify_data = shopify_api.get_all_products_prices(progress_callback=fetch_progress)
        except Exception as e:
            logging.error(f"Shopify veri çekme hatası: {e}")
            raise ValueError(f"Shopify'dan fiyat verileri çekilemedi: {e}")
        
        if not current_shopify_data:
            raise ValueError("Shopify'dan ürün verisi çekilemedi (Liste boş).")
            
        queue.put({'progress': 20, 'message': 'Değişiklikler analiz ediliyor...'})
        
        # 3. DIFF ANALİZİ (Hangi ürünler güncellenmeli?)
        products_to_update = defaultdict(list) # product_id -> [variant_updates]
        
        skipped_count = 0
        total_variants_checked = 0
        
        # --- DIFF MANTIĞI REVİZE ---
        # variants_df kullanarak: Variant SKU -> Base SKU -> Target Price
        variant_to_price_map = {}
        
        # 1. Ana ürün fiyatlarını al
        base_sku_prices = target_prices # Base SKU -> Price
        
        # 2. Varyantların fiyatlarını belirle
        # base_sku sütunu kontrolü
        has_base_sku = 'base_sku' in variants_df.columns
        
        for _, row in variants_df.iterrows():
            v_sku = str(row['MODEL KODU']).strip()
            b_sku = str(row['base_sku']).strip() if has_base_sku else v_sku
            
            if b_sku in base_sku_prices:
                variant_to_price_map[v_sku] = base_sku_prices[b_sku]
        
        # 3. Şimdi Shopify verilerini tara
        for item in current_shopify_data:
            total_variants_checked += 1
            sku = str(item.get('sku', '')).strip()
            product_id = item.get('product_id')
            variant_id = item.get('variant_id')
            current_price = float(item.get('price', 0))
            current_compare = float(item.get('compare_at_price')) if item.get('compare_at_price') else None
            
            target = variant_to_price_map.get(sku)
            
            # Eğer varyant SKU'su listede yoksa, belki ana SKU'dur?
            if not target and sku in base_sku_prices:
                target = base_sku_prices[sku]
            
            if target:
                new_price = target['price']
                new_compare = target['compare']
                
                # Değişiklik var mı?
                price_changed = abs(current_price - new_price) > 0.01
                compare_changed = False
                if new_compare is not None:
                    if current_compare is None:
                        compare_changed = True
                    else:
                        compare_changed = abs(current_compare - new_compare) > 0.01
                elif current_compare is not None:
                    # Yeni compare yok ama eskisi var -> silinmeli mi? 
                    # Mevcut mantıkta compare silme yok, sadece varsa güncelleme var.
                    pass
                
                if price_changed or compare_changed:
                    payload = {"id": variant_id, "price": f"{new_price:.2f}"}
                    if new_compare is not None:
                        payload["compareAtPrice"] = f"{new_compare:.2f}"
                    
                    products_to_update[product_id].append(payload)
                else:
                    skipped_count += 1
        
        total_products_to_update = len(products_to_update)
        queue.put({'progress': 30, 'message': f'Analiz tamamlandı. {total_products_to_update} ürün güncellenecek. ({skipped_count} varyant atlandı)'})
        
        if total_products_to_update == 0:
            queue.put({
                "status": "done", 
                "results": {
                    "success": 0, "failed": 0, "details": [],
                    "avg_rate": "0", "total_time": "0"
                }
            })
            return

        # 4. GÜNCELLEME (THREADED)
        processed_count = 0
        success_count = 0
        failed_count = 0
        failed_details = []
        start_time = time.time()
        
        from operations.price_sync import SmartRateLimiter, update_prices_for_single_product
        rate_limiter = SmartRateLimiter(max_requests_per_second=2.5, burst_capacity=15)
        
        with ThreadPoolExecutor(max_workers=actual_worker_count) as executor:
            # Future -> Product ID map
            futures = {
                executor.submit(
                    update_prices_for_single_product, 
                    shopify_api, p_id, updates, rate_limiter
                ): p_id 
                for p_id, updates in products_to_update.items()
            }
            
            for future in as_completed(futures):
                processed_count += 1
                p_id = futures[future]
                
                try:
                    result = future.result()
                    if result.get('status') == 'success':
                        success_count += 1
                        # queue.put({'log_detail': f"✅ Ürün {p_id}: Güncellendi"})
                    else:
                        failed_count += 1
                        failed_details.append({"sku": f"GID-{p_id}", "status": "failed", "reason": result.get('reason')})
                        queue.put({'log_detail': f"❌ Ürün {p_id}: {result.get('reason')}"})
                except Exception as e:
                    failed_count += 1
                    queue.put({'log_detail': f"❌ Ürün {p_id}: Hata - {e}"})
                
                # İstatistikler
                elapsed = time.time() - start_time
                rate = processed_count / elapsed if elapsed > 0 else 0
                eta = (total_products_to_update - processed_count) / rate / 60 if rate > 0 else 0
                
                if processed_count % 5 == 0 or processed_count == total_products_to_update:
                    progress = 30 + int((processed_count / total_products_to_update) * 70)
                    queue.put({
                        'progress': progress,
                        'message': f'Güncelleniyor: {processed_count}/{total_products_to_update} (Hız: {rate:.1f}/sn)',
                        'stats': {'rate': rate, 'eta': eta, 'success': success_count, 'failed': failed_count}
                    })

        total_time = time.time() - start_time
        avg_rate = processed_count / total_time if total_time > 0 else 0
        
        queue.put({
            "status": "done", 
            "results": {
                "success": success_count, 
                "failed": failed_count, 
                "details": failed_details,
                "avg_rate": f"{avg_rate:.2f} ürün/sn",
                "total_time": f"{total_time:.1f} saniye"
            }
        })

    except Exception as e:
        logging.error(f"Sync hatası: {traceback.format_exc()}")
        queue.put({"status": "error", "message": str(e)})

# --- ARAYÜZ ---
st.markdown("""
<div class="main-header">
    <h1>📊 Fiyat Stratejisi Panosu</h1>
    <p>Fiyat senaryoları oluşturun, Google E-Tablolar'a kaydedin ve Shopify'a gönderin.</p>
</div>
""", unsafe_allow_html=True)

# Adım 1: Veri Yükleme
st.subheader("Adım 1: Ürün Verilerini Yükle")
if st.session_state.df_for_display is None:
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Sentos'tan Yeni Fiyat Listesi Çek", use_container_width=True, disabled=st.session_state.update_in_progress):
            progress_bar = st.progress(0, text="Sentos API'ye bağlanılıyor...")
            def progress_callback(update):
                progress = update.get('progress', 0)
                message = update.get('message', 'Veriler işleniyor...')
                progress_bar.progress(progress / 100.0, text=message)
            try:
                sentos_api = SentosAPI(
                    st.session_state.sentos_api_url, 
                    st.session_state.sentos_api_key, 
                    st.session_state.sentos_api_secret, 
                    st.session_state.sentos_cookie
                )
                all_products = sentos_api.get_all_products(progress_callback=progress_callback)
                progress_bar.progress(100, text="Veriler işleniyor ve gruplanıyor...")
                if not all_products:
                    st.error("❌ Sentos API'den hiç ürün verisi gelmedi.")
                    progress_bar.empty()
                else:
                    df_variants, df_main = process_sentos_data(all_products)
                    st.session_state.df_variants = df_variants
                    st.session_state.df_for_display = df_main
                    progress_bar.empty()
                    st.toast(f"Veriler Sentos'tan çekildi. {len(df_main)} ana ürün ve {len(df_variants)} varyant hafızaya alındı.")
                    st.rerun()
            except Exception as e: 
                if 'progress_bar' in locals():
                    progress_bar.empty()
                st.error(f"API hatası: {e}")
    
    with col2:
        if st.button("📄 Kayıtlı Veriyi G-Sheets'ten Yükle", use_container_width=True, disabled=st.session_state.update_in_progress):
            with st.spinner("Google E-Tablolardan veriler yükleniyor..."):
                main_df, variants_df = load_pricing_data_from_gsheets()
            if main_df is not None and not main_df.empty:
                st.session_state.calculated_df = main_df
                st.session_state.df_for_display = main_df[['MODEL KODU', 'ÜRÜN ADI', 'ALIŞ FİYATI']]
                st.session_state.df_variants = variants_df
                
                variant_msg = ""
                if variants_df is not None and not variants_df.empty:
                    variant_msg = f" ve {len(variants_df)} varyant"
                
                st.toast(f"Veriler Google E-Tablolar'dan yüklendi{variant_msg}.")
                st.rerun()
            else:
                st.warning("Google E-Tablolar'dan veri yüklenemedi veya dosya boş.")
else:
    main_count = len(st.session_state.df_for_display)
    variants_df = st.session_state.get('df_variants')
    variants_count = len(variants_df) if variants_df is not None and not variants_df.empty else 0
    message = f"✅ {main_count} ana ürün verisi hafızada yüklü."
    if variants_count > 0:
        message += f" | 📦 **{variants_count} varyant verisi** Shopify'a gönderim için hazır."
    st.success(message)
    if st.button("🧹 Verileri Temizle ve Baştan Başla", use_container_width=True, disabled=st.session_state.update_in_progress):
        st.session_state.calculated_df = None
        st.session_state.df_for_display = None
        st.session_state.df_variants = None
        st.session_state.sync_log_list = []
        st.session_state.last_update_results = {}
        st.rerun()

if st.session_state.df_for_display is not None and not st.session_state.update_in_progress:
    st.markdown("---")
    st.subheader("Adım 2: Fiyatlandırma Kurallarını Uygula")
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([2, 2, 2, 2])
        markup_type = c1.radio("Kâr Marjı Tipi", ["Yüzde Ekle (%)", "Çarpan Kullan (x)"], key="markup_type")
        markup_value = c1.number_input("Değer", min_value=0.0, value=100.0 if markup_type == "Yüzde Ekle (%)" else 2.5, step=0.1, key="markup_value")
        add_vat = c2.checkbox("Satışa KDV Dahil Et", value=True, key="add_vat")
        vat_rate = c2.number_input("KDV Oranı (%)", 0, 100, 10, disabled=not add_vat, key="vat_rate")
        rounding_method_text = c3.radio("Fiyat Yuvarlama", ["Yok", "Yukarı (X9.99)", "Aşağı (X9.99)"], index=1, key="rounding")
        if c4.button("💰 Fiyatları Hesapla", type="primary", use_container_width=True):
            df = st.session_state.df_for_display.copy()
            df['SATIS_FIYATI_KDVSIZ'] = df['ALIŞ FİYATI'] * (1 + markup_value / 100) if markup_type == "Yüzde Ekle (%)" else df['ALIŞ FİYATI'] * markup_value
            df['SATIS_FIYATI_KDVLI'] = df['SATIS_FIYATI_KDVSIZ'] * (1 + vat_rate / 100) if add_vat else df['SATIS_FIYATI_KDVSIZ']
            rounding_method_arg = rounding_method_text.replace(" (X9.99)", "").replace("Aşağı", "Aşağı Yuvarla").replace("Yukarı", "Yukarı Yuvarla")
            df['NIHAI_SATIS_FIYATI'] = df['SATIS_FIYATI_KDVLI'].apply(lambda p: apply_rounding(p, rounding_method_arg))
            revenue = df['NIHAI_SATIS_FIYATI'] / (1 + vat_rate / 100) if add_vat else df['NIHAI_SATIS_FIYATI']
            df['KÂR'] = revenue - df['ALIŞ FİYATI']
            df['KÂR ORANI (%)'] = np.divide(df['KÂR'], df['ALIŞ FİYATI'], out=np.zeros_like(df['KÂR']), where=df['ALIŞ FİYATI']!=0) * 100
            st.session_state.calculated_df = df
            st.toast("Fiyatlar hesaplandı.")
            st.rerun()

if st.session_state.calculated_df is not None:
    st.markdown("---")
    st.subheader("Adım 3: Senaryoları Analiz Et")
    df = st.session_state.calculated_df
    vat_rate = st.session_state.get('vat_rate', 10)
    
    with st.expander("Tablo 1: Ana Fiyat ve Kârlılık Listesi (Referans)", expanded=True):
        main_df_display = df[['MODEL KODU', 'ÜRÜN ADI', 'ALIŞ FİYATI', 'SATIS_FIYATI_KDVSIZ', 'NIHAI_SATIS_FIYATI', 'KÂR', 'KÂR ORANI (%)']]
        st.dataframe(main_df_display.style.format({
            'ALIŞ FİYATI': '{:,.2f} ₺', 'SATIS_FIYATI_KDVSIZ': '{:,.2f} ₺', 'NIHAI_SATIS_FIYATI': '{:,.2f} ₺',
            'KÂR': '{:,.2f} ₺', 'KÂR ORANI (%)': '{:.2f}%'
        }), use_container_width=True)
    
    with st.expander("Tablo 2: Perakende İndirim Analizi", expanded=True):
        retail_discount = st.slider("İndirim Oranı (%)", 0, 50, 10, 5, key="retail_slider")
        retail_df = df.copy()
        retail_df['İNDİRİM ORANI (%)'] = retail_discount
        retail_df['İNDİRİMLİ SATIŞ FİYATI'] = retail_df['NIHAI_SATIS_FIYATI'] * (1 - retail_discount / 100)
        revenue_after_discount = retail_df['İNDİRİMLİ SATIŞ FİYATI'] / (1 + vat_rate / 100)
        retail_df['İNDİRİM SONRASI KÂR'] = revenue_after_discount - retail_df['ALIŞ FİYATI']
        retail_df['İNDİRİM SONRASI KÂR ORANI (%)'] = np.divide(retail_df['İNDİRİM SONRASI KÂR'], retail_df['ALIŞ FİYATI'], out=np.zeros_like(retail_df['İNDİRİM SONRASI KÂR']), where=retail_df['ALIŞ FİYATI']!=0) * 100
        st.session_state.retail_df = retail_df
        discount_df_display = retail_df[['MODEL KODU', 'ÜRÜN ADI', 'NIHAI_SATIS_FIYATI', 'İNDİRİM ORANI (%)', 'İNDİRİMLİ SATIŞ FİYATI', 'İNDİRİM SONRASI KÂR', 'İNDİRİM SONRASI KÂR ORANI (%)']]
        st.dataframe(discount_df_display.style.format({
            'NIHAI_SATIS_FIYATI': '{:,.2f} ₺', 'İNDİRİM ORANI (%)': '{:.0f}%', 'İNDİRİMLİ SATIŞ FİYATI': '{:,.2f} ₺',
            'İNDİRİM SONRASI KÂR': '{:,.2f} ₺', 'İNDİRİM SONRASI KÂR ORANI (%)': '{:.2f}%'
        }), use_container_width=True)
    
    with st.expander("Tablo 3: Toptan Satış Fiyat Analizi", expanded=True):
        wholesale_method = st.radio("Toptan Fiyat Yöntemi", ('Çarpanla', 'İndirimle'), horizontal=True, key="ws_method")
        wholesale_df = df.copy()
        if wholesale_method == 'Çarpanla':
            ws_multiplier = st.number_input("Toptan Çarpanı", 1.0, 5.0, 1.8, 0.1)
            wholesale_df["TOPTAN FİYAT (KDV'siz)"] = wholesale_df["ALIŞ FİYATI"] * ws_multiplier
        else:
            ws_discount = st.slider("Perakende Fiyatından İndirim (%)", 10, 70, 40, 5, key="ws_discount")
            wholesale_df["TOPTAN FİYAT (KDV'siz)"] = (wholesale_df["NIHAI_SATIS_FIYATI"] / (1 + vat_rate / 100)) * (1 - ws_discount / 100)
        wholesale_df["TOPTAN FİYAT (KDV'li)"] = wholesale_df["TOPTAN FİYAT (KDV'siz)"] * (1 + vat_rate / 100)
        wholesale_df['TOPTAN KÂR'] = wholesale_df["TOPTAN FİYAT (KDV'siz)"] - wholesale_df["ALIŞ FİYATI"]
        wholesale_df_display = wholesale_df[['MODEL KODU', 'ÜRÜN ADI', 'NIHAI_SATIS_FIYATI', "TOPTAN FİYAT (KDV'siz)", "TOPTAN FİYAT (KDV'li)", 'TOPTAN KÂR']]
        st.dataframe(wholesale_df_display.style.format({
            'NIHAI_SATIS_FIYATI': '{:,.2f} ₺', "TOPTAN FİYAT (KDV'siz)": '{:,.2f} ₺', "TOPTAN FİYAT (KDV'li)": '{:,.2f} ₺', 'TOPTAN KÂR': '{:,.2f} ₺'
        }), use_container_width=True)

    st.markdown("---")
    st.subheader("Adım 4: Kaydet ve Shopify'a Gönder")
    
    col1, col2 = st.columns(2)
    with col1:
        if st.button("💾 Fiyatları Google E-Tablolar'a Kaydet", use_container_width=True, disabled=st.session_state.update_in_progress):
            if st.session_state.df_variants is None or st.session_state.df_variants.empty:
                st.error("❌ HATA: Hafızada varyant verisi bulunamadı!")
                st.info("💡 Çözüm önerileri:")
                col_a, col_b = st.columns(2)
                with col_a:
                    st.write("1️⃣ Sentos'tan veri çekin (önerilen)")
                    st.write("2️⃣ Google Sheets'ten veri yükleyin")
                with col_b:
                    st.write("3️⃣ Varyantların kaydedildiğinden emin olun")
                    st.write("4️⃣ Sayfayı yenileyin ve tekrar deneyin")
            else:
                with st.spinner("Veriler Google E-Tablolar'a kaydediliyor..."):
                    
                    # --- DEĞİŞİKLİK BURADA ---
                    # Önceden sadece 4 sütun kaydediliyordu. Şimdi Tablo 1'de
                    # görüntülenen tüm ilgili sütunları kaydediyoruz.
                    cols_to_save = [
                        'MODEL KODU', 'ÜRÜN ADI', 'ALIŞ FİYATI', 
                        'SATIS_FIYATI_KDVSIZ', 'NIHAI_SATIS_FIYATI', 
                        'KÂR', 'KÂR ORANI (%)'
                    ]
                    # Kaydedilecek DataFrame'i bu sütunlarla oluştur
                    main_df_to_save = st.session_state.calculated_df[cols_to_save]
                    
                    discount_df = st.session_state.retail_df[['MODEL KODU', 'ÜRÜN ADI', 'İNDİRİMLİ SATIŞ FİYATI']]
                    wholesale_df = wholesale_df[['MODEL KODU', 'ÜRÜN ADI', "TOPTAN FİYAT (KDV'li)"]]
                    
                    success, url = save_pricing_data_to_gsheets(
                        main_df_to_save,  # Değiştirilmiş DataFrame'i buraya veriyoruz
                        discount_df, 
                        wholesale_df, 
                        st.session_state.df_variants
                    )
                    
                if success: 
                    variant_info = ""
                    if st.session_state.df_variants is not None and not st.session_state.df_variants.empty:
                        variant_info = f" ({len(st.session_state.df_variants)} varyant dahil)"
                    st.success(f"Veriler başarıyla kaydedildi{variant_info}! [E-Tabloyu Görüntüle]({url})")
                else:
                    st.error("Kaydetme sırasında hata oluştu.")
    
        with col2:
            with st.expander("⚙️ Güncelleme Ayarları", expanded=False):
                col_a, col_b = st.columns(2)
    
                # Güvenli ayarları al
                safe_settings = get_safe_thread_settings()
    
            with col_a:
                worker_count = st.slider(
                    "🔧 Paralel Worker Sayısı",
                    min_value=1,
                    max_value=10,  # Maksimum 10
                    value=8,       # Varsayılan 8
                    help="10 worker'a kadar desteklenir. Adaptive rate limiting koruması vardır."
                )

                st.info("""
                    **10-Worker Optimizasyon Aktif**
                    - Saniyede 2.5 istek kapasitesi
                    - 15 burst kapasitesi
                    - Adaptive throttle koruması
                    - Tek GraphQL call per product
                """)

            with col_b:
                retry_count = st.number_input(
                    "🔄 Tekrar Deneme Sayısı",
                    min_value=3,
                    max_value=5,
                    value=3,
                    help="Rate limit hatalarında kaç kez tekrar denensin?"
                )
    
                st.info("""
                    **Rate Limit Koruması Aktif**
                    - Saniyede 0.5 istek
                    - İstekler arası 3s minimum bekleme
                    - Bu ayarlar Shopify limitlerini aşmaz
        """)
    
        continue_from_last = st.checkbox(
            "⏯️ Kaldığı yerden devam et",
            value=False,
            help="Önceki güncelleme yarıda kaldıysa, başarısız olanları tekrar dene"
        )
        
        update_choice = st.selectbox("Hangi Fiyat Listesini Göndermek İstersiniz?", ["Ana Fiyatlar", "İndirimli Fiyatlar"])
        
        if continue_from_last and 'last_update_results' in st.session_state and not st.session_state.update_in_progress:
            last_results = st.session_state.last_update_results
            if last_results and isinstance(last_results, dict):
                st.info(f"""
                📊 Önceki güncelleme sonucu:
                - ✅ Başarılı: {last_results.get('success', 0)}
                - ❌ Başarısız: {last_results.get('failed', 0)}
                - 🔄 Tekrar denenecek: {last_results.get('failed', 0)} varyant
                """)
        
        if st.button(f"🚀 {update_choice} Shopify'a Gönder", use_container_width=True, type="primary", disabled=st.session_state.update_in_progress):
            if st.session_state.df_variants is None or st.session_state.df_variants.empty:
                st.error("❌ HATA: Hafızada varyant verisi bulunamadı!")
                st.info("💡 Çözüm önerileri: Sentos'tan veya Google Sheets'ten veri yükleyin.")
            else:
                st.session_state.update_in_progress = True
                st.session_state.sync_log_list = []
                st.session_state.sync_results = None
                
                # YENİ: Thread'e gönderilecek tüm argümanları bir sözlükte topla
                thread_args = {
                    "shopify_store": st.session_state.get('shopify_store'),
                    "shopify_token": st.session_state.get('shopify_token'),
                    "calculated_df": st.session_state.get('calculated_df'),
                    "retail_df": st.session_state.get('retail_df'),
                    "variants_df": st.session_state.get('df_variants'),
                    "update_choice": update_choice,
                    "continue_from_last": continue_from_last,
                    "last_failed_skus": st.session_state.get('last_failed_skus', []),
                    "worker_count": worker_count,
                    "retry_count": retry_count,
                    "queue": st.session_state.sync_progress_queue
                }

                thread = threading.Thread(
                    target=_run_price_sync,
                    kwargs=thread_args, # args yerine kwargs kullanmak daha okunaklı
                    daemon=True
                )
                thread.start()
                st.rerun()

# --- YENİ BÖLÜM: EKSTRA İŞLEMLER ---
st.markdown("---")
st.subheader("🛠️ Ekstra İşlemler")

extra_tab1, extra_tab2 = st.tabs(["📦 Tekil Ürün Güncelleme", "📚 Koleksiyon Bazlı Güncelleme"])

with extra_tab1:
    st.info("Belirli bir ürünün veya varyantın fiyatını manuel olarak güncelleyin.")
    col_ex1, col_ex2 = st.columns(2)
    
    single_sku = col_ex1.text_input("Ürün SKU veya ID (gid://...)", placeholder="Örn: TSHIRT-001")
    single_price = col_ex2.number_input("Yeni Fiyat (TL)", min_value=0.0, step=0.1)
    single_compare_price = col_ex2.number_input("İndirimsiz Fiyat (Opsiyonel)", min_value=0.0, step=0.1, value=0.0)
    
    if st.button("Tekil Güncelle", disabled=st.session_state.update_in_progress):
        if not single_sku or single_price <= 0:
            st.error("Lütfen geçerli bir SKU ve fiyat girin.")
        else:
            with st.spinner(f"{single_sku} güncelleniyor..."):
                from operations.price_sync import update_single_product_custom
                shopify_api = ShopifyAPI(st.session_state.shopify_store, st.session_state.shopify_token)
                
                result = update_single_product_custom(
                    shopify_api, 
                    single_sku, 
                    single_price, 
                    single_compare_price if single_compare_price > 0 else None
                )
                
                if result.get('status') == 'success':
                    st.success(f"✅ Başarılı! {result.get('updated_count')} varyant güncellendi.")
                else:
                    st.error(f"❌ Hata: {result.get('reason')}")

with extra_tab2:
    st.info("Bir koleksiyondaki tüm ürünlere toplu fiyat kuralı uygulayın.")
    
    if st.button("Koleksiyonları Yükle"):
        with st.spinner("Koleksiyonlar çekiliyor..."):
            shopify_api = ShopifyAPI(st.session_state.shopify_store, st.session_state.shopify_token)
            collections = shopify_api.get_all_collections()
            st.session_state.collections_list = {c['title']: c['id'] for c in collections}
            st.rerun()
    
    if 'collections_list' in st.session_state:
        selected_collection_name = st.selectbox("Koleksiyon Seçin", list(st.session_state.collections_list.keys()))
        selected_collection_id = st.session_state.collections_list[selected_collection_name]
        
        col_rule1, col_rule2 = st.columns(2)
        rule_type = col_rule1.selectbox("İşlem Tipi", [
            ("Yüzde Artır (%)", "percentage_inc"),
            ("Yüzde İndir (%)", "percentage_dec"),
            ("Sabit Tutar Ekle (+TL)", "fixed_amount"),
            ("İndirim Oranını Ayarla (%)", "set_discount_rate"),
            ("İndirimsiz Fiyatı İndirimliye Çevir (%)", "convert_to_discounted")
        ], format_func=lambda x: x[0])
        
        rule_value = col_rule2.number_input("Değer", min_value=0.0, step=1.0, value=10.0)
        
        # Session state initialization for confirmation
        if 'confirm_collection_update' not in st.session_state:
            st.session_state.confirm_collection_update = False

        if st.button("Toplu Güncelleme Başlat", type="primary", disabled=st.session_state.update_in_progress):
            st.session_state.confirm_collection_update = True
        
        if st.session_state.confirm_collection_update:
            st.warning("⚠️ Bu işlem seçili koleksiyondaki tüm ürünlerin fiyatlarını kalıcı olarak değiştirecektir!")
            col_conf1, col_conf2 = st.columns(2)
            with col_conf1:
                if st.button("✅ Evet, Onaylıyorum", type="primary", key="btn_confirm_update"):
                    st.session_state.confirm_collection_update = False # Reset
                    st.session_state.update_in_progress = True
                    # Queue'yu oluştur ve değişkene ata
                    st.session_state.sync_progress_queue = queue.Queue()
                    progress_queue = st.session_state.sync_progress_queue
                    
                    # Thread için gerekli verileri al
                    store_url = st.session_state.shopify_store
                    store_token = st.session_state.shopify_token
                    
                    def run_collection_update(q, store, token, col_id, r_type, r_val):
                        try:
                            q.put({'progress': 1, 'message': 'İşlem başlatılıyor...'})
                            
                            # Importları thread içinde yap
                            from operations.price_sync import update_collection_custom
                            from connectors.shopify_api import ShopifyAPI
                            
                            q.put({'progress': 2, 'message': 'Shopify bağlantısı kuruluyor...'})
                            shopify_api = ShopifyAPI(store, token)
                            
                            q.put({'progress': 5, 'message': 'Koleksiyon verileri çekiliyor...'})
                            result = update_collection_custom(
                                shopify_api, 
                                col_id, 
                                r_type, 
                                r_val, 
                                progress_queue=q
                            )
                            q.put({"status": "done", "results": result})
                            
                        except Exception as e:
                            logging.error(f"Collection update thread error: {e}")
                            q.put({"status": "error", "message": f"İşlem hatası: {str(e)}"})

                    # Thread'i başlat
                    threading.Thread(
                        target=run_collection_update, 
                        args=(progress_queue, store_url, store_token, selected_collection_id, rule_type[1], rule_value),
                        daemon=True
                    ).start()
                    
                    st.rerun()
            
            with col_conf2:
                if st.button("❌ İptal", key="btn_cancel_update"):
                    st.session_state.confirm_collection_update = False
                    st.rerun()

# Eğer bir işlem devam ediyorsa, ilerlemeyi gösteren alanı oluştur
if st.session_state.update_in_progress:
    status_container = st.container()
    progress_container = st.container()
    log_container = st.container()
    
    with progress_container:
        progress_bar = st.progress(0, text="Güncelleme işlemi başlatılıyor...")
        col1, col2, col3 = st.columns(3)
        with col1:
            speed_metric = st.empty()
        with col2:
            eta_metric = st.empty()
        with col3:
            status_metric = st.empty()
    
    with log_container:
        log_placeholder = st.empty()
    
    while st.session_state.update_in_progress:
        try:
            update_data = st.session_state.sync_progress_queue.get(timeout=1)
            
            if "progress" in update_data:
                progress = update_data['progress']
                message = update_data.get('message', 'İşleniyor...')
                progress_bar.progress(progress / 100.0, text=message)
                
            if "stats" in update_data:
                stats = update_data['stats']
                speed_metric.metric("Hız", f"{stats.get('rate', 0):.1f} varyant/sn")
                eta_metric.metric("Tahmini Süre", f"{stats.get('eta', 0):.1f} dakika")
                status_metric.metric("İşlem", f"%{update_data.get('progress', 0)}")
                
            if "log_detail" in update_data:
                st.session_state.sync_log_list.insert(0, f"<div>{update_data['log_detail']}</div>")
                log_html = "".join(st.session_state.sync_log_list[:30])
                log_placeholder.markdown(
                    f'''<div style="height:150px;overflow-y:auto;border:1px solid #444;background:#1e1e1e;padding:10px;border-radius:5px;font-family:monospace;font-size:12px;color:#00ff00;">{log_html}</div>''', 
                    unsafe_allow_html=True
                )
            
            if update_data.get("status") == "done":
                st.session_state.sync_results = update_data.get("results")
                st.session_state.last_update_results = update_data.get("results")
                failed_details = [d for d in st.session_state.sync_results.get("details", []) if d.get("status") == "failed"]
                st.session_state.last_failed_skus = [d.get("sku") for d in failed_details if d.get("sku")]
                st.session_state.update_in_progress = False
                st.rerun()
            
            if update_data.get("status") == "error":
                st.error("Güncelleme sırasında bir hata oluştu: " + update_data.get("message", "Bilinmeyen Hata"))
                st.session_state.update_in_progress = False
                st.rerun()
            
            st.empty()
            
        except queue.Empty:
            time.sleep(0.5)

# İşlem bittiğinde sonuçları göster
if st.session_state.sync_results:
    st.markdown("---")
    st.markdown("## 📊 Güncelleme Özeti")
    
    all_results = st.session_state.sync_results
    total_variants = sum(1 for d in all_results.get('details', []) if d.get('status'))
    
    summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
    with summary_col1:
        st.metric("Toplam İşlenen", total_variants)
    with summary_col2:
        st.metric("✅ Başarılı", all_results.get('success', 0))
    with summary_col3:
        st.metric("❌ Başarısız", all_results.get('failed', 0))
    with summary_col4:
        success_rate = (all_results.get('success', 0) / total_variants * 100) if total_variants > 0 else 0
        st.metric("Başarı Oranı", f"{success_rate:.1f}%")
    
    if all_results.get('failed', 0) > 0:
        st.error(f"❌ {all_results.get('failed', 0)} varyant güncellenemedi.")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔄 Başarısız Olanları Tekrar Dene", use_container_width=True):
                st.session_state.continue_from_last = True
                st.session_state.update_in_progress = False
                st.session_state.sync_results = None
                st.rerun()
        with col2:
            failed_details = [d for d in all_results["details"] if d.get("status") == "failed"]
            if failed_details:
                failed_df = pd.DataFrame(failed_details)
                csv = failed_df.to_csv(index=False)
                st.download_button(
                    label="📥 Başarısız SKU'ları İndir",
                    data=csv,
                    file_name=f"basarisiz_skular_{time.strftime('%Y%m%d_%H%M%S')}.csv",
                    mime="text/csv",
                    use_container_width=True
                )
    else:
        st.success(f"🎉 Tüm {all_results.get('success', 0)} varyant başarıyla güncellendi!")
    
    with st.expander("📋 Detaylı Rapor", expanded=False):
        if all_results.get('details'):
            report_df = pd.DataFrame(all_results['details'])
            
            tab1, tab2 = st.tabs(["✅ Başarılı", "❌ Başarısız"])
            
            with tab1:
                success_df = report_df[report_df['status'] == 'success']
                if not success_df.empty:
                    st.dataframe(
                        success_df[['sku', 'price']].head(200), 
                        use_container_width=True, 
                        hide_index=True
                    )
            
            with tab2:
                failed_df = report_df[report_df['status'] == 'failed']
                if not failed_df.empty:
                    st.markdown("#### Hata Dağılımı")
                    error_summary = failed_df['reason'].value_counts().head(10)
                    st.bar_chart(error_summary)
                    
                    st.markdown("#### Başarısız Varyantlar")
                    st.dataframe(
                        failed_df[['sku', 'price', 'reason']].head(200), 
                        use_container_width=True, 
                        hide_index=True
                    )