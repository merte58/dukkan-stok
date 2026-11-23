"""Tedarikçi kataloğu, PDF aktarımı ve ürün seçerek fiyat hesaplama."""
from io import BytesIO
import hashlib
import json
import pandas as pd
import streamlit as st
from katalog_core import FIELDS, calculate_price, clean, currency, normalize, number, validate_rows
from katalog_pdf import extract_candidates, identify_columns, inspect_pdf, map_table, render_page
from katalog_store import load_catalogue, load_pdf, save_document

LABELS = {"aktar": "Aktar", "marka": "Tedarikçi / marka", "urun_kodu": "Ürün kodu",
          "urun_adi": "Ürün adı", "liste_fiyati": "Liste fiyatı", "para_birimi": "Para birimi", "sayfa": "PDF sayfası"}


def money(value):
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " TL"


def select_product(row):
    st.session_state["kat_selected"] = row["id"]


def calculator(row, rates):
    key = "calc_" + hashlib.sha256(str(row["id"]).encode()).hexdigest()[:16]
    st.subheader(row["urun_adi"])
    st.caption(f"{row['marka']} · {row['urun_kodu']} · {row.get('dosya_adi', '')}")
    st.caption("Bu hesap kayıtlı stok fiyatını değiştirmez. Kâr, iskonto sonrası maliyetin üzerine eklenir.")
    try:
        initial_price = float(number(row["liste_fiyati"]))
        if initial_price < 0:
            raise ValueError("Negatif fiyat")
    except (ValueError, TypeError):
        st.error("Bu ürünün liste fiyatı geçersiz. Katalog kaydını kontrol edin.")
        return
    cols = st.columns(3)
    fiyat = cols[0].number_input("Liste fiyatı", min_value=0.0, value=initial_price, key=key+"_liste")
    birim = currency(row.get("para_birimi"))
    if birim not in ("TRY", "USD", "EUR"):
        st.error("Ürünün para birimi desteklenmiyor.")
        return
    cols[1].text_input("Para birimi", value=birim, disabled=True, key=key+"_birim")
    miktar = cols[2].number_input("Miktar", min_value=1.0, value=1.0, step=1.0, key=key+"_adet")
    cols = st.columns(3)
    isk = cols[0].number_input("İskonto (%)", min_value=0.0, max_value=100.0, value=0.0, key=key+"_isk")
    isk2 = cols[1].number_input("Ek iskonto (%)", min_value=0.0, max_value=100.0, value=0.0, key=key+"_isk2")
    kar = cols[2].number_input("Maliyet üzerine kâr (%)", min_value=0.0, value=25.0, key=key+"_kar")
    cols = st.columns(3)
    kdv = cols[0].number_input("KDV (%)", min_value=0.0, max_value=100.0, value=20.0, key=key+"_kdv")
    dahil = cols[1].selectbox("Katalog liste fiyatı", ["KDV hariç", "KDV dahil"], key=key+"_dahil") == "KDV dahil"
    live = rates.get(birim)
    kur = cols[2].number_input("1 " + birim + " = TL", min_value=0.0001,
                              value=float(live or 1), format="%.4f", disabled=birim == "TRY", key=key+"_kur")
    if birim != "TRY" and not live:
        st.warning("Güncel döviz kuru alınamadı. Hesap için kuru girip doğrulayın.")
    confirm = True
    if birim != "TRY":
        confirm = st.checkbox("Hesapta kullanılan döviz kurunu doğruladım", key=key+f"_kur_onay_{kur}")
        st.caption("Kuru elle değiştirebilirsiniz; seçili ürünün hesabında yukarıdaki kur kullanılır.")
    if not confirm:
        return
    try:
        result = calculate_price(fiyat, isk, isk2, kar, kdv, 1 if birim == "TRY" else kur, miktar, dahil)
    except ValueError as exc:
        st.error(str(exc))
        return
    cols = st.columns(3)
    cols[0].metric("Alış maliyeti · KDV hariç", money(result["maliyet"]))
    cols[1].metric("Birim satış · KDV hariç", money(result["net"]))
    cols[2].metric("Birim satış · KDV dahil", money(result["brut"]))
    st.metric("Toplam satış · KDV dahil", money(result["toplam"]))
    st.caption(f"Birim kâr (KDV hariç): {money(result['kar_tutari'])}. İskontolar toplanmaz, sırayla uygulanır.")
    quote = f"{row['urun_adi']} ({row['urun_kodu']})\nMiktar: {miktar:g}\nBirim fiyat (KDV dahil): {money(result['brut'])}\nToplam (KDV dahil): {money(result['toplam'])}"
    st.text_area("Müşteriye iletilecek fiyat", value=quote, height=130, key=key+"_quote_"+hashlib.sha256(quote.encode()).hexdigest()[:10])


