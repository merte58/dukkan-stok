import streamlit as st
import pandas as pd
import requests
from streamlit_gsheets import GSheetsConnection

# Sayfa Yapılandırması
st.set_page_config(page_title="Fiyat & Kur Takip Sistemi", layout="wide")

# Google Sheets Bağlantısı
conn = st.connection("gsheets", type=GSheetsConnection)

def get_data():
    try:
        df = conn.read(ttl="0s")
        return df.dropna(how="all")
    except Exception:
        return pd.DataFrame(columns=[
            "barkod", "urun_adi", "raf_konumu", "liste_fiyati",
            "para_birimi", "tedarikci_iskonto", "kar_marji", "kdv_orani", "stok_adedi"
        ])

# TCMB / Serbest Piyasa Anlık Kur Çekme Fonksiyonu
@st.cache_data(ttl=300)
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

# Sekmeler
tab_liste, tab_ekle, tab_duzenle = st.tabs(["📦 Fiyat & Stok Listesi", "➕ Yeni Ürün Ekle", "✏️ Ürün Düzenle / Sil"])

# 1. SEKME: LİSTELEME
with tab_liste:
    st.subheader("Google Sheets Bağlantılı Güncel Liste")
    df = get_data()

    if not df.empty and "urun_adi" in df.columns:
        def hesapla_fiyatlar(row):
            try:
                kur = kurlar.get(str(row["para_birimi"]), 1.0)
                liste = float(row["liste_fiyati"])
                iskonto = float(row["tedarikci_iskonto"])
                kar = float(row["kar_marji"])
                kdv = float(row["kdv_orani"])
                
                alis_tl = liste * (1 - iskonto / 100) * kur
                satis_kdvsiz = alis_tl * (1 + kar / 100)
                satis_kdvli = satis_kdvsiz * (1 + kdv / 100)
                return pd.Series([round(alis_tl, 2), round(satis_kdvsiz, 2), round(satis_kdvli, 2)])
            except Exception:
                return pd.Series([0.0, 0.0, 0.0])

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

        arama = st.text_input("🔍 Ürün Adı, Barkod veya Raf Konumu ile Arama Yapın")
        if arama:
            gosterilecek_tablo = gosterilecek_tablo[
                gosterilecek_tablo["Ürün Adı"].astype(str).str.contains(arama, case=False, na=False) |
                gosterilecek_tablo["Barkod/Kod"].astype(str).str.contains(arama, case=False, na=False) |
                gosterilecek_tablo["Dükkan / Raf Konumu"].astype(str).str.contains(arama, case=False, na=False)
            ]

        st.dataframe(gosterilecek_tablo, use_container_width=True, hide_index=True)

        csv_data = gosterilecek_tablo.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 Güncel Listeyi İndir (CSV)",
            data=csv_data,
            file_name="dukkan_fiyat_listesi.csv",
            mime="text/csv"
        )
    else:
        st.info("Google Sheets tablonuzda henüz veri yok veya ilk satır başlıkları bekleniyor.")

# 2. SEKME: YENİ ÜRÜN EKLEME
with tab_ekle:
    st.subheader("Yeni Ürün Kaydı")
    with st.form("yeni_urun_form", clear_on_submit=True):
        col_a, col_b = st.columns(2)
        barkod = col_a.text_input("Barkod / Stok Kodu")
        urun_adi = col_b.text_input("Ürün Adı")
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
        
        kaydet = st.form_submit_button("➕ Google Sheets'e Kaydet", use_container_width=True)
        
        if kaydet:
            if urun_adi:
                mevcut_df = get_data()
                yeni_satir = pd.DataFrame([{
                    "barkod": str(barkod),
                    "urun_adi": str(urun_adi),
                    "raf_konumu": str(raf_konumu),
                    "liste_fiyati": float(liste_fiyati),
                    "para_birimi": str(para_birimi),
                    "tedarikci_iskonto": float(tedarikci_iskonto),
                    "kar_marji": float(kar_marji),
                    "kdv_orani": float(kdv_orani),
                    "stok_adedi": int(stok_adedi)
                }])
                guncel_df = pd.concat([mevcut_df, yeni_satir], ignore_index=True)
                conn.update(data=guncel_df)
                st.success(f"'{urun_adi}' Google Sheets tablonuza eklendi!")
                st.rerun()
            else:
                st.error("Lütfen ürün adını girin.")

