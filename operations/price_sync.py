# operations/price_sync.py - 10 Worker Optimize Edilmiş Sürüm

import pandas as pd
import logging
import requests
import time
import random
import threading
from collections import deque
import sys
import os

# Proje kök dizinini Python path'ine ekle
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

class SmartRateLimiter:
    """10 worker için optimize edilmiş akıllı rate limiter"""
    def __init__(self, max_requests_per_second=2.5, burst_capacity=15):
        self.max_rate = max_requests_per_second
        self.burst_capacity = burst_capacity
        self.tokens = burst_capacity
        self.last_refill = time.time()
        self.lock = threading.Lock()
        self.backoff_until = 0
        self.throttle_count = 0
        
    def wait(self):
        with self.lock:
            now = time.time()
            
            # Backoff durumu
            if now < self.backoff_until:
                wait_time = self.backoff_until - now
                time.sleep(wait_time)
                now = time.time()
            
            # Token yenileme (burst capacity'ye kadar)
            elapsed = now - self.last_refill
            new_tokens = elapsed * self.max_rate
            self.tokens = min(self.burst_capacity, self.tokens + new_tokens)
            self.last_refill = now
            
            # Token kontrolü
            if self.tokens >= 1:
                self.tokens -= 1
                return
            
            # Token yetersiz, hesaplanmış bekleme
            wait_time = 1.0 / self.max_rate
            time.sleep(wait_time)
    
    def handle_throttle_error(self):
        """429 hatası geldiğinde adaptif throttle"""
        with self.lock:
            self.throttle_count += 1
            
            # Progressive backoff - her throttle'da daha uzun bekle
            backoff_time = min(30, 5 * (1.5 ** min(self.throttle_count, 5)))
            self.backoff_until = time.time() + backoff_time
            
            # Hızı düşür ama çok fazla değil
            self.max_rate = max(0.8, self.max_rate * 0.85)
            
            logging.warning(f"Rate limit! Backoff: {backoff_time:.1f}s, Yeni hız: {self.max_rate:.2f} req/sec")
    
    def handle_success(self):
        """Başarılı istekten sonra yavaşça hızı artır"""
        if self.throttle_count > 0:
            self.throttle_count = max(0, self.throttle_count - 1)
            if self.throttle_count == 0:
                self.max_rate = min(2.5, self.max_rate * 1.05)

def update_prices_for_single_product(shopify_api, product_id, variants_to_update, rate_limiter):
    """
    10-Worker optimize edilmiş bulk fiyat güncelleme
    """
    if not variants_to_update:
        return {"status": "skipped", "reason": "Güncellenecek varyant yok."}

    variants_input = []
    for variant_payload in variants_to_update:
        variant_input = {
            "id": variant_payload.get("id"),
            "price": variant_payload["price"]
        }
        
        if "compareAtPrice" in variant_payload:
            variant_input["compareAtPrice"] = variant_payload["compareAtPrice"]
            
        variants_input.append(variant_input)

    bulk_mutation = """
    mutation productVariantsBulkUpdate($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
        productVariantsBulkUpdate(productId: $productId, variants: $variants) {
            productVariants {
                id
                price
                compareAtPrice
            }
            userErrors {
                field
                message
                code
            }
        }
    }
    """
    
    max_retries = 5  # 10 worker için daha fazla retry
    for attempt in range(max_retries):
        try:
            rate_limiter.wait()
            
            result = shopify_api.execute_graphql(bulk_mutation, {
                "productId": product_id,
                "variants": variants_input
            })
            
            updated_variants = result.get('productVariantsBulkUpdate', {}).get('productVariants', [])
            errors = result.get('productVariantsBulkUpdate', {}).get('userErrors', [])
            
            if errors:
                is_throttled = any(err.get('code') == 'THROTTLED' for err in errors)
                
                if is_throttled and attempt < max_retries - 1:
                    rate_limiter.handle_throttle_error()
                    # Progressive wait - her denemede daha uzun bekle
                    wait_time = (1.5 ** attempt) + random.uniform(0.5, 2.0)
                    time.sleep(wait_time)
                    continue
                
                return {"status": "failed", "reason": f"Bulk update errors: {errors[:3]}"}  # İlk 3 hatayı göster
            
            rate_limiter.handle_success()
            success_count = len(updated_variants)
            return {"status": "success", "updated_count": success_count}
            
        except Exception as e:
            if "THROTTLED" in str(e) or "429" in str(e):
                if attempt < max_retries - 1:
                    rate_limiter.handle_throttle_error()
                    time.sleep((2 ** attempt) + random.uniform(1, 3))
                    continue
            
            if attempt == max_retries - 1:
                return {"status": "failed", "reason": f"Max retries exceeded: {str(e)}"}

    return {"status": "failed", "reason": "All retries failed"}