def pdf_view(conn, user, doc, records, rates):
    data = load_pdf(conn, user, doc["id"])
    if data is None:
        st.info("Bu kayıt için PDF dosyası yok.")
        return
    st.download_button("PDF kataloğu indir", data, file_name=doc["dosya_adi"], mime="application/pdf", key="download_"+doc["id"])
    page = st.number_input("Katalog sayfası", min_value=1, max_value=int(doc["sayfa_sayisi"]), value=1, step=1, key="docpage_"+doc["id"])
    st.image(render_page(data, page), use_container_width=True)
    products = [r for r in records if r["belge_id"] == doc["id"] and int(r.get("sayfa", 0)) == page]
    st.markdown("#### Bu sayfadaki ürünler")
    if not products:
        st.info("Bu sayfadan aktarılmış ürün yok. Ürünler aktarıldığında buradan seçip fiyat hesaplayabilirsiniz.")
    for row in products:
        st.button(f"{row['urun_kodu']} · {row['urun_adi']} — Fiyat hesapla", key="pdf_product_"+row["id"],
                  on_click=select_product, args=(row,), use_container_width=True)
    selected = next((r for r in products if r["id"] == st.session_state.get("kat_selected")), None)
    if selected:
        calculator(selected, rates)


def render_catalogue(conn, user, rates, upload=False, pdf_only=False):
    if upload:
        if user["rol"] != "admin":
            st.error("Katalog yüklemek için yönetici yetkisi gerekli.")
            return
        render_upload(conn, user)
        return
    try:
        documents, all_records, latest = load_catalogue(conn, user)
    except Exception:
        st.error("Kataloglar alınamadı. Bağlantıyı kontrol edip tekrar deneyin.")
        return
    if pdf_only:
        docs = [d for d in documents if d["pdf_var"]]
        if not docs:
            st.info("Henüz PDF kataloğu yüklenmedi.")
            return
        index = st.selectbox("Tedarikçi PDF kataloğu", range(len(docs)),
                             format_func=lambda i: f"{docs[i]['marka']} · {docs[i]['dosya_adi']} · {str(docs[i]['olusturma_zamani'])[:10]}")
        try:
            pdf_view(conn, user, docs[index], all_records, rates)
        except Exception:
            st.error("PDF görüntülenemedi. Dosyayı indirerek kontrol edin.")
        return
    st.caption("Ürün satırına tıklayın; iskonto, kâr ve KDV girerek satış fiyatını hesaplayın.")
    if not latest:
        st.info("Henüz katalog ürünü yok. Yönetici PDF, Excel veya CSV yükleyebilir.")
        return
    brands = sorted({r["marka"] for r in latest})
    cols = st.columns([1, 2])
    brand = cols[0].selectbox("Tedarikçi / marka", ["Tümü"] + brands, key="cat_brand")
    search = cols[1].text_input("Ürün kodu veya adı ara", key="cat_search").casefold().strip()
    records = [r for r in latest if (brand == "Tümü" or r["marka"] == brand) and
               (not search or search in (clean(r["urun_kodu"])+" "+clean(r["urun_adi"])).casefold())]
    if not records:
        st.info("Aramanıza uygun ürün bulunamadı.")
        return
    st.caption(f"{len(records)} ürün · Aynı marka ve kod için son aktarılan fiyat gösterilir.")
    table_key = "catalog_table_" + hashlib.sha256("|".join(r["id"] for r in records).encode()).hexdigest()[:16]
    display = pd.DataFrame(records)[FIELDS[:5]].rename(columns=LABELS)
    event = st.dataframe(display, use_container_width=True, hide_index=True,
                         on_select="rerun", selection_mode="single-row", key=table_key)
    rows = event.selection.rows
    selected = records[rows[0]] if rows and rows[0] < len(records) else None
    if selected:
        calculator(selected, rates)
        if selected.get("pdf_var"):
            if st.checkbox("Ürünün PDF sayfasını göster", key="source_"+selected["id"]):
                try:
                    data = load_pdf(conn, user, selected["belge_id"])
                    st.image(render_page(data, selected["sayfa"]), use_container_width=True)
                except Exception:
                    st.warning("Kaynak PDF sayfası görüntülenemedi.")


