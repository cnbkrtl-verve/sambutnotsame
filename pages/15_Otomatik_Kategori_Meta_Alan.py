"""
🏷️ Otomatik Kategori ve Meta Alan Güncelleme

Ürün başlıklarından otomatik kategori tespiti yaparak 
Shopify kategori ve meta alanlarını otomatik doldurur.
"""

import streamlit as st
import sys
import os

# Proje ana dizinini path'e ekle - mutlak yol kullan
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))

# Sys.path'i temizle ve doğru sırayla ekle
# 'streamlit_app.py' gibi dosya isimlerini kaldır, sadece dizinleri tut
sys.path = [p for p in sys.path if (p == '' or (os.path.exists(p) and os.path.isdir(p)))]
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# 🎨 GLOBAL CSS YÜKLEME
from utils.style_loader import load_global_css
load_global_css()


# Import işlemleri
try:
    # Standart importlar
    from connectors.shopify_api import ShopifyAPI
    import config_manager
    import logging
    import time
    
    # CategoryMetafieldManager için özel import
    # Eğer normal import çalışmazsa, doğrudan dosya yolundan yükle
    try:
        from utils.category_metafield_manager import CategoryMetafieldManager
    except (ImportError, ModuleNotFoundError):
        # Alternatif: Doğrudan dosyadan import et
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "category_metafield_manager",
            os.path.join(project_root, "utils", "category_metafield_manager.py")
        )
        category_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(category_module)
        CategoryMetafieldManager = category_module.CategoryMetafieldManager
        
except Exception as e:
    st.error(f"❌ Modül import hatası: {str(e)}")
    st.error(f"Python path (ilk 3): {sys.path[:3]}")
    st.error(f"Project root: {project_root}")
    utils_path = os.path.join(project_root, 'utils')
    st.error(f"Utils path exists: {os.path.exists(utils_path)}")
    if os.path.exists(utils_path):
        st.error(f"Utils contents: {os.listdir(utils_path)}")
    import traceback
    st.code(traceback.format_exc())
    st.stop()

st.set_page_config(
    page_title="Otomatik Kategori ve Meta Alan",
    page_icon="🏷️",
    layout="wide"
)

st.title("🏷️ Otomatik Kategori ve Meta Alan Güncelleme")
st.markdown("---")

# Kullanıcı giriş kontrolü
if "authentication_status" not in st.session_state or not st.session_state.get("authentication_status"):
    st.warning("⚠️ Lütfen önce giriş yapın.")
    st.stop()

username = st.session_state.get("username", "guest")

# API anahtarlarını yükle
user_keys = config_manager.load_all_user_keys(username)

if not user_keys.get("shopify_store") or not user_keys.get("shopify_token"):
    st.error("❌ Shopify API bilgileri eksik! Lütfen Settings sayfasından ekleyin.")
    st.stop()

# Bilgilendirme
st.info("""
### 🎯 Bu Modül Ne Yapar?

**Sorun:** Shopify'da her ürün için kategori ve meta alanlarını manuel doldurmak çok zaman alıyor.

**Çözüm:** Bu modül ürün başlıklarından otomatik olarak:
1. 📦 **Kategori tespit eder** (T-shirt, Elbise, Bluz, Pantolon, Şort vb.)
2. 🏷️ **Kategoriye uygun meta alanları belirler** (Yaka tipi, Kol tipi, Boy, Desen vb.)
3. ✨ **Ürün başlığından değerleri çıkarır** (V Yaka, Uzun Kol, Mini, Leopar vb.)
4. 💾 **Shopify'a otomatik yazar** (GraphQL API ile)

**Örnek:**
- Başlık: "Büyük Beden Uzun Kollu V Yaka Leopar Desenli Diz Üstü Elbise 285058"
- Kategori: **Elbise** ✅
- Meta Alanlar:
  - `custom.yaka_tipi` = "V Yaka" ✅
  - `custom.kol_tipi` = "Uzun Kol" ✅
  - `custom.boy` = "Diz Üstü" ✅
  - `custom.desen` = "Leopar" ✅
""")

st.markdown("---")

# Kategori istatistikleri göster
st.markdown("### 📊 Desteklenen Kategoriler ve Meta Alanları")

col1, col2 = st.columns([1, 2])

with col1:
    category_summary = CategoryMetafieldManager.get_category_summary()
    
    summary_data = []
    for category, count in category_summary.items():
        summary_data.append({
            'Kategori': category,
            'Meta Alan Sayısı': count
        })
    
    st.dataframe(summary_data, use_container_width=True, hide_index=True)

