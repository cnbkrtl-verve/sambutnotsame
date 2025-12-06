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
if 'workspace_url' not in st.session_state:
    st.session_state.workspace_url = [] 
if 'workspace_content' not in st.session_state:
    st.session_state.workspace_content = []
if 'workspace_image' not in st.session_state:
    st.session_state.workspace_image = []
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
        
        # Query complexity düşürüldü (250 -> 50) ve hata yönetimi eklendi
        query = """
        query ($cursor: String) {
            products(first: 50, after: $cursor) {
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
                        tags
                        seo {
                            title
                            description
                        }
                        options {
                            name
                            values
                        }
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
        try:
            # execute_graphql zaten 'data' kısmını döndürüyor
            result = shopify.execute_graphql(query, variables)
            
            if result and 'products' in result:
                data = result['products']
                new_products = [edge['node'] for edge in data['edges']]
                products.extend(new_products)
                
                has_next = data['pageInfo']['hasNextPage']
                cursor = data['pageInfo']['endCursor']
                
                if limit and len(products) >= limit:
                    products = products[:limit]
                    break
            else:
                # Eğer result boşsa veya products yoksa
                st.error(f"API Yanıtı Beklenmedik Format: {result}")
                break
        except Exception as e:
            st.error(f"Bağlantı Hatası: {str(e)}")
            break
            
        # İlerleme çubuğu simülasyonu
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
            img_url = p['featuredImage']['url'] if p.get('featuredImage') else ""
            img_alt = p['featuredImage']['altText'] if p.get('featuredImage') else ""
            sku = p['variants']['edges'][0]['node']['sku'] if p['variants']['edges'] else ""
            seo_title = p.get('seo', {}).get('title', '') if p.get('seo') else ""
            seo_desc = p.get('seo', {}).get('description', '') if p.get('seo') else ""
            tags = ", ".join(p.get('tags', []))
            
            df_data.append({
                "Seç": False,
                "Görsel": img_url,
                "Ürün Adı": p['title'],
                "SKU": sku,
                "Handle": p['handle'],
                "Alt Text": img_alt,
                "SEO Başlık": seo_title,
                "SEO Açıklama": seo_desc,
                "Etiketler": tags,
                "ID": p['id']
            })
        
        df = pd.DataFrame(df_data)
        
        # Filtreler
        col_search, col_filter = st.columns([2, 1])
        with col_search:
            search_term = st.text_input("Ara (Ürün Adı, SKU veya Handle)", placeholder="Örn: elbise")
        
        with col_filter:
            st.write("") # Hizalama için boşluk
            st.write("")
            select_all = st.checkbox("Listelenen Tümünü Seç", value=False, help="Aşağıdaki listede görünen tüm ürünleri seçili hale getirir.")
        
        if search_term:
            df = df[df['Ürün Adı'].str.contains(search_term, case=False) | 
                   df['SKU'].str.contains(search_term, case=False) |
                   df['Handle'].str.contains(search_term, case=False)]
        
        if select_all:
            df["Seç"] = True

        # Data Editor ile Seçim
        # Key'i dinamik yaparak select_all değiştiğinde resetlenmesini sağlıyoruz
        editor_key = f"editor_{select_all}_{len(df)}_{search_term}"
        
        edited_df = st.data_editor(
            df,
            column_config={
                "Seç": st.column_config.CheckboxColumn(
                    "Seç",
                    help="İşlem yapılacak ürünleri seçin",
                    default=False,
                    width="small"
                ),
                "Görsel": st.column_config.ImageColumn(
                    "Görsel",
                    help="Ürün ana görseli",
                    width="small"
                ),
                "Ürün Adı": st.column_config.TextColumn("Ürün Adı", width="medium"),
                "SKU": st.column_config.TextColumn("SKU", width="small"),
                "Handle": st.column_config.TextColumn("Handle", width="medium"),
                "Alt Text": st.column_config.TextColumn("Alt Text", width="medium"),
                "SEO Başlık": st.column_config.TextColumn("SEO Başlık", width="medium"),
                "SEO Açıklama": st.column_config.TextColumn("SEO Açıklama", width="large"),
                "Etiketler": st.column_config.TextColumn("Etiketler", width="medium"),
                "ID": None # ID'yi gizle
            },
            hide_index=True,
            use_container_width=True,
            height=600,
            key=editor_key
        )
        
        # Seçilenleri Çalışma Masasına Aktar
        selected_rows = edited_df[edited_df["Seç"] == True]
        st.info(f"{len(selected_rows)} ürün seçildi.")
        
        st.markdown("### 📤 İşlem Seçimi")
        col_btn1, col_btn2, col_btn3 = st.columns(3)
        
        selected_ids = selected_rows["ID"].tolist()
        selected_objs = [p for p in st.session_state.all_products if p['id'] in selected_ids]
        
        with col_btn1:
            if st.button("🔗 URL Yönetimine Gönder", use_container_width=True):
                st.session_state.workspace_url = selected_objs
                st.success(f"{len(selected_objs)} ürün URL modülüne aktarıldı!")
            if st.session_state.workspace_url:
                st.caption(f"Bekleyen: {len(st.session_state.workspace_url)} ürün")

        with col_btn2:
            if st.button("📝 İçerik Stüdyosuna Gönder", use_container_width=True):
                st.session_state.workspace_content = selected_objs
                st.success(f"{len(selected_objs)} ürün İçerik modülüne aktarıldı!")
            if st.session_state.workspace_content:
                st.caption(f"Bekleyen: {len(st.session_state.workspace_content)} ürün")

        with col_btn3:
            if st.button("🖼️ Görsel SEO'ya Gönder", use_container_width=True):
                st.session_state.workspace_image = selected_objs
                st.success(f"{len(selected_objs)} ürün Görsel modülüne aktarıldı!")
            if st.session_state.workspace_image:
                st.caption(f"Bekleyen: {len(st.session_state.workspace_image)} ürün")

# ==========================================
# 2. TAB: URL & YÖNLENDİRME (Smart Redirects)
# ==========================================
with tab_url:
    st.header("🔗 Akıllı URL Yönetimi")
    
    if not st.session_state.workspace_url:
        st.warning("Lütfen önce 'Ürün Kokpiti' sekmesinden ürün seçip 'URL Yönetimine Gönder' butonuna basın.")
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
            for p in st.session_state.workspace_url:
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
                        p_id = next(p['id'] for p in st.session_state.workspace_url if p['title'] == row["Ürün"])
                        
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
                st.session_state.workspace_url = [] # Temizle

# ==========================================
# 3. TAB: AI İÇERİK STÜDYOSU
# ==========================================
with tab_content:
    st.header("📝 AI İçerik Stüdyosu")
    
    if not st.session_state.workspace_content:
        st.warning("Lütfen önce 'Ürün Kokpiti' sekmesinden ürün seçip 'İçerik Stüdyosuna Gönder' butonuna basın.")
    else:
        col_ai_opts, col_ai_res = st.columns([1, 3])
        
        with col_ai_opts:
            st.subheader("İçerik Ayarları")
            target_type = st.multiselect("Üretilecek Alanlar", ["Ürün Açıklaması", "Meta Title & Description"], default=["Meta Title & Description"])
            
            tone = st.selectbox("İletişim Tonu", ["Satış Odaklı & İkna Edici", "Kurumsal & Profesyonel", "Samimi & Eğlenceli", "Lüks & Minimalist"])
            keywords = st.text_input("Hedef Anahtar Kelimeler", placeholder="yazlık elbise, pamuklu kumaş")
            
            use_image_analysis = st.checkbox("📸 Görsel Analizi Kullan", value=True, help="Ürün görselini de AI'a göndererek daha detaylı içerik üretilmesini sağlar.")
            custom_prompt = st.text_area("Ek Talimatlar", "Özellikleri madde madde yaz, SEO uyumlu olsun.")
            
            if st.button("✨ İçerik Üret", type="primary"):
                st.session_state.ai_results = []
                prog = st.progress(0)
                
                for i, p in enumerate(st.session_state.workspace_content):
                    res = {
                        "id": p['id'], 
                        "title": p['title'], 
                        "original_desc": p.get('description', ''),
                        "original_meta_title": p.get('seo', {}).get('title', ''),
                        "original_meta_desc": p.get('seo', {}).get('description', ''),
                        "new_desc": p.get('description', ''),
                        "new_meta_title": p.get('seo', {}).get('title', ''),
                        "new_meta_desc": p.get('seo', {}).get('description', '')
                    }
                    
                    full_prompt = f"Ton: {tone}. Anahtar Kelimeler: {keywords}. {custom_prompt}"
                    img_url = p.get('featuredImage', {}).get('url') if use_image_analysis and p.get('featuredImage') else None
                    
                    if "Ürün Açıklaması" in target_type:
                        res["new_desc"] = seo_manager.generate_product_description(
                            p['title'], 
                            p.get('description', ''), 
                            "Detaylar...", 
                            full_prompt,
                            image_url=img_url
                        )
                    
                    if "Meta Title & Description" in target_type:
                        # Meta çıktısını parse etmemiz gerekebilir, şimdilik düz metin olarak alıyoruz
                        meta_text = seo_manager.generate_seo_meta(
                            p['title'], 
                            p.get('description', ''), 
                            full_prompt,
                            image_url=img_url
                        )
                        # Basit parsing denemesi
                        if "Title:" in meta_text and "Description:" in meta_text:
                            try:
                                parts = meta_text.split("Description:")
                                res["new_meta_title"] = parts[0].replace("Title:", "").strip()
                                res["new_meta_desc"] = parts[1].strip()
                            except:
                                res["new_meta_desc"] = meta_text
                        else:
                            res["new_meta_desc"] = meta_text
                        
                    st.session_state.ai_results.append(res)
                    prog.progress((i + 1) / len(st.session_state.workspace_content))
                st.success("Üretim Tamamlandı!")

        with col_ai_res:
            st.subheader("Canlı Önizleme ve Düzenleme")
            
            # Veri hazırlığı
            if st.session_state.ai_results:
                # AI sonuçları varsa onları kullan
                display_data = st.session_state.ai_results
            else:
                # Yoksa mevcut verileri göster (boş yeni alanlarla)
                display_data = []
                for p in st.session_state.workspace_content:
                    display_data.append({
                        "id": p['id'],
                        "title": p['title'],
                        "original_desc": p.get('description', ''),
                        "original_meta_title": p.get('seo', {}).get('title', ''),
                        "original_meta_desc": p.get('seo', {}).get('description', ''),
                        "new_desc": p.get('description', ''), # Başlangıçta eskisiyle aynı
                        "new_meta_title": p.get('seo', {}).get('title', ''),
                        "new_meta_desc": p.get('seo', {}).get('description', '')
                    })

            df_content = pd.DataFrame(display_data)
            
            # Data Editor
            edited_content = st.data_editor(
                df_content,
                column_config={
                    "title": st.column_config.TextColumn("Ürün Adı", disabled=True, width="medium"),
                    "new_desc": st.column_config.TextColumn("Yeni Açıklama", width="large"),
                    "new_meta_title": st.column_config.TextColumn("Yeni Meta Başlık", width="medium"),
                    "new_meta_desc": st.column_config.TextColumn("Yeni Meta Açıklama", width="large"),
                    "original_desc": None, # Gizle
                    "original_meta_title": None,
                    "original_meta_desc": None,
                    "id": None
                },
                hide_index=True,
                use_container_width=True,
                height=500,
                key="editor_content"
            )
            
            col_save_desc, col_save_meta = st.columns(2)

            with col_save_desc:
                if st.button("Sadece Açıklamaları Kaydet", type="primary", use_container_width=True):
                    progress_bar = st.progress(0)
                    success_count = 0
                    rows_to_update = edited_content.to_dict('records')
                    total_rows = len(rows_to_update)

                    for i, row in enumerate(rows_to_update):
                        if row['new_desc'] != row['original_desc']:
                            mutation = """
                            mutation productUpdate($input: ProductInput!) {
                                productUpdate(input: $input) {
                                    product { id }
                                    userErrors { field message }
                                }
                            }
                            """
                            input_data = {
                                "id": row['id'],
                                "descriptionHtml": row['new_desc']
                            }
                            res = shopify.execute_graphql(mutation, {"input": input_data})
                            if res and not res.get('data', {}).get('productUpdate', {}).get('userErrors'):
                                success_count += 1
                            else:
                                st.error(f"Hata ({row['title']}): {res}")
                        progress_bar.progress((i + 1) / total_rows)
                    st.success(f"{success_count} ürün açıklaması güncellendi!")

            with col_save_meta:
                if st.button("Sadece SEO Meta Kaydet", type="primary", use_container_width=True):
                    progress_bar = st.progress(0)
                    success_count = 0
                    rows_to_update = edited_content.to_dict('records')
                    total_rows = len(rows_to_update)

                    for i, row in enumerate(rows_to_update):
                        new_mt = row['new_meta_title'] if row['new_meta_title'] else ""
                        orig_mt = row['original_meta_title'] if row['original_meta_title'] else ""
                        new_md = row['new_meta_desc'] if row['new_meta_desc'] else ""
                        orig_md = row['original_meta_desc'] if row['original_meta_desc'] else ""

                        if new_mt != orig_mt or new_md != orig_md:
                            mutation = """
                            mutation productUpdate($input: ProductInput!) {
                                productUpdate(input: $input) {
                                    product { id }
                                    userErrors { field message }
                                }
                            }
                            """
                            input_data = {
                                "id": row['id'],
                                "seo": {}
                            }
                            if new_mt != orig_mt:
                                input_data['seo']['title'] = new_mt
                            if new_md != orig_md:
                                input_data['seo']['description'] = new_md
                                
                            res = shopify.execute_graphql(mutation, {"input": input_data})
                            if res and not res.get('data', {}).get('productUpdate', {}).get('userErrors'):
                                success_count += 1
                            else:
                                st.error(f"Hata ({row['title']}): {res}")
                        progress_bar.progress((i + 1) / total_rows)
                    st.success(f"{success_count} ürün SEO bilgisi güncellendi!")

# ==========================================
# 4. TAB: GÖRSEL SEO
# ==========================================
with tab_image:
    st.header("🖼️ Görsel SEO (Alt Text)")
    
    if not st.session_state.workspace_image:
        st.warning("Lütfen önce 'Ürün Kokpiti' sekmesinden ürün seçip 'Görsel SEO'ya Gönder' butonuna basın.")
    else:
        st.info("Seçili ürünlerin görselleri için 'Ürün Adı + Renk' kombinasyonu ile otomatik Alt Text üretilir.")
        
        col_img_act, col_img_table = st.columns([1, 3])
        
        with col_img_act:
            if st.button("Alt Metinleri Oluştur", type="primary"):
                img_preview = []
                for p in st.session_state.workspace_image:
                    if p.get('featuredImage'):
                        # Renk bulma mantığı
                        color_val = ""
                        if 'options' in p:
                            for opt in p['options']:
                                if opt['name'].lower() in ['renk', 'color', 'colour']:
                                    # İlk rengi alıyoruz (genellikle ana varyant)
                                    if opt['values']:
                                        color_val = opt['values'][0]
                                    break
                        
                        # Renk varsa ekle, yoksa sadece ürün adı
                        suffix = f" - {color_val}" if color_val else ""
                        new_alt = f"{p['title']}{suffix} - Detaylı Görünüm"
                        
                        img_preview.append({
                            "Görsel": p['featuredImage']['url'],
                            "Ürün": p['title'],
                            "Renk": color_val,
                            "Mevcut Alt": p['featuredImage']['altText'],
                            "Yeni Alt": new_alt,
                            "id": p['id'],
                            "image_id": p['featuredImage']['id']
                        })
                st.session_state.img_preview_data = img_preview
                st.success("Alt metinler oluşturuldu!")

        with col_img_table:
            if 'img_preview_data' in st.session_state:
                df_img = pd.DataFrame(st.session_state.img_preview_data)
                
                edited_img = st.data_editor(
                    df_img,
                    column_config={
                        "Görsel": st.column_config.ImageColumn("Görsel", width="small"),
                        "Ürün": st.column_config.TextColumn("Ürün", disabled=True, width="medium"),
                        "Renk": st.column_config.TextColumn("Renk", disabled=True, width="small"),
                        "Mevcut Alt": st.column_config.TextColumn("Mevcut Alt", disabled=True, width="medium"),
                        "Yeni Alt": st.column_config.TextColumn("Yeni Alt (Düzenlenebilir)", width="large"),
                        "id": None,
                        "image_id": None
                    },
                    hide_index=True,
                    use_container_width=True,
                    height=500
                )
                
                if st.button("Görsel SEO'yu Uygula (Kaydet)", type="primary"):
                    progress_bar = st.progress(0)
                    success_count = 0
                    rows = edited_img.to_dict('records')
                    
                    for i, row in enumerate(rows):
                        if row['Yeni Alt'] != row['Mevcut Alt']:
                            mutation = """
                            mutation productImageUpdate($productId: ID!, $image: ImageInput!) {
                                productImageUpdate(productId: $productId, image: $image) {
                                    image {
                                        id
                                        altText
                                    }
                                    userErrors {
                                        field
                                        message
                                    }
                                }
                            }
                            """
                            variables = {
                                "productId": row['id'],
                                "image": {
                                    "id": row['image_id'],
                                    "altText": row['Yeni Alt']
                                }
                            }
                            
                            res = shopify.execute_graphql(mutation, variables)
                            if res and not res.get('data', {}).get('productImageUpdate', {}).get('userErrors'):
                                success_count += 1
                            else:
                                st.error(f"Hata ({row['Ürün']}): {res}")
                        
                        progress_bar.progress((i + 1) / len(rows))
                    
                    st.success(f"{success_count} görsel alt metni güncellendi!")
                    st.session_state.workspace_image = []
                    if 'img_preview_data' in st.session_state:
                        del st.session_state.img_preview_data
