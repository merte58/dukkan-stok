import streamlit as st
import pandas as pd
import requests
from sqlalchemy import text
import time

st.set_page_config(page_title="Fiyat & Kur Takip Sistemi", layout="wide")

# PostgreSQL / Supabase Bağlantısı
conn = st.connection("postgresql", type="sql")

def get_data():
    try:
        df = conn.query("SELECT * FROM urunler ORDER BY id DESC;", ttl="0s")
        return df
    except Exception:
        return pd.DataFrame()

# Kur çekme fonksiyonu
def get_live_rates():
    try:
        res = requests.get("https://open.er-api.com/v6/latest/TRY", timeout=5).json()
        rates = res.get("rates", {})
        usd = 1 / rates.get("USD", 1)
        eur = 1 / rates.get("EUR", 1)
        return {"TRY": 1.0, "USD": round(usd, 4), "EUR": round(eur, 4)}
    except Exception:
        return {"TRY": 1.0, "USD": 38.50, "EUR": 41.50}

# Session State ile kur kontrolü (Kısayol penceresi açılmaz)
if "kurlar" not in st.session_state or "son_kur_guncelleme" not in st.session_state:
    st.session_state.kurlar = get_live_rates()
    st.session_state.son_kur_guncelleme = time.time()
elif time.time() - st.session_state.son_kur_guncelleme > 300:
    st.session_state.kurlar = get_live_rates()
    st.session_state.son_kur_guncelleme = time.time()

kurlar = st.session_state.kurlar

# Üst Bar - Kur Göstergesi
st.title("⚡ Mağaza Fiyat & Kur Yönetim Paneli")
col_usd, col_eur, col_refresh = st.columns([1, 1, 1])
col_usd.metric("Dolar Kuru (USD/TRY)", f"₺{kurlar['USD']}")
col_eur.metric("Euro Kuru (EUR/TRY)", f"₺{kurlar['EUR']}")

if col_refresh.button("🔄 Kurları Yenile"):
    st.session_state.kurlar = get_live_rates()
    st.session_state.son_kur_guncelleme = time.time()
    st.rerun()

st.divider()
