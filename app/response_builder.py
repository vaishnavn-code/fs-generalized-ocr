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
        for kv in all_fields:
            base = re.sub(r"[^a-z0-9]+", "_", kv["label"].lower()).strip("_")
            if not base or kv["value"] in [v for k, v in fields.items() if k == base or k.startswith(base + "_")]:
                continue
            key, n = base, 1
            while key in fields:
                n += 1
                key = f"{base}_{n}"
            fields[key] = kv["value"]
        tables = [{"table_name": f"{t.get('type', 'table')}_{i}", "page": t.get("page"), "rows": _rows_to_objects(t.get("rows", []))} for i, t in enumerate(extraction.get("tables", []), 1)]
    data = {
        "document": {"filename": filename, "page_count": len(pages)},
        "document_metadata": {
            "document_category": kind,
            "sender": financial.get("supplier") if kind == "tax_invoice" else None,
            "counterparty_or_bp": financial.get("buyer") if kind == "tax_invoice" else None,
            "document_date": financial.get("invoice_date") if kind == "tax_invoice" else None,
        },
        "extracted_fields": fields,
        "tables": tables,
        "validation": financial.get("validation", {"status": "not_verified", "checks": {}}),
        "unmapped_text": extraction.get("unmapped_text", []),
        "coverage": extraction.get("coverage", {}),
        "warnings": warnings,
        "processing": {"page_sources": [{k: p.get(k) for k in ("page", "source", "native_word_count", "ocr_word_count", "ocr_regions", "ocr_confidence")} for p in pages], "timings_ms": extraction.get("timings_ms"), "total_ms": extraction.get("total_ms")},
    }
    if include_raw:
        # Debug view: every raw label/value pair, the raw pdfplumber grids and the page text.
        data["raw_content"] = {"all_fields": all_fields, "pages": pages, "tables": extraction.get("tables", []), "legacy_fields": extraction.get("fields", {})}
    return {"success": True, "message": "PDF extracted successfully", "data": data}