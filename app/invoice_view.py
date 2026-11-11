"""Arrange everything read from a tax invoice into named sections, so each value sits under the thing it
describes: invoice_details, supplier, buyer, ship_to, totals, bank_details, other_details.
Anything read but not recognised goes to additional_fields - it is never dropped."""
import re
from app.financial import number

IFSC = re.compile(r'\b[A-Z]{4}0[A-Z0-9]{6}\b')
INVOICE_KEYS = [('place_of_supply', r'place\s*of\s*supply'), ('due_date', r'due\s*date'),
                ('po_number', r"^(?:po|p\.o\.?|purchase\s*order|buyer'?s?\s*order)\b"), ('e_way_bill_no', r'e-?\s*way'),
                ('irn', r'^irn\b'), ('ack_no', r'^ack.*no'), ('ack_date', r'^ack.*date'),
                ('payment_terms', r'terms\s*of\s*payment|payment\s*terms'), ('delivery_note', r'delivery\s*note'),
                ('reference_no', r'^ref(?:erence)?\.?\s*(?:no|number)'), ('challan_no', r'challan'), ('vehicle_no', r'vehicle')]
BANK_KEYS = [('account_holder_name', r'holder'), ('account_number', r'a\s*/?\s*c\.?\s*no|account\s*(?:no|number)'),
             ('ifsc_code', r'ifs'), ('bank_name', r'^bank(?:\s*name)?$'), ('branch', r'^branch(?:\s*name)?$')]
OTHER_KEYS = [('description', r'^description$'), ('terms_and_conditions', r'terms\s*(?:&|and)\s*conditions'),
              ('declaration', r'^declaration'), ('notes', r'^(?:notes?|remarks?|narration)$')]
# Labels whose value is already reported as a normalised field.
NORMALISED = re.compile(r"^(?:(?:tax\s+)?(?:invoice|inv|bill)(?:\s*(?:no|number|num|#))?\.?|no\.?|invoice\s*date|date|sub\s*-?\s*total|"
                        r"total|grand\s*total|invoice\s*total|round(?:ed)?\s*off|taxable\s*(?:value|amount)|(?:c|s|i)gst\b.*|"
                        r"invoice\s*details|buyer.*|bill(?:ed)?\s*to|ship(?:ped)?\s*to|consignee)$", re.I)
ITEM_COLUMNS = ['description', 'hsn_sac', 'quantity', 'unit', 'unit_price', 'taxable_amount', 'gst_rate_percent', 'gst_amount', 'total_amount']


def _slug(label):
    return re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')


def _norm(text):
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())


def line_items(items):
    rows = []
    for n, item in enumerate(items, 1):
        row = {'sr_no': n}
        for key in ITEM_COLUMNS:
            row[key] = item.get('gross_amount') if key == 'total_amount' else item.get(key)
        if item.get('amount_basis') == 'unknown':
            # The document does not show whether this column is before or after tax; reported as printed.
            row['amount_as_printed'] = item.get('amount')
        rows.append(row)
    return rows


def build_invoice_view(extraction):
    fin = extraction.get('financial', {})
    party = fin.get('parties') or {'supplier': {}, 'buyer': {}, 'ship_to': {}, 'consumed': []}
    consumed = set(party.get('consumed', []))
    invoice = {'invoice_number': fin.get('invoice_number'), 'invoice_date': fin.get('invoice_date')}
    totals = {'total_quantity': fin.get('total_quantity'), 'taxable_amount': fin.get('taxable_amount'), 'cgst': fin.get('cgst'),
              'sgst': fin.get('sgst'), 'igst': fin.get('igst'), 'total_tax': fin.get('gst_amount'), 'sub_total': fin.get('subtotal'),
              'round_off': fin.get('round_off'), 'invoice_total': fin.get('invoice_total'), 'amount_in_words': None}
    bank, other, additional = {}, {}, {}
    supplier_norm = _norm(fin.get('supplier'))
    dates_seen = 0
    for kv in extraction.get('key_value_pairs', []):
        label, value = kv['label'].strip(), kv['value'].strip()
        low = label.lower()
        if (low, value.lower()) in consumed:
            continue
        if supplier_norm and _norm(label) and (_norm(label) in supplier_norm or supplier_norm in _norm(label)):
            continue   # the supplier's name line read as a label
        if re.search(r'in\s*words', low):
            key = 'tax_amount_in_words' if re.search(r'\btax\b', low) else 'amount_in_words'
            if not totals.get(key): totals[key] = value
            continue
        if re.match(r'^(?:received|paid|advance)', low) or re.match(r'^balance', low):
            key = 'balance_due' if low.startswith('balance') else 'amount_received'
            totals.setdefault(key, number(value) if number(value) is not None else value)
            continue
        if re.fullmatch(r'dated|date|invoice\s*date', low):
            dates_seen += 1
            if dates_seen == 1 or low != 'dated':
                continue
            label, low = 'Order Date', 'order date'   # a second 'Dated' on grid layouts belongs to the order reference
        placed = False
        for group, keys in ((invoice, INVOICE_KEYS), (bank, BANK_KEYS), (other, OTHER_KEYS)):
            for key, pattern in keys:
                if re.search(pattern, low):
                    if key == 'ifsc_code':
                        code = IFSC.search(value.upper())
                        if 'branch' in low and '&' in value: group.setdefault('branch', value.split('&')[0].strip())
                        value = code.group() if code else value
                    group.setdefault(key, value)
                    placed = True
                    break
            if placed: break
        if placed:
            continue
        if low == 'name' and 'bank' in value.lower():
            bank.setdefault('bank_name', value); continue
        if low == 'order date':
            invoice.setdefault('order_date', value); continue
        if NORMALISED.match(low):
            continue
        key, base, n = _slug(label), _slug(label), 1
        if not key: continue
        while key in additional and additional[key] != value:
            n += 1; key = f'{base}_{n}'
        additional[key] = value
    fields = {
        'invoice_details': invoice,
        'supplier': party['supplier'],
        'buyer': party['buyer'],
        'ship_to': party['ship_to'],
        'totals': totals,
        'bank_details': {k: bank[k] for k in ('bank_name', 'branch', 'account_number', 'ifsc_code', 'account_holder_name') if k in bank},
        'other_details': other,
        'additional_fields': additional,
    }
    tables = []
    items = fin.get('items') or []
    for page in sorted({i.get('page') or 1 for i in items}):
        tables.append({'table_name': 'line_items', 'page': page, 'rows': line_items([i for i in items if (i.get('page') or 1) == page])})
    for summary in extraction.get('tax_summaries', []):
        tables.append({'table_name': 'tax_summary', 'page': summary['page'], 'rows': summary['rows']})
    return fields, tables