def editor(rows, key, brand, birim, pdf_pages=None):
    if not rows:
        rows = [{"aktar": True, "marka": brand, "urun_kodu": "", "urun_adi": "", "liste_fiyati": "",
                 "para_birimi": birim, "sayfa": 1 if pdf_pages else 0}]
    frame = pd.DataFrame(rows)
    for field in FIELDS:
        if field not in frame:
            frame[field] = ""
    frame["liste_fiyati"] = frame["liste_fiyati"].map(clean)
    st.caption("Hücreleri düzenleyebilir, satır ekleyebilir veya Aktar işaretini kaldırabilirsiniz. Fiyatlar: 1.250,50 ya da 1250.50.")
    return st.data_editor(frame[["aktar"]+FIELDS], hide_index=True, use_container_width=True,
        num_rows="dynamic", key=key,
        column_config={
            "aktar": st.column_config.CheckboxColumn("Aktar", default=True),
            "marka": st.column_config.TextColumn("Tedarikçi / marka", default=brand),
            "urun_kodu": st.column_config.TextColumn("Ürün kodu"),
            "urun_adi": st.column_config.TextColumn("Ürün adı"),
            "liste_fiyati": st.column_config.TextColumn("Liste fiyatı"),
            "para_birimi": st.column_config.SelectboxColumn("Para birimi", options=["TRY", "USD", "EUR"], default=birim),
            "sayfa": st.column_config.NumberColumn("PDF sayfası", min_value=0, step=1, default=1 if pdf_pages else 0),
        }).to_dict("records")