# 3. SEKME: ÜRÜN DÜZENLEME & SİLME
with tab_duzenle:
    st.subheader("Mevcut Ürünü Düzenle veya Sil")
    df_duzenle = get_data()

    if not df_duzenle.empty and "urun_adi" in df_duzenle.columns:
        secenekler = [f"{idx} - {row['urun_adi']} (Raf: {row.get('raf_konumu', '')})" for idx, row in df_duzenle.iterrows()]
        secilen_idx_str = st.selectbox("Düzenlenecek Ürünü Seçin:", secenekler)
        secilen_index = int(secilen_idx_str.split(" - ")[0])
        secilen_urun = df_duzenle.loc[secilen_index]

        with st.form("duzenle_form"):
            col_d1, col_d2 = st.columns(2)
            d_barkod = col_d1.text_input("Barkod / Stok Kodu", value=str(secilen_urun.get("barkod", "") or ""))
            d_urun_adi = col_d2.text_input("Ürün Adı", value=str(secilen_urun.get("urun_adi", "") or ""))
            d_raf = st.text_input("Dükkan Konumu / Raf", value=str(secilen_urun.get("raf_konumu", "") or ""))

            col_d3, col_d4 = st.columns(2)
            d_liste = col_d3.number_input("Liste Fiyatı", min_value=0.0, value=float(secilen_urun.get("liste_fiyati", 0.0)), step=1.0)
            para_birimleri = ["USD", "EUR", "TRY"]
            curr_val = str(secilen_urun.get("para_birimi", "USD"))
            d_para = col_d4.selectbox("Birim", para_birimleri, index=para_birimleri.index(curr_val) if curr_val in para_birimleri else 0)

            col_d5, col_d6 = st.columns(2)
            d_iskonto = col_d5.number_input("Tedarikçi İskonto (%)", min_value=0.0, max_value=100.0, value=float(secilen_urun.get("tedarikci_iskonto", 0.0)))
            d_kar = col_d6.number_input("Hedef Kâr (%)", min_value=0.0, value=float(secilen_urun.get("kar_marji", 0.0)))

            col_d7, col_d8 = st.columns(2)
            kdv_secenekleri = [20, 10, 1]
            curr_kdv = int(secilen_urun.get("kdv_orani", 20))
            d_kdv = col_d7.selectbox("KDV (%)", kdv_secenekleri, index=kdv_secenekleri.index(curr_kdv) if curr_kdv in kdv_secenekleri else 0)
            d_stok = col_d8.number_input("Mevcut Stok", min_value=0, value=int(secilen_urun.get("stok_adedi", 0)), step=1)

            col_btn1, col_btn2 = st.columns(2)
            guncelle_btn = col_btn1.form_submit_button("💾 Değişiklikleri Google Sheets'e Kaydet", use_container_width=True)
            sil_btn = col_btn2.form_submit_button("🗑️ Ürünü Tablodan Sil", use_container_width=True)

            if guncelle_btn:
                df_duzenle.at[secilen_index, "barkod"] = str(d_barkod)
                df_duzenle.at[secilen_index, "urun_adi"] = str(d_urun_adi)
                df_duzenle.at[secilen_index, "raf_konumu"] = str(d_raf)
                df_duzenle.at[secilen_index, "liste_fiyati"] = float(d_liste)
                df_duzenle.at[secilen_index, "para_birimi"] = str(d_para)
                df_duzenle.at[secilen_index, "tedarikci_iskonto"] = float(d_iskonto)
                df_duzenle.at[secilen_index, "kar_marji"] = float(d_kar)
                df_duzenle.at[secilen_index, "kdv_orani"] = float(d_kdv)
                df_duzenle.at[secilen_index, "stok_adedi"] = int(d_stok)
                conn.update(data=df_duzenle)
                st.success(f"'{d_urun_adi}' güncellendi!")
                st.rerun()

            if sil_btn:
                df_duzenle = df_duzenle.drop(index=secilen_index).reset_index(drop=True)
                conn.update(data=df_duzenle)
                st.warning("Ürün silindi.")
                st.rerun()
    else:
        st.info("Düzenlenecek kayıtlı ürün bulunmuyor.")
