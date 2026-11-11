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
    pages = extraction["pages"]
    text = "\n".join(page["text"] for page in pages)
    kind = classify(text)
    financial = extraction.get("financial", {}) if kind == "tax_invoice" else {}
    warnings = list(extraction.get("warnings", []))
    if kind != "tax_invoice":
        warnings.append("No verified structured extractor for this document category; returning text and detected tables only")
    if kind == "tax_invoice" and financial.get("validation", {}).get("status") != "passed":
        warnings.append("Invoice financial checks were not fully verified")
    if kind == "tax_invoice" and financial.get("taxable_amount_source") == "derived_from_total_and_tax":
        warnings.append("Taxable amount was derived, not independently extracted from the source")
    structured_tables = []
    if financial.get("items"):
        structured_tables.append({"table_name": "invoice_items", "page": 1, "rows": financial["items"]})
    for i, table in enumerate(extraction.get("tables", []), 1):
        if table.get("type") == "items" and financial.get("items"):
            continue  # avoid duplicating invoice line items as a malformed raw table
        structured_tables.append({"table_name": f"{table.get('type', 'table')}_{i}", "page": table.get("page"), "rows": _rows_to_objects(table.get("rows", []))})
    fields = {}
    if kind == "tax_invoice":
        for key in ("invoice_number", "invoice_date", "supplier", "taxable_amount", "gst_amount", "cgst", "sgst", "igst", "round_off", "subtotal", "invoice_total", "item_count"):
            fields[key] = financial.get(key)
    if kind != "tax_invoice":
        for kv in extraction.get("key_value_pairs", []):
            key = re.sub(r"[^a-z0-9]+", "_", kv["label"].lower()).strip("_")
            if key and key not in fields:
                fields[key] = kv["value"]
        warnings = [w for w in warnings if not w.startswith("No verified structured extractor")]
        warnings.append("Fields are generic label/value pairs detected from the document layout; no category-specific validation was applied")
    data = {
        "document": {"filename": filename, "page_count": len(pages)},
        "document_metadata": {
            "document_category": kind,
            "sender": financial.get("supplier") if kind == "tax_invoice" else None,
            "counterparty_or_bp": None,
            "document_date": financial.get("invoice_date") if kind == "tax_invoice" else None,
        },
        "extracted_fields": fields,
        "all_fields": extraction.get("key_value_pairs", []),
        "tables": structured_tables,
        "validation": financial.get("validation", {"status": "not_verified", "checks": {}}),
        "warnings": warnings,
        "processing": {"page_sources": [{"page": p["page"], "source": p["source"]} for p in pages], "timings_ms": extraction.get("timings_ms"), "total_ms": extraction.get("total_ms")},
    }
    generalized = extraction.get("generalized")
    if kind == "tax_invoice" and generalized:
        # Compact, generalized invoice JSON (header / tables / tax breakdown / footer).
        data = {
            **generalized,
            "validation": data["validation"],
            "warnings": [w for w in warnings if not w.startswith("Scanned table cell")],
            "processing": data["processing"],
        }
    if include_raw:
        data["raw_content"] = {"pages": pages, "tables": extraction.get("tables", []), "legacy_fields": extraction.get("fields", {})}
    return {"success": True, "message": "PDF extracted successfully", "data": data}
