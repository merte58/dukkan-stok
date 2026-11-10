import streamlit as st
import pandas as pd
import requests
from sqlalchemy import text
import time
import uuid
import re
import hashlib
from io import BytesIO
from PIL import Image, ImageOps, ImageEnhance
import zxingcpp
from streamlit_qrcode_scanner import qrcode_scanner
from decimal import Decimal, ROUND_HALF_UP

st.set_page_config(page_title="Sinyal Elektrik - Ürün Yönetim Sistemi", layout="wide")

# --- Mobil barkod okuma ve ürün görselleri ---
FOTO_BUCKET = "urun-fotograflari"  # Özel (private) Supabase Storage bucket


def kamera_barkod(alan):
    """Telefonda canlı barkod tarar; kodu bir kez döndürür, stok işlemi yapmaz."""
    aktif_key = f"canli_tarama_aktif_{alan}"
    sonuc_key = f"canli_tarama_sonuc_{alan}"

    # Tespit edilen barkod, sonraki Streamlit çalışmasında ilgili arama alanına aktarılır.
    if sonuc_key in st.session_state:
        return st.session_state.pop(sonuc_key)

    with st.expander("📷 Canlı barkod okuyucu", expanded=st.session_state.get(aktif_key, False)):
        st.caption("Barkodu kameraya gösterin. Fotoğraf çekmeniz gerekmez. Barkod okununca kamera kapanır.")
        if not st.session_state.get(aktif_key, False):
            if st.button("📷 Barkod okut", key=f"tara_baslat_{alan}", use_container_width=True):
                st.session_state[aktif_key] = True
                st.rerun()
        else:
            if st.button("Kamerayı kapat", key=f"tara_kapat_{alan}"):
                st.session_state[aktif_key] = False
                st.rerun()
            # HTML5-Qrcode tabanlı bileşen kamerayı tarayıcı içinde açar.
            # Streamlit uygulaması HTTPS adresi üzerinden kullanılmalıdır.
            okunan = qrcode_scanner(key=f"canli_okuyucu_{alan}")
            if okunan and str(okunan).strip():
                st.session_state[sonuc_key] = str(okunan).strip()
                st.session_state[aktif_key] = False
                st.rerun()

        # Canlı bileşen bazı telefonlarda 1D/EAN-13 barkodlarını algılamayabilir.
        # Yedek okuyucu fotoğraf karesi üzerinden ZXing-C++ ile sunucuda çözümler.
        with st.expander("Çizgili barkod okunmuyorsa: fotoğrafla tara", expanded=False):
            st.caption("Kamerayı 15–25 cm uzakta tutun. Barkodu yatay, net ve iki kenarı boş kalacak şekilde çekin. Fotoğraf sadece kodu çözmek için kullanılır.")
            kare = st.camera_input("Barkod fotoğrafı çek", key=f"yedek_kamera_{alan}")
            if kare is not None:
                parmakizi = hashlib.sha256(kare.getvalue()).hexdigest()
                onceki = f"yedek_kamera_ozet_{alan}"
                if st.session_state.get(onceki) != parmakizi:
                    st.session_state[onceki] = parmakizi
                    try:
                        barkodlar = fotograf_barkod_oku(kare.getvalue())
                        if len(barkodlar) == 1:
                            st.session_state[sonuc_key] = barkodlar[0]
                            st.session_state[aktif_key] = False
                            st.rerun()
                        elif len(barkodlar) > 1:
                            st.warning("Birden fazla barkod bulundu. Tek ürün barkodu çekin.")
                        else:
                            st.warning("Barkod çözümlenemedi. Daha fazla ışık, net odak ve biraz mesafe deneyin.")
                    except Exception:
                        st.warning("Fotoğraf işlenemedi. Barkodu elle girebilirsiniz.")
    return None


def fotograf_barkod_oku(ham):
    """Yalnızca fotoğraf karesini ZXing ile çözer, hiçbir stok/satış işlemi yapmaz."""
    if len(ham) > 12 * 1024 * 1024:
        raise ValueError("Fotoğraf 12 MB sınırını aşıyor.")
    resim = ImageOps.exif_transpose(Image.open(BytesIO(ham))).convert("RGB")
    if resim.width * resim.height > 20_000_000:
        resim.thumbnail((4000, 4000))
    goruntuler = [resim]
    gri = ImageOps.grayscale(resim)
    goruntuler.append(gri)
    goruntuler.append(ImageEnhance.Contrast(gri).enhance(1.8))
    kodlar = []
    for im in goruntuler:
        for sonuc in zxingcpp.read_barcodes(im):
            kod = (sonuc.text or "").strip()
            if kod and kod not in kodlar:
                kodlar.append(kod)
        if kodlar:
            break
    return kodlar


