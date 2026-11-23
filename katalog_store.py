"""Katalog belgelerini PostgreSQL'de saklar; stok tablolarına yazmaz."""
import hashlib
import json
import uuid
from sqlalchemy import text
from katalog_core import validate_rows, merge_catalogue

SCHEMA = """CREATE TABLE IF NOT EXISTS public.tedarikci_katalog_belgeleri (
 id uuid PRIMARY KEY,
 marka text NOT NULL,
 dosya_adi text NOT NULL,
 dosya_ozeti text NOT NULL,
 pdf bytea,
 urunler jsonb NOT NULL DEFAULT '[]'::jsonb,
 sayfa_sayisi integer NOT NULL DEFAULT 0,
 olusturan_user_id uuid NOT NULL,
 olusturma_zamani timestamptz NOT NULL DEFAULT now(),
 UNIQUE(marka, dosya_ozeti),
 CHECK (pdf IS NULL OR octet_length(pdf) <= 20971520)
)"""


def exists(db):
    return db.execute(text("SELECT to_regclass('public.tedarikci_katalog_belgeleri')")).scalar() is not None


def authorize(db, user, admin=False):
    role = db.execute(text("SELECT rol FROM public.personel_yetkileri WHERE user_id=:u AND aktif=TRUE"),
                      {"u": user["id"]}).scalar_one_or_none()
    if role not in ("admin", "personel") or (admin and role != "admin"):
        raise PermissionError("Bu işlem için yetkiniz bulunmuyor.")


def load_catalogue(conn, user):
    documents, records = [], []
    with conn.session as db:
        authorize(db, user)
        if exists(db):
            docs = db.execute(text("""SELECT id, marka, dosya_adi, urunler, sayfa_sayisi,
                pdf IS NOT NULL AS pdf_var, olusturma_zamani
                FROM public.tedarikci_katalog_belgeleri ORDER BY olusturma_zamani DESC, id""")).mappings().all()
            for d in docs:
                d = dict(d)
                items = d.pop("urunler")
                if isinstance(items, str):
                    items = json.loads(items)
                d["id"] = str(d["id"])
                documents.append(d)
                for i, row in enumerate(items):
                    records.append({**row, "id": f"{d['id']}:{i}", "belge_id": d["id"],
                                    "dosya_adi": d["dosya_adi"], "pdf_var": d["pdf_var"]})
        if db.execute(text("SELECT to_regclass('public.katalog_urunleri')")).scalar() is not None:
            legacy = db.execute(text("SELECT * FROM public.katalog_urunleri ORDER BY id DESC")).mappings().all()
            records.extend({**dict(r), "id": f"eski:{r['id']}", "belge_id": "",
                            "dosya_adi": "Önceki katalog kayıtları", "sayfa": 0, "pdf_var": False} for r in legacy)
    return documents, records, merge_catalogue(records)


def load_pdf(conn, user, doc_id):
    with conn.session as db:
        authorize(db, user)
        raw = db.execute(text("SELECT pdf FROM public.tedarikci_katalog_belgeleri WHERE id=:id"),
                         {"id": doc_id}).scalar_one_or_none()
    return bytes(raw) if raw is not None else None


def save_document(conn, user, marka, filename, rows, pdf=None, pages=0):
    marka = marka.strip()
    if not marka:
        raise ValueError("Tedarikçi / marka adı gerekli.")
    if pdf:
        from katalog_pdf import inspect_pdf
        pages = inspect_pdf(pdf)
    valid, errors = validate_rows(rows, pages if pdf else None)
    if errors:
        raise ValueError("\n".join(errors[:10]))
    if not valid and pdf is None:
        raise ValueError("Kaydedilecek ürün bulunmuyor.")
    if any(r["marka"] != marka for r in valid):
        raise ValueError("Bir yüklemedeki ürünlerin tedarikçi/markası aynı olmalı.")
    payload = json.dumps(valid, ensure_ascii=False, sort_keys=True)
    # Aynı PDF'nin farklı sayfaları ayrı, izlenebilir aktarımlar olarak eklenebilir.
    fingerprint = hashlib.sha256((pdf or b"") + payload.encode()).hexdigest()
    doc_id = str(uuid.uuid4())
    with conn.session as db:
        authorize(db, user, admin=True)
        if not exists(db):
            db.execute(text(SCHEMA))
            db.execute(text("ALTER TABLE public.tedarikci_katalog_belgeleri ENABLE ROW LEVEL SECURITY"))
            db.execute(text("REVOKE ALL ON public.tedarikci_katalog_belgeleri FROM PUBLIC, anon, authenticated"))
        saved = db.execute(text("""INSERT INTO public.tedarikci_katalog_belgeleri
            (id, marka, dosya_adi, dosya_ozeti, pdf, urunler, sayfa_sayisi, olusturan_user_id)
            VALUES (:id,:m,:f,:h,:p,CAST(:rows AS jsonb),:n,:u)
            ON CONFLICT (marka, dosya_ozeti) DO NOTHING RETURNING id"""),
            {"id": doc_id, "m": marka, "f": filename, "h": fingerprint, "p": pdf,
             "rows": payload, "n": pages, "u": user["id"]}).scalar_one_or_none()
        if saved is None:
            raise ValueError("Bu katalog ve seçilen ürünler daha önce kaydedilmiş; tekrar eklenmedi.")
        db.commit()
    return doc_id, len(valid)
