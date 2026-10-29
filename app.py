import streamlit as st
import pandas as pd
import requests
from sqlalchemy import text
import time
import uuid
from decimal import Decimal, ROUND_HALF_UP

st.set_page_config(page_title="Sinyal Elektrik - Ürün Yönetim Sistemi", layout="wide")

# Supabase / PostgreSQL Bağlantısı
conn = st.connection("postgresql", type="sql")

# Veri Çekme Fonksiyonları
def get_dukkan_data():
    try:
        return conn.query("SELECT * FROM urunler ORDER BY id DESC;", ttl="0s")
    except Exception:
        return pd.DataFrame()

def get_katalog_data():
    try:
        return conn.query("SELECT * FROM katalog_urunleri ORDER BY marka ASC, urun_adi ASC;", ttl="0s")
    except Exception:
        return pd.DataFrame()

# Kur Çekme Fonksiyonu
def get_live_rates():
    try:
        res = requests.get("https://open.er-api.com/v6/latest/TRY", timeout=5).json()
        rates = res.get("rates", {})
        usd = 1 / rates.get("USD", 1)
        eur = 1 / rates.get("EUR", 1)
        return {"TRY": 1.0, "USD": round(usd, 4), "EUR": round(eur, 4)}
    except Exception:
        return {"TRY": 1.0, "USD": 38.50, "EUR": 41.50}

# Session State ile kur hafızası
if "kurlar" not in st.session_state or "son_kur_guncelleme" not in st.session_state:
    st.session_state.kurlar = get_live_rates()
    st.session_state.son_kur_guncelleme = time.time()
elif time.time() - st.session_state.son_kur_guncelleme > 300:
    st.session_state.kurlar = get_live_rates()
    st.session_state.son_kur_guncelleme = time.time()

kurlar = st.session_state.kurlar

# Başlık ve Üst Bar
st.title("⚡ Sinyal Elektrik - Ürün Yönetim Sistemi")
col_usd, col_eur, col_refresh = st.columns([1, 1, 1])
col_usd.metric("Dolar Kuru (USD/TRY)", f"₺{kurlar['USD']}")
col_eur.metric("Euro Kuru (EUR/TRY)", f"₺{kurlar['EUR']}")

if col_refresh.button("🔄 Kurları Yenile"):
    st.session_state.kurlar = get_live_rates()
    st.session_state.son_kur_guncelleme = time.time()
    st.rerun()

st.divider()

# Sekmeler
tab_katalog, tab_katalog_yukle, tab_liste, tab_ekle, tab_duzenle, tab_satis = st.tabs([
    "📖 Katalog Fiyat Listesi",
    "📥 Katalog Ürün Yükle/Ekle",
    "📦 Dükkan Stok & Fiyat Listesi",
    "➕ Dükkana Ürün Ekle",
    "✏️ Dükkan Ürün Düzenle/Sil",
    "🛒 Barkod & Satış"
])

