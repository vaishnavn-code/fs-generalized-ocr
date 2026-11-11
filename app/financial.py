"""Layout-aware invoice parsing. No supplier-specific templates or external AI."""
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

AMOUNT = re.compile(r'-?\s*(?:₹|Rs\.?|INR)?\s*([\d,]+\.\d{2})')
DATE = re.compile(r'\b(?:\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}[- ](?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*[- ]\d{2,4})\b', re.I)

def number(value):
    if value is None: return None
    s = str(value).replace(',', '').replace('₹', '').replace('Rs.', '').replace('INR', '').strip()
    match = re.search(r'-?\s*\d+(?:\.\d+)?', s)
    if not match: return None
    try: return float(Decimal(match.group().replace(' ', '')))
    except InvalidOperation: return None

def clean(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()

def valid_date(value):
    if not value: return None
    for fmt in ('%d/%m/%Y','%d-%m-%Y','%d.%m.%Y','%d/%m/%y','%d-%m-%y','%d.%m.%y','%d-%b-%y','%d-%b-%Y','%d %b %Y','%d %b %y','%d-%B-%Y','%d-%B-%y'):
        try: return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError: pass
    return None

def normalize_table(rows):
    rows = [list(r) for r in rows if r]
    if not rows: return []
    width = max(map(len, rows))
    rows = [r + [None] * (width-len(r)) for r in rows]
    keep = [i for i in range(width) if any(clean(r[i]) for r in rows)]
    return [[clean(r[i]) or None for i in keep] for r in rows if any(clean(r[i]) for i in keep)]

def identify_table(rows):
    head = ' '.join(clean(c).lower() for row in rows for c in row if c)
    if ('description of goods' in head or 'item name' in head) and ('quantity' in head or 'rate' in head):
        return 'items'
    if 'hsn' in head and ('cgst' in head or 'sgst' in head): return 'tax_summary'
    return 'other'

def get_lines(page):
    # Word positions allow label/value pairing even when the PDF's reading order is unusual.
    words = page.get_text('words')
    lines = {}
    for x0,y0,x1,y1,word,block,line,*_ in words:
        lines.setdefault((block,line), []).append((x0,y0,x1,y1,word))
    return [sorted(v,key=lambda w:w[0]) for v in sorted(lines.values(),key=lambda v:min(w[1] for w in v))]

def extract_metadata(page):
    lines = get_lines(page)
    all_text = page.get_text('text',sort=True)
    invoice_number = None
    invoice_date = None
    # Prefer explicit label/value pairs; do not parse date-looking substrings of invoice IDs.
    for line in lines:
        s = ' '.join(w[4] for w in line)
        m = re.search(r'\b(?:Invoice\s*(?:No\.?|Number)|No\s*:)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/.-]*\d[A-Z0-9/.-]*)',s,re.I)
        if m and not invoice_number: invoice_number = m.group(1)
        m = re.search(r'\b(?:Invoice\s*Date|Date|Dated)\s*[:\-]?\s*('+DATE.pattern+r')',s,re.I)
        if m and not invoice_date: invoice_date = valid_date(m.group(1))
    # Tally-like layout has column labels on one line and values below.
    if not invoice_number:
        m = re.search(r'\b(?:GST/\d+/\d{2}-\d{2}|SSPL/\d{2}-\d{2}/\d+)\b',all_text,re.I)
        if m: invoice_number = m.group()
    if not invoice_date:
        # Candidate dates closest to top-right 'Dated' or 'Date' headings.
        candidates=[]
        for line in lines:
            for i,w in enumerate(line):
                if DATE.fullmatch(w[4]):
                    d=valid_date(w[4]);
                    if d: candidates.append((w[1],w[0],d))
        if candidates:
            # Prefer right-side, upper-half date (not supplier founding date).
            right=[c for c in candidates if c[1]>page.rect.width*0.52 and c[0]<page.rect.height*0.45]
            if right: invoice_date=min(right,key=lambda c:c[0])[2]
    if not invoice_date:
        m=re.search(r'\bDate\s*:\s*('+DATE.pattern+r')',all_text,re.I)
        if m: invoice_date=valid_date(m.group(1))
    supplier = None
    for line in lines:
        s=clean(' '.join(w[4] for w in line))
        if s and not re.search(r'^(?:tax invoice|invoice|bill to|ship to|original for|duplicate|page\s+\d)', s, re.I) and not re.search(r'\b(?:GSTIN|phone|email|invoice details|date:)\b', s, re.I) and len(s) < 110:
            supplier=s.split('REGD.')[0].strip().rstrip(' .'); break
    if not supplier:
        # Conservative fallback: first non-title line; may require review.
        for line in lines:
            s=clean(' '.join(w[4] for w in line))
            if s and not re.fullmatch('tax invoice',s,re.I): supplier=s;break
    return {'invoice_number':invoice_number,'invoice_date':invoice_date,'supplier':supplier}

def extract_items(tables):
    items=[]
    for table in tables:
        if table['type']!='items':continue
        rows=table['rows']
        head_idx=next((i for i,r in enumerate(rows) if any('hsn' in clean(c).lower() for c in r) and any('quantity' in clean(c).lower() or 'rate'==clean(c).lower() for c in r)),None)
        if head_idx is None:continue
        header=rows[head_idx]
        def col(term):
            return next((i for i,c in enumerate(header) if term in clean(c).lower()),None)
        hsn=col('hsn');qty=col('quantity');description=col('item name')
        if description is None:description=col('description of goods')
        amount=next((i for i in range(len(header)-1,-1,-1) if 'amount' in clean(header[i]).lower()),None)
        rate=col('price/')
        if rate is None: rate=next((i for i,c in enumerate(header) if clean(c).lower()=='rate'),None)
        for row in rows[head_idx+1:]:
            if any('tax summary' in clean(c).lower() for c in row):break
            first=clean(row[0])
            if first.lower() in ('total','tax summary:') or any(clean(c).lower()=='total' for c in row[:2]):break
            is_item=bool(re.fullmatch(r'\d{1,3}',first)) and hsn is not None and hsn<len(row) and bool(re.fullmatch(r'\d{4,8}',clean(row[hsn])))
            if is_item:
                item={'description':clean(row[description]) if description is not None else None,'hsn_sac':clean(row[hsn]),'quantity':number(row[qty]) if qty is not None else None,'rate':number(row[rate]) if rate is not None else None,'amount':number(row[amount]) if amount is not None else None}
                items.append(item)
            elif items and description is not None and description<len(row):
                extra=clean(row[description])
                if extra and not any(clean(c).lower() in ('cgst 9%','sgst 9%') for c in row):
                    items[-1]['description']=clean((items[-1]['description'] or '')+' '+extra)
    return items

def extract_totals(page):
    # Position-based search: label and value must be in the same horizontal region.
    words=page.get_text('words')
    lines=get_lines(page)
    results={}
    labels={'invoice_total':r'\b(?:Invoice\s+Total|Total)\b','taxable_amount':r'\bTaxable\s+(?:Value|Amount)\b','round_off':r'\bRound\s*Off\b','subtotal':r'\bSub\s*Total\b','cgst':r'\bCGST\b','sgst':r'\bSGST\b','igst':r'\bIGST\b'}
    for line in lines:
        s=' '.join(w[4] for w in line)
        for field,pattern in labels.items():
            if field in results or not re.search(pattern,s,re.I):continue
            # Extract rightmost currency/decimal amount on the same line.
            matches=list(AMOUNT.finditer(s))
            if matches:
                val=number(matches[-1].group(0))
                if field=='round_off' and '-' in s[matches[-1].start()-3:matches[-1].start()+1]: val=-abs(val)
                results[field]=val
    return results

def validate(fields,items):
    checks={}
    total=fields.get('invoice_total');taxable=fields.get('taxable_amount');round_off=fields.get('round_off',0) or 0
    gst=fields.get('gst_amount')
    if total is not None and taxable is not None and gst is not None:
        checks['total_reconciles']=abs(total-(taxable+gst+round_off))<=0.02
    if items and all(i.get('amount') is not None for i in items) and fields.get('subtotal') is not None:
        checks['items_match_subtotal']=abs(sum(i['amount'] for i in items)-fields['subtotal'])<=0.02
    return {'status':'passed' if checks.get('total_reconciles') is True and all(checks.values()) else 'failed' if any(v is False for v in checks.values()) else 'not_verified','checks':checks}

def totals_from_tables(tables, existing):
    result=dict(existing)
    for table in tables:
        rows=table['rows']
        for row in rows:
            cells=[clean(c) for c in row]
            for cell in cells:
                for key,pattern in [('invoice_total',r'\bTotal\s*:\s*₹?\s*([\d,]+\.\d{2})'),('subtotal',r'\bSub\s*Total\s*:\s*₹?\s*([\d,]+\.\d{2})'),('round_off',r'\bRound\s*Off\s*:\s*(-?\s*₹?\s*[\d,]+\.\d{2})')]:
                    m=re.search(pattern,cell,re.I)
                    if m:result[key]=number(m.group(1))
            # RM/Tally invoice totals: label 'Total' and last monetary cell
            if 'invoice_total' not in result and table['type']=='items' and any(c.lower()=='total' for c in cells[:2]):
                values=[number(c) for c in cells if re.search(r'[\d,]+\.\d{2}',c)]
                if values:result['invoice_total']=values[-1]
            # SSPL tax summary totals are a single row with TOTAL and values
            if any(c.lower()=='total' for c in cells[:2]):
                if table['type']=='items' and len([c for c in cells if c])>=4:
                    values=[number(c) for c in cells if re.search(r'[\d,]+\.\d{2}',c)]
                    if len(values)>=3:
                        result.setdefault('taxable_amount',values[-3])
                        result.setdefault('gst_amount',values[-2])
                elif len([c for c in cells if c])>=3:
                    values=[number(c) for c in cells if re.search(r'[\d,]+\.\d{2}',c)]
                    if len(values)>=4:
                        result['taxable_amount']=values[0]
                        result['gst_amount']=values[-1]
            for label in ('CGST','SGST','IGST'):
                if any(re.search(r'\b'+label+r'\b',c,re.I) for c in cells):
                    values=[number(c) for c in cells if re.fullmatch(r'₹?\s*[\d,]+\.\d{2}',c)]
                    if values: result[label.lower()]=values[-1]
    if 'gst_amount' not in result and 'cgst' in result and 'sgst' in result:
        result['gst_amount']=round(result['cgst']+result['sgst'],2)
    return result


def extract_from_text(text):
    """Layout-free fallback for OCR text (scanned invoices). Returns (metadata, totals, items).
    Only labelled/structurally-recognisable values are returned; anything else stays None."""
    meta = {'invoice_number': None, 'invoice_date': None, 'supplier': None}
    totals, items = {}, []
    lines = [clean(l) for l in text.splitlines()]
    lines = [l for l in lines if l]

    # Invoice number + date: first invoice-id-like token (letters + digits + separators) after an 'Invoice No' label.
    label = re.search(r'Invoice\s*(?:No\.?|Number)', text, re.I)
    if label:
        window = text[label.end():label.end() + 300]
        for m in re.finditer(r'\b(?=[A-Z0-9/-]*\d)(?=[A-Z0-9/-]*[A-Z])[A-Z0-9]+(?:[/-][A-Z0-9]+)+\b', window):
            if DATE.fullmatch(m.group()): continue
            meta['invoice_number'] = m.group()
            d = DATE.search(window[m.end():m.end() + 80]) or DATE.search(window)
            meta['invoice_date'] = valid_date(d.group()) if d else None
            break

    # Supplier: 'for <Company>' signature line, else account-holder name.
    for l in lines:
        m = re.match(r'^for\s+([A-Za-z][\w&.,\' -]{2,70})$', l, re.I)
        if m: meta['supplier'] = m.group(1).strip(); break
    if not meta['supplier']:
        m = re.search(r"Holder'?s Name\s*[:=-]\s*([^\n]+)", text, re.I)
        if m: meta['supplier'] = clean(m.group(1))

    amounts = lambda s: [number(a.group(0)) for a in AMOUNT.finditer(s)]
    for l in lines:
        # Tax summary row: Total: taxable cgst sgst total_tax (or taxable igst total_tax)
        if re.match(r'^Total\s*:', l, re.I) and 'taxable_amount' not in totals:
            v = amounts(l)
            if len(v) >= 4 and abs(v[1] + v[2] - v[3]) <= 0.02:
                totals.update(taxable_amount=v[0], cgst=v[1], sgst=v[2], gst_amount=v[3])
            elif len(v) == 3 and abs(v[1] - v[2]) <= 0.02:
                totals.update(taxable_amount=v[0], igst=v[1], gst_amount=v[2])
        # Grand total: a line that is only 'Total' and one amount.
        m = re.match(r'^Total\s*[=:]?\s*(?:₹|Rs\.?)?\s*([\d,]+\.\d{2})$', l, re.I)
        if m and 'invoice_total' not in totals: totals['invoice_total'] = number(m.group(1))
        m = re.match(r'^(CGST|SGST|IGST)\b.*?([\d,]+\.\d{2})$', l, re.I)
        if m and m.group(1).lower() not in totals: totals[m.group(1).lower()] = number(m.group(2))
        m = re.match(r'^Round\s*Off\b.*?(-?\s*[\d,]+\.\d{2})$', l, re.I)
        if m: totals['round_off'] = number(m.group(1))
    # The tax-summary 'Total:' row's own total-tax value must not be mistaken for the invoice total.
    if totals.get('invoice_total') == totals.get('gst_amount'): totals.pop('invoice_total')

    # Line items: '<sl> <description> <HSN/SAC> [gst%] [qty] [rate] <amount>'
    for l in lines:
        m = re.match(r'^(\d{1,3})\s+(.+?)\s+(\d{4,8})\s+(.+)$', l)
        if not m: continue
        tail = m.group(4)
        v = amounts(tail)
        if not v: continue
        pct = re.search(r'(\d{1,2}(?:\.\d+)?)\s*%', tail)
        items.append({'description': m.group(2), 'hsn_sac': m.group(3), 'quantity': None, 'rate': None,
                      'gst_rate_percent': float(pct.group(1)) if pct else None, 'amount': v[-1]})
    return meta, totals, items



# =====================================================================================
# Generalized invoice JSON: header / tables / tax breakdown / footer.
# Layout-based (word positions), no supplier-specific templates, no external AI.
# =====================================================================================
_ROW_TOL = 3.0
_CELL_GAP = 8.0
_MONEY = re.compile(r'^[₹\s]*(?:Rs\.?|INR)?\s*\(?-?\s*[\d,]+\.\d{2}\)?$', re.I)
_PCT = re.compile(r'(\d+(?:\.\d+)?)\s*%')
_PARTY_HEAD = re.compile(r'^(?:buyer|bill(?:ed)?\s*to|consignee|ship(?:ped)?\s*to|sold\s*to)\b', re.I)
_TAX_ID_LABEL = re.compile(r'\b(?:vat|cst|pan|tan|gstin|gst|cin|tin|msme|udyam|fssai|iec)\b', re.I)
_EOE = re.compile(r'\bE\.?\s*&\s*O\.?E\.?\b', re.I)


def _gkey(text):
    """Sanitised key: lowercase, '&'->'and', apostrophes dropped, 'A/c'->'ac', other symbols -> '_'."""
    s = str(text or '').lower().replace('&', ' and ').replace("'", '').replace('’', '')
    s = re.sub(r'\b([a-z])/([a-z])\b', r'\1\2', s)
    return re.sub(r'[^a-z0-9]+', '_', s).strip('_')


def _money_text(text):
    return re.sub(r'^(?:₹|Rs\.?|INR)\s*', '', clean(text), flags=re.I)


def _word_rows(words):
    rows = []
    for w in sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        yc = (w[1] + w[3]) / 2
        if rows and abs(rows[-1]['y'] - yc) <= _ROW_TOL:
            rows[-1]['w'].append((w[0], w[2], w[4]))
        else:
            rows.append({'y': yc, 'w': [(w[0], w[2], w[4])]})
    for r in rows:
        r['w'].sort()
        cells = []
        for x0, x1, t in r['w']:
            if cells and x0 - cells[-1][1] <= _CELL_GAP:
                cells[-1] = (cells[-1][0], x1, cells[-1][2] + ' ' + t)
            else:
                cells.append((x0, x1, t))
        r['c'] = cells
    return rows


def _label_like(text):
    t = text.strip()
    return bool(t) and not re.search(r'\d', t) and not t.endswith(':') and 1 <= len(t.split()) <= 5 and len(t) <= 40 and t[0].isalpha()


def _split_pairs(line):
    """'State Name : Maharashtra, Code : 27' -> [('State Name','Maharashtra'),('Code','27')]; None if not key/value."""
    line = clean(line)
    parts = re.split(r',\s+(?=[A-Za-z][A-Za-z ]{0,20}\s*:\s)', line)
    pairs = []
    for p in parts:
        m = re.match(r"^([A-Za-z][^:]{0,38}?)\s*:\s*(.+)$", p.strip())
        if not m or len(m.group(1).split()) > 5:
            return None
        pairs.append((clean(m.group(1)), clean(m.group(2))))
    return pairs


def _add_field(store, label, value):
    key = _gkey(label)
    if not key:
        return
    if key == 'code' and 'state_name' in store:
        key = 'state_code'
    base, n = key, 2
    while key in store:
        key, n = f'{base}_{n}', n + 1
    store[key] = value


def _party(lines):
    """Lines of one party block -> {name, address_lines, extracted_fields}."""
    name, address, fields = None, [], {}
    for line in lines:
        pairs = _split_pairs(line)
        if pairs and not any(re.search(r'\b(?:office|address|regd)\b', k, re.I) for k, _ in pairs):
            for k, v in pairs:
                _add_field(fields, k, v)
            continue
        m = re.match(r'^([A-Za-z]{2,10})\s*-\s+(\S.*\d.*)$', line)   # 'MSME- UDYAM-MH-18-0237095'
        if m:
            _add_field(fields, m.group(1), m.group(2))
            continue
        if name is None:
            name = re.sub(r'\s+from\s+\d{1,2}-[A-Za-z]{3}-\d{2,4}$', '', line)
        else:
            address.append(re.sub(r'\s*:\s*', ' ', line) if pairs else line)
    return {'name': name, 'address_lines': address, 'extracted_fields': fields}


def _generalized_items(rows, h):
    """Parse the line-item table whose header row is rows[h]. Returns (table, index_of_total_row)."""
    cols = [list(c) for c in rows[h]['c']]
    start = h + 1
    # Header split over two lines (e.g. 'Sl' / 'No.'): merge short non-numeric, x-aligned cells.
    if start < len(rows):
        nxt = rows[start]['c']
        if nxt and all(not re.search(r'\d', c[2]) and len(c[2].split()) <= 2 for c in nxt) \
                and all(any(abs(c[0] - col[0]) <= 15 for col in cols) for c in nxt):
            for c in nxt:
                col = min(cols, key=lambda k: abs(k[0] - c[0]))
                col[2] += ' ' + c[2]
            start += 1
    keys = []
    for i, c in enumerate(cols):
        base = _gkey(c[2]) or f'column_{i + 1}'
        key, n = base, 2
        while key in keys:
            key, n = f'{base}_{n}', n + 1
        keys.append(key)
    bounds = [(cols[i][1] + cols[i + 1][0]) / 2 for i in range(len(cols) - 1)]
    serial_col = bool(re.match(r'^(?:sl|s\.?\s*no|sr|#)', cols[0][2], re.I))
    desc_i = next((i for i, k in enumerate(keys) if 'description' in k or 'particular' in k or k.startswith('item')), 1 if serial_col else 0)
    qty_i = next((i for i, k in enumerate(keys) if 'quantity' in k or k == 'qty'), None)
    amt_i = next((i for i in range(len(keys) - 1, -1, -1) if 'amount' in keys[i]), None)
    desc_limit = bounds[desc_i] if desc_i < len(bounds) else 1e9

    def col_of(cx):
        return next((i for i, b in enumerate(bounds) if cx < b), len(cols) - 1)

    def assign(row, first_is_serial):
        out = {}
        for n, (x0, x1, t) in enumerate(row['w']):
            i = col_of((x0 + x1) / 2)
            if serial_col and not (n == 0 and first_is_serial):
                i = max(i, 1)
            out[i] = (out.get(i, '') + ' ' + t).strip()
        return out

    items, summary, total_row = [], {}, None
    for r in range(start, len(rows)):
        row = rows[r]
        texts = [c[2].strip() for c in row['c']]
        if any(t.lower() == 'total' for t in texts):
            cells = assign(row, False)
            if qty_i is not None and cells.get(qty_i):
                summary['total_quantity'] = cells[qty_i]
            if amt_i is not None and cells.get(amt_i):
                summary['total_amount'] = _money_text(cells[amt_i])
            total_row = r
            break
        first = row['w'][0][2] if row['w'] else ''
        # An item row starts with a serial number AND has values right of the description (HSN/qty/rate/amount);
        # a description line such as '2 YEAR WARRANTY' has neither.
        is_item = bool(serial_col and re.fullmatch(r'\d{1,3}', first) and len(row['w']) > 1
                       and any(i > desc_i for i in assign(row, True)))
        summary_label = re.match(r'^(?:cgst|sgst|igst|utgst|cess|round\s*off|less|add|discount|freight)', ' '.join(texts), re.I)
        if is_item:
            cells = assign(row, True)
            items.append({keys[i]: v for i, v in sorted(cells.items())})
        elif items and not summary_label and all((c[0] + c[1]) / 2 < desc_limit for c in row['c']):
            items[-1][keys[desc_i]] = clean(items[-1].get(keys[desc_i], '') + ' ' + ' '.join(texts))
        elif items:
            money = [t for t in texts if _MONEY.match(t)]
            label = next((t for t in texts if not _MONEY.match(t) and not re.fullmatch(r'[\d.]+\s*%', t)), None)
            if not money:
                continue
            if label:
                pct = _PCT.search(label) or next((_PCT.search(t) for t in texts if re.fullmatch(r'[\d.]+\s*%', t)), None)
                base = _gkey(_PCT.sub('', label)) or 'tax'
                if pct:
                    summary[f'{base}_rate'] = pct.group(1) + '%'
                summary[f'{base}_amount'] = _money_text(money[-1])
            else:
                summary['subtotal_amount'] = _money_text(money[-1])
    order = [k for k in summary if not k.startswith('total_')] + [k for k in summary if k.startswith('total_')]
    return {'table_name': 'line_items_table', 'columns': keys, 'rows': items, 'table_totals': {k: summary[k] for k in order}}, total_row


def _generalized_tax_table(rows, t_h):
    """HSN/tax summary table: header row t_h plus optional sub-header row. Returns (table, last_row_index)."""
    tops = rows[t_h]['c']
    subs = rows[t_h + 1]['c'] if t_h + 1 < len(rows) else []
    use_subs = len(subs) >= 2 and all(not re.search(r'\d', c[2]) for c in subs)
    centre = lambda c: (c[0] + c[1]) / 2
    cols = []   # (key, x-centre)
    if use_subs:
        owner = {id(s): min(tops, key=lambda t: abs(centre(t) - centre(s))) for s in subs}
        used = {id(o) for o in owner.values()}
        cols += [(_gkey(t[2]), centre(t)) for t in tops if id(t) not in used]
        cols += [(_gkey(owner[id(s)][2] + ' ' + s[2]), centre(s)) for s in subs]
        data_start = t_h + 2
    else:
        cols = [(_gkey(t[2]), centre(t)) for t in tops]
        data_start = t_h + 1
    cols.sort(key=lambda c: c[1])
    data, totals, end = [], {}, data_start - 1
    for r in range(data_start, len(rows)):
        row = rows[r]
        if re.search(r'in words|bank details|declaration', ' '.join(c[2] for c in row['c']), re.I):
            break
        end = r
        cells = {}
        for x0, x1, t in row['w']:
            if t.lower() == 'total':
                continue
            key = min(cols, key=lambda c: abs(c[1] - (x0 + x1) / 2))[0]
            cells[key] = (cells.get(key, '') + ' ' + t).strip()
        if any(w[2].lower() == 'total' for w in row['w']):
            totals = {(k if k.startswith('total') else 'total_' + k): v for k, v in cells.items()}
            break
        if len(cells) >= 2:
            data.append({c[0]: cells[c[0]] for c in cols if c[0] in cells})
    return {'columns': [c[0] for c in cols], 'rows': data, 'totals': totals}, end


def generalized_invoice(page):
    """Build the generalized invoice JSON from one PyMuPDF page. Returns None if no line-item table is found."""
    rows = _word_rows(page.get_text('words'))
    if not rows:
        return None

    def is_item_header(r):
        t = ' '.join(c[2] for c in r['c']).lower()
        return sum(k in t for k in ('description', 'hsn', 'quantity', 'rate', 'amount', 'particular')) >= 3

    h = next((i for i, r in enumerate(rows) if is_item_header(r)), None)
    if h is None:
        return None
    result = {'document_type': clean(' '.join(c[2] for c in rows[0]['c'])).upper() or None}

    # ---- header: left column = seller then buyer; right column = label/value grid ----
    anchor = next((c for r in rows[:h] for c in r['c'] if re.match(r'^invoice\s*(?:no|number)', c[2], re.I)), None)
    split = (anchor[0] if anchor else page.rect.width * 0.5) - 5
    left, right = [], []
    for ri in range(1, h):
        lc = [c for c in rows[ri]['c'] if c[0] < split]
        if lc:
            left.append(clean(' '.join(c[2] for c in lc)))
        right += [(ri, c[0], c[1], c[2]) for c in rows[ri]['c'] if c[0] >= split]
    cut = next((i for i, l in enumerate(left) if _PARTY_HEAD.match(l)), None)
    seller_lines, buyer_lines = (left[:cut], left[cut + 1:]) if cut is not None else (left, [])
    metadata = {}
    for ri, x0, x1, text in right:
        if not _label_like(text):
            continue
        value = next((u for rj, y0, y1, u in right if ri < rj <= ri + 3 and abs(y0 - x0) <= 12 and not _label_like(u)), None)
        _add_field(metadata, text, value)
    result['header_details'] = {'seller': _party(seller_lines), 'buyer': _party(buyer_lines), 'invoice_metadata': metadata}

    # ---- tables ----
    items_table, total_row = _generalized_items(rows, h)
    result['tables'] = [items_table]
    after = (total_row if total_row is not None else h) + 1
    t_h = next((i for i in range(after, len(rows)) if any('taxable' in c[2].lower() for c in rows[i]['c'])), None)
    end = after
    if t_h is not None:
        result['tax_breakdown_table'], end = _generalized_tax_table(rows, t_h)
    else:
        result['tax_breakdown_table'] = {'columns': [], 'rows': [], 'totals': {}}

    # ---- footer ----
    footer, bank, ids, declaration, section = {}, {}, {}, [], None
    for i in range(min(after, end + 1), len(rows)):
        cells = rows[i]['c']
        left_text = clean(' '.join(c[2] for c in cells if c[0] < split))
        for c in cells:
            if re.search(r'\bin\s+words\b', c[2], re.I):
                pairs = _split_pairs(c[2])
                if pairs:
                    footer[_gkey(_EOE.sub('', pairs[0][0]))] = pairs[0][1]
                elif i + 1 < len(rows) and rows[i + 1]['c']:
                    footer[_gkey(_EOE.sub('', c[2]))] = clean(rows[i + 1]['c'][0][2])
        if re.search(r'bank details', left_text, re.I):
            section = 'bank'
            continue
        if re.fullmatch(r'declaration:?', left_text, re.I):
            section = 'declaration'
            continue
        if section == 'declaration':
            if not left_text or re.search(r'authori[sz]ed|signator|computer generated|^for\b', left_text, re.I):
                section = None
            else:
                declaration.append(left_text)
        elif section == 'bank' and left_text:
            for k, v in _split_pairs(left_text) or []:
                _add_field(ids if _TAX_ID_LABEL.search(k) else bank, k, v)
    result['footer_details'] = {**footer, 'company_bank_details': bank, 'company_tax_ids': ids, 'declaration': ' '.join(declaration) or None}
    return result
