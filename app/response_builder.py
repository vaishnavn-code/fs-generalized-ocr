"""Consistent API response with named table columns and optional raw diagnostics."""
import re


def _key(value, index):
    label = re.sub(r"[^a-z0-9]+", "_", str(value or "").lower()).strip("_")
    return label or f"column_{index + 1}"


def _rows_to_objects(rows):
    if not rows:
        return []
    # Use a header only if it is recognisably a table header; otherwise neutral names.
    first = [str(c or "").strip() for c in rows[0]]
    looks_like_header = sum(bool(re.search(r"(?:item|description|hsn|quantity|rate|amount|total|facility|security)", c, re.I)) for c in first) >= 2
    headers = first if looks_like_header else [f"column_{i+1}" for i in range(max(map(len, rows)))]
    keys, seen = [], set()
    for i, header in enumerate(headers):
        base = _key(header, i)
        key = base
        suffix = 2
        while key in seen:
            key = f"{base}_{suffix}"
            suffix += 1
        seen.add(key)
        keys.append(key)
    out = []
    for row in (rows[1:] if looks_like_header else rows):
        entry = {keys[i]: value for i, value in enumerate(row) if i < len(keys) and value is not None and str(value).strip()}
        if entry:
            out.append(entry)
    return out


def build_response(extraction, filename, include_raw=False):
    from app.document_classifier import classify
    from app.invoice_view import build_invoice_view
    pages = extraction["pages"]
    text = "\n".join(page["text"] for page in pages)
    kind = classify(text)
    financial = extraction.get("financial", {}) if kind == "tax_invoice" else {}
    warnings = list(extraction.get("warnings", []))
    all_fields = [{k: v for k, v in kv.items() if k not in ("name", "lines")} for kv in extraction.get("key_value_pairs", [])]
    if kind == "tax_invoice":
        if financial.get("validation", {}).get("status") != "passed":
            warnings.append("Invoice financial checks were not fully verified")
        if financial.get("taxable_amount_source") == "derived_from_total_and_tax":
            warnings.append("Taxable amount was derived, not independently extracted from the source")
        fields, tables = build_invoice_view(extraction)
    else:
        # No category-specific layout yet: every label/value pair by key, repeated labels as _2, _3 ...
        warnings.append("Fields are generic label/value pairs detected from the document layout; no category-specific validation was applied")
        fields = {}
        letter = extraction.get("letter", {})
        for key in ("date", "reference", "ref_no", "addressed_to", "subject"):
            if letter.get(key):
                fields[key] = ", ".join(letter[key]) if isinstance(letter[key], list) else letter[key]
        for kv in all_fields:
            base = re.sub(r"[^a-z0-9]+", "_", kv["label"].lower()).strip("_")
            if not base or kv["value"] in [v for k, v in fields.items() if k == base or k.startswith(base + "_")]:
                continue
            key, n = base, 1
            while key in fields:
                n += 1
                key = f"{base}_{n}"
            fields[key] = kv["value"]
        # Tables rebuilt from word positions (column titles joined across heading lines) come first;
        # a ruled grid from the PDF is added only for pages where no such table was found.
        generic = extraction.get("generic_tables", [])
        tables = [{"table_name": f"table_{i}", "page": t["page"], "columns": t["columns"], "rows": t["rows"]} for i, t in enumerate(generic, 1)]
        covered = {t["page"] for t in generic}
        for t in extraction.get("tables", []):
            if t.get("page") not in covered:
                tables.append({"table_name": f"table_{len(tables) + 1}", "page": t.get("page"), "rows": _rows_to_objects(t.get("rows", []))})
    generic_sender = generic_title = generic_date = generic_counterparty = None
    if kind != "tax_invoice":
        from app.financial import DATE, valid_date
        title_lines = extraction.get("headings", [])
        letter = extraction.get("letter", {})
        generic_sender = title_lines[0] if title_lines else None
        generic_title = letter.get("subject") or " - ".join(title_lines[1:]) or None
        generic_date = letter.get("date")
        generic_counterparty = (letter.get("addressed_to") or [None])[0]
        for kv in ([] if generic_date else all_fields):
            if re.search(r"\bdate\b|\bdated\b", kv["label"], re.I) and not re.search(r"due|expiry|maturity|reversal|settlement|value\s*date", kv["label"], re.I):
                m = DATE.search(kv["value"])
                generic_date = valid_date(m.group()) if m else None
                if generic_date:
                    break
    data = {
        "document": {"filename": filename, "page_count": len(pages)},
        "document_metadata": {
            "document_category": kind,
            "title": None if kind == "tax_invoice" else generic_title,
            "sender": financial.get("supplier") if kind == "tax_invoice" else generic_sender,
            "counterparty_or_bp": financial.get("buyer") if kind == "tax_invoice" else generic_counterparty,
            "document_date": financial.get("invoice_date") if kind == "tax_invoice" else generic_date,
        },
        "extracted_fields": fields,
        "tables": tables,
        "validation": financial.get("validation", {"status": "not_verified", "checks": {}}),
        "unmapped_text": extraction.get("unmapped_text", []),
        "coverage": extraction.get("coverage", {}),
        "warnings": warnings,
        "processing": {"page_sources": [{k: p.get(k) for k in ("page", "source", "native_word_count", "ocr_word_count", "ocr_regions", "ocr_confidence")} for p in pages], "timings_ms": extraction.get("timings_ms"), "total_ms": extraction.get("total_ms")},
    }
    if kind != "tax_invoice":
        # Letters, reports, statements: running text is content, returned as paragraphs in reading order,
        # with the lines that repeat on every page (letterhead / footer) kept apart.
        data.pop("unmapped_text")
        data["text_content"] = extraction.get("text_content", [])
        data["letterhead"] = extraction.get("letterhead", [])
        cov = dict(data["coverage"])
        cov["returned_as_text_content_or_letterhead"] = cov.pop("returned_as_unmapped_text", 0)
        data["coverage"] = cov
        data = {k: data[k] for k in ("document", "document_metadata", "extracted_fields", "tables", "text_content", "letterhead", "validation", "coverage", "warnings", "processing")}
    if include_raw:
        # Debug view: every raw label/value pair, the raw pdfplumber grids and the page text.
        data["raw_content"] = {"all_fields": all_fields, "pages": pages, "tables": extraction.get("tables", []), "legacy_fields": extraction.get("fields", {})}
    return {"success": True, "message": "PDF extracted successfully", "data": data}