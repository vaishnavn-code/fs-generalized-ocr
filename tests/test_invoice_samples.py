"""Run against the four supplied invoice PDFs (kept outside the ZIP)."""
from pathlib import Path
import sys
from app.extractor import extract_pdf

CASES = [
 ('Tax Invoice_SSPL 25-26 1843_02_02_26.pdf','SSPL/25-26/1843','2026-02-02',1,2950.0),
 ('263 Fsl004 fan (1).pdf','GST/263/25-26','2025-09-01',2,1652.0),
 ('269 fsl154 (1).pdf','GST/269/25-26','2025-09-04',2,5900.0),
 ('Tax Invoice_SSPL 25-26 1813_30_01_26.pdf','SSPL/25-26/1813','2026-01-30',3,3486.0),
]

def run(folder):
    for name,number,date,count,total in CASES:
        path=Path(folder)/name
        if not path.exists():
            print('MISSING',path);return 1
        result=extract_pdf(path.read_bytes())
        f=result['financial']
        assert f['invoice_number']==number,(name,'number',f)
        assert f['invoice_date']==date,(name,'date',f)
        assert f['item_count']==count,(name,'items',f)
        assert abs(f['invoice_total']-total)<.01,(name,'total',f)
        assert f['validation']['status']=='passed',(name,'validation',f)
        print('PASS',name,'time_ms=',result['total_ms'])
    return 0

if __name__=='__main__':
    sys.exit(run(sys.argv[1] if len(sys.argv)>1 else '.'))
