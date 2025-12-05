import streamlit as st
import pandas as pd
from connectors.shopify_api import ShopifyAPI
from config_manager import load_all_user_keys
from utils.seo_manager import SEOManager
import time

st.set_page_config(page_title="SEO ve İçerik Yönetimi", layout="wide")

# --- Yetkilendirme ve Kurulum ---
keys = load_all_user_keys("admin")
if not keys["shopify_store"] or not keys["shopify_token"]:
    st.error("Shopify API anahtarları bulunamadı. Lütfen ayarlardan ekleyin.")
    st.stop()

shopify = ShopifyAPI(keys["shopify_store"], keys["shopify_token"])

# Session State Başlatma
if 'seo_products' not in st.session_state:
    st.session_state.seo_products = []

# --- Sidebar Ayarlar ---
st.sidebar.header("⚙️ AI ve Model Ayarları")

# API Ayarları (Kullanıcı değiştirebilir)
ai_api_key = st.sidebar.text_input("AI API Key", value=keys.get("ai_api_key", ""), type="password")
ai_api_base = st.sidebar.text_input("AI API Base URL", value=keys.get("ai_api_base", "https://api.gptproto.com/v1"))
ai_model = st.sidebar.selectbox(
    "Model Seçimi", 
    ["gpt-5.1", "gemini-3-pro", "gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"],
    index=0 if "gpt-5.1" == keys.get("ai_model") else 2 # Varsayılan olarak listeden uygun olanı seçmeye çalışır
)

# SEO Manager Başlat
seo_manager = SEOManager(ai_api_key, ai_api_base, ai_model)

st.title("🚀 SEO ve İçerik Yönetimi")
st.markdown("---")

# Sekmeler
tab_url, tab_content, tab_image = st.tabs(["🔗 URL (Handle) Yönetimi", "📝 AI İçerik & Meta", "🖼️ Görsel SEO"])

# --- 1. URL (Handle) Yönetimi ---
with tab_url:
    st.header("Ürün Link (Handle) Düzenleyici")
    
    col1, col2 = st.columns([1, 3])
    
    with col1:
        st.subheader("İşlem Ayarları")
        handle_mode = st.radio(
            "İşlem Tipi",
            ["Temizle (Türkçe -> İngilizce)", "Sayıları Kaldır", "Kelime Çıkar", "Özel Ekleme (Ön/Arka)"]
        )
        
        remove_words = ""
        if handle_mode == "Kelime Çıkar":
            remove_words = st.text_input("Çıkarılacak Kelimeler (Virgülle ayırın)", placeholder="yeni, indirim, kampanya")
            
        add_prefix = st.text_input("Başa Ekle (Prefix)", placeholder="Örn: kadin-giyim")
        add_suffix = st.text_input("Sona Ekle (Suffix)", placeholder="Örn: 2025")
        
        fetch_limit = st.number_input("Çekilecek Ürün Sayısı", min_value=10, max_value=250, value=50)
        if st.button("Ürünleri Getir", key="btn_fetch_handle"):
            with st.spinner("Ürünler Shopify'dan çekiliyor..."):
                # Basit bir query ile ürünleri alalım
                query = """
                {
                    products(first: %d) {
                        edges {
                            node {
                                id
                                title
                                handle
                            }
                        }
                    }
                }
                """ % fetch_limit
                result = shopify.execute_graphql(query)
                if result and 'data' in result:
                    products = [edge['node'] for edge in result['data']['products']['edges']]
                    st.session_state.seo_products = products
                    st.success(f"{len(products)} ürün çekildi.")
                else:
                    st.error("Ürünler çekilemedi.")

    with col2:
        if st.session_state.seo_products:
            st.subheader("Önizleme ve Onay")
            
            preview_data = []
            for p in st.session_state.seo_products:
                old_h = p['handle']
                
                # Mod seçimine göre işlem
                mode_key = "clean_only"
                if handle_mode == "Sayıları Kaldır": mode_key = "remove_numbers"
                elif handle_mode == "Kelime Çıkar": mode_key = "remove_words"
                
                new_h = seo_manager.process_handle(
                    old_h, 
                    mode=mode_key, 
                    remove_words=remove_words, 
                    add_prefix=add_prefix, 
                    add_suffix=add_suffix
                )
                
                preview_data.append({
                    "Ürün Adı": p['title'],
                    "Eski Handle": old_h,
                    "Yeni Handle": new_h,
                    "Değişim": "✅" if old_h != new_h else "-"
                })
            
            df_preview = pd.DataFrame(preview_data)
            st.dataframe(df_preview, use_container_width=True)
            
            if st.button("Değişiklikleri Uygula (Shopify'a Gönder)", type="primary"):
                progress_bar = st.progress(0)
                success_count = 0
                
                for i, row in enumerate(preview_data):
                    if row["Eski Handle"] != row["Yeni Handle"]:
                        # GraphQL Mutation
                        mutation = """
                        mutation productUpdate($input: ProductInput!) {
                            productUpdate(input: $input) {
                                product {
                                    id
                                    handle
                                }
                                userErrors {
                                    field
                                    message
                                }
                            }
                        }
                        """
                        # ID'yi bul
                        p_id = next(p['id'] for p in st.session_state.seo_products if p['title'] == row["Ürün Adı"])
                        
                        variables = {
                            "input": {
                                "id": p_id,
                                "handle": row["Yeni Handle"]
                            }
                        }
                        
                        res = shopify.execute_graphql(mutation, variables)
                        if res and not res.get('data', {}).get('productUpdate', {}).get('userErrors'):
                            success_count += 1
                    
                    progress_bar.progress((i + 1) / len(preview_data))
                
                st.success(f"{success_count} ürünün linki güncellendi!")
                st.session_state.seo_products = [] # Listeyi temizle

