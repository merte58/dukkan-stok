"""Katalog fiyat hesabı ve doğrulama. Veritabanı/arayüz bağımlılığı yoktur."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import math
import re
import unicodedata

FIELDS = ["marka", "urun_kodu", "urun_adi", "liste_fiyati", "para_birimi", "sayfa"]


def clean(value):
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def normalize(value):
    value = clean(value).translate(str.maketrans("ıİ", "iI")).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()).split())


def number(value):
    """TR/EN fiyat yazımı; 1.234 / 1,234 binlik, 12.34 / 12,34 ondalıktır."""
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        result = Decimal(str(value))
    else:
        s = re.sub(r"(?i)(TRY|TL|USD|EUR|₺|\$|€)", "", clean(value))
        s = re.sub(r"\s+", "", s)
        if not re.fullmatch(r"[+-]?\d+(?:[.,]\d+)*", s):
            raise ValueError("Geçerli bir fiyat yazın.")
        if "," in s and "." in s:
            decimal_sep = "," if s.rfind(",") > s.rfind(".") else "."
            grouping = "." if decimal_sep == "," else ","
            integer, decimals = s.rsplit(decimal_sep, 1)
            if not re.fullmatch(r"[+-]?\d{1,3}(?:" + re.escape(grouping) + r"\d{3})+", integer):
                raise ValueError("Binlik ayırıcıları kontrol edin.")
            s = integer.replace(grouping, "") + "." + decimals
        elif "," in s or "." in s:
            sep = "," if "," in s else "."
            if re.fullmatch(r"[+-]?\d{1,3}(?:" + re.escape(sep) + r"\d{3})+", s):
                s = s.replace(sep, "")
            elif s.count(sep) == 1:
                s = s.replace(sep, ".")
            else:
                raise ValueError("Fiyatın ayırıcıları geçersiz.")
        try:
            result = Decimal(s)
        except InvalidOperation as exc:
            raise ValueError("Fiyat okunamadı.") from exc
    if not result.is_finite():
        raise ValueError("Fiyat sonlu bir sayı olmalı.")
    return result


def currency(value, default="TRY"):
    s = clean(value).upper()
    return {"TL": "TRY", "₺": "TRY", "$": "USD", "€": "EUR"}.get(s, s or default)


def calculate_price(liste, iskonto=0, ek_iskonto=0, kar=0, kdv=20, kur=1, miktar=1, kdv_dahil=False):
    values = [Decimal(str(x)) for x in (liste, iskonto, ek_iskonto, kar, kdv, kur, miktar)]
    if not all(x.is_finite() for x in values):
        raise ValueError("Hesap değerleri sonlu sayılar olmalı.")
    liste, iskonto, ek_iskonto, kar, kdv, kur, miktar = values
    if liste < 0 or not 0 <= iskonto <= 100 or not 0 <= ek_iskonto <= 100:
        raise ValueError("Fiyat negatif olamaz; iskontolar 0–100 arasında olmalı.")
    if kar < 0 or not 0 <= kdv <= 100 or kur <= 0 or miktar <= 0:
        raise ValueError("Kâr/KDV, kur veya miktar geçersiz.")
    net_liste = liste / (1 + kdv / 100) if kdv_dahil else liste
    maliyet = net_liste * (1 - iskonto / 100) * (1 - ek_iskonto / 100) * kur
    net = maliyet * (1 + kar / 100)
    q = lambda v: v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    brut = q(net * (1 + kdv / 100))
    return {"maliyet": q(maliyet), "net": q(net), "brut": brut,
            "toplam": q(brut * miktar), "kar_tutari": q(net - maliyet)}


def validate_rows(rows, max_page=None):
    valid, errors, seen = [], [], set()
    for index, raw in enumerate(rows, 1):
        if raw.get("aktar", True) is False:
            continue
        if not any(clean(raw.get(k)) for k in FIELDS[:5]):
            continue
        row = {k: clean(raw.get(k)) for k in FIELDS}
        try:
            if not row["marka"] or not row["urun_kodu"] or not row["urun_adi"]:
                raise ValueError("Tedarikçi/marka, ürün kodu ve ürün adı gerekli.")
            if any(len(row[k]) > 500 for k in ("marka", "urun_kodu", "urun_adi")):
                raise ValueError("Metin alanları en fazla 500 karakter olabilir.")
            fiyat = number(raw.get("liste_fiyati"))
            if fiyat < 0 or fiyat > Decimal("999999999999"):
                raise ValueError("Liste fiyatı 0–999999999999 arasında olmalı.")
            row["para_birimi"] = currency(row["para_birimi"])
            if row["para_birimi"] not in ("TRY", "USD", "EUR"):
                raise ValueError("Para birimi TRY, USD veya EUR olmalı.")
            page = Decimal(row["sayfa"] or "0")
            if not page.is_finite() or page != page.to_integral_value() or page < 0:
                raise ValueError("Sayfa numarası tam sayı olmalı.")
            if max_page is not None and not 1 <= page <= max_page:
                raise ValueError(f"PDF sayfası 1–{max_page} arasında olmalı.")
            row["sayfa"] = int(page)
            row["liste_fiyati"] = str(fiyat)
            ident = (normalize(row["marka"]), row["urun_kodu"].casefold())
            if ident in seen:
                raise ValueError("Aynı marka ve ürün kodu iki kez seçilmiş.")
            seen.add(ident)
            valid.append(row)
        except (ValueError, InvalidOperation) as exc:
            errors.append(f"Satır {index}: {exc}")
    return valid, errors


def merge_catalogue(records):
    """Yeni belgeler önce verilmelidir; aynı marka/kodun en güncel kaydı seçilir."""
    result, seen = [], set()
    for row in records:
        key = (normalize(row.get("marka")), clean(row.get("urun_kodu")).casefold())
        if not key[1]:
            key = (*key, clean(row.get("id")))
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result
