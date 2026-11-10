"""Run with optional requirements-docling.txt installed. Timing includes conversion, not per-stage internals."""
import argparse
import json
import statistics
import time
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
from docling.datamodel.accelerator_options import AcceleratorOptions, AcceleratorDevice

CONFIGS = {'full': (True, True), 'no_ocr': (False, True), 'no_tables': (True, False), 'minimal': (False, False)}

def benchmark(path, repeats):
    output = {}
    for name, (ocr, tables) in CONFIGS.items():
        options = PdfPipelineOptions()
        options.do_ocr = ocr
        options.do_table_structure = tables
        options.generate_page_images = False
        if tables:
            options.table_structure_options.mode = TableFormerMode.FAST
        options.accelerator_options = AcceleratorOptions(num_threads=4, device=AcceleratorDevice.CPU)
        start = time.perf_counter()
        converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)})
        creation_s = time.perf_counter() - start
        timings, statuses = [], []
        for i in range(repeats + 1):
            start = time.perf_counter()
            result = converter.convert(path)
            elapsed = time.perf_counter() - start
            timings.append(elapsed)
            statuses.append(str(result.status))
        output[name] = {'converter_creation_s': round(creation_s, 3), 'first_conversion_s': round(timings[0], 3), 'warm_median_s': round(statistics.median(timings[1:]), 3), 'warm_runs_s': [round(t, 3) for t in timings[1:]], 'statuses': statuses}
        print(name, output[name], flush=True)
    return output

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('pdf')
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be >= 1')
    with open('docling_benchmark.json', 'w', encoding='utf-8') as f:
        json.dump(benchmark(args.pdf, args.repeats), f, indent=2)