with col2:
    selected_category = st.selectbox(
        "Kategori Detayları",
        options=list(category_summary.keys())
    )
    
    if selected_category:
        metafields = CategoryMetafieldManager.get_metafields_for_category(selected_category)
        
        st.markdown(f"**{selected_category}** kategorisi için meta alanlar:")
        for field_key, field_info in metafields.items():
            st.markdown(f"- `{field_info['key']}`: {field_info['description']}")

st.markdown("---")

# ⚠️ METAFIELD DEFINITIONS OLUŞTURMA
st.markdown("### 🔧 Metafield Definitions Oluştur (İLK ADIM!)")
st.warning("""
⚠️ **ÖNEMLİ**: Meta alanların Shopify'da görünmesi için önce **metafield definitions** oluşturulmalı!

Bu işlem sadece **BİR KERE** yapılır. Zaten oluşturulmuşsa tekrar yapmaya gerek yok.
""")

if st.button("🏗️ Tüm Kategoriler İçin Metafield Definitions Oluştur", type="primary"):
    with st.spinner("Metafield definitions oluşturuluyor..."):
        try:
            shopify_api = ShopifyAPI(
                user_keys["shopify_store"],
                user_keys["shopify_token"]
            )
            
            categories = ['Elbise', 'T-shirt', 'Bluz', 'Pantolon', 'Şort', 'Etek', 
                         'Gömlek', 'Hırka', 'Mont', 'Sweatshirt', 'Tunik', 'Süveter']
            
            total_created = 0
            results_md = ""
            
            for category in categories:
                result = shopify_api.create_all_metafield_definitions_for_category(category)
                total_created += result.get('created', 0)
                
                if result.get('success'):
                    results_md += f"✅ **{category}**: {result['created']} definition oluşturuldu/kontrol edildi\n\n"
                else:
                    results_md += f"❌ **{category}**: Hata - {result.get('errors', [])}\n\n"
                
                time.sleep(0.5)  # Rate limit
            
            st.success(f"✅ Toplam {total_created} metafield definition oluşturuldu/kontrol edildi!")
            st.markdown(results_md)
            
        except Exception as e:
            st.error(f"❌ Hata: {str(e)}")
            import traceback
            with st.expander("Detaylı Hata"):
                st.code(traceback.format_exc())

st.markdown("---")

# Güncelleme Ayarları
st.markdown("### ⚙️ Güncelleme Ayarları")

col1, col2, col3, col4 = st.columns(4)

with col1:
    test_mode = st.checkbox("🧪 Test Modu (İlk 20 ürün)", value=True)
    
with col2:
    dry_run = st.checkbox("🔍 DRY RUN (Sadece göster, güncelleme)", value=True)

with col3:
    update_category = st.checkbox("📦 Kategori güncelle", value=True)
    update_metafields = st.checkbox("🏷️ Meta alanları güncelle", value=True)

with col4:
    use_shopify_suggestions = st.checkbox("🎯 Shopify Önerilerini Kullan", value=True, 
                                          help="Shopify'ın önerdiği kategori ve meta alanları otomatik kullanılır")

st.markdown("---")

# Önizleme Butonu
if st.button("👁️ Önizleme Yap", type="secondary"):
    with st.spinner("Ürünler yükleniyor ve analiz ediliyor..."):
        try:
            shopify_api = ShopifyAPI(
                user_keys["shopify_store"],
                user_keys["shopify_token"]
            )
            
            # Ürünleri yükle
            shopify_api.load_all_products_for_cache()
            
            # Unique ürünleri al
            unique_products = {}
            for product_data in shopify_api.product_cache.values():
                gid = product_data.get('gid')
                if gid and gid not in unique_products:
                    unique_products[gid] = product_data
            
            products = list(unique_products.values())[:20 if test_mode else len(unique_products)]
            
            st.success(f"✅ {len(products)} ürün yüklendi")
            
            # Önizleme tablosu
            preview_data = []
            
            for product in products[:10]:  # İlk 10 ürünü göster
                title = product.get('title', '')
                gid = product.get('gid', '')
                variants = product.get('variants', [])
                description = product.get('description', '')
                
                # Kategori tespit
                category = CategoryMetafieldManager.detect_category(title)
                
                if category:
                    # Taxonomy ID al
                    taxonomy_id = CategoryMetafieldManager.get_taxonomy_id(category)
                    
                    # 🌟 YENİ: Shopify önerilerini al (varsa)
                    shopify_recommendations = None
                    try:
                        recommendations_data = shopify_api.get_product_recommendations(gid)
                        if recommendations_data:
                            shopify_recommendations = recommendations_data
                            logging.info(f"✨ Shopify önerileri alındı: {gid}")
                    except Exception as e:
                        logging.warning(f"Shopify önerileri alınamadı: {e}")
                    
                    # Meta alanları hazırla (TÜM VERI KAYNAKLARIYLA)
                    metafields = CategoryMetafieldManager.prepare_metafields_for_shopify(
                        category=category,
                        product_title=title,
                        product_description=description,
                        variants=variants,
                        shopify_recommendations=shopify_recommendations
                    )
                    
                    metafield_summary = ', '.join([f"{mf['key']}: {mf['value']}" for mf in metafields])
                    
                    preview_data.append({
                        'Ürün': title[:50] + '...' if len(title) > 50 else title,
                        'Kategori': f"{category} ({taxonomy_id})" if taxonomy_id else category,
                        'Meta Alanlar': metafield_summary if metafield_summary else 'Yok'
                    })
                else:
                    preview_data.append({
                        'Ürün': title[:50] + '...' if len(title) > 50 else title,
                        'Kategori': '❌ Tespit edilemedi',
                        'Meta Alanlar': '-'
                    })
            
            st.dataframe(preview_data, use_container_width=True, hide_index=True)
            
            # İstatistikler
            total_with_category = sum(1 for p in products if CategoryMetafieldManager.detect_category(p.get('title', '')))
            
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Toplam Ürün", len(products))
            with col2:
                st.metric("Kategori Tespit Edildi", total_with_category)
            with col3:
                st.metric("Başarı Oranı", f"{(total_with_category/len(products)*100):.1f}%")
            
        except Exception as e:
            st.error(f"❌ Hata: {str(e)}")