# ----------------------------------------------------
# 1. SEKME: KATALOG LİSTESİ & ARAMA
# ----------------------------------------------------
with tab_katalog:
    st.subheader("Marka Katalog Fiyatları")
    df_kat = get_katalog_data()

    if not df_kat.empty and "urun_adi" in df_kat.columns:
        # Marka Filtresi ve Arama
        col_m, col_s = st.columns([1, 2])
        markalar = ["Tümü"] + sorted(list(df_kat["marka"].dropna().unique()))
        secilen_marka = col_m.selectbox("Marka Filtrele:", markalar)
        arama_kat = col_s.text_input("🔍 Ürün Kodu veya Ürün Adı Ara:")

        filtre_df = df_kat.copy()
        if secilen_marka != "Tümü":
            filtre_df = filtre_df[filtre_df["marka"] == secilen_marka]

        if arama_kat:
            filtre_df = filtre_df[
                filtre_df["urun_adi"].astype(str).str.contains(arama_kat, case=False, na=False) |
                filtre_df["urun_kodu"].astype(str).str.contains(arama_kat, case=False, na=False)
            ]

        # Canlı Kur TL Karşılığı Hesaplama
        def kat_tl_hesapla(row):
            try:
                kur = kurlar.get(str(row["para_birimi"]), 1.0)
                liste = float(row["liste_fiyati"])
                return round(liste * kur, 2)
            except Exception:
                return 0.0

        filtre_df["Liste Fiyatı (TL)"] = filtre_df.apply(kat_tl_hesapla, axis=1)

        tablo_kat_goster = filtre_df[[
            "marka", "urun_kodu", "urun_adi", "liste_fiyati", "para_birimi", "Liste Fiyatı (TL)"
        ]].rename(columns={
            "marka": "Marka",
            "urun_kodu": "Ürün Kodu",
            "urun_adi": "Ürün Adı",
            "liste_fiyati": "Liste Fiyatı (Döviz/TL)",
            "para_birimi": "Birim",
            "Liste Fiyatı (TL)": "Güncel Liste Fiyatı (₺)"
        })

        st.dataframe(tablo_kat_goster, use_container_width=True, hide_index=True)
        st.caption(f"Toplam {len(tablo_kat_goster)} katalog ürünü listeleniyor.")
    else:
        st.info("Henüz katalog ürünü yüklenmedi. 'Katalog Ürün Yükle/Ekle' sekmesinden tekli veya toplu Excel yükleyebilirsiniz.")

# ----------------------------------------------------
# 2. SEKME: KATALOG ÜRÜN YÜKLE / EKLE
# ----------------------------------------------------
with tab_katalog_yukle:
    st.subheader("Kataloğa Ürün Ekleme")
    
    secim_tipi = st.radio("Ekleme Yöntemi Seçin:", ["📁 Excel / CSV ile Toplu Yükle", "➕ Tek Tek Ürün Ekle"], horizontal=True)

    if secim_tipi == "➕ Tek Tek Ürün Ekle":
        with st.form("tek_katalog_ekle_form", clear_on_submit=True):
            col_k1, col_k2 = st.columns(2)
            k_marka = col_k1.text_input("Marka (Örn: Siemens, Schneider, Viko, Forlife)")
            k_kod = col_k2.text_input("Ürün Kodu (Örn: 5SY4110-7, BFR430)")
            
            k_ad = st.text_input("Ürün Adı")
            
            col_k3, col_k4 = st.columns(2)
            k_fiyat = col_k3.number_input("Liste Fiyatı", min_value=0.0, value=100.0, step=1.0)
            k_birim = col_k4.selectbox("Para Birimi", ["TRY", "USD", "EUR"])

            kat_kaydet = st.form_submit_button("💾 Kataloğa Kaydet", use_container_width=True)
            if kat_kaydet:
                if k_marka and k_ad:
                    with conn.session as s:
                        s.execute(
                            text("""
                                INSERT INTO katalog_urunleri (marka, urun_kodu, urun_adi, liste_fiyati, para_birimi)
                                VALUES (:marka, :urun_kodu, :urun_adi, :liste_fiyati, :para_birimi)
                            """),
                            {
                                "marka": str(k_marka).strip(),
                                "urun_kodu": str(k_kod).strip(),
                                "urun_adi": str(k_ad).strip(),
                                "liste_fiyati": float(k_fiyat),
                                "para_birimi": str(k_birim)
                            }
                        )
                        s.commit()
                    st.success(f"'{k_ad}' katalog veritabanına eklendi!")
                    st.rerun()
                else:
                    st.error("Lütfen Marka ve Ürün Adı alanlarını doldurun.")

    else:
        st.markdown("""
        **Toplu Yükleme Formatı:**
        Yükleyeceğiniz Excel (`.xlsx`) veya CSV dosyasında şu sütun başlıkları bulunmalıdır:
        `marka`, `urun_kodu`, `urun_adi`, `liste_fiyati`, `para_birimi`
        """)

        dosya = st.file_uploader("Excel veya CSV Dosyası Yükleyin", type=["xlsx", "csv"])
        if dosya:
            try:
                if dosya.name.endswith(".csv"):
                    df_upload = pd.read_csv(dosya)
                else:
                    df_upload = pd.read_excel(dosya)

                st.write("Yüklenecek Veri Önizlemesi:")
                st.dataframe(df_upload.head(5))

                if st.button("🚀 Bu Ürünleri Katalog Veritabanına Aktar", use_container_width=True):
                    gerekli_kolonlar = ["marka", "urun_kodu", "urun_adi", "liste_fiyati", "para_birimi"]
                    
                    # Kolon isimlerini küçük harfe çekelim
                    df_upload.columns = [str(c).lower().strip() for c in df_upload.columns]
                    
                    if all(col in df_upload.columns for col in gerekli_kolonlar):
                        with conn.session as s:
                            for _, r in df_upload.iterrows():
                                s.execute(
                                    text("""
                                        INSERT INTO katalog_urunleri (marka, urun_kodu, urun_adi, liste_fiyati, para_birimi)
                                        VALUES (:marka, :urun_kodu, :urun_adi, :liste_fiyati, :para_birimi)
                                    """),
                                    {
                                        "marka": str(r["marka"]),
                                        "urun_kodu": str(r["urun_kodu"]),
                                        "urun_adi": str(r["urun_adi"]),
                                        "liste_fiyati": float(r["liste_fiyati"]),
                                        "para_birimi": str(r["para_birimi"]).upper()
                                    }
                                )
                            s.commit()
                        st.success(f"Tebrikler! Toplam {len(df_upload)} adet katalog ürünü başarıyla aktarıldı.")
                        st.rerun()
                    else:
                        st.error(f"Dosyanızda gerekli sütunlar eksik! Olması gereken sütunlar: {gerekli_kolonlar}")
            except Exception as e:
                st.error(f"Dosya işlenirken hata oluştu: {e}")