def _process_one_product_for_price_sync(shopify_api, product_base_sku, all_variants_df, price_data_df, price_col, compare_col, rate_limiter):
    """
    10-Worker için optimize edilmiş tek ürün işleme
    Tek GraphQL call ile hem ürün hem varyantları al
    """
    try:
        # İlk önce fiyat verisini kontrol et
        price_row = price_data_df.loc[price_data_df['MODEL KODU'] == product_base_sku]
        if price_row.empty:
            return {"status": "skipped", "reason": f"Fiyat bulunamadı: {product_base_sku}"}
        
        price_to_set = price_row.iloc[0][price_col]
        compare_price_to_set = price_row.iloc[0].get(compare_col)

        # OPTIMIZE: Tek GraphQL ile ürün ve tüm varyantlarını al
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
            return {"status": "skipped", "reason": "Eşleşen varyant bulunamadı"}

        # Bulk update çalıştır
        result = update_prices_for_single_product(shopify_api, product_id, updates, rate_limiter)
        
        if result.get('status') == 'success':
            rate_limiter.handle_success()
            
        return result

    except Exception as e:
        logging.error(f"Ürün {product_base_sku} işlenirken hata: {str(e)}")
        return {"status": "failed", "reason": str(e)}

def update_single_product_custom(shopify_api, sku_or_id, new_price, compare_at_price=None, rate_limiter=None):
    """
    Tek bir ürün veya varyant için manuel fiyat güncellemesi yapar.
    sku_or_id: Ürün SKU'su veya Shopify ID'si (gid://shopify/Product/...)
    """
    try:
        if not rate_limiter:
            rate_limiter = SmartRateLimiter()
            
        # 1. ID mi SKU mu kontrol et
        is_id = str(sku_or_id).startswith("gid://")
        
        if is_id:
            # ID ise direkt güncelleme dene (Product ID varsayıyoruz)
            # Ancak varyant ID'lerini bulmamız lazım.
            query = """
            query getProductVariants($id: ID!) {
              product(id: $id) {
                variants(first: 100) {
                  edges { node { id } }
                }
              }
            }
            """
            rate_limiter.wait()
            result = shopify_api.execute_graphql(query, {"id": sku_or_id})
            variants = result.get('product', {}).get('variants', {}).get('edges', [])
            
            if not variants:
                return {"status": "failed", "reason": "Ürün veya varyant bulunamadı."}
                
            updates = []
            for v in variants:
                payload = {"id": v['node']['id'], "price": str(new_price)}
                if compare_at_price:
                    payload["compareAtPrice"] = str(compare_at_price)
                updates.append(payload)
                
            return update_prices_for_single_product(shopify_api, sku_or_id, updates, rate_limiter)
            
        else:
            # SKU ise, mevcut _process_one_product_for_price_sync mantığına benzer bir yol izle
            # Ancak burada DataFrame yerine doğrudan değerleri kullanacağız.
            
            # Önce ürünü bul
            query = """
            query getProductBySku($query: String!) {
              products(first: 1, query: $query) {
                edges {
                  node {
                    id
                    variants(first: 100) {
                      edges { node { id sku } }
                    }
                  }
                }
              }
            }
            """
            rate_limiter.wait()
            result = shopify_api.execute_graphql(query, {"query": f"sku:{sku_or_id}"})
            edges = result.get('products', {}).get('edges', [])
            
            if not edges:
                return {"status": "failed", "reason": f"SKU bulunamadı: {sku_or_id}"}
                
            product = edges[0]['node']
            product_id = product['id']
            
            updates = []
            for v in product.get('variants', {}).get('edges', []):
                node = v['node']
                # Tam eşleşme veya varyant SKU'su ana SKU ile başlıyorsa
                if node.get('sku') == sku_or_id or str(node.get('sku')).startswith(str(sku_or_id)):
                    payload = {"id": node['id'], "price": str(new_price)}
                    if compare_at_price:
                        payload["compareAtPrice"] = str(compare_at_price)
                    updates.append(payload)
            
            if not updates:
                return {"status": "failed", "reason": "Eşleşen varyant bulunamadı."}
                
            return update_prices_for_single_product(shopify_api, product_id, updates, rate_limiter)

    except Exception as e:
        return {"status": "failed", "reason": str(e)}

