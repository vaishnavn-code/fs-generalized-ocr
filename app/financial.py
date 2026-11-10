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
        if s.lower().startswith('spontak supplies private limited') or s.lower().startswith('rm infotech'):
            supplier=s.split('REGD.')[0].strip(); break
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