# Güncelleme Butonu
st.markdown("---")

if st.button("🚀 Güncellemeyi Başlat", type="primary", disabled=(not update_category and not update_metafields)):
    if dry_run:
        st.warning("⚠️ DRY RUN modu aktif - Değişiklikler Shopify'a yazılmayacak")
    
    with st.spinner("Güncelleme yapılıyor..."):
        try:
            shopify_api = ShopifyAPI(
                user_keys["shopify_store"],
                user_keys["shopify_token"]
            )
            
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Ürünleri yükle
            status_text.text("📦 Ürünler yükleniyor...")
            shopify_api.load_all_products_for_cache()
            
            # Unique ürünleri al
            unique_products = {}
            for product_data in shopify_api.product_cache.values():
                gid = product_data.get('gid')
                if gid and gid not in unique_products:
                    unique_products[gid] = product_data
            
            products = list(unique_products.values())[:20 if test_mode else len(unique_products)]
            
            status_text.text(f"✅ {len(products)} ürün yüklendi")
            
            # Sonuçlar
            stats = {
                'total': len(products),
                'updated': 0,
                'skipped': 0,
                'failed': 0
            }
            
            results_container = st.container()
            
            with results_container:
                st.markdown("### 📊 Güncelleme Sonuçları:")
                results_placeholder = st.empty()
                
                results_html = ""
                
                for idx, product in enumerate(products):
                    gid = product.get('gid')
                    title = product.get('title', 'Bilinmeyen')
                    variants = product.get('variants', [])
                    description = product.get('description', '')
                    
                    progress = (idx + 1) / len(products)
                    progress_bar.progress(progress)
                    status_text.text(f"[{idx + 1}/{len(products)}] {title[:50]}...")
                    
                    # Kategori tespit
                    category = CategoryMetafieldManager.detect_category(title)
                    
                    if not category:
                        stats['skipped'] += 1
                        results_html += f"""
                        <div style='padding: 8px; margin: 3px 0; border-left: 3px solid #ffc107; background: #fff8e1;'>
                            <small>⏭️ Kategori tespit edilemedi: <b>{title[:60]}</b></small>
                        </div>
                        """
                        results_placeholder.markdown(results_html, unsafe_allow_html=True)
                        continue
                    
                    # Taxonomy ID al
                    taxonomy_id = CategoryMetafieldManager.get_taxonomy_id(category)
                    
                    # 🌟 YENİ: Shopify önerilerini al (varsa)
                    shopify_recommendations = None
                    try:
                        recommendations_data = shopify_api.get_product_recommendations(gid)
                        if recommendations_data:
                            shopify_recommendations = recommendations_data
                    except Exception as e:
                        logging.warning(f"Shopify önerileri alınamadı: {e}")
                    
                    # Meta alanları hazırla (TÜM VERI KAYNAKLARIYLA)
                    metafields = CategoryMetafieldManager.prepare_metafields_for_shopify(
                        category=category,
                        product_title=title,
                        product_description=description,
                        variants=variants,
                        shopify_recommendations=shopify_recommendations
                    )
                    
                    if dry_run:
                        # DRY RUN: Sadece göster
                        stats['updated'] += 1
                        metafield_list = ', '.join([f"{mf['key']}: {mf['value']}" for mf in metafields])
                        cat_display = f"{category} ({taxonomy_id})" if taxonomy_id else category
                        
                        results_html += f"""
                        <div style='padding: 8px; margin: 3px 0; border-left: 3px solid #2196f3; background: #e3f2fd;'>
                            <small>🔍 <b>{title[:60]}</b></small><br>
                            <small>&nbsp;&nbsp;&nbsp;&nbsp;Kategori: <b>{cat_display}</b> | Meta: {metafield_list}</small>
                        </div>
                        """
                    else:
                        # GERÇEK GÜNCELLEME
                        try:
                            result = shopify_api.update_product_category_and_metafields(
                                gid,
                                category if update_category else None,
                                metafields if update_metafields else [],
                                use_shopify_suggestions=use_shopify_suggestions,  # Yeni parametre
                                taxonomy_id=taxonomy_id if update_category else None
                            )
                            
                            if result.get('success'):
                                stats['updated'] += 1
                                updated_cat = result.get('updated_category', category)
                                results_html += f"""
                                <div style='padding: 8px; margin: 3px 0; border-left: 3px solid #4caf50; background: #e8f5e9;'>
                                    <small>✅ <b>{title[:60]}</b></small><br>
                                    <small>&nbsp;&nbsp;&nbsp;&nbsp;{result.get('message', 'Güncellendi')}</small>
                                </div>
                                """
                            else:
                                stats['failed'] += 1
                                results_html += f"""
                                <div style='padding: 8px; margin: 3px 0; border-left: 3px solid #f44336; background: #ffebee;'>
                                    <small>❌ <b>{title[:60]}</b></small><br>
                                    <small>&nbsp;&nbsp;&nbsp;&nbsp;Hata: {result.get('message', 'Bilinmeyen')}</small>
                                </div>
                                """
                            
                            time.sleep(0.5)  # Rate limit
                            
                        except Exception as e:
                            stats['failed'] += 1
                            results_html += f"""
                            <div style='padding: 8px; margin: 3px 0; border-left: 3px solid #f44336; background: #ffebee;'>
                                <small>❌ <b>{title[:60]}</b></small><br>
                                <small>&nbsp;&nbsp;&nbsp;&nbsp;Hata: {str(e)}</small>
                            </div>
                            """
                    
                    results_placeholder.markdown(results_html, unsafe_allow_html=True)
            
            # Özet
            st.markdown("---")
            st.markdown("### 📊 Özet:")
            
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Toplam", stats['total'])
            with col2:
                st.metric("Güncellendi", stats['updated'])
            with col3:
                st.metric("Atlandı", stats['skipped'])
            with col4:
                st.metric("Hata", stats['failed'])
            
            if dry_run:
                st.warning("💡 DRY RUN moduydu - Gerçek güncelleme için DRY RUN'ı kapatıp tekrar çalıştırın.")
            elif stats['updated'] > 0:
                st.success(f"✅ {stats['updated']} ürün başarıyla güncellendi!")
            
            progress_bar.progress(1.0)
            status_text.text("✅ Tamamlandı!")
            
        except Exception as e:
            st.error(f"❌ Hata: {str(e)}")
            import traceback
            with st.expander("Detaylı Hata"):
                st.code(traceback.format_exc())

