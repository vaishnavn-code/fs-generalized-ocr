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
