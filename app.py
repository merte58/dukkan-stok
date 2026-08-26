import streamlit as st
import pandas as pd
import sqlite3
import requests

# Sayfa Yapılandırması
st.set_page_config(page_title="Fiyat & Kur Takip Sistemi", layout="wide")

# Veritabanı Başlatma ve Tablo Güncelleme
conn = sqlite3.connect("stok_fiyat.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
    CREATE TABLE IF NOT EXISTS urunler (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        barkod TEXT,
        urun_adi TEXT,
        raf_konumu TEXT,
        liste_fiyati REAL,
        para_birimi TEXT,
        tedarikci_iskonto REAL,
        kar_marji REAL,
        kdv_orani REAL,
        stok_adedi INTEGER
    )
""")
conn.commit()

# Eski veritabanı varsa raf_konumu sütununu otomatik ekleme kontrolü
try:
    cursor.execute("ALTER TABLE urunler ADD COLUMN raf_konumu TEXT DEFAULT ''")
    conn.commit()
except sqlite3.OperationalError:
    pass

# TCMB / Serbest Piyasa Anlık Kur Çekme Fonksiyonu
@st.cache_data(ttl=300)  # 5 dakikada bir kuru yeniler
def get_live_rates():
    try:
        res = requests.get("https://open.er-api.com/v6/latest/TRY", timeout=5).json()
        rates = res.get("rates", {})
        usd = 1 / rates.get("USD", 1)
        eur = 1 / rates.get("EUR", 1)
        return {"TRY": 1.0, "USD": round(usd, 4), "EUR": round(eur, 4)}
    except Exception:
        return {"TRY": 1.0, "USD": 38.50, "EUR": 41.50}

kurlar = get_live_rates()

# Üst Bar - Anlık Kur Göstergesi
st.title("⚡ Mağaza Fiyat & Kur Yönetim Paneli")
col_usd, col_eur, col_refresh = st.columns([1, 1, 1])
col_usd.metric("Dolar Kuru (USD/TRY)", f"₺{kurlar['USD']}")
col_eur.metric("Euro Kuru (EUR/TRY)", f"₺{kurlar['EUR']}")
if col_refresh.button("🔄 Kurları Yenile"):
    st.cache_data.clear()
    st.rerun()

st.divider()

# Yan Panel: Hızlı Ürün Ekleme Formu
with st.sidebar:
    st.header("➕ Yeni Ürün Ekle")
    with st.form("yeni_urun_form", clear_on_submit=True):
        barkod = st.text_input("Barkod / Stok Kodu")
        urun_adi = st.text_input("Ürün Adı")
        raf_konumu = st.text_input("Dükkan Konumu / Raf (Örn: Raf A-3, Çekmece 2, Vitrin)")
        
        col1, col2 = st.columns(2)
        liste_fiyati = col1.number_input("Liste Fiyatı", min_value=0.0, value=100.0, step=1.0)
        para_birimi = col2.selectbox("Birim", ["USD", "EUR", "TRY"])
        
        col3, col4 = st.columns(2)
        tedarikci_iskonto = col3.number_input("Tedarikçi İskonto (%)", min_value=0.0, max_value=100.0, value=30.0)
        kar_marji = col4.number_input("Hedef Kâr (%)", min_value=0.0, value=25.0)
        
        col5, col6 = st.columns(2)
        kdv_orani = col5.selectbox("KDV (%)", [20, 10, 1], index=0)
        stok_adedi = col6.number_input("Mevcut Stok", min_value=0, value=10, step=1)
        
        kaydet = st.form_submit_button("Ürünü Kaydet", use_container_width=True)
        
        if kaydet:
            if urun_adi:
                cursor.execute("""
                    INSERT INTO urunler (barkod, urun_adi, raf_konumu, liste_fiyati, para_birimi, tedarikci_iskonto, kar_marji, kdv_orani, stok_adedi)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (barkod, urun_adi, raf_konumu, liste_fiyati, para_birimi, tedarikci_iskonto, kar_marji, kdv_orani, stok_adedi))
                conn.commit()
                st.success(f"{urun_adi} başarıyla eklendi!")
                st.rerun()
            else:
                st.error("Lütfen ürün adını girin.")

# Ana Ekran: Ürün Listesi ve Dinamik Fiyat Tablosu
st.subheader("📦 Güncel Fiyat, Stok ve Raf Konum Listesi")

df = pd.read_sql_query("SELECT * FROM urunler", conn)

if not df.empty:
    def hesapla_fiyatlar(row):
        kur = kurlar.get(row["para_birimi"], 1.0)
        alis_maliyeti_doviz = row["liste_fiyati"] * (1 - row["tedarikci_iskonto"] / 100)
        alis_maliyeti_tl = alis_maliyeti_doviz * kur
        
        kdv_haric_satis = alis_maliyeti_tl * (1 + row["kar_marji"] / 100)
        kdv_dahil_satis = kdv_haric_satis * (1 + row["kdv_orani"] / 100)
        
        return pd.Series([
            round(alis_maliyeti_tl, 2),
            round(kdv_haric_satis, 2),
            round(kdv_dahil_satis, 2)
        ])

    df[["Maliyet (TL)", "Satış (KDV Hariç)", "Nihai Satış (KDV Dahil)"]] = df.apply(hesapla_fiyatlar, axis=1)

    gosterilecek_tablo = df[[
        "barkod", "urun_adi", "raf_konumu", "stok_adedi", "liste_fiyati", "para_birimi",
        "tedarikci_iskonto", "kar_marji", "Maliyet (TL)", "Nihai Satış (KDV Dahil)"
    ]].rename(columns={
        "barkod": "Barkod/Kod",
        "urun_adi": "Ürün Adı",
        "raf_konumu": "Dükkan / Raf Konumu",
        "stok_adedi": "Stok",
        "liste_fiyati": "Liste Fiyatı",
        "para_birimi": "Kur",
        "tedarikci_iskonto": "İsk. (%)",
        "kar_marji": "Kâr (%)",
        "Maliyet (TL)": "Maliyet (₺)",
        "Nihai Satış (KDV Dahil)": "Etiket Fiyatı (₺)"
    })

    # Arama / Filtreleme
    arama = st.text_input("🔍 Ürün Adı, Barkod veya Raf Konumu ile Arama Yapın")
    if arama:
        gosterilecek_tablo = gosterilecek_tablo[
            gosterilecek_tablo["Ürün Adı"].str.contains(arama, case=False, na=False) |
            gosterilecek_tablo["Barkod/Kod"].str.contains(arama, case=False, na=False) |
            gosterilecek_tablo["Dükkan / Raf Konumu"].str.contains(arama, case=False, na=False)
        ]

    st.dataframe(gosterilecek_tablo, use_container_width=True, hide_index=True)

    # Excel İndirme Butonu
    csv_data = gosterilecek_tablo.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label="📥 Güncel Listeyi Excel/CSV Olarak İndir",
        data=csv_data,
        file_name="guncel_stok_ve_raf_fiyatlari.csv",
        mime="text/csv"
    )
else:
    st.info("Henüz eklenmiş bir ürün yok. Sol menüden ilk ürününüzü ekleyebilirsiniz.")