def update_collection_custom(shopify_api, collection_id, adjustment_type, value, rate_limiter=None, progress_queue=None):
    """
    Koleksiyondaki tüm ürünlerin fiyatlarını günceller.
    adjustment_type: 'percentage_inc' (%), 'percentage_dec' (%), 'fixed_amount' (+)
    value: Değer (örn: 10)
    """
    try:
        if not rate_limiter:
            rate_limiter = SmartRateLimiter()
            
        # 1. Ürünleri çek (İlerleme bildirimi ile)
        def fetch_callback(msg):
            if progress_queue:
                progress_queue.put({'progress': 5, 'message': msg})

        products = shopify_api.get_products_by_collection(collection_id, progress_callback=fetch_callback)
        total_products = len(products)
        
        if total_products == 0:
            return {"status": "failed", "reason": "Koleksiyonda ürün yok."}
            
        success_count = 0
        failed_count = 0
        processed = 0
        
        for product in products:
            processed += 1
            product_id = product['id']
            product_title = product['title']
            
            updates = []
            for v_edge in product.get('variants', {}).get('edges', []):
                variant = v_edge['node']
                current_price = float(variant.get('price', 0))
                
                # Yeni fiyatı hesapla
                new_price = current_price
                if adjustment_type == 'percentage_inc':
                    new_price = current_price * (1 + value / 100)
                elif adjustment_type == 'percentage_dec':
                    new_price = current_price * (1 - value / 100)
                elif adjustment_type == 'fixed_amount':
                    new_price = current_price + value
                elif adjustment_type == 'set_discount_rate':
                    # İndirim oranını ayarla (Compare At Price üzerinden)
                    compare_at = float(variant.get('compareAtPrice') or 0)
                    if compare_at > 0:
                        new_price = compare_at * (1 - value / 100)
                    else:
                        # Compare At Price yoksa işlem yapma (veya mevcut fiyatı koru)
                        continue
                
                # Yuvarlama (X.99)
                import math
                new_price = math.floor(new_price) + 0.99
                
                updates.append({
                    "id": variant['id'],
                    "price": f"{new_price:.2f}"
                })
            
            if updates:
                result = update_prices_for_single_product(shopify_api, product_id, updates, rate_limiter)
                if result.get('status') == 'success':
                    success_count += 1
                else:
                    failed_count += 1
            
            if progress_queue:
                progress_queue.put({
                    'progress': int((processed / total_products) * 100),
                    'message': f"İşleniyor: {product_title} ({processed}/{total_products})"
                })
                
        return {
            "status": "success", 
            "total": total_products, 
            "updated": success_count, 
            "failed": failed_count
        }

    except Exception as e:
        return {"status": "failed", "reason": str(e)}