def render_upload(conn, user):
    if user["rol"] != "admin":
        return
    if st.session_state.get("catalog_saved"):
        st.success(st.session_state.pop("catalog_saved"))
    st.subheader("Tedarikçi kataloğu yükle")
    kind = st.radio("Dosya türü", ["PDF", "Excel / CSV", "Tek ürün"], horizontal=True, key="import_kind")
    cols = st.columns(2)
    brand = cols[0].text_input("Tedarikçi / marka adı", key="import_brand").strip()
    birim = cols[1].selectbox("Varsayılan para birimi", ["TRY", "USD", "EUR"], key="import_currency")
    raw, pages, rows, filename = None, None, [], "Manuel ürün"
    signature = hashlib.sha256((kind+brand+birim).encode()).hexdigest()[:12]
    if kind == "PDF":
        file = st.file_uploader("Tedarikçi PDF kataloğu / fiyat listesi (en fazla 20 MB)", type=["pdf"], key="catalog_pdf")
        if file is None:
            return
        raw, filename = file.getvalue(), file.name
        try:
            pages = inspect_pdf(raw)
        except ValueError as exc:
            st.error(str(exc))
            return
        signature = hashlib.sha256(raw+(brand+birim).encode()).hexdigest()[:16]
        cols = st.columns(2)
        first = cols[0].number_input("İlk sayfa", 1, pages, 1, key="first_"+signature)
        last = cols[1].number_input("Son sayfa", first, pages, min(pages, first+49), key=f"last_{signature}_{first}")
        st.caption("PDF'deki metin ve tablolar okunur. Taranmış görsellerde otomatik OCR yoktur; ürünleri aşağıdaki tabloya elle ekleyebilirsiniz. Her taramada en fazla 50 sayfa seçin.")
        if st.button("PDF'den ürünleri çıkar", type="primary", disabled=not brand or last-first >= 50):
            try:
                with st.spinner("PDF ürünleri okunuyor…"):
                    extracted = extract_candidates(raw, brand, birim, first, last)
                st.session_state["extract_"+signature] = extracted
                st.session_state["draft_"+signature] = extracted["urunler"]
                st.session_state["revision_"+signature] = st.session_state.get("revision_"+signature, 0) + 1
            except Exception:
                st.error("PDF otomatik okunamadı. Sayfa aralığını küçültün veya ürünleri elle ekleyin.")
        extracted = st.session_state.get("extract_"+signature)
        if extracted:
            if extracted["bos_sayfalar"]:
                st.warning("Metni okunamayan sayfalar: " + ", ".join(map(str, extracted["bos_sayfalar"])))
            if not extracted["urunler"]:
                st.info("Ürün satırları otomatik belirlenemedi. Tablo sütunlarını eşleştirebilir veya ürünleri elle ekleyebilirsiniz.")
            with st.expander("PDF metni ve tablo sütunlarını kontrol et"):
                for item in extracted["metinler"]:
                    st.text_area(f"Sayfa {item['sayfa']} metni", item["metin"], height=130, key=f"text_{signature}_{item['sayfa']}")
                tables = extracted["tablolar"]
                if tables:
                    ti = st.selectbox("Eşleştirilecek tablo", range(len(tables)),
                                      format_func=lambda i: f"Tablo {i+1} · Sayfa {tables[i]['sayfa']}", key="map_table_"+signature)
                    table = tables[ti]
                    df = pd.DataFrame(table["satirlar"]).fillna("")
                    st.dataframe(df, hide_index=True, use_container_width=True)
                    guessed = identify_columns(table["satirlar"][0])
                    mapping = {}
                    for field in ["urun_kodu", "urun_adi", "liste_fiyati", "para_birimi"]:
                        choices = [None]+list(range(len(df.columns)))
                        mapping[field] = st.selectbox(LABELS[field]+" sütunu", choices,
                            index=choices.index(guessed.get(field)), format_func=lambda i: "Seçilmedi" if i is None else f"Sütun {i}",
                            key=f"mapping_{signature}_{ti}_{field}")
                    skip = st.number_input("Atlanacak başlık satırı", 0, len(df), 1, key=f"skip_{signature}_{ti}")
                    if st.button("Bu tablodan ürün listesini oluştur", key="map_apply_"+signature):
                        if any(mapping.get(f) is None for f in ["urun_kodu", "urun_adi", "liste_fiyati"]):
                            st.error("Kod, ad ve fiyat sütunlarını seçin.")
                        else:
                            st.session_state["draft_"+signature] = map_table(table["satirlar"], mapping, brand, birim, table["sayfa"], skip)
                            st.session_state["revision_"+signature] = st.session_state.get("revision_"+signature, 0) + 1
                            st.rerun()
        rows = st.session_state.get("draft_"+signature, [])
        if st.checkbox("PDF sayfasını önizle", key="preview_"+signature):
            page = st.number_input("Önizleme sayfası", 1, pages, first, key="previewpage_"+signature)
            st.image(render_page(raw, page), use_container_width=True)
    elif kind == "Excel / CSV":
        file = st.file_uploader("Excel (.xlsx) veya CSV", type=["xlsx", "csv"], key="catalog_sheet")
        st.caption("Kod, ad ve fiyat sütunlarını seçin. Kodlardaki baştaki sıfırlar metin olarak korunur.")
        if file is None:
            return
        if file.size > 20 * 1024 * 1024:
            st.error("Dosya en fazla 20 MB olabilir.")
            return
        filename = file.name
        signature = hashlib.sha256(file.getvalue()+(brand+birim).encode()).hexdigest()[:16]
        try:
            df = pd.read_csv(BytesIO(file.getvalue()), dtype=str, keep_default_na=False, sep=None, engine="python") if filename.lower().endswith(".csv") else pd.read_excel(BytesIO(file.getvalue()), dtype=str, keep_default_na=False)
            if len(df) > 10000:
                st.error("Bir yüklemede en fazla 10.000 ürün olabilir.")
                return
            st.dataframe(df.head(10), hide_index=True, use_container_width=True)
            guessed = identify_columns(df.columns)
            mapping = {}
            for field in ["urun_kodu", "urun_adi", "liste_fiyati", "para_birimi"]:
                options = [None]+list(range(len(df.columns)))
                mapping[field] = st.selectbox(LABELS[field]+" sütunu", options, index=options.index(guessed.get(field)),
                    format_func=lambda i: "Seçilmedi / varsayılan" if i is None else str(df.columns[i]), key="sheet_"+signature+field)
            rows = map_table(df.values.tolist(), mapping, brand, birim, 0)
            signature += hashlib.sha256(json.dumps(mapping).encode()).hexdigest()[:6]
        except Exception:
            st.error("Dosya okunamadı. CSV için UTF-8 kodlaması veya .xlsx dosyası kullanın.")
            return
    rows = editor(rows, "editor_"+signature+str(st.session_state.get("revision_"+signature, 0)), brand, birim, pages)
    valid, errors = validate_rows(rows, pages)
    st.caption(f"Kaydedilmeye hazır: {len(valid)} ürün")
    if errors:
        for error in errors[:10]:
            st.warning(error)
    only_pdf = False
    if raw is not None:
        only_pdf = st.checkbox("Yalnızca PDF dosyasını kaydet; ürünleri şimdi aktarma", key="onlypdf_"+signature)
    reviewed = st.checkbox("Ürün kodlarını, fiyatları ve para birimlerini kontrol ettim" if not only_pdf else "PDF dosyasını kataloğa eklemeyi onaylıyorum", key="reviewed_"+signature)
    if st.button("Kataloğa kaydet", type="primary", disabled=not reviewed or not brand or (not only_pdf and (bool(errors) or not valid))):
        try:
            _, count = save_document(conn, user, brand, filename, [] if only_pdf else rows, raw, pages or 0)
            st.session_state["catalog_saved"] = f"{brand}: katalog kaydedildi, {count} ürün eklendi. Ürünler ve Fiyat Hesabı bölümünden açabilirsiniz."
            st.rerun()
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        except Exception:
            st.error("Katalog kaydedilemedi. Bağlantıyı ve veritabanı yazma yetkisini kontrol edin; mevcut stoklar değişmedi.")
