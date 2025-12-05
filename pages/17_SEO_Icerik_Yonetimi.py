import streamlit as st
import pandas as pd
from connectors.shopify_api import ShopifyAPI
from config_manager import load_all_user_keys
from utils.seo_manager import SEOManager
import time

st.set_page_config(page_title="SEO Operasyon Merkezi", layout="wide", page_icon="🚀")

# --- Özel CSS ve Stil ---
st.markdown("""
<style>
    .stTabs [data-baseweb="tab-list"] {
        gap: 24px;
    }
    .stTabs [data-baseweb="tab"] {
        height: 50px;
        white-space: pre-wrap;
        background-color: #f0f2f6;
        border-radius: 4px 4px 0px 0px;
        gap: 1px;
        padding-top: 10px;
        padding-bottom: 10px;
    }
    .stTabs [aria-selected="true"] {
        background-color: #ffffff;
        border-top: 2px solid #ff4b4b;
    }
    .metric-card {
        background-color: #ffffff;
        padding: 15px;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        text-align: center;
    }
    .success-box {
        padding: 10px;
        background-color: #d4edda;
        color: #155724;
        border-radius: 5px;
        margin-bottom: 10px;
    }
</style>
""", unsafe_allow_html=True)

# --- Yetkilendirme ve Kurulum ---
keys = load_all_user_keys("admin")
if not keys["shopify_store"] or not keys["shopify_token"]:
    st.error("Shopify API anahtarları bulunamadı. Lütfen ayarlardan ekleyin.")
    st.stop()

shopify = ShopifyAPI(keys["shopify_store"], keys["shopify_token"])

# Session State Başlatma
if 'all_products' not in st.session_state:
    st.session_state.all_products = [] # Tüm çekilen ürünler
if 'workspace_products' not in st.session_state:
    st.session_state.workspace_products = [] # İşlem yapılacak seçili ürünler
if 'ai_results' not in st.session_state:
    st.session_state.ai_results = []

# --- Sidebar Ayarlar ---
with st.sidebar:
    st.header("⚙️ Ayarlar")
    st.markdown("---")
    
    st.subheader("🤖 AI Yapılandırması")
    ai_api_key = st.text_input("AI API Key", value=keys.get("ai_api_key", ""), type="password")
    ai_api_base = st.text_input("AI API Base URL", value=keys.get("ai_api_base", "https://api.gptproto.com/v1"))
    ai_model = st.selectbox(
        "Model Seçimi", 
        ["gpt-5.1", "gemini-3-pro", "gpt-4o", "gpt-4-turbo"],
        index=2
    )
    
    st.markdown("---")
    st.info(f"Aktif Mağaza: **{keys['shopify_store']}**")

# SEO Manager Başlat
seo_manager = SEOManager(ai_api_key, ai_api_base, ai_model)