def urun_fotograf_sec(alan):
    """Ürün fotoğrafı kameradan veya galeriden alınır; ancak Kaydet ile yüklenir."""
    with st.expander("🖼️ Ürün fotoğrafı ekle / değiştir", expanded=False):
        sec = st.radio("Kaynak", ["Galeriden / dosyadan", "Kameradan çek"], key=f"fotok_{alan}", horizontal=True)
        foto = (st.file_uploader("Ürün fotoğrafı seç (JPG/PNG/WEBP)", type=["jpg", "jpeg", "png", "webp"], key=f"fotou_{alan}")
                if sec == "Galeriden / dosyadan" else
                st.camera_input("Ürünün fotoğrafını çek", key=f"fotoc_{alan}"))
        if foto is not None:
            st.image(foto, width=220)
        return foto


def fotograf_hazirla(foto):
    """Özel depoya gitmeden önce boyutları küçültüp JPEG'e dönüştürür."""
    try:
        ham = foto.getvalue()
        if len(ham) > 12 * 1024 * 1024:
            raise ValueError("Fotoğraf 12 MB'den büyük olmamalı.")
        im = ImageOps.exif_transpose(Image.open(BytesIO(ham)))
        im.thumbnail((1200, 1200))
        if im.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.getchannel("A"))
            im = bg
        else:
            im = im.convert("RGB")
        b = BytesIO()
        im.save(b, format="JPEG", quality=77, optimize=True)
        data = b.getvalue()
        if len(data) > 1024 * 1024:
            raise ValueError("Sıkıştırılmış fotoğraf 1 MB sınırını aşıyor.")
        return data
    except (OSError, ValueError) as exc:
        raise ValueError(f"Fotoğraf hazırlanamadı: {exc}") from exc


def storage_basliklari():
    return {"apikey": st.secrets["supabase_auth"]["anon_key"],
            "Authorization": f"Bearer {st.session_state.auth_access_token}"}


def foto_yukle(foto, urun_id):
    """Benzersiz yeni isimle yükler; var olan fotoğrafa overwrite yapmaz."""
    if foto is None:
        return None
    veri = fotograf_hazirla(foto)
    dosya_yolu = f"urun-{int(urun_id)}/{uuid.uuid4().hex}.jpg"
    url = st.secrets["supabase_auth"]["url"].rstrip("/")
    from urllib.parse import quote
    tam_url = f"{url}/storage/v1/object/{FOTO_BUCKET}/{quote(dosya_yolu, safe='/')}"
    baslik = storage_basliklari()
    baslik["Content-Type"] = "image/jpeg"
    yanit = requests.post(tam_url, headers=baslik, data=veri, timeout=35)
    if yanit.status_code not in (200, 201):
        raise RuntimeError(f"Fotoğraf yüklenemedi (HTTP {yanit.status_code}). Özel Storage bucket ve RLS kurulumunu kontrol edin.")
    return dosya_yolu


@st.cache_data(ttl=300, show_spinner=False, max_entries=350)
def foto_onizleme(foto_yolu, access_token, boyut=240):
    """Özel Storage'dan fotoğrafı yalnızca oturum sahibi token ile okur; küçük görsele çevirir."""
    from urllib.parse import quote
    if not isinstance(foto_yolu, str) or not foto_yolu.strip():
        return None
    base = st.secrets["supabase_auth"]["url"].rstrip("/")
    adres = f"{base}/storage/v1/object/authenticated/{FOTO_BUCKET}/{quote(foto_yolu, safe='/')}"
    yanit = requests.get(adres, headers={"apikey": st.secrets["supabase_auth"]["anon_key"],
                                       "Authorization": f"Bearer {access_token}"}, timeout=12)
    if yanit.status_code != 200 or not yanit.headers.get("content-type", "").startswith("image/"):
        return None
    if len(yanit.content) > 4 * 1024 * 1024:
        return None
    resim = ImageOps.exif_transpose(Image.open(BytesIO(yanit.content))).convert("RGB")
    resim.thumbnail((boyut, boyut))
    hedef = BytesIO()
    resim.save(hedef, format="JPEG", quality=75, optimize=True)
    return hedef.getvalue()


