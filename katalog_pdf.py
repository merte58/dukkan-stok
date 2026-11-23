"""PDF'den kontrol edilecek ürün adayları çıkarır; doğrudan kayıt yapmaz."""
from io import BytesIO
import re
import pdfplumber
import fitz
from katalog_core import clean, normalize, number, currency

MAX_BYTES = 20 * 1024 * 1024
MAX_PAGES = 250
ALIASES = {
    "urun_kodu": {"urun kodu", "stok kodu", "kod", "model", "product code", "code", "barkod"},
    "urun_adi": {"urun adi", "urun", "aciklama", "urun aciklamasi", "malzeme", "description", "product", "product name"},
    "liste_fiyati": {"liste fiyati", "birim fiyat", "birim fiyati", "fiyat", "price", "list price", "liste fiyat"},
    "para_birimi": {"para birimi", "doviz", "currency"},
    "marka": {"marka", "tedarikci", "brand"},
}


def identify_columns(headers):
    mapping = {}
    for i, header in enumerate(headers):
        h = normalize(header)
        # Birim fiyat başlığındaki para sembolleri ayrı bir sütun değildir.
        h = re.sub(r"\b(tl|try|usd|eur)\b", "", h).strip()
        for key, aliases in ALIASES.items():
            if h in aliases and key not in mapping:
                mapping[key] = i
    return mapping


def inspect_pdf(data):
    if len(data) > MAX_BYTES or not data.startswith(b"%PDF-"):
        raise ValueError("Geçerli bir PDF seçin (en fazla 20 MB).")
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            if doc.needs_pass:
                raise ValueError("Şifre korumalı PDF yüklenemiyor.")
            if not 1 <= len(doc) <= MAX_PAGES:
                raise ValueError(f"PDF en fazla {MAX_PAGES} sayfa olabilir.")
            return len(doc)
    except (RuntimeError, fitz.FileDataError) as exc:
        raise ValueError("PDF açılamadı; dosyayı kontrol edin.") from exc


def render_page(data, page):
    with fitz.open(stream=data, filetype="pdf") as doc:
        p = doc[max(0, min(int(page) - 1, len(doc) - 1))]
        scale = min(1.6, 1500 / max(p.rect.width, p.rect.height))
        return p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).tobytes("png")


def map_table(rows, mapping, marka, birim, page, skip=0):
    products = []
    for values in rows[skip:]:
        def get(key):
            i = mapping.get(key)
            return clean(values[i]) if i is not None and i < len(values) else ""
        if not any(clean(x) for x in values):
            continue
        products.append({"aktar": True, "marka": get("marka") or marka,
                         "urun_kodu": get("urun_kodu"), "urun_adi": get("urun_adi"),
                         "liste_fiyati": get("liste_fiyati"),
                         "para_birimi": currency(get("para_birimi"), birim), "sayfa": page})
    return products


def extract_candidates(data, marka, birim="TRY", first=1, last=None):
    total = inspect_pdf(data)
    first, last = max(1, int(first)), min(int(last or total), total)
    if last < first or last - first >= 50:
        raise ValueError("Bir işlemde en fazla 50 sayfa tarayın.")
    candidates, tables, empty_pages, text_pages = [], [], [], []
    with pdfplumber.open(BytesIO(data)) as doc:
        for page_no in range(first, last + 1):
            page = doc.pages[page_no - 1]
            text = page.extract_text() or ""
            text_pages.append({"sayfa": page_no, "metin": text})
            if not text.strip():
                empty_pages.append(page_no)
            recognized = False
            for table in page.extract_tables():
                table = [[clean(c) for c in row] for row in table if any(clean(c) for c in row)]
                if not table:
                    continue
                tables.append({"sayfa": page_no, "satirlar": table})
                for header_no, header in enumerate(table[:5]):
                    mapping = identify_columns(header)
                    if {"urun_kodu", "urun_adi", "liste_fiyati"} <= set(mapping):
                        products = map_table(table, mapping, marka, birim, page_no, header_no + 1)
                        for p in products:
                            try:
                                p["liste_fiyati"] = str(number(p["liste_fiyati"]))
                            except ValueError:
                                p["aktar"] = False
                        candidates.extend(products)
                        recognized = True
                        break
            if not recognized:
                # Yalnızca satır sonunda açıkça para birimi olan adaylar.
                # Güç, ölçü, ambalaj adedi fiyat olarak tahmin edilmez.
                pattern = r"^\s*(\S+)\s+(.+?)\s+([\d][\d.,]*)\s*(TL|TRY|USD|EUR|₺|\$|€)\s*$"
                for line in text.splitlines():
                    match = re.match(pattern, line, re.I)
                    if match:
                        code, name, price, money = match.groups()
                        if not any(c.isdigit() for c in code):
                            continue
                        try:
                            parsed = str(number(price))
                        except ValueError:
                            continue
                        candidates.append({"aktar": True, "marka": marka, "urun_kodu": code,
                                           "urun_adi": name, "liste_fiyati": parsed,
                                           "para_birimi": currency(money), "sayfa": page_no})
    return {"urunler": candidates, "tablolar": tables, "metinler": text_pages,
            "bos_sayfalar": empty_pages, "sayfa_sayisi": total}
