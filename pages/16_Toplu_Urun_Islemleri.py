import streamlit as st
import pandas as pd
import sys
import os
import time

# Proje kök dizinini ekle
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from utils.style_loader import load_global_css
from connectors.shopify_api import ShopifyAPI
from config_manager import load_all_user_keys

# Sayfa Ayarları
st.set_page_config(page_title="Toplu Ürün İşlemleri", page_icon="🏷️", layout="wide")
load_global_css()

st.title("🏷️ Toplu Ürün İşlemleri (Etiket, Marka, Tür)")
st.markdown("Ürünlerin etiketlerini, markalarını ve türlerini toplu olarak güncelleyin.")

if 'authentication_status' not in st.session_state or not st.session_state.authentication_status:
    st.warning("Lütfen önce giriş yapın.")
    st.stop()

# API Bağlantısı
try:
    user_keys = load_all_user_keys(st.session_state.username)
    shopify = ShopifyAPI(user_keys['shopify_store'], user_keys['shopify_token'])
except Exception as e:
    st.error(f"API Bağlantı Hatası: {e}")
    st.stop()

# --- 1. Ürün Seçimi ---
st.header("1. Ürün Seçimi")

selection_mode = st.radio(
    "Ürün Kaynağı Seçin:",
    ["Koleksiyon Bazlı", "Manuel Arama", "Tüm Ürünler (Dikkat!)"],
    horizontal=True
)

if 'target_products' not in st.session_state:
    st.session_state.target_products = []

if selection_mode == "Koleksiyon Bazlı":
    collections = shopify.get_all_collections()
    collection_options = {c['title']: c['id'] for c in collections}
    selected_collection_name = st.selectbox("Koleksiyon Seçin:", list(collection_options.keys()))
    
    if st.button("Koleksiyondaki Ürünleri Getir"):
        with st.spinner("Ürünler çekiliyor..."):
            collection_id = collection_options[selected_collection_name]
            # get_products_by_collection returns list of nodes
            products = shopify.get_products_by_collection(collection_id)
            st.session_state.target_products = products
            st.success(f"{len(products)} ürün bulundu.")

elif selection_mode == "Manuel Arama":
    search_query = st.text_input("Arama Terimi (Ürün Adı, SKU, Tag vb.):")
    if st.button("Ara") and search_query:
        with st.spinner("Aranıyor..."):
            products = shopify.search_products(search_query, limit=50)
            st.session_state.target_products = products
            st.success(f"{len(products)} ürün bulundu.")

elif selection_mode == "Tüm Ürünler (Dikkat!)":
    st.warning("Bu işlem mağazadaki TÜM ürünleri çekecektir. Çok uzun sürebilir.")
    if st.button("Tüm Ürünleri Getir"):
        with st.spinner("Tüm ürünler çekiliyor..."):
            # get_all_products_for_export returns list of nodes
            products = shopify.get_all_products_for_export()
            st.session_state.target_products = products
            st.success(f"{len(products)} ürün bulundu.")

