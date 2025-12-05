import re
import openai
import logging
import streamlit as st

class SEOManager:
    def __init__(self, api_key, api_base, model_name):
        self.api_key = api_key
        self.api_base = api_base
        self.model_name = model_name
        self.client = None
        
        if self.api_key:
            try:
                self.client = openai.OpenAI(
                    api_key=self.api_key,
                    base_url=self.api_base
                )
            except Exception as e:
                logging.error(f"AI Client başlatılamadı: {e}")

    def generate_text(self, system_prompt, user_prompt, temperature=0.7):
        """
        Genel amaçlı metin üretim fonksiyonu.
        """
        if not self.client:
            return "Hata: AI API anahtarı yapılandırılmamış."

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=temperature
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logging.error(f"AI Üretim Hatası: {e}")
            return f"Hata oluştu: {str(e)}"

    def generate_product_description(self, product_name, current_description, features, custom_prompt):
        """
        Ürün açıklaması üretir.
        """
        system_prompt = "Sen uzman bir e-ticaret SEO içerik yazarısın. Ürünleri satışa dönüştürecek, samimi ve profesyonel bir dille anlatırsın."
        
        user_content = f"""
        Aşağıdaki ürün için SEO uyumlu, satış odaklı bir ürün açıklaması yaz.
        
        Ürün Adı: {product_name}
        Mevcut Açıklama: {current_description}
        Özellikler: {features}
        
        Kullanıcı Talimatları: {custom_prompt}
        """
        
        return self.generate_text(system_prompt, user_content)

    def generate_seo_meta(self, product_name, description, custom_prompt):
        """
        Meta Title ve Meta Description üretir.
        """
        system_prompt = "Sen bir SEO uzmanısın. Google arama sonuçları için en uygun başlık ve açıklamaları yazarsın."
        
        user_content = f"""
        Aşağıdaki ürün için Meta Title (max 60 karakter) ve Meta Description (max 160 karakter) oluştur.
        Çıktıyı şu formatta ver:
        Title: [Başlık]
        Description: [Açıklama]
        
        Ürün Adı: {product_name}
        Ürün İçeriği: {description[:500]}...
        
        Kullanıcı Talimatları: {custom_prompt}
        """
        
        return self.generate_text(system_prompt, user_content)

    def generate_image_alt_text(self, product_name, variant_title, custom_prompt):
        """
        Görsel için Alt Text üretir (Bağlamsal).
        """
        system_prompt = "Sen bir görsel SEO uzmanısın. Görme engelliler ve arama motorları için görselleri en iyi tanımlayan kısa metinler (alt text) yazarsın."
        
        user_content = f"""
        Aşağıdaki ürün görseli için açıklayıcı bir Alt Text (Alternatif Metin) yaz.
        
        Ürün: {product_name}
        Varyant/Renk: {variant_title}
        
        Kullanıcı Talimatları: {custom_prompt}
        """
        
        return self.generate_text(system_prompt, user_content)

    # --- URL (Handle) İşlemleri ---
    
    @staticmethod
    def clean_handle(text):
        """
        Türkçe karakterleri değiştirir ve URL uyumlu hale getirir.
        """
        text = text.lower()
        replacements = {
            'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c',
            'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'
        }
        for src, dest in replacements.items():
            text = text.replace(src, dest)
        
        # Alfanümerik olmayan karakterleri tire ile değiştir
        text = re.sub(r'[^a-z0-9]+', '-', text)
        # Tekrarlayan tireleri temizle
        text = re.sub(r'-+', '-', text)
        # Baştaki ve sondaki tireleri temizle
        text = text.strip('-')
        return text

    @staticmethod
    def process_handle(current_handle, mode, remove_words=None, add_prefix="", add_suffix=""):
        """
        Handle üzerinde gelişmiş işlemler yapar.
        """
        new_handle = current_handle
        
        if mode == "clean_only":
            # Sadece temizlik (zaten clean_handle ile yapılmış varsayılır ama tekrar geçebiliriz)
            pass
            
        elif mode == "remove_numbers":
            # Sayıları kaldır
            new_handle = re.sub(r'\d+', '', new_handle)
            new_handle = re.sub(r'-+', '-', new_handle).strip('-')
            
        elif mode == "remove_words" and remove_words:
            # Belirli kelimeleri çıkar
            words = [w.strip() for w in remove_words.split(',')]
            for word in words:
                if word:
                    # Kelimeyi tam eşleşme veya tireler arasında bul
                    pattern = re.compile(rf'(-|^){re.escape(word)}(-|$)')
                    new_handle = pattern.sub('-', new_handle)
            new_handle = re.sub(r'-+', '-', new_handle).strip('-')
            
        # Prefix / Suffix
        if add_prefix:
            prefix = SEOManager.clean_handle(add_prefix)
            if not new_handle.startswith(prefix):
                new_handle = f"{prefix}-{new_handle}"
                
        if add_suffix:
            suffix = SEOManager.clean_handle(add_suffix)
            if not new_handle.endswith(suffix):
                new_handle = f"{new_handle}-{suffix}"
                
        return new_handle
