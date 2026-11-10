import argparse
import json
import statistics
import time
from pathlib import Path
from app.extractor import extract_pdf

parser = argparse.ArgumentParser()
parser.add_argument('pdf')
parser.add_argument('--repeats', type=int, default=5)
args = parser.parse_args()
if args.repeats < 1:
    parser.error('--repeats must be >= 1')
data = Path(args.pdf).read_bytes()
run_times = []
for i in range(args.repeats + 1):
    t = time.perf_counter()
    result = extract_pdf(data)
    if i:
        run_times.append(time.perf_counter() - t)
summary = {'page_sources': [p['source'] for p in result['pages']], 'fields': result['fields'], 'table_count': len(result['tables']), 'warm_median_s': round(statistics.median(run_times), 3), 'warm_runs_s': [round(x, 3) for x in run_times]}
print(json.dumps(summary, indent=2))