# --- Yardımcı Fonksiyonlar ---
def fetch_products_recursive(limit=None):
    """Shopify'dan cursor tabanlı tüm ürünleri çeker."""
    products = []
    cursor = None
    has_next = True
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    while has_next:
        status_text.text(f"Ürünler çekiliyor... Toplam: {len(products)}")
        
        query = """
        query ($cursor: String) {
            products(first: 250, after: $cursor) {
                pageInfo {
                    hasNextPage
                    endCursor
                }
                edges {
                    node {
                        id
                        title
                        handle
                        description
                        featuredImage {
                            id
                            altText
                            url
                        }
                        variants(first: 1) {
                            edges {
                                node {
                                    sku
                                    price
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        variables = {"cursor": cursor}
        result = shopify.execute_graphql(query, variables)
        
        if result and 'data' in result:
            data = result['data']['products']
            new_products = [edge['node'] for edge in data['edges']]
            products.extend(new_products)
            
            has_next = data['pageInfo']['hasNextPage']
            cursor = data['pageInfo']['endCursor']
            
            if limit and len(products) >= limit:
                products = products[:limit]
                break
        else:
            break
            
        # İlerleme çubuğu simülasyonu (tam sayı bilinmediği için döngüsel)
        progress_bar.progress((len(products) % 100) / 100)
        
    progress_bar.empty()
    status_text.empty()
    return products

def create_redirect(path, target):
    """301 Yönlendirmesi oluşturur."""
    mutation = """
    mutation urlRedirectCreate($urlRedirect: UrlRedirectInput!) {
        urlRedirectCreate(urlRedirect: $urlRedirect) {
            urlRedirect {
                id
            }
            userErrors {
                field
                message
            }
        }
    }
    """
    variables = {
        "urlRedirect": {
            "path": path,
            "target": target
        }
    }
    return shopify.execute_graphql(mutation, variables)

# --- Ana Sayfa Düzeni ---
st.title("🚀 SEO Operasyon Merkezi")
st.markdown("Ürünlerinizi analiz edin, içeriklerini zenginleştirin ve teknik SEO hatalarını giderin.")

# Sekmeler
tab_cockpit, tab_url, tab_content, tab_image = st.tabs([
    "🎛️ Ürün Kokpiti", 
    "🔗 URL & Yönlendirme", 
    "📝 AI İçerik Stüdyosu", 
    "🖼️ Görsel SEO"
])

# ==========================================
# 1. TAB: ÜRÜN KOKPİTİ (Product Cockpit)
# ==========================================
with tab_cockpit:
    col_fetch, col_stats = st.columns([1, 3])
    
    with col_fetch:
        st.subheader("Veri Kaynağı")
        fetch_mode = st.radio("Çekim Modu", ["İlk 50 Ürün (Hızlı)", "İlk 250 Ürün", "Tüm Mağaza (Yavaş)"])
        
        limit_map = {"İlk 50 Ürün (Hızlı)": 50, "İlk 250 Ürün": 250, "Tüm Mağaza (Yavaş)": None}
        
        if st.button("Ürünleri Getir / Yenile", type="primary"):
            st.session_state.all_products = fetch_products_recursive(limit=limit_map[fetch_mode])
            st.success(f"{len(st.session_state.all_products)} ürün başarıyla çekildi.")

    with col_stats:
        if st.session_state.all_products:
            st.subheader("Hızlı Analiz")
            total = len(st.session_state.all_products)
            missing_meta = sum(1 for p in st.session_state.all_products if not p.get('description')) # Basit kontrol
            missing_img = sum(1 for p in st.session_state.all_products if not p.get('featuredImage'))
            
            c1, c2, c3 = st.columns(3)
            c1.metric("Toplam Ürün", total)
            c2.metric("Açıklaması Eksik", missing_meta, delta_color="inverse")
            c3.metric("Görseli Eksik", missing_img, delta_color="inverse")

    st.markdown("---")
    
    if st.session_state.all_products:
        st.subheader("🔍 Filtrele ve Seç")
        
        # DataFrame Hazırlığı
        df_data = []
        for p in st.session_state.all_products:
            img_alt = p['featuredImage']['altText'] if p.get('featuredImage') else ""
            sku = p['variants']['edges'][0]['node']['sku'] if p['variants']['edges'] else ""
            df_data.append({
                "ID": p['id'],
                "Ürün Adı": p['title'],
                "SKU": sku,
                "Handle": p['handle'],
                "Mevcut Alt Text": img_alt,
                "Seç": False
            })
        
        df = pd.DataFrame(df_data)
        
        # Filtreler
        col_search, col_filter = st.columns([2, 1])
        with col_search:
            search_term = st.text_input("Ara (Ürün Adı, SKU veya Handle)", placeholder="Örn: elbise")
        
        if search_term:
            df = df[df['Ürün Adı'].str.contains(search_term, case=False) | 
                   df['SKU'].str.contains(search_term, case=False) |
                   df['Handle'].str.contains(search_term, case=False)]

        # Data Editor ile Seçim
        edited_df = st.data_editor(
            df,
            column_config={
                "Seç": st.column_config.CheckboxColumn(
                    "İşlem İçin Seç",
                    help="Bu ürünleri diğer sekmelerde düzenlemek için seçin.",
                    default=False,
                ),
                "ID": None # ID'yi gizle
            },
            hide_index=True,
            use_container_width=True,
            height=400
        )
        
        # Seçilenleri Çalışma Masasına Aktar
        selected_rows = edited_df[edited_df["Seç"] == True]
        
        col_action_1, col_action_2 = st.columns([1, 4])
        with col_action_1:
            if st.button("Seçilenleri Çalışma Masasına Ekle ➡️"):
                selected_ids = selected_rows["ID"].tolist()
                # ID'ye göre orijinal objeleri bul
                selected_objs = [p for p in st.session_state.all_products if p['id'] in selected_ids]
                st.session_state.workspace_products = selected_objs
                st.success(f"{len(selected_objs)} ürün çalışma masasına eklendi!")
        
        with col_action_2:
            if st.session_state.workspace_products:
                st.info(f"📂 Çalışma Masasında **{len(st.session_state.workspace_products)}** ürün bekliyor.")

# ==========================================
# 2. TAB: URL & YÖNLENDİRME (Smart Redirects)
# ==========================================
with tab_url:
    st.header("🔗 Akıllı URL Yönetimi")
    
    if not st.session_state.workspace_products:
        st.warning("Lütfen önce 'Ürün Kokpiti' sekmesinden ürün seçin.")
    else:
        col_url_settings, col_url_preview = st.columns([1, 2])
        
        with col_url_settings:
            st.subheader("Kural Seti")
            handle_mode = st.radio(
                "Düzenleme Modu",
                ["Temizle (TR Karakter -> ENG)", "Sayıları Kaldır", "Kelime Çıkar", "Özel Ekleme (Prefix/Suffix)"]
            )
            
            remove_words = ""
            if handle_mode == "Kelime Çıkar":
                remove_words = st.text_input("Çıkarılacaklar (Virgülle)", placeholder="yeni, indirim")
                
            add_prefix = st.text_input("Başa Ekle", placeholder="kadin-giyim")
            add_suffix = st.text_input("Sona Ekle", placeholder="2025")
            
            st.markdown("---")
            auto_redirect = st.checkbox("✅ Otomatik 301 Yönlendirmesi Oluştur", value=True, help="Eski linki yeni linke yönlendirir. 404 hatalarını önler.")

        with col_url_preview:
            st.subheader("Önizleme")
            
            preview_data = []
            for p in st.session_state.workspace_products:
                old_h = p['handle']
                mode_key = "clean_only"
                if handle_mode == "Sayıları Kaldır": mode_key = "remove_numbers"
                elif handle_mode == "Kelime Çıkar": mode_key = "remove_words"
                
                new_h = seo_manager.process_handle(
                    old_h, mode=mode_key, remove_words=remove_words, 
                    add_prefix=add_prefix, add_suffix=add_suffix
                )
                
                preview_data.append({
                    "Ürün": p['title'],
                    "Eski URL": old_h,
                    "Yeni URL": new_h,
                    "Durum": "Değişecek" if old_h != new_h else "Aynı",
                    "Yönlendirme": "Oluşturulacak" if (old_h != new_h and auto_redirect) else "-"
                })
            
            df_preview = pd.DataFrame(preview_data)
            st.dataframe(df_preview, use_container_width=True)
            
            if st.button("Değişiklikleri Uygula ve Yönlendirmeleri Oluştur", type="primary"):
                progress_bar = st.progress(0)
                log_container = st.container()
                
                success_count = 0
                redirect_count = 0
                
                for i, row in enumerate(preview_data):
                    if row["Eski URL"] != row["Yeni URL"]:
                        # 1. Ürün Handle Güncelle
                        p_id = next(p['id'] for p in st.session_state.workspace_products if p['title'] == row["Ürün"])
                        
                        mutation = """
                        mutation productUpdate($input: ProductInput!) {
                            productUpdate(input: $input) {
                                product { id handle }
                                userErrors { field message }
                            }
                        }
                        """
                        res = shopify.execute_graphql(mutation, {"input": {"id": p_id, "handle": row["Yeni URL"]}})
                        
                        if res and not res.get('data', {}).get('productUpdate', {}).get('userErrors'):
                            success_count += 1
                            
                            # 2. Redirect Oluştur (Eğer seçiliyse)
                            if auto_redirect:
                                # Shopify path formatı: /products/handle
                                old_path = f"/products/{row['Eski URL']}"
                                new_path = f"/products/{row['Yeni URL']}"
                                
                                red_res = create_redirect(old_path, new_path)
                                if red_res and not red_res.get('data', {}).get('urlRedirectCreate', {}).get('userErrors'):
                                    redirect_count += 1
                        else:
                            log_container.error(f"Hata ({row['Ürün']}): {res}")
                            
                    progress_bar.progress((i + 1) / len(preview_data))
                
                st.success(f"İşlem Tamamlandı! {success_count} ürün güncellendi, {redirect_count} yönlendirme oluşturuldu.")
                st.session_state.workspace_products = [] # Temizle

# ==========================================
# 3. TAB: AI İÇERİK STÜDYOSU
# ==========================================
with tab_content:
    st.header("📝 AI İçerik Stüdyosu")
    
    if not st.session_state.workspace_products:
        st.warning("Lütfen önce 'Ürün Kokpiti' sekmesinden ürün seçin.")
    else:
        col_ai_opts, col_ai_res = st.columns([1, 2])
        
        with col_ai_opts:
            st.subheader("İçerik Ayarları")
            target_type = st.multiselect("Üretilecek Alanlar", ["Ürün Açıklaması", "Meta Title & Description"], default=["Meta Title & Description"])
            
            tone = st.selectbox("İletişim Tonu", ["Satış Odaklı & İkna Edici", "Kurumsal & Profesyonel", "Samimi & Eğlenceli", "Lüks & Minimalist"])
            keywords = st.text_input("Hedef Anahtar Kelimeler", placeholder="yazlık elbise, pamuklu kumaş")
            
            custom_prompt = st.text_area("Ek Talimatlar", "Özellikleri madde madde yaz, SEO uyumlu olsun.")
            
            if st.button("✨ İçerik Üret", type="primary"):
                st.session_state.ai_results = []
                prog = st.progress(0)
                
                for i, p in enumerate(st.session_state.workspace_products):
                    res = {"id": p['id'], "title": p['title'], "original_desc": p.get('description', '')}
                    
                    full_prompt = f"Ton: {tone}. Anahtar Kelimeler: {keywords}. {custom_prompt}"
                    
                    if "Ürün Açıklaması" in target_type:
                        res["new_desc"] = seo_manager.generate_product_description(p['title'], p.get('description', ''), "Detaylar...", full_prompt)
                    
                    if "Meta Title & Description" in target_type:
                        res["new_meta"] = seo_manager.generate_seo_meta(p['title'], p.get('description', ''), full_prompt)
                        
                    st.session_state.ai_results.append(res)
                    prog.progress((i + 1) / len(st.session_state.workspace_products))
                st.success("Üretim Tamamlandı!")

        with col_ai_res:
            st.subheader("Sonuçlar ve Karşılaştırma")
            if st.session_state.ai_results:
                for res in st.session_state.ai_results:
                    with st.expander(f"📄 {res['title']}", expanded=True):
                        c1, c2 = st.columns(2)
                        
                        if "new_desc" in res:
                            with c1:
                                st.caption("Mevcut Açıklama")
                                st.text_area("Eski", res["original_desc"], height=150, disabled=True, key=f"old_{res['id']}")
                            with c2:
                                st.caption("AI Önerisi")
                                st.text_area("Yeni", res["new_desc"], height=150, key=f"new_{res['id']}")
                        
                        if "new_meta" in res:
                            st.info("Meta Önerisi:\n" + res["new_meta"])
                
                if st.button("Tümünü Kaydet (Shopify)", type="primary"):
                    st.info("Kaydetme işlemi simüle edildi. (GraphQL entegrasyonu eklenecek)")

# ==========================================
# 4. TAB: GÖRSEL SEO
# ==========================================
with tab_image:
    st.header("🖼️ Görsel SEO (Alt Text)")
    
    if not st.session_state.workspace_products:
        st.warning("Lütfen önce 'Ürün Kokpiti' sekmesinden ürün seçin.")
    else:
        st.info("Seçili ürünlerin görselleri için 'Ürün Adı + Varyant' kombinasyonu ile otomatik Alt Text üretilir.")
        
        if st.button("Alt Metinleri Oluştur ve Önizle"):
            img_preview = []
            for p in st.session_state.workspace_products:
                if p.get('featuredImage'):
                    new_alt = f"{p['title']} - Detaylı Görünüm"
                    img_preview.append({
                        "Ürün": p['title'],
                        "Görsel ID": p['featuredImage']['id'],
                        "Mevcut Alt": p['featuredImage']['altText'],
                        "Yeni Alt": new_alt
                    })
            
            st.dataframe(pd.DataFrame(img_preview), use_container_width=True)
            st.button("Görsel SEO'yu Uygula", type="primary")
