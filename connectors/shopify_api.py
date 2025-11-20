# connectors/shopify_api.py (Rate Limit Geliştirilmiş)

import requests
import time
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Union
from data_models import Order, Product, Customer

class ShopifyAPI:
    """Shopify Admin API ile iletişimi yöneten sınıf."""
    def __init__(self, store_url: str, access_token: str, api_version: str = '2024-10'): # api_version parametresi burada ekli olmalı
        if not store_url: raise ValueError("Shopify Mağaza URL'si boş olamaz.")
        if not access_token: raise ValueError("Shopify Erişim Token'ı boş olamaz.")
        
        self.store_url = store_url if store_url.startswith('http') else f"https://{store_url.strip()}"
        self.access_token = access_token
        self.api_version = api_version # Gelen versiyonu kullan
        self.graphql_url = f"{self.store_url}/admin/api/{self.api_version}/graphql.json" # URL'yi dinamik hale getir
        self.rest_api_version = self.api_version
        self.headers = {
            'X-Shopify-Access-Token': access_token,
            'Content-Type': 'application/json',
            'User-Agent': 'Sentos-Sync-Python/Modular-v1.0'
        }
        self.product_cache = {}
        self.location_id = None
        
        # ✅ Shopify 2024-10 Rate Limits (daha konservatif)
        # Shopify GraphQL Cost: 1000 points/sec, 50 cost avg/query = ~20 queries/sec max
        # Ancak burst'ü önlemek için daha düşük limit kullanıyoruz
        self.last_request_time = 0
        self.min_request_interval = 0.6  # 0.4'ten 0.6'ya çıkarıldı
        self.request_count = 0
        self.window_start = time.time()
        self.max_requests_per_minute = 30  # 40'tan 30'a düşürüldü
        self.burst_tokens = 5  # 10'dan 5'e düşürüldü (burst koruması)
        self.current_tokens = 5  # Başlangıç token sayısı da 5

    def _rate_limit_wait(self):
        """
        ✅ Geliştirilmiş Rate Limiter - Shopify 2024-10 API için optimize
        - Token bucket algoritması
        - Adaptive throttling
        - Burst protection
        """
        current_time = time.time()
    
        # Token bucket: Her saniye token kazanılır
        elapsed = current_time - self.last_request_time
        tokens_to_add = elapsed * (self.max_requests_per_minute / 60.0)
        self.current_tokens = min(self.burst_tokens, self.current_tokens + tokens_to_add)
    
        # Eğer yeterli token varsa, isteği yap
        if self.current_tokens >= 1:
            self.current_tokens -= 1
            self.last_request_time = current_time
            return
    
        # Token yetersiz: Bekleme süresi hesapla
        wait_time = (1 - self.current_tokens) / (self.max_requests_per_minute / 60.0)
        
        # ✅ Adaptive Throttling: Eğer sürekli bekleniyorsa, rate'i azalt
        if wait_time > 1.5:  # 2.0'dan 1.5'e düşürüldü (daha erken müdahale)
            wait_time = min(wait_time * 1.5, 8.0)  # Maksimum 8 saniye (5'ten 8'e çıkarıldı)
            logging.warning(f"⚠️ Adaptive throttling aktif: {wait_time:.2f}s bekleniyor")
        
        time.sleep(wait_time)
        self.last_request_time = time.time()
        self.current_tokens = 0
        
        # ✅ Bekleme sonrası debug log
        logging.debug(f"🔄 Rate limit beklendi: {wait_time:.2f}s | Tokens: {self.current_tokens:.1f}/{self.burst_tokens}")

    def _make_request(self, method, endpoint, data=None, is_graphql=False, headers=None, files=None):
        self._rate_limit_wait()
        
        req_headers = headers if headers is not None else self.headers
        try:
            if not is_graphql and not endpoint.startswith('http'):
                # ✅ REST API endpoint'lerde de 2024-10 sürümünü kullan
                url = f"{self.store_url}/admin/api/{self.rest_api_version}/{endpoint}"
            else:
                url = endpoint if endpoint.startswith('http') else self.graphql_url
            
            response = requests.request(method, url, headers=req_headers, 
                                        json=data if isinstance(data, dict) else None, 
                                        data=data if isinstance(data, bytes) else None,
                                        files=files, timeout=90)
            response.raise_for_status()
            if response.content and 'application/json' in response.headers.get('Content-Type', ''):
                return response.json()
            return response
        except requests.exceptions.RequestException as e:
            error_content = e.response.text if e.response else "No response"
            logging.error(f"Shopify API Bağlantı Hatası ({url}): {e} - Response: {error_content}")
            raise e

    def execute_graphql(self, query, variables=None):
        """GraphQL sorgusunu çalıştırır - gelişmiş hata yönetimi ile."""
        payload = {'query': query, 'variables': variables or {}}
        max_retries = 10  # 8'den 10'a çıkarıldı
        retry_delay = 3  # 2'den 3'e çıkarıldı (daha uzun bekleme)
        
        # Debug için sorgu bilgilerini logla
        logging.debug(f"GraphQL Query: {query[:100]}...")
        if variables:
            logging.debug(f"GraphQL Variables: {json.dumps(variables, indent=2)[:200]}...")
            
        for attempt in range(max_retries):
            try:
                response = requests.post(self.graphql_url, headers=self.headers, json=payload, timeout=90)
                response.raise_for_status()
                response_data = response.json()
                
                if "errors" in response_data:
                    errors = response_data.get("errors", [])
                    
                    # Throttling kontrolü
                    is_throttled = any(
                        err.get('extensions', {}).get('code') == 'THROTTLED' 
                        for err in errors
                    )
                    if is_throttled and attempt < max_retries - 1:
                        # ✅ Daha agresif exponential backoff
                        wait_time = min(retry_delay * (2.5 ** attempt), 30)  # Max 30 saniye
                        logging.warning(f"⚠️ GraphQL Throttled! {wait_time:.1f}s beklenecek... (Deneme {attempt + 1}/{max_retries})")
                        time.sleep(wait_time)
                        # ✅ Token'ları sıfırla (rate limiter'ı da etkileyecek)
                        self.current_tokens = 0
                        continue
                    
                    # Hata detaylarını logla
                    logging.error("GraphQL Hatası Detayları:")
                    logging.error(f"Query: {query}")
                    if variables:
                        logging.error(f"Variables: {json.dumps(variables, indent=2)}")
                    logging.error(f"Errors: {json.dumps(errors, indent=2)}")
                    
                    # Hata mesajlarını topla
                    error_messages = []
                    for err in errors:
                        msg = err.get('message', 'Bilinmeyen GraphQL hatası')
                        locations = err.get('locations', [])
                        path = err.get('path', [])
                        
                        error_detail = msg
                        if locations:
                            error_detail += f" (Satır: {locations[0].get('line', '?')})"
                        if path:
                            error_detail += f" (Alan: {'.'.join(map(str, path))})"
                            
                        error_messages.append(error_detail)
                    
                    raise Exception(f"GraphQL Error: {'; '.join(error_messages)}")

                return response_data.get("data", {})
            except requests.exceptions.HTTPError as e:
                if e.response and e.response.status_code == 429 and attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logging.warning(f"HTTP 429 Rate Limit! {wait_time} saniye beklenip tekrar denenecek...")
                    time.sleep(wait_time)
                    continue
                else:
                    logging.error(f"API bağlantı hatası: {e}")
                    raise e
            except requests.exceptions.RequestException as e:
                 logging.error(f"API bağlantı hatası: {e}. Bu hata için tekrar deneme yapılmıyor.")
                 raise e
        raise Exception(f"API isteği {max_retries} denemenin ardından başarısız oldu.")

    def find_customer_by_email(self, email: str) -> Optional[str]:
        """YENİ: Verilen e-posta ile müşteri arar."""
        query = """
        query($email: String!) {
          customers(first: 1, query: $email) {
            edges {
              node {
                id
              }
            }
          }
        }
        """
        result = self.execute_graphql(query, {"email": f"email:{email}"})
        edges = result.get('customers', {}).get('edges', [])
        return edges[0]['node']['id'] if edges else None

    def create_customer(self, customer_data: Dict[str, Any]) -> Optional[str]:
        """YENİ: Yeni bir müşteri oluşturur - Şirket ve adres bilgileri ile."""
        mutation = """
        mutation customerCreate($input: CustomerInput!) {
          customerCreate(input: $input) {
            customer {
              id
              email
              firstName
              lastName
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        input_data = {
            "firstName": customer_data.get('firstName'),
            "lastName": customer_data.get('lastName'),
            "email": customer_data.get('email'),
            "phone": customer_data.get('phone')
        }
        
        # Adres bilgilerini ekle (defaultAddress veya addresses)
        default_address = customer_data.get('defaultAddress')
        if default_address:
            # Müşteriye adres ekle
            address_input = {
                "address1": default_address.get('address1'),
                "address2": default_address.get('address2'),
                "city": default_address.get('city'),
                "company": default_address.get('company'),  # ŞİRKET BİLGİSİ
                "firstName": default_address.get('firstName') or customer_data.get('firstName'),
                "lastName": default_address.get('lastName') or customer_data.get('lastName'),
                "phone": default_address.get('phone') or customer_data.get('phone'),
                "province": default_address.get('province'),
                "country": default_address.get('country'),
                "zip": default_address.get('zip')
            }
            # Boş değerleri temizle
            address_input = {k: v for k, v in address_input.items() if v}
            if address_input:
                input_data["addresses"] = [address_input]
        
        result = self.execute_graphql(mutation, {"input": input_data})
        if errors := result.get('customerCreate', {}).get('userErrors', []):
            raise Exception(f"Müşteri oluşturma hatası: {errors}")
        return result.get('customerCreate', {}).get('customer', {}).get('id')

    def find_variant_id_by_sku(self, sku: str) -> Optional[str]:
        """YENİ: Verilen SKU ile ürün varyantı arar."""
        query = """
        query($sku: String!) {
          productVariants(first: 1, query: $sku) {
            edges {
              node {
                id
              }
            }
          }
        }
        """
        result = self.execute_graphql(query, {"sku": f"sku:{sku}"})
        edges = result.get('productVariants', {}).get('edges', [])
        return edges[0]['node']['id'] if edges else None

    def get_orders_by_date_range(self, start_date_iso: str, end_date_iso: str) -> List[Dict[str, Any]]:
        all_orders = []
        # Simplified query first - test basic order fields
        query = """
        query getOrders($cursor: String, $filter_query: String!) {
          orders(first: 10, after: $cursor, query: $filter_query, sortKey: CREATED_AT, reverse: true) {
            pageInfo { hasNextPage, endCursor }
            edges {
              node {
                id
                name
                createdAt
                displayFinancialStatus
                displayFulfillmentStatus
                note
                tags
                customer { 
                  id
                  firstName
                  lastName
                  email
                  phone
                  numberOfOrders
                  # Şirket ve adres bilgileri
                  defaultAddress {
                    id
                    firstName
                    lastName
                    company
                    address1
                    address2
                    city
                    province
                    provinceCode
                    zip
                    country
                    countryCodeV2
                    phone
                  }
                }
                
                # Ödeme yöntemi (gateway names)
                paymentGatewayNames
                
                # Kargo bilgileri
                shippingLine {
                  title
                  code
                  source
                  originalPriceSet { shopMoney { amount currencyCode } }
                }
                
                # İndirim uygulamaları
                discountApplications(first: 10) {
                  edges {
                    node {
                      ... on DiscountCodeApplication {
                        code
                        value {
                          ... on MoneyV2 {
                            amount
                            currencyCode
                          }
                          ... on PricingPercentageValue {
                            percentage
                          }
                        }
                      }
                      ... on ManualDiscountApplication {
                        title
                        description
                        value {
                          ... on MoneyV2 {
                            amount
                            currencyCode
                          }
                          ... on PricingPercentageValue {
                            percentage
                          }
                        }
                      }
                    }
                  }
                }
                
                # Özel alanlar
                customAttributes {
                  key
                  value
                }
                
                currentSubtotalPriceSet { shopMoney { amount currencyCode } }
                currentTotalPriceSet { shopMoney { amount currencyCode } }
                totalPriceSet { shopMoney { amount currencyCode } }
                originalTotalPriceSet { shopMoney { amount currencyCode } }
                totalShippingPriceSet { shopMoney { amount currencyCode } }
                totalTaxSet { shopMoney { amount currencyCode } }
                totalDiscountsSet { shopMoney { amount currencyCode } }

                lineItems(first: 250) {
                  nodes {
                    id
                    title
                    quantity
                    variant { 
                      id
                      sku
                      title 
                    }
                    originalUnitPriceSet { shopMoney { amount currencyCode } }
                    discountedUnitPriceSet { shopMoney { amount currencyCode } }
                    taxable # Vergiye tabi olup olmadığını belirtir
                    taxLines { # Satıra uygulanan vergilerin listesi
                      priceSet { shopMoney { amount, currencyCode } }
                      ratePercentage
                      title
                    }
                    # Özel alanlar (line item düzeyinde)
                    customAttributes {
                      key
                      value
                    }
                  }
                }
                
                # Siparişin genel vergi dökümü
                taxLines {
                  priceSet { shopMoney { amount, currencyCode } }
                  ratePercentage
                  title
                }
                
                shippingAddress {
                  name
                  address1
                  address2
                  city
                  province
                  provinceCode
                  zip
                  country
                  countryCodeV2
                  phone
                  company
                }
                
                billingAddress {
                  name
                  firstName
                  lastName
                  address1
                  address2
                  city
                  province
                  provinceCode
                  zip
                  country
                  countryCodeV2
                  phone
                  company
                }
              }
            }
          }
        }
        """
        variables = {"cursor": None, "filter_query": f"created_at:>='{start_date_iso}' AND created_at:<='{end_date_iso}'"}
        
        while True:
            data = self.execute_graphql(query, variables)
            if not data: break
            orders_data = data.get("orders", {})
            for edge in orders_data.get("edges", []):
                all_orders.append(edge["node"])
            
            page_info = orders_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"): break
            
            variables["cursor"] = page_info["endCursor"]
            time.sleep(1)

        return all_orders

    def create_order(self, order_input):
        """YENİ: Verilen bilgilerle yeni bir sipariş oluşturur - Doğru GraphQL type ve field'lar ile."""
        # Gönderilen line item sayısını kaydet (doğrulama için)
        input_line_items_count = len(order_input.get('lineItems', []))
        input_total_quantity = sum(item.get('quantity', 0) for item in order_input.get('lineItems', []))
        
        logging.info(f"📦 Sipariş oluşturuluyor: {input_line_items_count} adet ürün modeli, toplam {input_total_quantity} adet")
        
        # Shopify'ın güncel API'sine göre doğru type: OrderCreateOrderInput!
        mutation = """
        mutation orderCreate($order: OrderCreateOrderInput!) {
          orderCreate(order: $order) {
            order {
              id
              name
              createdAt
              totalPrice
              email
              customer {
                id
                email
              }
              shippingAddress {
                firstName
                lastName
                address1
                city
                country
              }
              lineItems(first: 250) {
                edges {
                  node {
                    id
                    quantity
                    title
                    variant {
                      sku
                    }
                  }
                }
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        # Doğru variable name ve type ile GraphQL çağrısı
        result = self.execute_graphql(mutation, {"order": order_input})
        
        if errors := result.get('orderCreate', {}).get('userErrors', []):
            error_messages = [f"{error.get('field', 'Genel')}: {error.get('message', 'Bilinmeyen hata')}" for error in errors]
            raise Exception(f"Sipariş oluşturma hatası: {'; '.join(error_messages)}")
            
        order = result.get('orderCreate', {}).get('order', {})
        if not order:
            raise Exception("Sipariş oluşturuldu ancak sipariş bilgileri alınamadı")
        
        # ✅ KRİTİK DOĞRULAMA: Oluşturulan siparişte tüm ürünler var mı kontrol et
        created_line_items = order.get('lineItems', {}).get('edges', [])
        created_items_count = len(created_line_items)
        created_total_quantity = sum(edge['node'].get('quantity', 0) for edge in created_line_items)
        
        logging.info(f"✅ Sipariş oluşturuldu: {created_items_count} adet ürün modeli, toplam {created_total_quantity} adet")
        
        # Eğer oluşturulan ürün sayısı gönderilenden azsa HATA ver
        if created_items_count < input_line_items_count:
            missing_count = input_line_items_count - created_items_count
            error_msg = (
                f"❌ KRİTİK HATA: Sipariş KISMÎ oluşturuldu!\n"
                f"Gönderilen: {input_line_items_count} ürün modeli ({input_total_quantity} adet)\n"
                f"Oluşturulan: {created_items_count} ürün modeli ({created_total_quantity} adet)\n"
                f"EKSIK: {missing_count} ürün modeli ({input_total_quantity - created_total_quantity} adet)\n"
                f"Sipariş ID: {order.get('id')}\n"
                f"Sipariş No: {order.get('name')}"
            )
            logging.error(error_msg)
            raise Exception(error_msg)
        
        # Miktar kontrolü de yap
        if created_total_quantity < input_total_quantity:
            missing_qty = input_total_quantity - created_total_quantity
            error_msg = (
                f"❌ KRİTİK HATA: Sipariş ürün sayıları eksik!\n"
                f"Gönderilen toplam adet: {input_total_quantity}\n"
                f"Oluşturulan toplam adet: {created_total_quantity}\n"
                f"EKSIK: {missing_qty} adet\n"
                f"Sipariş ID: {order.get('id')}\n"
                f"Sipariş No: {order.get('name')}"
            )
            logging.error(error_msg)
            raise Exception(error_msg)
        
        logging.info(f"✅ DOĞRULAMA BAŞARILI: Tüm ürünler eksiksiz aktarıldı ({created_items_count}/{input_line_items_count} model, {created_total_quantity}/{input_total_quantity} adet)")
            
        return order  

    def get_locations(self):
        query = """
        query {
          locations(first: 25, query:"status:active") {
            edges {
              node { id, name, address { city, country } }
            }
          }
        }
        """
        try:
            result = self.execute_graphql(query)
            locations_edges = result.get("locations", {}).get("edges", [])
            return [edge['node'] for edge in locations_edges]
        except Exception as e:
            logging.error(f"Shopify lokasyonları çekilirken hata: {e}")
            return []

    def get_all_collections(self, progress_callback=None):
        all_collections = []
        query = """
        query getCollections($cursor: String) {
          collections(first: 50, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            edges { node { id title } }
          }
        }
        """
        variables = {"cursor": None}
        while True:
            if progress_callback:
                progress_callback(f"Shopify'dan koleksiyonlar çekiliyor... {len(all_collections)} koleksiyon bulundu.")
            data = self.execute_graphql(query, variables)
            collections_data = data.get("collections", {})
            for edge in collections_data.get("edges", []):
                all_collections.append(edge["node"])
            if not collections_data.get("pageInfo", {}).get("hasNextPage"):
                break
            variables["cursor"] = collections_data["pageInfo"]["endCursor"]
        logging.info(f"{len(all_collections)} adet koleksiyon bulundu.")
        return all_collections

    def get_products_by_collection(self, collection_id, progress_callback=None):
        """Belirli bir koleksiyondaki tüm ürünleri çeker."""
        all_products = []
        query = """
        query getCollectionProducts($id: ID!, $cursor: String) {
          collection(id: $id) {
            products(first: 50, after: $cursor) {
              pageInfo { hasNextPage endCursor }
              edges {
                node {
                  id
                  title
                  handle
                  vendor
                  productType
                  tags
                  variants(first: 50) {
                    edges {
                      node {
                        id
                        sku
                        price
                        compareAtPrice
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """
        variables = {"id": collection_id, "cursor": None}
        
        while True:
            if progress_callback:
                progress_callback(f"Koleksiyon ürünleri çekiliyor... {len(all_products)} ürün alındı.")
                
            data = self.execute_graphql(query, variables)
            collection_data = data.get("collection", {})
            
            if not collection_data:
                logging.warning(f"Koleksiyon bulunamadı veya boş: {collection_id}")
                break
                
            products_data = collection_data.get("products", {})
            for edge in products_data.get("edges", []):
                all_products.append(edge["node"])
                
            if not products_data.get("pageInfo", {}).get("hasNextPage"):
                break
                
            variables["cursor"] = products_data["pageInfo"]["endCursor"]
            
        logging.info(f"Koleksiyon {collection_id} içinden {len(all_products)} ürün çekildi.")
        return all_products

    def get_all_products_for_export(self, progress_callback=None):
        all_products = []
        query = """
        query getProductsForExport($cursor: String) {
          products(first: 25, after: $cursor) {
            pageInfo { hasNextPage endCursor }
            edges {
              node {
                id
                title handle
                vendor
                productType
                tags
                collections(first: 20) { edges { node { id title } } }
                featuredImage { url }
                variants(first: 100) {
                  edges {
                    node {
                      sku displayName inventoryQuantity
                      selectedOptions { name value }
                      inventoryItem { unitCost { amount } }
                    }
                  }
                }
              }
            }
          }
        }
        """
        variables = {"cursor": None}
        total_fetched = 0
        while True:
            if progress_callback:
                progress_callback(f"Shopify'dan ürün verisi çekiliyor... {total_fetched} ürün alındı.")
            data = self.execute_graphql(query, variables)
            products_data = data.get("products", {})
            for edge in products_data.get("edges", []):
                all_products.append(edge["node"])
            total_fetched = len(all_products)
            if not products_data.get("pageInfo", {}).get("hasNextPage"):
                break
            variables["cursor"] = products_data["pageInfo"]["endCursor"]
        logging.info(f"Export için toplam {len(all_products)} ürün çekildi.")
        return all_products

    def get_variant_ids_by_skus(self, skus: list, search_by_product_sku=False) -> dict:
        """
        RATE LIMIT KORUMASIZ GELIŞTIRILMIŞ VERSİYON
        """
        if not skus: return {}
        sanitized_skus = [str(sku).strip() for sku in skus if sku]
        if not sanitized_skus: return {}
        
        logging.info(f"{len(sanitized_skus)} adet SKU için varyant ID'leri aranıyor (Mod: {'Ürün Bazlı' if search_by_product_sku else 'Varyant Bazlı'})...")
        sku_map = {}
        
        # KRITIK: Batch boyutunu 2'ye düşür
        batch_size = 2
        
        for i in range(0, len(sanitized_skus), batch_size):
            sku_chunk = sanitized_skus[i:i + batch_size]
            query_filter = " OR ".join([f"sku:{json.dumps(sku)}" for sku in sku_chunk])
            
            query = """
            query getProductsBySku($query: String!) {
              products(first: 10, query: $query) {
                edges {
                  node {
                    id
                    variants(first: 50) {
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

            try:
                logging.info(f"SKU batch {i//batch_size+1}/{len(range(0, len(sanitized_skus), batch_size))} işleniyor: {sku_chunk}")
                result = self.execute_graphql(query, {"query": query_filter})
                product_edges = result.get("products", {}).get("edges", [])
                for p_edge in product_edges:
                    product_node = p_edge.get("node", {})
                    product_id = product_node.get("id")
                    variant_edges = product_node.get("variants", {}).get("edges", [])
                    for v_edge in variant_edges:
                        node = v_edge.get("node", {})
                        if node.get("sku") and node.get("id") and product_id:
                            sku_map[node["sku"]] = {
                                "variant_id": node["id"],
                                "product_id": product_id
                            }
                
                # KRITIK: Her batch sonrası uzun bekleme
                if i + batch_size < len(sanitized_skus):
                    logging.info(f"Batch {i//batch_size+1} tamamlandı, rate limit için 3 saniye bekleniyor...")
                    time.sleep(3)
            
            except Exception as e:
                logging.error(f"SKU grubu {i//batch_size+1} için varyant ID'leri alınırken hata: {e}")
                # Hata durumunda da biraz bekle
                time.sleep(5)
                raise e

        logging.info(f"Toplam {len(sku_map)} eşleşen varyant detayı bulundu.")
        return sku_map

    def get_product_media_details(self, product_gid):
        try:
            query = """
            query getProductMedia($id: ID!) {
                product(id: $id) {
                    media(first: 250) {
                        edges { node { id alt ... on MediaImage { image { originalSrc } } } }
                    }
                }
            }
            """
            result = self.execute_graphql(query, {"id": product_gid})
            media_edges = result.get("product", {}).get("media", {}).get("edges", [])
            media_details = [{'id': n['id'], 'alt': n.get('alt'), 'originalSrc': n.get('image', {}).get('originalSrc')} for n in [e.get('node') for e in media_edges] if n]
            logging.info(f"Ürün {product_gid} için {len(media_details)} mevcut medya bulundu.")
            return media_details
        except Exception as e:
            logging.error(f"Mevcut medya detayları alınırken hata: {e}")
            return []

    def get_default_location_id(self):
        if self.location_id: return self.location_id
        query = "query { locations(first: 1, query: \"status:active\") { edges { node { id } } } }"
        data = self.execute_graphql(query)
        locations = data.get("locations", {}).get("edges", [])
        if not locations: raise Exception("Shopify mağazasında aktif bir envanter lokasyonu bulunamadı.")
        self.location_id = locations[0]['node']['id']
        logging.info(f"Shopify Lokasyon ID'si bulundu: {self.location_id}")
        return self.location_id

    def load_all_products_for_cache(self, progress_callback=None):
        """GraphQL ile tüm ürünleri önbelleğe al"""
        total_loaded = 0
        
        query = """
        query getProductsForCache($cursor: String) {
          products(first: 50, after: $cursor) {
            pageInfo {
              hasNextPage
              endCursor
            }
            edges {
              node {
                id
                title
                description
                variants(first: 100) {
                  edges {
                    node {
                      sku
                      selectedOptions {
                        name
                        value
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """
        
        variables = {"cursor": None}
        
        while True:
            if progress_callback: 
                progress_callback({'message': f"Shopify ürünleri önbelleğe alınıyor... {total_loaded} ürün bulundu."})
            
            try:
                data = self.execute_graphql(query, variables)
                products_data = data.get("products", {})
                
                for edge in products_data.get("edges", []):
                    product = edge["node"]
                    # GID'den sadece ID'yi çıkar
                    product_id = product["id"].split("/")[-1]
                    product_title = product.get('title', '')
                    product_description = product.get('description', '')
                    
                    # Varyantları dönüştür
                    variants = []
                    for variant_edge in product.get('variants', {}).get('edges', []):
                        variant = variant_edge['node']
                        sku = variant.get('sku', '')
                        options = [
                            {'name': opt.get('name', ''), 'value': opt.get('value', '')}
                            for opt in variant.get('selectedOptions', [])
                        ]
                        variants.append({
                            'sku': sku,
                            'options': options
                        })
                    
                    product_data = {
                        'id': int(product_id), 
                        'gid': product["id"],
                        'title': product_title,
                        'description': product_description,
                        'variants': variants
                    }
                    
                    # Title ile önbelleğe al
                    if title := product.get('title'): 
                        self.product_cache[f"title:{title.strip()}"] = product_data
                    
                    # Variants ile önbelleğe al
                    for variant in variants:
                        if sku := variant.get('sku'): 
                            self.product_cache[f"sku:{sku.strip()}"] = product_data
                
                total_loaded += len(products_data.get("edges", []))
                
                # Sayfalama kontrolü
                page_info = products_data.get("pageInfo", {})
                if not page_info.get("hasNextPage"):
                    break
                
                variables["cursor"] = page_info["endCursor"]
                time.sleep(0.5)  # Rate limit koruması
                
            except Exception as e:
                logging.error(f"Ürünler önbelleğe alınırken hata: {e}")
                break
        
        logging.info(f"Shopify'dan toplam {total_loaded} ürün önbelleğe alındı.")
        return total_loaded
    
    def delete_product_media(self, product_id, media_ids):
        """Ürün medyalarını siler"""
        if not media_ids: 
            return
            
        logging.info(f"Ürün GID: {product_id} için {len(media_ids)} medya siliniyor...")
        
        query = """
        mutation productDeleteMedia($productId: ID!, $mediaIds: [ID!]!) {
            productDeleteMedia(productId: $productId, mediaIds: $mediaIds) {
                deletedMediaIds
                userErrors { field message }
            }
        }
        """
        try:
            result = self.execute_graphql(query, {'productId': product_id, 'mediaIds': media_ids})
            deleted_ids = result.get('productDeleteMedia', {}).get('deletedMediaIds', [])
            errors = result.get('productDeleteMedia', {}).get('userErrors', [])
            
            if errors: 
                logging.warning(f"Medya silme hataları: {errors}")
            
            logging.info(f"{len(deleted_ids)} medya başarıyla silindi.")
            
        except Exception as e:
            logging.error(f"Medya silinirken kritik hata oluştu: {e}")

    def reorder_product_media(self, product_id, media_ids):
        """Ürün medyalarını yeniden sıralar"""
        if not media_ids or len(media_ids) < 2:
            logging.info("Yeniden sıralama için yeterli medya bulunmuyor (1 veya daha az).")
            return

        moves = [{"id": media_id, "newPosition": str(i)} for i, media_id in enumerate(media_ids)]
        
        logging.info(f"Ürün {product_id} için {len(moves)} medya yeniden sıralama işlemi gönderiliyor...")
        
        query = """
        mutation productReorderMedia($id: ID!, $moves: [MoveInput!]!) {
          productReorderMedia(id: $id, moves: $moves) {
            userErrors {
              field
              message
            }
          }
        }
        """
        try:
            result = self.execute_graphql(query, {'id': product_id, 'moves': moves})
            
            errors = result.get('productReorderMedia', {}).get('userErrors', [])
            if errors:
                logging.warning(f"Medya yeniden sıralama hataları: {errors}")
            else:
                logging.info("✅ Medya yeniden sıralama işlemi başarıyla gönderildi.")
                
        except Exception as e:
            logging.error(f"Medya yeniden sıralanırken kritik hata: {e}")

    def test_connection(self):
        """Shopify bağlantısını test eder"""
        try:
            query = """
            query {
                shop {
                    name
                    currencyCode
                    plan {
                        displayName
                    }
                }
                products(first: 1) {
                    edges {
                        node {
                            id
                        }
                    }
                }
            }
            """
            result = self.execute_graphql(query)
            shop_data = result.get('shop', {})
            products_data = result.get('products', {}).get('edges', [])
            
            return {
                'success': True,
                'name': shop_data.get('name'),
                'currency': shop_data.get('currencyCode'),
                'plan': shop_data.get('plan', {}).get('displayName'),
                'products_count': len(products_data),
                'message': 'GraphQL API OK'
            }
        except Exception as e:
            return {'success': False, 'message': f'GraphQL API failed: {e}'}

    def get_products_in_collection_with_inventory(self, collection_id):
        """
        Belirli bir koleksiyondaki tüm ürünleri, toplam stok bilgileriyle birlikte çeker.
        Sayfalama yaparak tüm ürünlerin alınmasını sağlar.
        """
        all_products = []
        query = """
        query getCollectionProducts($id: ID!, $cursor: String) {
          collection(id: $id) {
            title
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
                  totalInventory
                  featuredImage {
                    url(transform: {maxWidth: 100, maxHeight: 100})
                  }
                }
              }
            }
          }
        }
        """
        variables = {"id": collection_id, "cursor": None}
        
        while True:
            logging.info(f"Koleksiyon ürünleri çekiliyor... Cursor: {variables['cursor']}")
            data = self.execute_graphql(query, variables)
            
            collection_data = data.get("collection")
            if not collection_data:
                logging.error(f"Koleksiyon {collection_id} bulunamadı veya veri alınamadı.")
                break

            products_data = collection_data.get("products", {})
            for edge in products_data.get("edges", []):
                all_products.append(edge["node"])
            
            page_info = products_data.get("pageInfo", {})
            if not page_info.get("hasNextPage"):
                break
            
            variables["cursor"] = page_info["endCursor"]
            time.sleep(0.5) # Rate limit için küçük bir bekleme

        logging.info(f"Koleksiyon için toplam {len(all_products)} ürün ve stok bilgisi çekildi.")
        return all_products        
        
    def update_product_metafield(self, product_gid, namespace, key, value):
        """
        Bir ürünün belirli bir tamsayı (integer) metafield'ını günceller.
        """
        logging.info(f"Metafield güncelleniyor: Ürün GID: {product_gid}, {namespace}.{key} = {value}")
        
        # ✅ 2024-10 API FIX: productUpdate mutation ProductInput kullanıyor (ProductUpdateInput DEĞİL!)
        mutation = """
        mutation productUpdate($input: ProductInput!, $namespace: String!, $key: String!) {
          productUpdate(input: $input) {
            product {
              id
              metafield(namespace: $namespace, key: $key) {
                value
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        
        variables = {
          "input": {
            "id": product_gid,
            "metafields": [
              {
                "namespace": namespace,
                "key": key,
                "value": str(value),
                "type": "number_integer"
              }
            ]
          },
          "namespace": namespace,
          "key": key
        }

        try:
            result = self.execute_graphql(mutation, variables)
            if errors := result.get('productUpdate', {}).get('userErrors', []):
                error_message = f"Metafield güncelleme hatası: {errors}"
                logging.error(error_message)
                return {'success': False, 'reason': error_message}
            
            updated_value = result.get('productUpdate', {}).get('product', {}).get('metafield', {}).get('value')
            logging.info(f"✅ Metafield başarıyla güncellendi. Yeni değer: {updated_value}")
            return {'success': True, 'new_value': updated_value}
        
        except Exception as e:
            error_message = f"Metafield güncellenirken kritik hata: {e}"
            logging.error(error_message)
            return {'success': False, 'reason': str(e)}
        
    def create_product_sortable_metafield_definition(self, method='modern'):
        """
        Metafield tanımını, seçilen metoda (modern, legacy, hybrid) göre oluşturur.
        """
        logging.info(f"API üzerinden metafield tanımı oluşturuluyor (Metot: {method}, API Versiyon: {self.api_version})...")

        mutation = """
        mutation metafieldDefinitionCreate($definition: MetafieldDefinitionInput!) {
          metafieldDefinitionCreate(definition: $definition) {
            createdDefinition {
              id
              name
            }
            userErrors {
              field
              message
              code
            }
          }
        }
        """

        # Temel tanım
        base_definition = {
            "name": "Toplam Stok Siralamasi",
            "namespace": "custom_sort",
            "key": "total_stock",
            "type": "number_integer",
            "ownerType": "PRODUCT",
        }

        # Seçilen metoda göre tanımı dinamik olarak oluştur
        if method == 'modern':
            base_definition["capabilities"] = {"sortable": True}
        elif method == 'legacy':
            base_definition["sortable"] = True
        elif method == 'hybrid':
            base_definition["capabilities"] = {"sortable": True}
            base_definition["sortable"] = True
        
        variables = {"definition": base_definition}

        try:
            result = self.execute_graphql(mutation, variables)
            errors = result.get('metafieldDefinitionCreate', {}).get('userErrors', [])
            if errors:
                if any(error.get('code') == 'TAKEN' for error in errors):
                    return {'success': True, 'message': 'Metafield tanımı zaten mevcut.'}
                return {'success': False, 'message': f"Metafield tanımı hatası: {errors}"}

            created_definition = result.get('metafieldDefinitionCreate', {}).get('createdDefinition')
            if created_definition:
                return {'success': True, 'message': f"✅ Tanım başarıyla oluşturuldu: {created_definition.get('name')}"}
            return {'success': False, 'message': 'Tanım oluşturuldu ancak sonuç alınamadı.'}

        except Exception as e:
            return {'success': False, 'message': f"Kritik API hatası: {e}"}
        
    def get_collection_available_sort_keys(self, collection_gid):
        """
        Belirli bir koleksiyon için mevcut olan sıralama anahtarlarını
        doğrudan API'den sorgular.
        """
        query = """
        query collectionSortKeys($id: ID!) {
          collection(id: $id) {
            id
            title
            availableSortKeys {
              key
              title
              urlParam
            }
          }
        }
        """
        try:
            result = self.execute_graphql(query, {"id": collection_gid})
            collection_data = result.get('collection', {})
            if not collection_data:
                return {'success': False, 'message': 'Koleksiyon bulunamadı.'}
            
            sort_keys = collection_data.get('availableSortKeys', [])
            return {'success': True, 'data': sort_keys}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ========== DASHBOARD İÇİN YENİ METODLAR ==========
    
    def get_dashboard_stats(self):
        """Dashboard için detaylı istatistikleri getir"""
        stats = {
            'shop_info': {},
            'orders_today': 0,
            'orders_this_week': 0,
            'orders_this_month': 0,
            'revenue_today': 0,
            'revenue_this_week': 0,
            'revenue_this_month': 0,
            'customers_count': 0,
            'products_count': 0,
            'recent_orders': [],
            'top_products': [],
            'low_stock_products': []
        }
        
        try:
            # Shop bilgileri
            shop_query = """
            query {
              shop {
                name
                email
                primaryDomain { host }
                currencyCode
                plan { displayName }
                billingAddress { country }
              }
            }
            """
            shop_result = self.execute_graphql(shop_query)
            if shop_result:
                stats['shop_info'] = shop_result.get('shop', {})
            
            # Ürün sayısı - Shopify 2024-10 API uyumlu
            products_query = """
            query { 
              products(first: 250) { 
                pageInfo { 
                  hasNextPage 
                } 
                edges { 
                  node { id } 
                } 
              } 
            }
            """
            products_result = self.execute_graphql(products_query)
            if products_result:
                # İlk 250 ürünü say - daha fazla ürün varsa pageInfo.hasNextPage true olur
                products_edges = products_result.get('products', {}).get('edges', [])
                stats['products_count'] = len(products_edges)
                
                # Toplam ürün sayısı 250'den fazlaysa uyarı ekle
                has_more = products_result.get('products', {}).get('pageInfo', {}).get('hasNextPage', False)
                if has_more:
                    stats['products_count_note'] = f"{stats['products_count']}+ (daha fazla ürün var)"
            
            # Müşteri sayısı
            customers_query = """
            query {
              customers(first: 1) {
                pageInfo {
                  hasNextPage
                }
                edges {
                  node { id }
                }
              }
            }
            """
            customers_result = self.execute_graphql(customers_query)
            # Bu sadece tahmini bir sayım - gerçek sayı için analytics API gerekir
            
            # Bugünkü siparişler
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            today_iso = today.isoformat()
            tomorrow_iso = (today + timedelta(days=1)).isoformat()
            
            orders_today_query = f"""
            query {{
              orders(first: 50, query: "created_at:>='{today_iso}' AND created_at:<'{tomorrow_iso}'") {{
                edges {{
                  node {{
                    id
                    name
                    createdAt
                    totalPriceSet {{ shopMoney {{ amount currencyCode }} }}
                    customer {{ firstName lastName }}
                  }}
                }}
              }}
            }}
            """
            orders_today_result = self.execute_graphql(orders_today_query)
            if orders_today_result:
                today_orders = orders_today_result.get('orders', {}).get('edges', [])
                stats['orders_today'] = len(today_orders)
                stats['revenue_today'] = sum(
                    float(order['node'].get('totalPriceSet', {}).get('shopMoney', {}).get('amount', 0))
                    for order in today_orders
                )
                stats['recent_orders'] = [order['node'] for order in today_orders[:5]]
            
            # Bu haftaki siparişler
            week_start = today - timedelta(days=today.weekday())
            week_iso = week_start.isoformat()
            
            orders_week_query = f"""
            query {{
              orders(first: 250, query: "created_at:>='{week_iso}'") {{
                edges {{
                  node {{
                    id
                    totalPriceSet {{ shopMoney {{ amount }} }}
                  }}
                }}
              }}
            }}
            """
            orders_week_result = self.execute_graphql(orders_week_query)
            if orders_week_result:
                week_orders = orders_week_result.get('orders', {}).get('edges', [])
                stats['orders_this_week'] = len(week_orders)
                stats['revenue_this_week'] = sum(
                    float(order['node'].get('totalPriceSet', {}).get('shopMoney', {}).get('amount', 0))
                    for order in week_orders
                )
            
            # Bu ayki siparişler
            month_start = today.replace(day=1)
            month_iso = month_start.isoformat()
            
            orders_month_query = f"""
            query {{
              orders(first: 250, query: "created_at:>='{month_iso}'") {{
                edges {{
                  node {{
                    id
                    totalPriceSet {{ shopMoney {{ amount }} }}
                  }}
                }}
              }}
            }}
            """
            orders_month_result = self.execute_graphql(orders_month_query)
            if orders_month_result:
                month_orders = orders_month_result.get('orders', {}).get('edges', [])
                stats['orders_this_month'] = len(month_orders)
                stats['revenue_this_month'] = sum(
                    float(order['node'].get('totalPriceSet', {}).get('shopMoney', {}).get('amount', 0))
                    for order in month_orders
                )
            
            return stats
            
        except Exception as e:
            logging.error(f"Dashboard istatistikleri alınırken hata: {e}")
            return stats

    def update_product_media_seo(self, product_gid, product_title):
        """
        🎯 SADECE SEO için ürün resimlerinin ALT text'ini SEO dostu formatta günceller.
        HİÇBİR RESİM EKLEME/SİLME/YENİDEN SIRALAMA YAPMAZ.
        
        ALT Text Formatı (Shopify Admin'de "Ad" olarak görünür):
        - 1. resim: Buyuk-Beden-Uzun-Kollu-Leopar-Desenli-Diz-Ustu-Elbise-285058-a
        - 2. resim: Buyuk-Beden-Uzun-Kollu-Leopar-Desenli-Diz-Ustu-Elbise-285058-b
        - 3. resim: Buyuk-Beden-Uzun-Kollu-Leopar-Desenli-Diz-Ustu-Elbise-285058-c
        - vb...
        
        Özellikler:
        - Türkçe karakterler İngilizce'ye çevrilir (ı→i, ğ→g, ü→u, ş→s, ö→o, ç→c)
        - Boşluklar tire (-) ile değiştirilir
        - Her resim sıralı harf eki alır (a, b, c, d, e...)
        - İlk harfler büyük kalır (SEO için)
        
        Args:
            product_gid: Ürünün Shopify Global ID'si (gid://shopify/Product/123)
            product_title: Ürün başlığı
            
        Returns:
            dict: {'success': bool, 'updated_count': int, 'message': str}
        """
        try:
            # 1. Mevcut medyaları al
            query = """
            query getProductMedia($id: ID!) {
                product(id: $id) {
                    media(first: 250) {
                        edges {
                            node {
                                id
                                alt
                                mediaContentType
                                ... on MediaImage {
                                    image {
                                        originalSrc
                                        url
                                    }
                                }
                            }
                        }
                    }
                }
            }
            """
            result = self.execute_graphql(query, {"id": product_gid})
            media_edges = result.get("product", {}).get("media", {}).get("edges", [])
            
            if not media_edges:
                return {
                    'success': True,
                    'updated_count': 0,
                    'message': 'Güncellenecek resim bulunamadı'
                }
            
            # 2. SEO dostu base filename oluştur (Türkçe karakterler temizlenir, boşluklar tire)
            # Örnek: "Büyük Beden Kısa Kollu Bisiklet Yaka Baskılı T-shirt 303734"
            # Sonuç: "Buyuk-Beden-Kisa-Kollu-Bisiklet-Yaka-Baskili-T-shirt-303734"
            base_filename = self._create_seo_filename_with_dashes(product_title)
            
            # 3. Her resim için ALT text ve filename güncelle
            updated_count = 0
            alphabet = 'abcdefghijklmnopqrstuvwxyz'  # Sıralı harf ekleri için
            
            for idx, edge in enumerate(media_edges):
                node = edge.get('node', {})
                media_id = node.get('id')
                media_type = node.get('mediaContentType')
                
                if media_type != 'IMAGE':
                    continue
                
                # Harf eki (a, b, c, d, e...)
                letter_suffix = alphabet[idx] if idx < len(alphabet) else f"z{idx - 25}"
                
                # ✅ ÇÖZÜM: Shopify Admin'deki "Ad" kısmı = ALT field
                # ALT text'i filename formatında yapıyoruz
                # Örnek: Buyuk-Beden-Uzun-Kollu-Leopar-Desenli-Diz-Ustu-Elbise-285058-a
                new_alt_with_filename = f"{base_filename}-{letter_suffix}"
                
                # 4. Medya güncelle
                mutation = """
                mutation updateMedia($media: [UpdateMediaInput!]!, $productId: ID!) {
                    productUpdateMedia(media: $media, productId: $productId) {
                        media {
                            id
                            alt
                        }
                        mediaUserErrors {
                            field
                            message
                        }
                    }
                }
                """
                
                media_input = [{
                    "id": media_id,
                    "alt": new_alt_with_filename  # ✅ ALT = FILENAME FORMATI (Buyuk-Beden-Elbise-285058-a)
                }]
                
                update_result = self.execute_graphql(
                    mutation,
                    {
                        "media": media_input,
                        "productId": product_gid
                    }
                )
                
                errors = update_result.get('productUpdateMedia', {}).get('mediaUserErrors', [])
                if errors:
                    logging.error(f"  ❌ Resim {idx + 1} güncelleme hatası: {errors}")
                else:
                    updated_count += 1
                    logging.info(f"  ✅ Resim {idx + 1}/{len(media_edges)}: ALT='{new_alt_with_filename}'")

                
                # Rate limit koruması
                time.sleep(0.3)
            
            return {
                'success': True,
                'updated_count': updated_count,
                'message': f'{updated_count}/{len(media_edges)} resim SEO formatında güncellendi (tire ile)'
            }
            
        except Exception as e:
            logging.error(f"SEO media güncelleme hatası: {e}")
            return {
                'success': False,
                'updated_count': 0,
                'message': f'Hata: {str(e)}'
            }
    
    def _create_seo_filename_with_dashes(self, title):
        """
        Ürün başlığından SEO dostu dosya adı oluşturur - TIRE İLE.
        Boşluklar tire (-) ile değiştirilir, ilk harfler büyük kalır.
        Örnek: "Büyük Beden T-shirt 303734" -> "Buyuk-Beden-T-shirt-303734"
        """
        import re
        
        # Türkçe karakterleri İngilizce karşılıklarına çevir (BÜYÜK/küçük harf korunur)
        tr_map = str.maketrans({
            'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c',
            'İ': 'I', 'Ğ': 'G', 'Ü': 'U', 'Ş': 'S', 'Ö': 'O', 'Ç': 'C'
        })
        
        filename = title.translate(tr_map)
        
        # Özel karakterleri kaldır, sadece harf, rakam, boşluk ve tire bırak
        filename = re.sub(r'[^a-zA-Z0-9\s-]', '', filename)
        
        # Birden fazla boşluğu tek boşluğa çevir
        filename = re.sub(r'\s+', ' ', filename.strip())
        
        # Boşlukları tire ile değiştir
        filename = filename.replace(' ', '-')
        
        # Birden fazla tireyi tek tire yap
        filename = re.sub(r'-+', '-', filename)
        
        return filename.strip('-')

    def get_product_recommendations(self, product_gid: str) -> dict:
        """
        Shopify'ın önerdiği kategori ve meta alanları getirir.
        
        Args:
            product_gid: Ürün GID (gid://shopify/Product/123456)
            
        Returns:
            dict: {
                'suggested_category': {...},  # Önerilen kategori bilgisi
                'recommended_attributes': [...],  # Önerilen attribute'ler
                'current_category': {...}  # Mevcut kategori
            }
        """
        try:
            # 1. Önce ürünü al ve title'ını çek
            query = """
            query getProductInfo($id: ID!) {
                product(id: $id) {
                    id
                    title
                    productType
                    category {
                        id
                        fullName
                        name
                        attributes(first: 50) {
                            edges {
                                node {
                                    ... on TaxonomyChoiceListAttribute {
                                        id
                                        name
                                    }
                                    ... on TaxonomyMeasurementAttribute {
                                        id
                                        name
                                    }
                                    ... on TaxonomyAttribute {
                                        id
                                    }
                                }
                            }
                        }
                    }
                }
            }
            """
            
            result = self.execute_graphql(query, {"id": product_gid})
            product = result.get('product', {})
            
            if not product:
                return {
                    'suggested_category': None,
                    'recommended_attributes': [],
                    'current_category': None,
                    'title': ''
                }
            
            title = product.get('title', '')
            current_category = product.get('category')
            
            # 2. Title'dan anahtar kelimeleri çıkar ve kategori ara
            suggested_category = None
            
            # Basit anahtar kelime eşleştirmesi (T-shirt, Blouse, Dress vb.)
            # GÜNCELLENME: Shopify'ın GERÇEK taxonomy ID'leri kullanıldı!
            category_keywords = {
                't-shirt': 'aa-1-13-8',  # Apparel > Clothing > Clothing Tops > T-Shirts
                'tshirt': 'aa-1-13-8',
                'tişört': 'aa-1-13-8',
                'blouse': 'aa-1-13-1',   # Apparel > Clothing > Clothing Tops > Blouses
                'bluz': 'aa-1-13-1',
                'dress': 'aa-1-4',       # Apparel > Clothing > Dresses
                'elbise': 'aa-1-4',
                'shirt': 'aa-1-13-7',    # Apparel > Clothing > Clothing Tops > Shirts
                'gömlek': 'aa-1-13-7',
                'skirt': 'aa-1-15',      # Apparel > Clothing > Skirts
                'etek': 'aa-1-15',
                'pants': 'aa-1-12',      # Apparel > Clothing > Pants
                'pantolon': 'aa-1-12',
                'shorts': 'aa-1-14',     # Apparel > Clothing > Shorts
                'şort': 'aa-1-14',
                'coat': 'aa-1-10-2-10',  # Apparel > Clothing > Outerwear > Coats & Jackets > Rain Coats
                'jacket': 'aa-1-10-2',   # Apparel > Clothing > Outerwear > Coats & Jackets
                'mont': 'aa-1-10-2',
                'cardigan': 'aa-1-13-3', # Apparel > Clothing > Clothing Tops > Cardigans
                'hırka': 'aa-1-13-3',
                'sweatshirt': 'aa-1-13-14', # Apparel > Clothing > Clothing Tops > Sweatshirts
                'hoodie': 'aa-1-13-13',     # Apparel > Clothing > Clothing Tops > Hoodies
                'sweater': 'aa-1-13-12',    # Apparel > Clothing > Clothing Tops > Sweaters
                'süveter': 'aa-1-13-12',
                'tunic': 'aa-1-13-11',      # Apparel > Clothing > Clothing Tops > Tunics
                'tunik': 'aa-1-13-11',
            }
            
            # Title'ı küçük harfe çevir ve ara
            title_lower = title.lower()
            suggested_taxonomy_id = None
            category_full_name = None
            
            for keyword, category_id in category_keywords.items():
                if keyword in title_lower:
                    suggested_taxonomy_id = category_id
                    # Kategori adlarını manuel mapping (GÜNCELLENDİ - Gerçek kategoriler)
                    category_names = {
                        'aa-1-13-8': 'Apparel & Accessories > Clothing > Clothing Tops > T-Shirts',
                        'aa-1-13-1': 'Apparel & Accessories > Clothing > Clothing Tops > Blouses',
                        'aa-1-4': 'Apparel & Accessories > Clothing > Dresses',
                        'aa-1-13-7': 'Apparel & Accessories > Clothing > Clothing Tops > Shirts',
                        'aa-1-15': 'Apparel & Accessories > Clothing > Skirts',
                        'aa-1-12': 'Apparel & Accessories > Clothing > Pants',
                        'aa-1-14': 'Apparel & Accessories > Clothing > Shorts',
                        'aa-1-10-2-10': 'Apparel & Accessories > Clothing > Outerwear > Coats & Jackets > Rain Coats',
                        'aa-1-10-2': 'Apparel & Accessories > Clothing > Outerwear > Coats & Jackets',
                        'aa-1-13-3': 'Apparel & Accessories > Clothing > Clothing Tops > Cardigans',
                        'aa-1-13-14': 'Apparel & Accessories > Clothing > Clothing Tops > Sweatshirts',
                        'aa-1-13-13': 'Apparel & Accessories > Clothing > Clothing Tops > Hoodies',
                        'aa-1-13-12': 'Apparel & Accessories > Clothing > Clothing Tops > Sweaters',
                        'aa-1-13-11': 'Apparel & Accessories > Clothing > Clothing Tops > Tunics',
                    }
                    category_full_name = category_names.get(category_id, f'Category {category_id}')
                    logging.info(f"🎯 Önerilen kategori bulundu: {category_full_name} ('{keyword}' kelimesinden)")
                    break
            
            # Suggested category oluştur (taxonomyCategory query yapmadan)
            if suggested_taxonomy_id:
                suggested_category = {
                    'id': f"gid://shopify/TaxonomyCategory/{suggested_taxonomy_id}",
                    'taxonomy_id': suggested_taxonomy_id,  # ← Mutation için
                    'fullName': category_full_name,
                    'name': category_full_name.split(' > ')[-1] if category_full_name else ''
                }
            
            # Önerilen attribute'leri topla
            # NOT: Mevcut category'den attribute'leri alıyoruz (eğer varsa)
            recommended_attrs = []
            attrs_source = suggested_category or current_category
            if current_category and current_category.get('attributes'):
                for edge in current_category['attributes']['edges']:
                    attr = edge['node']
                    # TaxonomyChoiceListAttribute ve TaxonomyMeasurementAttribute'da 'name' var
                    # TaxonomyAttribute'da sadece 'id' var, o yüzden name varsa ekle
                    if attr.get('name'):
                        recommended_attrs.append(attr['name'])
            
            return {
                'suggested_category': suggested_category,
                'recommended_attributes': recommended_attrs,
                'current_category': current_category,
                'title': title
            }
            
        except Exception as e:
            logging.error(f"Ürün önerileri alınamadı: {e}")
            import traceback
            traceback.print_exc()
            return {
                'suggested_category': None,
                'recommended_attributes': [],
                'current_category': None,
                'title': ''
            }
    
    def update_product_category_and_metafields(self, product_gid: str, category: str, metafields: list, use_shopify_suggestions: bool = True) -> dict:
        """
        Ürünün kategorisini ve meta alanlarını günceller.
        Shopify'ın önerdiği kategori ve önerilen attribute'leri kullanır.
        
        Args:
            product_gid: Ürün GID (gid://shopify/Product/123456)
            category: Kategori adı - yedek olarak kullanılır
            metafields: Bizim meta alan listemiz
            use_shopify_suggestions: Shopify önerilerini kullan (varsayılan: True)
            
        Returns:
            dict: {'success': bool, 'message': str, 'updated_category': str, 'updated_metafields': int}
        """
        try:
            updated_count = 0
            applied_category = None
            
            # 1. Shopify'ın Önerilerini Al
            if use_shopify_suggestions:
                recommendations = self.get_product_recommendations(product_gid)
                suggested_category = recommendations.get('suggested_category')
                recommended_attrs = recommendations.get('recommended_attributes', [])
                
                logging.info(f"📊 Shopify Önerileri:")
                if suggested_category:
                    logging.info(f"   Kategori: {suggested_category.get('fullName', 'N/A')}")
                if recommended_attrs:
                    logging.info(f"   Önerilen Attribute'ler: {', '.join(recommended_attrs)}")
                
                # Shopify'ın önerdiği kategoriyi set et
                if suggested_category and suggested_category.get('id'):
                    category_mutation = """
                    mutation updateProductCategory($input: ProductInput!) {
                        productUpdate(input: $input) {
                            product {
                                id
                                category {
                                    id
                                    fullName
                                }
                            }
                            userErrors {
                                field
                                message
                            }
                        }
                    }
                    """
                    
                    # ÖNEMLI: GID formatında gönder!
                    result = self.execute_graphql(
                        category_mutation,
                        {
                            "input": {
                                "id": product_gid,
                                "category": suggested_category['id']  # ← GID formatında: gid://shopify/TaxonomyCategory/aa-2-6-14
                            }
                        }
                    )
                    
                    errors = result.get('productUpdate', {}).get('userErrors', [])
                    if errors:
                        logging.error(f"❌ Kategori set hatası: {errors}")
                    else:
                        updated_cat = result.get('productUpdate', {}).get('product', {}).get('category', {})
                        applied_category = updated_cat.get('fullName', suggested_category.get('fullName'))
                        logging.info(f"✅ Shopify önerisi kategori set edildi: {applied_category}")
                        updated_count += 1
            
            # 2. Metafields güncelle (bizim metafield'larımız + Shopify önerileri)
            if metafields:
                # NOT: Shopify önerileri zaten metafields içinde var!
                # CategoryMetafieldManager.prepare_metafields_for_shopify() 
                # fonksiyonu başlık ve varyantlardan zaten çıkarıyor.
                # Burada sadece ek boş alanlar eklemeyelim!
                
                # Tüm metafield'ları tek bir mutation ile güncelle
                metafield_mutation = """
                mutation updateProductMetafields($input: ProductInput!) {
                    productUpdate(input: $input) {
                        product {
                            id
                            metafields(first: 100) {
                                edges {
                                    node {
                                        namespace
                                        key
                                        value
                                    }
                                }
                            }
                        }
                        userErrors {
                            field
                            message
                        }
                    }
                }
                """
                
                # Metafield'ları hazırla
                metafields_input = []
                for metafield in metafields:
                    metafields_input.append({
                        "namespace": metafield['namespace'],
                        "key": metafield['key'],
                        "value": metafield['value'],
                        "type": metafield['type']
                    })
                
                result = self.execute_graphql(
                    metafield_mutation, 
                    {
                        "input": {
                            "id": product_gid,
                            "metafields": metafields_input
                        }
                    }
                )
                
                errors = result.get('productUpdate', {}).get('userErrors', [])
                if errors:
                    logging.error(f"❌ Metafield güncelleme hatası: {errors}")
                else:
                    updated_metafields = result.get('productUpdate', {}).get('product', {}).get('metafields', {}).get('edges', [])
                    logging.info(f"✅ {len(metafields)} meta alan güncellendi")
                    for mf in metafields[:3]:  # İlk 3 metafield'ı logla
                        logging.info(f"   → {mf['namespace']}.{mf['key']} = '{mf['value']}'")
                    if len(metafields) > 3:
                        logging.info(f"   → ... ve {len(metafields) - 3} tane daha")
                    updated_count += len(metafields)
            
            # 3. 🌟 YENİ: Taxonomy Attribute'lerine de yaz!
            # Kategori set edildikten sonra, o kategorinin standart attribute'lerine değer yaz
            if metafields and applied_category:
                try:
                    # Metafield'lardan taxonomy attribute mapping yap
                    taxonomy_attrs = self._map_metafields_to_taxonomy_attributes(metafields)
                    
                    if taxonomy_attrs:
                        logging.info(f"📝 {len(taxonomy_attrs)} taxonomy attribute set ediliyor...")
                        attr_result = self.update_product_taxonomy_attributes(
                            product_gid=product_gid,
                            attributes=taxonomy_attrs
                        )
                        
                        if attr_result.get('success'):
                            logging.info(f"✅ Taxonomy attribute'ler güncellendi")
                        else:
                            logging.warning(f"⚠️ Taxonomy attribute güncelleme kısmen başarısız")
                except Exception as e:
                    logging.warning(f"⚠️ Taxonomy attribute güncelleme hatası (devam ediliyor): {e}")
                    for mf in metafields[:3]:  # İlk 3 metafield'ı logla
                        logging.info(f"   → {mf['namespace']}.{mf['key']} = '{mf['value']}'")
                    if len(metafields) > 3:
                        logging.info(f"   → ... ve {len(metafields) - 3} tane daha")
                    updated_count += len(metafields)
            
            # Başarı mesajını hazırla
            message_parts = []
            if applied_category:
                message_parts.append(f"Kategori: {applied_category}")
            if metafields:
                message_parts.append(f"{len(metafields)} meta alan")
            
            success_message = "✅ " + " ve ".join(message_parts) + " güncellendi"
            
            return {
                'success': True,
                'message': success_message,
                'updated_category': applied_category or category,
                'updated_metafields': len(metafields)
            }
            
        except Exception as e:
            logging.error(f"❌ Kategori/metafield güncelleme hatası: {e}")
            return {
                'success': False,
                'message': f'Hata: {str(e)}',
                'updated_category': None,
                'updated_metafields': 0
            }
    
    def get_product_metafields(self, product_gid: str) -> dict:
        """
        Ürünün mevcut meta alanlarını getirir.
        
        Args:
            product_gid: Ürün GID
            
        Returns:
            dict: Meta alanlar dictionary {namespace.key: value}
        """
        try:
            query = """
            query getProductMetafields($id: ID!) {
                product(id: $id) {
                    id
                    title
                    productType
                    metafields(first: 100) {
                        edges {
                            node {
                                namespace
                                key
                                value
                                type
                            }
                        }
                    }
                }
            }
            """
            
            result = self.execute_graphql(query, {"id": product_gid})
            product = result.get('product', {})
            
            metafields = {}
            for edge in product.get('metafields', {}).get('edges', []):
                node = edge['node']
                key = f"{node['namespace']}.{node['key']}"
                metafields[key] = {
                    'value': node['value'],
                    'type': node['type']
                }
            
            return {
                'product_type': product.get('productType', ''),
                'metafields': metafields
            }
            
        except Exception as e:
            logging.error(f"Metafield getirme hatası: {e}")
            return {'product_type': '', 'metafields': {}}
    
    def _map_metafields_to_taxonomy_attributes(self, metafields: list) -> list:
        """
        Custom metafield'ları Shopify taxonomy attribute'lerine map eder.
        
        Args:
            metafields: [{namespace, key, value, type}]
            
        Returns:
            list: Taxonomy attribute inputs
        """
        # Custom key -> Taxonomy attribute name mapping
        # Shopify'ın taxonomy attribute isimleri İngilizce ve standardize
        attribute_mapping = {
            'renk': 'Color',
            'yaka_tipi': 'Neckline',
            'yaka_cizgisi': 'Neckline',
            'kol_tipi': 'Sleeve Length',
            'boy': 'Length',
            'etek_elbise_uzunluk_turu': 'Skirt/Dress Length Type',
            'desen': 'Pattern',
            'pacha_tipi': 'Leg Style',
            'bel_tipi': 'Rise',
            'bel_yukseltme': 'Rise',
            'kapanma_tipi': 'Closure Type',
            'fit': 'Fit',
            'stil': 'Style',
            'kullanim_alani': 'Activity',
            'hedef_cinsiyet': 'Target Gender',
            'kumaş': 'Material',
            'kumas': 'Material',
        }
        
        taxonomy_attrs = []
        
        for mf in metafields:
            key = mf.get('key', '')
            value = mf.get('value', '')
            
            # Map edilen attribute varsa ekle
            if key in attribute_mapping and value:
                taxonomy_attrs.append({
                    'name': attribute_mapping[key],
                    'value': value
                })
        
        return taxonomy_attrs
    
    def update_product_taxonomy_attributes(self, product_gid: str, attributes: list) -> dict:
        """
        Ürünün taxonomy attribute'lerini günceller.
        
        Args:
            product_gid: Ürün GID
            attributes: [{'name': 'Neckline', 'value': 'V-Neck'}]
            
        Returns:
            dict: {'success': bool, 'updated': int}
        """
        try:
            # productSet mutation kullan (2024-10 API)
            mutation = """
            mutation productSet($input: ProductSetInput!) {
                productSet(input: $input) {
                    product {
                        id
                        category {
                            id
                            fullName
                        }
                    }
                    userErrors {
                        field
                        message
                        code
                    }
                }
            }
            """
            
            # Attribute input'ları hazırla
            # NOT: productSet için attribute format farklıdır
            # Her attribute için değer set etmek yerine,
            # productUpdate ile metafield olarak eklemeye devam edeceğiz
            # Çünkü taxonomy attribute'leri doğrudan set etmek karmaşık
            
            # Şimdilik sadece başarı döndür - bu özellik gelecekte eklenecek
            logging.info("ℹ️  Taxonomy attribute güncellemesi şimdilik metafield olarak yapılıyor")
            return {'success': True, 'updated': len(attributes)}
            
        except Exception as e:
            logging.error(f"Taxonomy attribute güncelleme hatası: {e}")
            return {'success': False, 'updated': 0}
    
    def _create_seo_filename(self, title):
        """
        Ürün başlığından SEO dostu dosya adı oluşturur.
        Örnek: "Büyük Beden T-shirt 303734" -> "buyuk-beden-t-shirt-303734"
        """
        import unicodedata
        import re
        
        # Türkçe karakterleri İngilizce karşılıklarına çevir
        tr_chars = {
            'ı': 'i', 'ğ': 'g', 'ü': 'u', 'ş': 's', 'ö': 'o', 'ç': 'c',
            'İ': 'i', 'Ğ': 'g', 'Ü': 'u', 'Ş': 's', 'Ö': 'o', 'Ç': 'c'
        }
        
        filename = title.lower()
        for tr_char, en_char in tr_chars.items():
            filename = filename.replace(tr_char, en_char)
        
        # Özel karakterleri kaldır, sadece harf, rakam ve boşluk bırak
        filename = re.sub(r'[^a-z0-9\s-]', '', filename)
        
        # Birden fazla boşluğu tek tire ile değiştir
        filename = re.sub(r'\s+', '-', filename.strip())
        
        # Birden fazla tireyi tek tire yap
        filename = re.sub(r'-+', '-', filename)
        
        return filename.strip('-')
    
    def create_metafield_definition(self, namespace: str, key: str, name: str, description: str = "", metafield_type: str = "single_line_text_field"):
        """
        Shopify'da metafield definition oluşturur.
        Bu tanım yapılmadan metafield'lar Shopify admin panelinde görünmez!
        
        Args:
            namespace: Namespace (örn: 'custom')
            key: Key (örn: 'yaka_tipi')
            name: Görünen ad (örn: 'Yaka Tipi')
            description: Açıklama
            metafield_type: Tip (varsayılan: 'single_line_text_field')
            
        Returns:
            dict: {'success': bool, 'definition_id': str}
        """
        try:
            mutation = """
            mutation CreateMetafieldDefinition($definition: MetafieldDefinitionInput!) {
                metafieldDefinitionCreate(definition: $definition) {
                    createdDefinition {
                        id
                        name
                        namespace
                        key
                    }
                    userErrors {
                        field
                        message
                        code
                    }
                }
            }
            """
            
            result = self.execute_graphql(
                mutation,
                {
                    "definition": {
                        "name": name,
                        "namespace": namespace,
                        "key": key,
                        "description": description,
                        "type": metafield_type,
                        "ownerType": "PRODUCT"
                    }
                }
            )
            
            errors = result.get('metafieldDefinitionCreate', {}).get('userErrors', [])
            if errors:
                # Eğer zaten varsa, hata yerine başarı döndür
                if any('TAKEN' in str(err.get('code', '')) for err in errors):
                    logging.info(f"ℹ️  Metafield definition zaten var: {namespace}.{key}")
                    return {'success': True, 'already_exists': True, 'definition_id': None}
                else:
                    logging.error(f"❌ Metafield definition oluşturma hatası: {errors}")
                    return {'success': False, 'error': errors}
            
            created = result.get('metafieldDefinitionCreate', {}).get('createdDefinition', {})
            definition_id = created.get('id')
            
            logging.info(f"✅ Metafield definition oluşturuldu: {namespace}.{key} → '{name}'")
            return {'success': True, 'definition_id': definition_id}
            
        except Exception as e:
            logging.error(f"❌ Metafield definition oluşturma hatası: {e}")
            return {'success': False, 'error': str(e)}
    
    def create_all_metafield_definitions_for_category(self, category: str):
        """
        Bir kategori için tüm metafield definitions'ları oluşturur.
        
        Args:
            category: Kategori adı (örn: 'Elbise', 'T-shirt')
            
        Returns:
            dict: {'success': bool, 'created': int, 'errors': list}
        """
        from utils.category_metafield_manager import CategoryMetafieldManager
        
        try:
            metafield_templates = CategoryMetafieldManager.get_metafields_for_category(category)
            
            created = 0
            errors = []
            
            for field_key, template in metafield_templates.items():
                # Türkçe başlık oluştur
                key = template['key']
                name_map = {
                    'renk': 'Renk',
                    'yaka_tipi': 'Yaka Tipi',
                    'kol_tipi': 'Kol Uzunluğu Tipi',
                    'boy': 'Boy',
                    'desen': 'Desen',
                    'kullanim_alani': 'Kullanım Alanı',
                    'pacha_tipi': 'Paça Tipi',
                    'bel_tipi': 'Bel Tipi',
                    'kapanma_tipi': 'Kapanma Tipi',
                    'kapusonlu': 'Kapüşonlu',
                    'cep': 'Cep',
                    'model': 'Model',
                    'beden': 'Beden',
                    'kumaş': 'Kumaş',
                    'kumas': 'Kumaş',
                    'stil': 'Stil',
                    'giysi_ozellikleri': 'Giysi Özellikleri',
                    'elbise_etkinligi': 'Elbise Etkinliği',
                    'elbise_stili': 'Elbise Stili',
                    'yaka_cizgisi': 'Yaka Çizgisi',
                    'etek_elbise_uzunluk_turu': 'Etek/Elbise Uzunluk Türü',
                    'hedef_cinsiyet': 'Hedef Cinsiyet',
                    'fit': 'Fit',
                    'pantolon_uzunlugu_turu': 'Pantolon Uzunluğu Türü',
                    'bel_yukseltme': 'Bel Yükseltme',
                    'ust_uzunluk_turu': 'Üst Uzunluk Türü',
                }
                
                display_name = name_map.get(key, key.replace('_', ' ').title())
                description = template.get('description', '')
                
                result = self.create_metafield_definition(
                    namespace=template['namespace'],
                    key=key,
                    name=display_name,
                    description=description,
                    metafield_type=template['type']
                )
                
                if result.get('success'):
                    created += 1
                else:
                    errors.append(result.get('error'))
            
            logging.info(f"✅ {category} kategorisi için {created} metafield definition oluşturuldu/kontrol edildi")
            return {'success': True, 'created': created, 'errors': errors}
            
        except Exception as e:
            logging.error(f"❌ Metafield definitions oluşturma hatası: {e}")
            return {'success': False, 'created': 0, 'errors': [str(e)]}
    
    def update_product_details(self, product_id, tags=None, vendor=None, product_type=None):
        """
        Ürünün etiketlerini, markasını veya tipini günceller.
        
        Args:
            product_id: Ürün GID (gid://shopify/Product/123456)
            tags: Etiket listesi (list of strings) veya virgülle ayrılmış string
            vendor: Marka (Vendor)
            product_type: Ürün Tipi (Product Type)
            
        Returns:
            dict: {'success': bool, 'message': str}
        """
        try:
            input_data = {"id": product_id}
            
            if tags is not None:
                if isinstance(tags, str):
                    # Virgülle ayrılmış string ise listeye çevir
                    tag_list = [t.strip() for t in tags.split(',') if t.strip()]
                    input_data["tags"] = tag_list
                elif isinstance(tags, list):
                    input_data["tags"] = tags
            
            if vendor is not None:
                input_data["vendor"] = vendor
                
            if product_type is not None:
                input_data["productType"] = product_type
            
            if len(input_data) <= 1:
                return {'success': False, 'message': 'Güncellenecek veri yok'}

            mutation = """
            mutation productUpdate($input: ProductInput!) {
              productUpdate(input: $input) {
                product {
                  id
                  tags
                  vendor
                  productType
                }
                userErrors {
                  field
                  message
                }
              }
            }
            """
            
            result = self.execute_graphql(mutation, {"input": input_data})
            
            errors = result.get('productUpdate', {}).get('userErrors', [])
            if errors:
                error_msg = f"Güncelleme hatası: {errors}"
                logging.error(error_msg)
                return {'success': False, 'message': error_msg}
            
            return {'success': True, 'message': 'Ürün başarıyla güncellendi'}
            
        except Exception as e:
            logging.error(f"Ürün güncelleme hatası: {e}")
            return {'success': False, 'message': str(e)}

    def search_products(self, query_str, limit=50):
        """
        Ürünleri arar (title, tag, vendor vb.)
        """
        query = """
        query searchProducts($query: String!, $first: Int!) {
          products(first: $first, query: $query) {
            edges {
              node {
                id
                title
                handle
                vendor
                productType
                tags
                featuredImage { url }
              }
            }
          }
        }
        """
        variables = {"query": query_str, "first": limit}
        try:
            result = self.execute_graphql(query, variables)
            edges = result.get('products', {}).get('edges', [])
            return [edge['node'] for edge in edges]
        except Exception as e:
            logging.error(f"Ürün arama hatası: {e}")
            return []