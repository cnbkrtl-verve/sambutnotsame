"""
🏷️ Otomatik Kategori ve Meta Alan Yönetim Sistemi

Ürün başlığından otomatik kategori tespiti ve kategori bazlı meta alanlarını doldurur.
Shopify'da manuel işlem yapmadan kategori ve meta alanlarını otomatik günceller.
"""

import re
import logging
import json
import os
from typing import Dict, List, Optional, Tuple

# Varyant helper fonksiyonlarını import et
try:
    from .variant_helpers import get_color_list_as_string
except ImportError:
    # Eğer relative import çalışmazsa, absolute import dene
    try:
        from utils.variant_helpers import get_color_list_as_string
    except ImportError:
        # Son çare: fonksiyonu burada tanımla
        def get_color_list_as_string(variants, separator=', '):
            """Fallback: Varyantlardan renk listesi çıkar"""
            if not variants:
                return None
            colors = set()
            for variant in variants:
                for option in variant.get('options', []):
                    if option.get('name', '').lower() in ['color', 'renk', 'colour']:
                        color = option.get('value')
                        if color:
                            colors.add(color)
            return separator.join(sorted(list(colors))) if colors else None

class CategoryMetafieldManager:
    """
    Kategori tespit ve meta alan yönetimi için merkezi sınıf.
    """
    
    _config = None
    
    @classmethod
    def _load_config(cls):
        if cls._config is None:
            try:
                config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'category_config.json')
                with open(config_path, 'r', encoding='utf-8') as f:
                    cls._config = json.load(f)
            except Exception as e:
                logging.error(f"Konfigürasyon dosyası yüklenemedi: {e}")
                cls._config = {"categories": {}, "patterns": {}}
        return cls._config

    @classmethod
    def get_category_keywords(cls):
        config = cls._load_config()
        keywords = {}
        for cat, data in config.get('categories', {}).items():
            keywords[cat] = data.get('keywords', [])
        return keywords

    @classmethod
    def get_category_metafields(cls):
        config = cls._load_config()
        metafields = {}
        for cat, data in config.get('categories', {}).items():
            metafields[cat] = data.get('metafields', {})
        return metafields
    
    @staticmethod
    def detect_category(product_title: str) -> Optional[str]:
        """
        Ürün başlığından kategori tespit eder.
        
        Args:
            product_title: Ürün başlığı
            
        Returns:
            Tespit edilen kategori veya None
        """
        if not product_title:
            return None
        
        title_lower = product_title.lower()
        keywords_map = CategoryMetafieldManager.get_category_keywords()
        
        # Öncelik sırasına göre kontrol et
        for category, keywords in keywords_map.items():
            for keyword in keywords:
                if keyword.lower() in title_lower:
                    logging.info(f"Kategori tespit edildi: '{category}' (Anahtar: '{keyword}')")
                    return category
        
        logging.warning(f"'{product_title}' için kategori tespit edilemedi")
        return None

    @staticmethod
    def get_taxonomy_id(category: str) -> Optional[str]:
        """
        Kategori adı için Taxonomy ID (GID) döndürür.
        """
        config = CategoryMetafieldManager._load_config()
        cat_data = config.get('categories', {}).get(category)
        if cat_data:
            return cat_data.get('taxonomy_id')
        return None
    
    @staticmethod
    def extract_metafield_values(
        product_title: str, 
        category: str,
        product_description: str = "",
        variants: List[Dict] = None,
        shopify_recommendations: Dict = None
    ) -> Dict[str, str]:
        """
        🔍 ÇOK KATMANLI META ALAN ÇIKARMA SİSTEMİ
        
        4 Katmanlı Veri Kaynağı (Öncelik Sırasına Göre):
        1. Shopify Önerileri (En yüksek öncelik - Shopify'ın AI önerileri)
        2. Varyant Bilgileri (Renk, Beden, Materyal seçenekleri)
        3. Ürün Başlığı (Regex pattern matching ile detaylı analiz)
        4. Ürün Açıklaması (Başlıkta bulunamayanlar için)
        
        Args:
            product_title: Ürün başlığı
            category: Tespit edilen kategori
            product_description: Ürün açıklaması (HTML olabilir)
            variants: Ürün varyantları [{title, options: [{name, value}]}]
            shopify_recommendations: Shopify'ın önerdiği attribute'ler
            
        Returns:
            Meta alan değerleri (key: value)
        """
        values = {}
        title_lower = product_title.lower()
        desc_lower = product_description.lower() if product_description else ""
        
        config = CategoryMetafieldManager._load_config()
        patterns = config.get('patterns', {})
        
        # ============================================
        # KATMAN 1: SHOPIFY ÖNERİLERİNDEN AL (EN YÜKSEK ÖNCELİK)
        # ============================================
        if shopify_recommendations:
            recommended_attrs = shopify_recommendations.get('recommended_attributes', [])
            
            # recommended_attrs bir liste of strings'dir (örn: ["Collar Type", "Sleeve Length"])
            # Bu attribute isimleri sadece hangi alanların önemli olduğunu gösterir
            # Değerleri başlık, varyant veya açıklamadan çıkaracağız
            
            # Şimdilik Shopify attribute isimlerini logla (gelecekte API'den değer de alabiliriz)
            if recommended_attrs:
                logging.info(f"✨ Shopify önerilen attribute'ler: {', '.join(recommended_attrs)}")
                # Not: Shopify sadece attribute ismi öneriyor, değer önermiyor
                # Değerleri diğer katmanlardan (varyant, başlık, açıklama) çıkaracağız
        
        # ============================================
        # KATMAN 2: VARYANT BİLGİLERİNDEN AL
        # ============================================
        if variants:
            # Renk bilgisini çıkar (zaten get_color_list_as_string var)
            color_value = get_color_list_as_string(variants)
            if color_value and 'renk' not in values:
                values['renk'] = color_value
                logging.info(f"🎨 Varyantlardan renk çıkarıldı: '{color_value}'")
            
            # Diğer varyant seçeneklerini de kontrol et
            for variant in variants:
                options = variant.get('options', [])
                for option in options:
                    option_name = option.get('name', '').lower()
                    option_value = option.get('value', '')
                    
                    # Beden/Size
                    if option_name in ['size', 'beden', 'boyut'] and 'beden' not in values:
                        # Varyantlardan beden listesi çıkar
                        sizes = set()
                        for v in variants:
                            for opt in v.get('options', []):
                                if opt.get('name', '').lower() in ['size', 'beden', 'boyut']:
                                    sizes.add(opt.get('value', ''))
                        if sizes:
                            values['beden'] = ', '.join(sorted(list(sizes)))
                            logging.info(f"📏 Varyantlardan beden çıkarıldı: '{values['beden']}'")
                    
                    # Kumaş/Material
                    if option_name in ['material', 'kumaş', 'kumaş tipi', 'fabric'] and 'kumaş' not in values:
                        values['kumaş'] = option_value
                        logging.info(f"🧵 Varyantlardan kumaş çıkarıldı: '{option_value}'")
        
        # ============================================
        # KATMAN 3: BAŞLIKTAN REGEX İLE ÇIKAR
        # ============================================
        for field, pattern_list in patterns.items():
            if field not in values:  # Sadece henüz dolmamış alanları doldur
                for pattern, value in pattern_list:
                    if re.search(pattern, title_lower):
                        values[field] = value
                        logging.info(f"📝 Başlıktan çıkarıldı: {field} = '{value}'")
                        break  # İlk eşleşmeyi al
        
        # ============================================
        # KATMAN 4: AÇIKLAMADAN ÇIKAR (SON ÇARE)
        # ============================================
        if desc_lower:
            for field, pattern_list in patterns.items():
                if field not in values:  # Sadece henüz dolmamış alanları doldur
                    for pattern, value in pattern_list:
                        if re.search(pattern, desc_lower):
                            values[field] = value
                            logging.info(f"📄 Açıklamadan çıkarıldı: {field} = '{value}'")
                            break  # İlk eşleşmeyi al
        
        return values
    
    @staticmethod
    def get_metafields_for_category(category: str) -> Dict[str, dict]:
        """
        Belirtilen kategori için meta alan şablonlarını döndürür.
        
        Args:
            category: Kategori adı
            
        Returns:
            Meta alan şablonları
        """
        return CategoryMetafieldManager.get_category_metafields().get(category, {})
    
    @staticmethod
    def prepare_metafields_for_shopify(
        category: str, 
        product_title: str,
        product_description: str = "",
        variants: List[Dict] = None,
        shopify_recommendations: Dict = None
    ) -> List[Dict]:
        """
        Shopify GraphQL için metafield input formatını hazırlar.
        
        Args:
            category: Ürün kategorisi
            product_title: Ürün başlığı
            product_description: Ürün açıklaması
            variants: Ürün varyantları (renk bilgisi için)
            shopify_recommendations: Shopify AI önerileri
            
        Returns:
            Shopify metafield input listesi
        """
        metafield_templates = CategoryMetafieldManager.get_metafields_for_category(category)
        
        # 🌟 UPGRADED: Tüm veri kaynaklarını kullan
        extracted_values = CategoryMetafieldManager.extract_metafield_values(
            product_title=product_title,
            category=category,
            product_description=product_description,
            variants=variants,
            shopify_recommendations=shopify_recommendations
        )
        
        shopify_metafields = []
        
        for field_key, template in metafield_templates.items():
            # Meta alan key'ini çıkar (custom.yaka_tipi -> yaka_tipi)
            key = template['key']
            
            # Çıkarılan değerler içinde varsa kullan
            if key in extracted_values:
                value = extracted_values[key]
                
                shopify_metafields.append({
                    'namespace': template['namespace'],
                    'key': template['key'],
                    'value': value,
                    'type': template['type']
                })
                
                logging.info(f"Shopify metafield hazırlandı: {template['namespace']}.{template['key']} = '{value}'")
        
        return shopify_metafields
    
    @staticmethod
    def get_category_summary() -> Dict[str, int]:
        """
        Kategori istatistiklerini döndürür.
        
        Returns:
            Kategori adı ve meta alan sayısı
        """
        summary = {}
        metafields = CategoryMetafieldManager.get_category_metafields()
        for category, fields in metafields.items():
            summary[category] = len(fields)
        return summary


# Kullanım örneği
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Test
    test_titles = [
        "Büyük Beden Uzun Kollu Leopar Desenli Diz Üstü Elbise 285058",
        "Büyük Beden Bisiklet Yaka Yarım Kollu Düz Renk T-shirt 303734",
        "Büyük Beden V Yaka Kısa Kol Çiçekli Bluz 256478",
        "Büyük Beden Yüksek Bel Dar Paça Siyah Pantolon 123456"
    ]
    
    for title in test_titles:
        print(f"\n{'='*60}")
        print(f"Ürün: {title}")
        print(f"{'='*60}")
        
        # Kategori tespit
        category = CategoryMetafieldManager.detect_category(title)
        print(f"Kategori: {category}")
        
        if category:
            # Meta alanları hazırla
            metafields = CategoryMetafieldManager.prepare_metafields_for_shopify(category, title)
            print(f"\nOluşturulan Meta Alanlar ({len(metafields)}):")
            for mf in metafields:
                print(f"  - {mf['namespace']}.{mf['key']} = '{mf['value']}'")