# ----------------------------------------------------
# 3. SEKME: DÜKKAN STOK LİSTESİ
# ----------------------------------------------------
with tab_liste:
    st.subheader("Dükkan Stok & Satış Fiyatları")
    df = get_dukkan_data()

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
            "id", "barkod", "urun_adi", "raf_konumu", "stok_adedi", "liste_fiyati", "para_birimi",
            "tedarikci_iskonto", "kar_marji", "Maliyet (TL)", "Nihai Satış (KDV Dahil)"
        ]].rename(columns={
            "id": "ID",
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

        arama = st.text_input("🔍 Dükkan Ürünlerinde Arama Yapın")
        if arama:
            gosterilecek_tablo = gosterilecek_tablo[
                gosterilecek_tablo["Ürün Adı"].astype(str).str.contains(arama, case=False, na=False) |
                gosterilecek_tablo["Barkod/Kod"].astype(str).str.contains(arama, case=False, na=False) |
                gosterilecek_tablo["Dükkan / Raf Konumu"].astype(str).str.contains(arama, case=False, na=False)
            ]

        st.dataframe(gosterilecek_tablo, use_container_width=True, hide_index=True)

        csv_data = gosterilecek_tablo.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 Dükkan Listesini İndir (CSV)",
            data=csv_data,
            file_name="sinyal_elektrik_dukkan_stok.csv",
            mime="text/csv"
        )
    else:
        st.info("Dükkanda henüz kayıtlı ürün yok.")

# ----------------------------------------------------
# 4. SEKME: DÜKKANA ÜRÜN EKLE
# ----------------------------------------------------
with tab_ekle:
    st.subheader("Dükkana Yeni Stok / Ürün Kaydı")
    with st.form("yeni_dukkan_urun_form", clear_on_submit=True):
        col_a, col_b = st.columns(2)
        barkod = col_a.text_input("Barkod / Stok Kodu")
        urun_adi = col_b.text_input("Ürün Adı")
        raf_konumu = st.text_input("Dükkan Konumu / Raf (Örn: Raf A-3, Pano Yanı, Çekmece 2)")
        
        col1, col2 = st.columns(2)
        liste_fiyati = col1.number_input("Liste Fiyatı", min_value=0.0, value=100.0, step=1.0)
        para_birimi = col2.selectbox("Birim", ["USD", "EUR", "TRY"])
        
        col3, col4 = st.columns(2)
        tedarikci_iskonto = col3.number_input("Tedarikçi İskonto (%)", min_value=0.0, max_value=100.0, value=30.0)
        kar_marji = col4.number_input("Hedef Kâr (%)", min_value=0.0, value=25.0)
        
        col5, col6 = st.columns(2)
        kdv_orani = col5.selectbox("KDV (%)", [20, 10, 1], index=0)
        stok_adedi = col6.number_input("Mevcut Stok Adedi", min_value=0, value=10, step=1)
        
        kaydet = st.form_submit_button("➕ Dükkan Stoğuna Kaydet", use_container_width=True)
        
        if kaydet:
            if urun_adi:
                with conn.session as s:
                    s.execute(
                        text("""
                            INSERT INTO urunler (barkod, urun_adi, raf_konumu, liste_fiyati, para_birimi, tedarikci_iskonto, kar_marji, kdv_orani, stok_adedi)
                            VALUES (:barkod, :urun_adi, :raf_konumu, :liste_fiyati, :para_birimi, :tedarikci_iskonto, :kar_marji, :kdv_orani, :stok_adedi)
                        """),
                        {
                            "barkod": str(barkod),
                            "urun_adi": str(urun_adi),
                            "raf_konumu": str(raf_konumu),
                            "liste_fiyati": float(liste_fiyati),
                            "para_birimi": str(para_birimi),
                            "tedarikci_iskonto": float(tedarikci_iskonto),
                            "kar_marji": float(kar_marji),
                            "kdv_orani": float(kdv_orani),
                            "stok_adedi": int(stok_adedi)
                        }
                    )
                    s.commit()
                st.success(f"'{urun_adi}' dükkan stoğuna başarıyla eklendi!")
                st.rerun()
            else:
                st.error("Lütfen ürün adını girin.")

# ----------------------------------------------------
# 5. SEKME: DÜKKAN ÜRÜN DÜZENLE / SİL
# ----------------------------------------------------
with tab_duzenle:
    st.subheader("Dükkan Ürününü Düzenle veya Sil")
    df_duzenle = get_dukkan_data()

    if not df_duzenle.empty and "urun_adi" in df_duzenle.columns:
        secenekler = {f"{row['urun_adi']} (ID: {row['id']} | Raf: {row.get('raf_konumu', '')})": row["id"] for _, row in df_duzenle.iterrows()}
        secilen_etiket = st.selectbox("Düzenlenecek Ürünü Seçin:", list(secenekler.keys()))
        secilen_id = secenekler[secilen_etiket]
        secilen_urun = df_duzenle[df_duzenle["id"] == secilen_id].iloc[0]

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
            d_stok = col_d8.number_input("Mevcut Stok Adedi", min_value=0, value=int(secilen_urun.get("stok_adedi", 0)), step=1)

            col_btn1, col_btn2 = st.columns(2)
            guncelle_btn = col_btn1.form_submit_button("💾 Değişiklikleri Kaydet", use_container_width=True)
            sil_btn = col_btn2.form_submit_button("🗑️ Ürünü Sil", use_container_width=True)

            if guncelle_btn:
                with conn.session as s:
                    s.execute(
                        text("""
                            UPDATE urunler
                            SET barkod = :barkod, urun_adi = :urun_adi, raf_konumu = :raf_konumu,
                                liste_fiyati = :liste_fiyati, para_birimi = :para_birimi,
                                tedarikci_iskonto = :tedarikci_iskonto, kar_marji = :kar_marji,
                                kdv_orani = :kdv_orani, stok_adedi = :stok_adedi
                            WHERE id = :id
                        """),
                        {
                            "barkod": str(d_barkod),
                            "urun_adi": str(d_urun_adi),
                            "raf_konumu": str(d_raf),
                            "liste_fiyati": float(d_liste),
                            "para_birimi": str(d_para),
                            "tedarikci_iskonto": float(d_iskonto),
                            "kar_marji": float(d_kar),
                            "kdv_orani": float(d_kdv),
                            "stok_adedi": int(d_stok),
                            "id": int(secilen_id)
                        }
                    )
                    s.commit()
                st.success(f"'{d_urun_adi}' güncellendi!")
                st.rerun()

            if sil_btn:
                with conn.session as s:
                    s.execute(text("DELETE FROM urunler WHERE id = :id"), {"id": int(secilen_id)})
                    s.commit()
                st.warning("Ürün silindi.")
                st.rerun()
    else:
        st.info("Düzenlenecek dükkan ürünü bulunmuyor.")

# ----------------------------------------------------
# 6. SEKME: BARKODLA SORGULAMA & SATIŞ
# ----------------------------------------------------
# Ön koşul: supabase_satis_kurulum.sql dosyası SQL Editor'da bir kez çalıştırılmalı.
# Barkod okuyucunun USB HID (klavye) modunda ve Enter son ekiyle çalışması önerilir.

def para_yuvarla(tutar):
    return Decimal(str(tutar)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def urun_satis_fiyati(row, secili_kurlar):
    kur = Decimal(str(secili_kurlar.get(str(row["para_birimi"]), 1.0)))
    liste = Decimal(str(row["liste_fiyati"]))
    iskonto = Decimal(str(row["tedarikci_iskonto"]))
    kar = Decimal(str(row["kar_marji"]))
    kdv = Decimal(str(row["kdv_orani"]))
    tutar = liste * (1 - iskonto / 100) * kur * (1 + kar / 100) * (1 + kdv / 100)
    return para_yuvarla(tutar)


def fiyat_goster(tutar):
    return f"{Decimal(str(tutar)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


if "satis_sepeti" not in st.session_state:
    st.session_state.satis_sepeti = {}  # {urun_id: adet}
if "satis_islem_no" not in st.session_state:
    st.session_state.satis_islem_no = str(uuid.uuid4())

with tab_satis:
    st.subheader("🛒 Barkodla Fiyat Sorgula / Satış Yap")
    if st.session_state.get("satis_basarili_mesaj"):
        st.success(st.session_state.pop("satis_basarili_mesaj"))
    st.caption("Barkod okuyucuyla okutun veya barkodu elle girin. Sorgulama stoktan düşmez; stok sadece satış onayında düşer.")
    with st.form("barkod_okut_form", clear_on_submit=True):
        barkod_sorgu = st.text_input("Barkod / Stok Kodu", placeholder="Barkodu okutun (Enter)")
        eklenecek_adet = st.number_input("Sepete eklenecek adet", min_value=1, value=1, step=1)
        barkod_ara = st.form_submit_button("🔎 Sorgula ve Sepete Ekle", use_container_width=True)

    if barkod_ara:
        barkod_deger = barkod_sorgu.strip()
        if not barkod_deger:
            st.warning("Lütfen barkod okutun veya kod girin.")
        else:
            try:
                with conn.session as db:
                    urunler = db.execute(
                        text("SELECT * FROM urunler WHERE BTRIM(barkod) = :barkod ORDER BY id"),
                        {"barkod": barkod_deger},
                    ).mappings().all()
                if not urunler:
                    st.error("Bu barkod için ürün bulunamadı.")
                elif len(urunler) > 1:
                    st.error("Bu barkod birden fazla üründe kayıtlı. Ürün kayıtlarını düzeltmeden satış yapılamaz.")
                else:
                    urun = dict(urunler[0])
                    urun_id = int(urun["id"])
                    mevcut = int(urun["stok_adedi"])
                    sepetteki = st.session_state.satis_sepeti.get(urun_id, 0)
                    st.info(f"{urun['urun_adi']} | Raf: {urun.get('raf_konumu') or '-'} | Stok: {mevcut} | Fiyat: {fiyat_goster(urun_satis_fiyati(urun, kurlar))}")
                    if sepetteki + int(eklenecek_adet) > mevcut:
                        st.error("Yetersiz stok: sepetteki miktar da hesaba katıldı.")
                    else:
                        st.session_state.satis_sepeti[urun_id] = sepetteki + int(eklenecek_adet)
                        st.success("Ürün sepete eklendi.")
            except Exception as hata:
                st.error(f"Barkod sorgusu başarısız: {hata}")

    st.markdown("#### Satış Sepeti")
    sepet = st.session_state.satis_sepeti
    if not sepet:
        st.info("Sepet boş. Satış yapmak için barkod okutun.")
    else:
        try:
            with conn.session as db:
                satirlar = db.execute(
                    text("SELECT * FROM urunler WHERE id = ANY(:ids) ORDER BY id"),
                    {"ids": list(sepet.keys())},
                ).mappings().all()
            bulunan = {int(r["id"]): dict(r) for r in satirlar}
            onizleme = []
            hata_var = False
            toplam = Decimal("0.00")
            for urun_id, adet in sepet.items():
                if urun_id not in bulunan:
                    st.error(f"ID {urun_id} ürün artık veritabanında bulunmuyor.")
                    hata_var = True
                    continue
                u = bulunan[urun_id]
                fiyat = urun_satis_fiyati(u, kurlar)
                tutar = para_yuvarla(fiyat * adet)
                toplam += tutar
                if adet > int(u["stok_adedi"]):
                    hata_var = True
                onizleme.append({
                    "ID": urun_id, "Ürün": u["urun_adi"], "Barkod": u["barkod"],
                    "Adet": adet, "Mevcut Stok": int(u["stok_adedi"]),
                    "Birim Satış (TL)": float(fiyat), "Tutar (TL)": float(tutar),
                })
            st.dataframe(pd.DataFrame(onizleme), use_container_width=True, hide_index=True)
            st.metric("Sepet Toplamı (KDV dahil)", fiyat_goster(toplam))
            if hata_var:
                st.error("Sepette stok yetersizliği veya silinmiş ürün var. Satış onaylanamaz.")

            col_sil, col_bosalt = st.columns(2)
            with col_sil:
                silinecek_id = st.selectbox(
                    "Sepetten çıkarılacak ürün", list(sepet.keys()),
                    format_func=lambda i: bulunan.get(i, {}).get("urun_adi", str(i)),
                )
                if st.button("➖ Seçili Ürünü Sepetten Çıkar"):
                    sepet.pop(silinecek_id, None)
                    st.rerun()
            with col_bosalt:
                st.write(" ")
                if st.button("🗑️ Sepeti Temizle"):
                    sepet.clear()
                    st.session_state.satis_islem_no = str(uuid.uuid4())
                    st.rerun()

            st.warning("Satışı onayladığınızda ürün stokları kalıcı olarak azalır. İşlem tek veritabanı transaction'ında kaydedilir.")
            satis_onay = st.button("✅ SATIŞI ONAYLA VE STOKTAN DÜŞ", type="primary", disabled=hata_var)
            if satis_onay:
                try:
                    islem_no = st.session_state.satis_islem_no
                    # İşlemin tamamı atomiktir: kilitle, kontrol et, satış oluştur, düş, satırları ekle, commit.
                    with conn.session as db:
                        # Yeniden onay göndermek aynı işlem numarasıyla ikinci kez stok düşürmemeli.
                        onceki = db.execute(
                            text("SELECT id FROM satislar WHERE islem_no = :islem_no"),
                            {"islem_no": islem_no},
                        ).scalar_one_or_none()
                        if onceki is not None:
                            raise ValueError("Bu satış daha önce kaydedilmiş. Sayfayı yenileyin.")

                        kilitli_urunler = db.execute(
                            text("SELECT * FROM urunler WHERE id = ANY(:ids) ORDER BY id FOR UPDATE"),
                            {"ids": sorted(sepet.keys())},
                        ).mappings().all()
                        kilitli = {int(r["id"]): dict(r) for r in kilitli_urunler}
                        if len(kilitli) != len(sepet):
                            raise ValueError("Sepetteki ürünlerden biri artık bulunmuyor.")

                        kalemler = []
                        satis_toplami = Decimal("0.00")
                        for uid, miktar in sorted(sepet.items()):
                            urun = kilitli[uid]
                            if int(miktar) <= 0 or int(urun["stok_adedi"]) < int(miktar):
                                raise ValueError(f"{urun['urun_adi']}: yetersiz stok. Satış iptal edildi.")
                            birim = urun_satis_fiyati(urun, kurlar)
                            satir_tutari = para_yuvarla(birim * miktar)
                            satis_toplami += satir_tutari
                            kalemler.append((uid, urun, int(miktar), birim, satir_tutari))

                        satis_id = db.execute(
                            text("INSERT INTO satislar (islem_no, toplam_tl) VALUES (:islem_no, :toplam) RETURNING id"),
                            {"islem_no": islem_no, "toplam": satis_toplami},
                        ).scalar_one()
                        for uid, urun, miktar, birim, satir_tutari in kalemler:
                            sonuc = db.execute(
                                text("UPDATE urunler SET stok_adedi = stok_adedi - :miktar WHERE id = :id AND stok_adedi >= :miktar"),
                                {"id": uid, "miktar": miktar},
                            )
                            if sonuc.rowcount != 1:
                                raise ValueError(f"{urun['urun_adi']} stok düşümü başarısız; satış geri alındı.")
                            db.execute(
                                text("""INSERT INTO satis_kalemleri
                                     (satis_id, urun_id, barkod, urun_adi, miktar, birim_fiyat_tl, satir_toplam_tl)
                                     VALUES (:satis, :urun, :barkod, :ad, :miktar, :birim, :toplam)"""),
                                {"satis": satis_id, "urun": uid, "barkod": str(urun.get("barkod") or ""),
                                 "ad": str(urun["urun_adi"]), "miktar": miktar,
                                 "birim": birim, "toplam": satir_tutari},
                            )
                        db.commit()
                    sepet.clear()
                    st.session_state.satis_islem_no = str(uuid.uuid4())
                    st.session_state.satis_basarili_mesaj = f"Satış #{satis_id} tamamlandı. Toplam: {fiyat_goster(satis_toplami)}. Stoklar düşüldü."
                    st.rerun()
                except Exception as hata:
                    st.error(f"Satış kaydedilemedi, stoklar değiştirilmedi: {hata}")
        except Exception as hata:
            st.error(f"Sepet yüklenemedi: {hata}")

    st.markdown("#### Son Satışlar")
    try:
        with conn.session as db:
            son_satislar = db.execute(text("SELECT id, olusturma_zamani, toplam_tl FROM satislar ORDER BY id DESC LIMIT 20")).mappings().all()
        if son_satislar:
            st.dataframe(pd.DataFrame([dict(s) for s in son_satislar]), use_container_width=True, hide_index=True)
        else:
            st.caption("Henüz satış kaydı yok.")
    except Exception:
        st.info("Satış tabloları henüz kurulmamış olabilir. Önce SQL kurulum dosyasını çalıştırın.")