# Ürün Listesi Gösterimi
if st.session_state.target_products:
    products = st.session_state.target_products
    
    # DataFrame'e çevir
    df_data = []
    for p in products:
        df_data.append({
            "ID": p.get('id'),
            "Resim": p.get('featuredImage', {}).get('url') if p.get('featuredImage') else None,
            "Ürün Adı": p.get('title'),
            "Mevcut Tür": p.get('productType', ''),
            "Mevcut Marka": p.get('vendor', ''),
            "Mevcut Etiketler": ", ".join(p.get('tags', [])) if isinstance(p.get('tags'), list) else p.get('tags', '')
        })
    
    df = pd.DataFrame(df_data)
    
    st.write(f"**Seçili Ürünler ({len(products)}):**")
    st.dataframe(
        df, 
        column_config={
            "Resim": st.column_config.ImageColumn("Resim", width="small"),
        },
        use_container_width=True,
        hide_index=True
    )
    
    if st.button("Listeyi Temizle", type="secondary"):
        st.session_state.target_products = []
        st.rerun()
    
    st.divider()
    
    # --- 2. İşlem Seçimi ---
    st.header("2. Yapılacak İşlemler")
    
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.subheader("🏷️ Etiket (Tag)")
        enable_tags = st.checkbox("Etiketleri Güncelle")
        tag_action = "Ekle (Mevcutlara ekle)"
        new_tags = ""
        if enable_tags:
            tag_action = st.radio("İşlem:", ["Ekle (Mevcutlara ekle)", "Değiştir (Hepsini sil ve yaz)"])
            new_tags = st.text_input("Etiketler (Virgülle ayırın):", placeholder="yeni sezon, indirim, yazlık")
    
    with col2:
        st.subheader("🏢 Marka (Vendor)")
        enable_vendor = st.checkbox("Markayı Güncelle")
        new_vendor = ""
        if enable_vendor:
            new_vendor = st.text_input("Yeni Marka Adı:")
            
    with col3:
        st.subheader("👕 Otomatik Tür (Type)")
        enable_auto_type = st.checkbox("İsimden Tür Belirle")
        keywords_list = []
        if enable_auto_type:
            st.info("Ürün isminde geçen kelimelere göre 'Product Type' alanını otomatik doldurur.")
            
            default_keywords = [
                't-shirt', 'sweatshirt', 'kazak', 'süveter', 'tayt', 'tunik', 'tulum', 
                'mont', 'eşofman altı', 'şort', 'ceket', 'hırka', 'elbise', 'bluz', 
                'etek', 'pantolon', 'gömlek', 'büstiyer', 'body', 'kaban'
            ]
            
            keywords_text = st.text_area(
                "Tanımlı Kelimeler (Her satıra bir tane):", 
                value="\n".join(default_keywords),
                height=200
            )
            keywords_list = [k.strip() for k in keywords_text.split('\n') if k.strip()]

    # --- 3. İşlemi Başlat ---
    st.header("3. Onay ve Başlat")
    
    if st.button("🚀 İşlemleri Başlat", type="primary"):
        if not (enable_tags or enable_vendor or enable_auto_type):
            st.warning("Lütfen en az bir işlem seçin.")
        else:
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            success_count = 0
            fail_count = 0
            
            total = len(products)
            
            for i, product in enumerate(products):
                p_id = product.get('id')
                p_title = product.get('title', '')
                current_tags = product.get('tags', [])
                if isinstance(current_tags, str):
                    current_tags = [t.strip() for t in current_tags.split(',')]
                
                updates = {}
                
                # 1. Etiket Mantığı
                if enable_tags and new_tags:
                    input_tags_list = [t.strip() for t in new_tags.split(',') if t.strip()]
                    
                    if tag_action == "Ekle (Mevcutlara ekle)":
                        # Eğer eklenecek tüm etiketler zaten varsa, güncelleme yapma
                        if all(tag in current_tags for tag in input_tags_list):
                            # Değişiklik yok, updates'e ekleme
                            pass
                        else:
                            # Mevcutlarla birleştir, duplicate önle
                            final_tags = list(set(current_tags + input_tags_list))
                            updates['tags'] = final_tags
                    else:
                        # Tamamen değiştir
                        final_tags = input_tags_list
                        updates['tags'] = final_tags
                
                # 2. Marka Mantığı
                if enable_vendor and new_vendor:
                    # Eğer marka zaten aynıysa güncelleme yapma
                    if product.get('vendor') != new_vendor:
                        updates['vendor'] = new_vendor
                
                # 3. Otomatik Tür Mantığı
                if enable_auto_type:
                    # Kelimeleri uzunluklarına göre sırala (uzun olan önce eşleşsin)
                    sorted_keywords = sorted(keywords_list, key=len, reverse=True)
                    
                    found_type = None
                    title_lower = p_title.lower()
                    
                    for kw in sorted_keywords:
                        if kw.lower() in title_lower:
                            found_type = kw.title() # Baş harfi büyüt
                            break
                    
                    if found_type:
                        # Eğer tür zaten aynıysa güncelleme yapma
                        if product.get('productType') != found_type:
                            updates['product_type'] = found_type
                
                # Güncelleme varsa API çağır
                if updates:
                    status_text.text(f"İşleniyor ({i+1}/{total}): {p_title}")
                    
                    result = shopify.update_product_details(
                        product_id=p_id,
                        tags=updates.get('tags'),
                        vendor=updates.get('vendor'),
                        product_type=updates.get('product_type')
                    )
                    
                    if result.get('success'):
                        success_count += 1
                    else:
                        fail_count += 1
                        st.error(f"Hata ({p_title}): {result.get('message')}")
                else:
                    # Güncelleme gerekmedi, sadece progress ilerlet
                    status_text.text(f"Atlanıyor (Değişiklik yok) ({i+1}/{total}): {p_title}")
                    pass
                
                progress_bar.progress((i + 1) / total)
                time.sleep(0.1) # Rate limit koruması
            
            st.success(f"İşlem Tamamlandı! ✅ {success_count} başarılı, ❌ {fail_count} hatalı.")
            st.balloons()

else:
    st.info("Lütfen yukarıdan bir kaynak seçip ürünleri getirin.")