# Yardım bölümü
with st.expander("❓ Yardım ve İpuçları"):
    st.markdown("""
    ### Kategori Tespit Kuralları
    
    Sistem ürün başlığında şu anahtar kelimeleri arar:
    
    - **Elbise:** elbise, dress
    - **T-shirt:** t-shirt, tshirt, tişört
    - **Bluz:** bluz, blouse, gömlek
    - **Pantolon:** pantolon, pants, jean, kot
    - **Şort:** şort, short
    - **Etek:** etek, skirt
    - **Ceket:** ceket, jacket, mont, kaban
    - Ve daha fazlası...
    
    ### Meta Alan Çıkarma
    
    Başlıktan otomatik çıkarılan değerler:
    
    - **Yaka:** V yaka, Bisiklet yaka, Hakim yaka vb.
    - **Kol:** Uzun kol, Kısa kol, Kolsuz vb.
    - **Boy:** Mini, Midi, Maxi, Diz üstü vb.
    - **Desen:** Leopar, Çiçekli, Düz, Çizgili vb.
    - **Paça:** Dar paça, Bol paça vb.
    - **Bel:** Yüksek bel, Normal bel vb.
    
    ### İpuçları
    
    1. ✅ İlk önce **Test Modu** ve **DRY RUN** ile deneyin
    2. ✅ Önizleme yaparak sonuçları kontrol edin
    3. ✅ Ürün başlıklarının açıklayıcı olması önemli
    4. ✅ Kategori tespit edilemezse başlığı düzenleyin
    """)