# --- 2. AI İçerik & Meta ---
with tab_content:
    st.header("🤖 AI Destekli İçerik Üretimi")
    
    col_ai_settings, col_ai_action = st.columns([1, 2])
    
    with col_ai_settings:
        st.info("Model: " + ai_model)
        target_field = st.multiselect("Hangi Alanlar Üretilsin?", ["Ürün Açıklaması", "Meta Title & Description"], default=["Meta Title & Description"])
        
        desc_prompt = st.text_area("Açıklama Promptu", "Müşteriyi harekete geçiren, özelliklere vurgu yapan, samimi bir dil kullan.")
        meta_prompt = st.text_area("Meta Promptu", "Google aramalarında tıklanma oranını artıracak, anahtar kelime odaklı başlık ve açıklama.")
        
        if st.button("Seçili Ürünler İçin Üret", key="btn_gen_content"):
            if not st.session_state.seo_products:
                st.warning("Önce 'URL Yönetimi' sekmesinden veya buradan ürünleri çekmelisiniz.")
            else:
                st.session_state.ai_results = []
                
                progress_text = st.empty()
                bar = st.progress(0)
                
                for i, p in enumerate(st.session_state.seo_products):
                    progress_text.text(f"İşleniyor: {p['title']}")
                    
                    result = {"id": p['id'], "title": p['title']}
                    
                    # Mevcut verileri al (Basitlik için burada tekrar sorgu atmıyoruz, handle kısmında description çekmemiştik, o yüzden burada detay çekmek gerekebilir. Şimdilik title üzerinden gidiyoruz)
                    
                    if "Ürün Açıklaması" in target_field:
                        # Gerçek senaryoda ürünün mevcut açıklamasını da çekmek gerekir.
                        new_desc = seo_manager.generate_product_description(p['title'], "Mevcut açıklama yok", "Detaylar...", desc_prompt)
                        result["new_description"] = new_desc
                        
                    if "Meta Title & Description" in target_field:
                        new_meta = seo_manager.generate_seo_meta(p['title'], "Ürün detayları...", meta_prompt)
                        result["new_meta"] = new_meta
                        
                    st.session_state.ai_results.append(result)
                    bar.progress((i + 1) / len(st.session_state.seo_products))
                
                st.success("AI Üretimi Tamamlandı! Aşağıdan kontrol edip onaylayın.")

    with col_ai_action:
        if 'ai_results' in st.session_state and st.session_state.ai_results:
            st.subheader("AI Önerileri")
            for res in st.session_state.ai_results:
                with st.expander(f"Ürün: {res['title']}"):
                    if "new_description" in res:
                        st.markdown("**Yeni Açıklama:**")
                        st.text_area("Düzenle", res["new_description"], key=f"desc_{res['id']}", height=150)
                    
                    if "new_meta" in res:
                        st.markdown("**Yeni Meta:**")
                        st.text_area("Düzenle", res["new_meta"], key=f"meta_{res['id']}", height=100)
            
            if st.button("Hepsini Kaydet", type="primary"):
                st.info("Kaydetme fonksiyonu bu demo için devre dışı (GraphQL mutation eklenecek).")

# --- 3. Görsel SEO ---
with tab_image:
    st.header("🖼️ Görsel Alt Text (Alt Metin) Optimizasyonu")
    st.markdown("""
    Bu modül, ürün adını ve varyant bilgisini kullanarak görseller için **bağlamsal alt metinler** üretir.
    Görüntü işleme yerine metin tabanlı üretim yaptığı için çok hızlı ve maliyetsizdir.
    """)
    
    img_prompt = st.text_input("Görsel Promptu", "Görme engelliler için betimleyici, ürünün rengini ve türünü içeren kısa bir cümle.")
    
    if st.button("Görsel Alt Metinlerini Üret"):
        if not st.session_state.seo_products:
            st.warning("Lütfen önce ürünleri çekin.")
        else:
            st.info("Bu özellik, seçilen ürünlerin tüm görsellerini tarar ve her biri için benzersiz bir alt text üretir.")
            # Demo output
            st.write("Örnek Üretim:")
            st.code(f"Görsel 1: {st.session_state.seo_products[0]['title']} - Önden Görünüm")
            st.code(f"Görsel 2: {st.session_state.seo_products[0]['title']} - Detay")