def fotograflı_kartlar(urunler, kurlar):
    """Filtrelenmiş ürünleri 12'şerli sayfalarda gösterir; her karta sadece satış fiyatını koyar."""
    if urunler.empty:
        return
    st.markdown("#### Fotoğraflı ürünler")
    st.caption("Her sayfada en fazla 12 küçük fotoğraf yüklenir. Fotoğrafı olmayan ürünlerde bilgi kartı görünür.")
    son_sayfa = max(1, (len(urunler) + 11) // 12)
    sayfa = st.number_input("Sayfa", min_value=1, max_value=son_sayfa, value=1, step=1, key="urun_foto_sayfa")
    alt = (sayfa - 1) * 12
    parcali = urunler.iloc[alt:alt + 12]
    for i in range(0, len(parcali), 3):
        sutunlar = st.columns(3)
        for sutun, (_, urun) in zip(sutunlar, parcali.iloc[i:i+3].iterrows()):
            with sutun:
                with st.container(border=True):
                    yol = urun.get("foto_yolu")
                    if isinstance(yol, str) and yol.strip():
                        try:
                            goruntu = foto_onizleme(yol, st.session_state.auth_access_token)
                            if goruntu is not None:
                                st.image(goruntu, use_container_width=True)
                            else:
                                st.caption("Fotoğraf yüklenemedi")
                        except (requests.RequestException, OSError, ValueError):
                            st.caption("Fotoğraf görüntülenemiyor")
                    else:
                        st.caption("Fotoğraf eklenmemiş")
                    st.markdown(f"**{str(urun.get('urun_adi', 'Ürün'))}**")
                    st.caption(f"Barkod: {str(urun.get('barkod', '')).strip()} | Raf: {str(urun.get('raf_konumu', ''))}")
                    st.write(f"Stok: **{int(urun.get('stok_adedi', 0))}**")
                    try:
                        kur = Decimal(str(kurlar.get(str(urun.get("para_birimi", "TRY")), 1)))
                        liste = Decimal(str(urun.get("liste_fiyati", 0)))
                        iskonto = Decimal(str(urun.get("tedarikci_iskonto", 0)))
                        kar = Decimal(str(urun.get("kar_marji", 0)))
                        kdv = Decimal(str(urun.get("kdv_orani", 0)))
                        fiyat = (liste * (1 - iskonto / 100) * kur * (1 + kar / 100) * (1 + kdv / 100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        st.write(f"Satış fiyatı: **{fiyat:,.2f} TL**")
                    except Exception:
                        st.caption("Fiyat hesaplanamadı")


def foto_goster(foto_yolu, genislik=230):
    if not isinstance(foto_yolu, str) or not foto_yolu.strip():
        return
    from urllib.parse import quote
    url = st.secrets["supabase_auth"]["url"].rstrip("/")
    tam_url = f"{url}/storage/v1/object/authenticated/{FOTO_BUCKET}/{quote(foto_yolu, safe='/')}"
    try:
        yanit = requests.get(tam_url, headers=storage_basliklari(), timeout=12)
        if yanit.status_code == 200 and yanit.headers.get("content-type", "").startswith("image/"):
            st.image(yanit.content, width=genislik)
        else:
            st.caption("Ürün fotoğrafı görüntülenemedi.")
    except requests.RequestException:
        st.caption("Fotoğraf bağlantısı kurulamadı.")


# PostgreSQL kimlik bilgileri yalnızca Streamlit Secrets içinde tutulur.
conn = st.connection("postgresql", type="sql")

# Her istek / yeniden çizimde JWT'yi Supabase Auth üzerinden doğruluyoruz.
def kimligi_dogrula():
    token = st.session_state.get("auth_access_token")
    if not token:
        return None
    try:
        base = st.secrets["supabase_auth"]["url"].rstrip("/")
        anon = st.secrets["supabase_auth"]["anon_key"]
        r = requests.get(f"{base}/auth/v1/user", headers={"apikey": anon, "Authorization": f"Bearer {token}"}, timeout=8)
        if r.status_code != 200:
            return None
        profil = r.json()
        uid = profil.get("id")
        if not uid:
            return None
        with conn.session as db:
            rol = db.execute(text("SELECT rol FROM public.personel_yetkileri WHERE user_id = :u AND aktif = TRUE"), {"u": uid}).scalar_one_or_none()
        if rol not in ("admin", "personel"):
            return None
        return {"id": uid, "email": profil.get("email", ""), "rol": rol}
    except Exception:
        return None


def oturum_ac(email, parola):
    base = st.secrets["supabase_auth"]["url"].rstrip("/")
    anon = st.secrets["supabase_auth"]["anon_key"]
    res = requests.post(f"{base}/auth/v1/token?grant_type=password", headers={"apikey": anon, "Content-Type": "application/json"}, json={"email": email, "password": parola}, timeout=12)
    if res.status_code != 200:
        raise ValueError("E-posta veya şifre hatalı ya da hesap doğrulanmamış.")
    st.session_state.auth_access_token = res.json()["access_token"]


st.markdown("""<style>
.block-container{padding-top:1.4rem;max-width:1250px}
[data-testid=stSidebar]{min-width:245px}
.stButton>button{min-height:42px;border-radius:9px}
@media(max-width:640px){.block-container{padding-left:.8rem;padding-right:.8rem}}
</style>""", unsafe_allow_html=True)

if "auth_access_token" not in st.session_state:
    st.session_state.auth_access_token = None
kullanici = kimligi_dogrula()
if kullanici is None:
    st.title("⚡ Sinyal Elektrik")
    st.subheader("Ürün ve Stok Yönetimi | Personel Girişi")
    st.info("Yetkili hesabınızla giriş yapın. İlk hesaplar Supabase panelinden yönetici tarafından oluşturulur.")
    with st.form("giris_formu"):
        email = st.text_input("E-posta adresi")
        password = st.text_input("Şifre", type="password")
        login = st.form_submit_button("Giriş Yap", use_container_width=True, type="primary")
    if login:
        try:
            oturum_ac(email.strip(), password)
            if kimligi_dogrula() is None:
                st.session_state.auth_access_token = None
                st.error("Bu hesap için aktif personel yetkisi tanımlanmamış. Yöneticiye başvurun.")
            else:
                st.rerun()
        except Exception as exc:
            st.error(f"Giriş yapılamadı: {exc}")
    st.stop()

is_admin = kullanici["rol"] == "admin"
with st.sidebar:
    st.title("⚡ Sinyal Elektrik")
    st.caption(f"{kullanici['email']} | {'Yönetici' if is_admin else 'Personel'}")
    menu_items = ["🏠 Ana Sayfa", "🛒 Barkodlu Satış", "📦 Stok ve Fiyat Sorgula", "➕ Stok Girişi", "➖ Stok Çıkışı", "↩️ Satış İptali", "✏️ Ürün Düzenle/Sil"]
    if is_admin:
        menu_items += ["📊 Satış Raporları", "📖 Katalog", "📥 Katalog Yükle", "🆕 Ürün Ekle"]
    menu = st.radio("İşlemler", menu_items, label_visibility="collapsed")
    if st.button("Güvenli Çıkış", use_container_width=True):
        st.session_state.auth_access_token = None
        st.session_state.pop("satis_sepeti", None)
        st.session_state.pop("satis_islem_no", None)
        st.rerun()

# Veri Çekme Fonksiyonları
def get_dukkan_data():
    try:
        sorgu = "SELECT * FROM urunler ORDER BY id DESC;" if is_admin else "SELECT id, barkod, urun_adi, raf_konumu, stok_adedi, liste_fiyati, para_birimi, tedarikci_iskonto, kar_marji, kdv_orani, foto_yolu FROM urunler ORDER BY id DESC;"
        return conn.query(sorgu, ttl="0s")
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

st.title("⚡ Sinyal Elektrik | " + menu.split(" ", 1)[1])
if is_admin:
    with st.expander("Döviz Kurları", expanded=False):
        col_usd, col_eur, col_refresh = st.columns(3)
        col_usd.metric("USD/TRY", f"₺{kurlar['USD']}")
        col_eur.metric("EUR/TRY", f"₺{kurlar['EUR']}")
        if col_refresh.button("Kurları Yenile"):
            st.session_state.kurlar = get_live_rates()
            st.session_state.son_kur_guncelleme = time.time()
            st.rerun()

if menu == "🏠 Ana Sayfa":
    st.subheader("Hızlı Başlangıç")
    with conn.session as db:
        urun_sayisi, toplam_stok = db.execute(text("SELECT COUNT(*), COALESCE(SUM(stok_adedi),0) FROM urunler")).one()
    c1, c2 = st.columns(2)
    c1.metric("Ürün çeşidi", int(urun_sayisi))
    c2.metric("Toplam stok (birim)", int(toplam_stok))
    st.info("Soldaki menüden Barkodlu Satış, Stok Sorgulama, Stok Girişi veya Stok Çıkışı'nı seçin. Mobil cihazlarda menüyü sol üstten açabilirsiniz.")
    if is_admin:
        st.caption("Yönetici panelinde maliyetler, kataloglar, satış raporları ve personel yetkileri yönetilir.")

# ----------------------------------------------------
# 1. SEKME: KATALOG LİSTESİ & ARAMA
# ----------------------------------------------------
if is_admin and menu == "📖 Katalog":
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
if is_admin and menu == "📥 Katalog Yükle":
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
if menu == "📦 Stok ve Fiyat Sorgula":
    st.subheader("Dükkan Stok & Satış Fiyatları")
    df = get_dukkan_data()
    ham_urunler = df.copy()
    camera_value = kamera_barkod("stok_sorgula")
    if camera_value is not None:
        st.session_state["stok_arama_kodu"] = camera_value

    if not is_admin and not df.empty:
        # Hesaplama sunucu tarafında yapılır; maliyet ve kâr kolonları kullanıcıya iletilmez.
        def gorunen_fiyat(r):
            try:
                liste = Decimal(str(r["liste_fiyati"]))
                kur = Decimal(str(kurlar.get(str(r["para_birimi"]), 1)))
                isk = Decimal(str(r["tedarikci_iskonto"]))
                kar = Decimal(str(r["kar_marji"]))
                kdv = Decimal(str(r["kdv_orani"]))
                return float((liste * (1-isk/100) * kur * (1+kar/100) * (1+kdv/100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            except Exception:
                return None
        df["Satış Fiyatı (TL)"] = df.apply(gorunen_fiyat, axis=1)
        df = df.rename(columns={"barkod": "Barkod", "urun_adi": "Ürün Adı", "raf_konumu": "Raf", "stok_adedi": "Stok"})
        filtre = st.text_input("Ürün adı veya barkod ara", key="stok_arama_kodu")
        if filtre:
            df = df[df["Ürün Adı"].astype(str).str.contains(filtre, case=False, na=False, regex=False) |
                    df["Barkod"].astype(str).str.contains(filtre, case=False, na=False, regex=False)]
        st.dataframe(df[["Barkod", "Ürün Adı", "Raf", "Stok", "Satış Fiyatı (TL)"]], hide_index=True, use_container_width=True)
        if len(df) == 1 and filtre.strip():
            foto_goster(df.iloc[0].get("foto_yolu"))
    elif not is_admin:
        st.info("Ürün kaydı bulunmuyor.")
    elif not df.empty and "urun_adi" in df.columns:
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

        arama = st.text_input("🔍 Dükkan Ürünlerinde Arama Yapın", key="stok_arama_kodu")
        if arama:
            gosterilecek_tablo = gosterilecek_tablo[
                gosterilecek_tablo["Ürün Adı"].astype(str).str.contains(arama, case=False, na=False) |
                gosterilecek_tablo["Barkod/Kod"].astype(str).str.contains(arama, case=False, na=False) |
                gosterilecek_tablo["Dükkan / Raf Konumu"].astype(str).str.contains(arama, case=False, na=False)
            ]

        st.dataframe(gosterilecek_tablo, use_container_width=True, hide_index=True)
        if len(gosterilecek_tablo) == 1 and arama.strip():
            foto_goster(df.loc[df["id"] == int(gosterilecek_tablo.iloc[0]["ID"])].iloc[0].get("foto_yolu"))

        csv_data = gosterilecek_tablo.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 Dükkan Listesini İndir (CSV)",
            data=csv_data,
            file_name="sinyal_elektrik_dukkan_stok.csv",
            mime="text/csv"
        )
    else:
        st.info("Dükkanda henüz kayıtlı ürün yok.")
    if not df.empty and camera_value:
        barkod_kaynak = ham_urunler
        gorsel = barkod_kaynak[barkod_kaynak["barkod"].astype(str).str.strip() == camera_value]
        if len(gorsel) == 1:
            foto_goster(gorsel.iloc[0].get("foto_yolu"))

    # Her kullanıcının yalnızca satış fiyatını görebileceği resimli liste.
    # Ham kayıtlar kartlara yalnızca sunucu tarafında işlenmek üzere aktarılır.
    if not ham_urunler.empty:
        filtre_kodu = st.session_state.get("stok_arama_kodu", "").strip()
        gorunen_urunler = ham_urunler
        if filtre_kodu:
            maske = (gorunen_urunler["urun_adi"].astype(str).str.contains(filtre_kodu, case=False, na=False, regex=False) |
                     gorunen_urunler["barkod"].astype(str).str.contains(filtre_kodu, case=False, na=False, regex=False) |
                     gorunen_urunler["raf_konumu"].astype(str).str.contains(filtre_kodu, case=False, na=False, regex=False))
            gorunen_urunler = gorunen_urunler.loc[maske]
        fotograflı_kartlar(gorunen_urunler, kurlar)

# ----------------------------------------------------
# 4. SEKME: DÜKKANA ÜRÜN EKLE
# ----------------------------------------------------
if is_admin and menu == "🆕 Ürün Ekle":
    st.subheader("Dükkana Yeni Stok / Ürün Kaydı")
    kamera_kod = kamera_barkod("yeni_urun")
    if kamera_kod is not None:
        st.session_state["yeni_barkod"] = kamera_kod
    yeni_foto = urun_fotograf_sec("yeni_urun")
    with st.form("yeni_dukkan_urun_form", clear_on_submit=True):
        col_a, col_b = st.columns(2)
        barkod = col_a.text_input("Barkod / Stok Kodu", key="yeni_barkod")
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
                    yeni_id = s.execute(
                        text("""
                            INSERT INTO urunler (barkod, urun_adi, raf_konumu, liste_fiyati, para_birimi, tedarikci_iskonto, kar_marji, kdv_orani, stok_adedi)
                            VALUES (:barkod, :urun_adi, :raf_konumu, :liste_fiyati, :para_birimi, :tedarikci_iskonto, :kar_marji, :kdv_orani, :stok_adedi) RETURNING id
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
                    yeni_id = yeni_id.scalar_one()
                    s.commit()
                if yeni_foto is not None:
                    try:
                        yeni_yol = foto_yukle(yeni_foto, yeni_id)
                        with conn.session as s:
                            s.execute(text("UPDATE urunler SET foto_yolu=:f WHERE id=:i"), {"f":yeni_yol,"i":int(yeni_id)})
                            s.commit()
                    except Exception as exc:
                        st.warning(f"Ürün kaydedildi ama fotoğraf yüklenemedi: {exc}")
                st.success(f"'{urun_adi}' dükkan stoğuna başarıyla eklendi!")
                st.rerun()
            else:
                st.error("Lütfen ürün adını girin.")

# ----------------------------------------------------
# 5. SEKME: DÜKKAN ÜRÜN DÜZENLE / SİL
# ----------------------------------------------------
if menu == "✏️ Ürün Düzenle/Sil":
    st.subheader("Dükkan Ürününü Düzenle" + (" veya Sil" if is_admin else ""))
    if not is_admin:
        st.info("Personel ürün adı, barkod ve raf konumunu düzenleyebilir. Maliyet, iskonto, kâr, fiyat ve stok miktarı sadece yönetici tarafından değiştirilebilir. Stok eklemek için Stok Girişi menüsünü kullanın.")
    df_duzenle = get_dukkan_data()

    if not df_duzenle.empty and "urun_adi" in df_duzenle.columns:
        secenekler = {f"{row['urun_adi']} (ID: {row['id']} | Raf: {row.get('raf_konumu', '')})": row["id"] for _, row in df_duzenle.iterrows()}
        secilen_etiket = st.selectbox("Düzenlenecek Ürünü Seçin:", list(secenekler.keys()))
        secilen_id = secenekler[secilen_etiket]
        secilen_urun = df_duzenle[df_duzenle["id"] == secilen_id].iloc[0]
        foto_goster(secilen_urun.get("foto_yolu"))
        degisen_foto = urun_fotograf_sec(f"duzenle_{secilen_id}")
        with st.form("duzenle_form"):
            col_d1, col_d2 = st.columns(2)
            d_barkod = col_d1.text_input("Barkod / Stok Kodu", value=str(secilen_urun.get("barkod", "") or ""))
            d_urun_adi = col_d2.text_input("Ürün Adı", value=str(secilen_urun.get("urun_adi", "") or ""))
            d_raf = st.text_input("Dükkan Konumu / Raf", value=str(secilen_urun.get("raf_konumu", "") or ""))

            if is_admin:
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

            if is_admin:
                col_btn1, col_btn2 = st.columns(2)
                guncelle_btn = col_btn1.form_submit_button("💾 Değişiklikleri Kaydet", use_container_width=True)
                sil_btn = col_btn2.form_submit_button("🗑️ Ürünü Sil", use_container_width=True)
            else:
                guncelle_btn = st.form_submit_button("💾 Ürün Bilgilerini Kaydet", use_container_width=True)
                sil_btn = False

            if guncelle_btn:
                if not d_urun_adi.strip():
                    st.error("Ürün adı boş olamaz.")
                else:
                    try:
                        with conn.session as db:
                            # Sunucu tarafı ayrım: personelin SQL ifadesine asla fiyat/maliyet sütunları eklenmez.
                            if is_admin:
                                db.execute(text("""UPDATE urunler SET barkod=:barkod, urun_adi=:ad, raf_konumu=:raf,
                                    liste_fiyati=:liste, para_birimi=:para, tedarikci_iskonto=:iskonto,
                                    kar_marji=:kar, kdv_orani=:kdv, stok_adedi=:stok WHERE id=:id"""),
                                    {"barkod":d_barkod.strip(), "ad":d_urun_adi.strip(), "raf":d_raf.strip(),
                                     "liste":float(d_liste), "para":d_para, "iskonto":float(d_iskonto),
                                     "kar":float(d_kar), "kdv":float(d_kdv), "stok":int(d_stok), "id":int(secilen_id)})
                            else:
                                db.execute(text("""UPDATE urunler SET barkod=:barkod, urun_adi=:ad,
                                    raf_konumu=:raf WHERE id=:id"""),
                                    {"barkod":d_barkod.strip(), "ad":d_urun_adi.strip(), "raf":d_raf.strip(), "id":int(secilen_id)})
                            db.commit()
                        if degisen_foto is not None:
                            yeni_yol = foto_yukle(degisen_foto, int(secilen_id))
                            with conn.session as db:
                                db.execute(text("UPDATE urunler SET foto_yolu=:f WHERE id=:id"), {"f":yeni_yol,"id":int(secilen_id)})
                                db.commit()
                        st.success("Ürün bilgileri güncellendi.")
                        st.rerun()
                    except Exception as exc:
                        st.error(f"Güncelleme başarısız: {exc}")

            if sil_btn and is_admin:
                try:
                    with conn.session as db:
                        db.execute(text("DELETE FROM urunler WHERE id = :id"), {"id":int(secilen_id)})
                        db.commit()
                    st.warning("Ürün silindi.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Silme başarısız: {exc}")
    else:
        st.info("Düzenlenecek dükkan ürünü bulunmuyor.")

# Yönetim işlemleri: stok girişini tek transaction ve satır kilidi ile uygula.
if menu in ("➕ Stok Girişi", "➖ Stok Çıkışı"):
    cikis = menu == "➖ Stok Çıkışı"
   if cikis:
    st.caption(
        "Stok çıkışı; fire, sayım düzeltmesi gibi satış harici işlemler içindir. "
        "Satış için Barkodlu Satış menüsünü kullanın."
    )
    st.caption("Stok çıkışı; fire, sayım düzeltmesi gibi satış harici işlemler içindir. Satış için Barkodlu Satış menüsünü kullanın.") if cikis else None
    kamera_kod = kamera_barkod("stok_hareket")
    if kamera_kod is not None:
        st.session_state["stok_islem_barkod"] = kamera_kod
    with st.form("stok_hareket_form", clear_on_submit=False):
        kod = st.text_input("Barkod / Stok Kodu", key="stok_islem_barkod")
        adet = st.number_input("Miktar", min_value=1, step=1, value=1)
        notlar = st.text_input("İşlem nedeni / açıklama", key="hareket_notu")
        onay = st.checkbox("Stoktan düşümü onaylıyorum") if cikis else True
        kaydet = st.form_submit_button("Stoktan Çıkar" if cikis else "Stoğa Ekle", use_container_width=True)
    if kaydet:
        if cikis and (not onay or not notlar.strip()):
            st.error("Çıkış için neden yazılması ve onay verilmesi gerekir.")
        else:
            try:
                with conn.session as db:
                    bulunan = db.execute(text("SELECT id, urun_adi, stok_adedi, foto_yolu FROM urunler WHERE BTRIM(barkod)=:b FOR UPDATE"), {"b":kod.strip()}).mappings().all()
                    if len(bulunan) != 1:
                        raise ValueError("Barkod bulunamadı veya birden fazla ürüne kayıtlı.")
                    u = bulunan[0]
                    if cikis:
                        sonuc = db.execute(text("UPDATE urunler SET stok_adedi=stok_adedi-:a WHERE id=:id AND stok_adedi>=:a"), {"a":int(adet),"id":u["id"]})
                        if sonuc.rowcount != 1:
                            raise ValueError("Yetersiz stok. İşlem kaydedilmedi.")
                    else:
                        db.execute(text("UPDATE urunler SET stok_adedi=stok_adedi+:a WHERE id=:id"), {"a":int(adet),"id":u["id"]})
                    db.execute(text("INSERT INTO public.stok_hareketleri (urun_id, miktar, islem_tipi, aciklama, yapan_user_id) VALUES (:id,:a,:t,:n,:uid)"), {"id":u["id"],"a":int(adet),"t":"cikis" if cikis else "giris","n":notlar.strip(),"uid":kullanici["id"]})
                    db.commit()
                st.success(f"{u['urun_adi']}: {'-' if cikis else '+'}{adet} adet işlendi.")
                foto_goster(u.get("foto_yolu"))
            except Exception as exc:
                st.error(f"Stok işlemi başarısız: {exc}")

if is_admin and menu == "📊 Satış Raporları":
    st.subheader("Satış Raporları")
    with conn.session as db:
        rows = db.execute(text("SELECT id, olusturma_zamani, toplam_tl, durum, olusturan_user_id FROM public.satislar ORDER BY id DESC LIMIT 200")).mappings().all()
    if rows:
        df_report = pd.DataFrame([dict(r) for r in rows])
        st.dataframe(df_report, hide_index=True, use_container_width=True)
        st.metric("Aktif satış toplamı (son 200 kayıt)", f"{df_report.loc[df_report.durum=='tamamlandi','toplam_tl'].sum():,.2f} TL")
    else:
        st.info("Henüz satış bulunmuyor.")

if menu == "↩️ Satış İptali":
    st.subheader("Satış İptali ve Stok İadesi")
    with conn.session as db:
        rows = db.execute(text("SELECT id, olusturma_zamani, toplam_tl FROM satislar WHERE durum='tamamlandi' ORDER BY id DESC LIMIT 100")).mappings().all()
    if not rows:
        st.info("İptal edilebilecek satış yok.")
    else:
        selection = st.selectbox("İptal edilecek satış", [int(r["id"]) for r in rows], format_func=lambda i: f"Satış #{i}")
        with conn.session as db:
            kalem = db.execute(text("SELECT urun_adi, miktar FROM satis_kalemleri WHERE satis_id=:s"), {"s":selection}).mappings().all()
        st.dataframe(pd.DataFrame([dict(k) for k in kalem]), hide_index=True, use_container_width=True)
        if not is_admin:
            st.caption("Personel yalnızca satışın ürünlerini ve adetlerini görür; toplam ciro ve maliyet bilgileri gösterilmez.")
        with st.form("iptal_form"):
            sebep = st.text_input("İptal nedeni")
            onay = st.checkbox("Bu satışın tamamını iptal edip stoklarını geri eklemek istiyorum.")
            iptal = st.form_submit_button("Satışı İptal Et ve Stokları İade Et", type="primary")
        if iptal:
            if not onay or not sebep.strip():
                st.error("İptal nedeni ve onay gerekli.")
            else:
                try:
                    with conn.session as db:
                        kilit = db.execute(text("SELECT durum FROM satislar WHERE id=:id FOR UPDATE"), {"id":selection}).scalar_one()
                        if kilit != 'tamamlandi':
                            raise ValueError("Satış zaten iptal edilmiş.")
                        items = db.execute(text("SELECT urun_id, miktar FROM satis_kalemleri WHERE satis_id=:id ORDER BY urun_id"), {"id":selection}).mappings().all()
                        for item in items:
                            updated = db.execute(text("UPDATE urunler SET stok_adedi=stok_adedi+:n WHERE id=:uid"), {"n":item["miktar"],"uid":item["urun_id"]})
                            if updated.rowcount != 1:
                                raise ValueError("İade edilecek ürün silinmiş. İşlem geri alındı.")
                            db.execute(text("INSERT INTO stok_hareketleri(urun_id,miktar,islem_tipi,aciklama,yapan_user_id,satis_id) VALUES (:u,:n,'iade',:a,:actor,:sale)"), {"u":item["urun_id"],"n":item["miktar"],"a":sebep,"actor":kullanici["id"],"sale":selection})
                        db.execute(text("UPDATE satislar SET durum='iptal', iptal_nedeni=:neden, iptal_eden_user_id=:actor, iptal_zamani=NOW() WHERE id=:id"), {"neden":sebep,"actor":kullanici["id"],"id":selection})
                        db.commit()
                    st.success(f"Satış #{selection} iptal edildi, ürünler stoğa iade edildi.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"İptal başarısız: {exc}")

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

if menu == "🛒 Barkodlu Satış":
    st.subheader("🛒 Barkodla Fiyat Sorgula / Satış Yap")
    if st.session_state.get("satis_basarili_mesaj"):
        st.success(st.session_state.pop("satis_basarili_mesaj"))
    st.caption("Barkod okuyucuyla okutun veya barkodu elle girin. Sorgulama stoktan düşmez; stok sadece satış onayında düşer.")
    kamera_kod = kamera_barkod("satis")
    if kamera_kod is not None:
        st.session_state["satis_kodu"] = kamera_kod
    with st.form("barkod_okut_form", clear_on_submit=False):
        barkod_sorgu = st.text_input("Barkod / Stok Kodu", placeholder="Barkodu okutun veya kamerayla fotoğraf çekin", key="satis_kodu")
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
                    foto_goster(urun.get("foto_yolu"))
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
                            text("INSERT INTO satislar (islem_no, toplam_tl, olusturan_user_id) VALUES (:islem_no, :toplam, :uid) RETURNING id"),
                            {"islem_no": islem_no, "toplam": satis_toplami, "uid": kullanici["id"]},
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

    if is_admin:
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
