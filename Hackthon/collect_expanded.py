"""Download the expanded catalogue and merge source-backed data, resumably.

Run: python collect_expanded.py [--workers 4] [--limit N]
Rebuild tables from downloaded data: python collect_expanded.py --build-only
Existing medicine fields and assets are preserved. Network failures are recorded.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image
from pypdf import PdfReader
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import collect_medications as legacy

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / 'heart_failure_collection_list_expanded.csv'
EXTRA = ['category', 'hf_context_zh', 'mechanism_zh', 'clinical_role_zh',
         'strength_note_zh', 'clinical_source_url', 'mechanism_source_url',
         'dosage_form_as_listed', 'pharmaceutical_company_as_listed',
         'ingredient_content_as_listed', 'product_hf_indication_status',
         'scope_note_zh', 'notes_zh', 'checked_on']


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as fh:
        reader = csv.DictReader(fh)
        return list(reader.fieldnames), list(reader)


def save_json(path, value):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def session():
    s = requests.Session()
    s.headers.update(legacy.UA)
    retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=['GET'], respect_retry_after_header=True)
    s.mount('https://', HTTPAdapter(max_retries=retry))
    return s


def get(s, url):
    time.sleep(0.35)
    r = s.get(url, timeout=(15, 60))
    r.raise_for_status()
    return r


def asset(s, url, folder, stem, kind):
    r = get(s, url)
    data = r.content
    detected = legacy.sniff(data)
    if detected not in ({'pdf'} if kind == 'pdf' else {'jpeg', 'png'}):
        raise ValueError(f'Expected {kind}, got {detected}: {url}')
    ext = {'jpeg': '.jpg', 'png': '.png', 'pdf': '.pdf'}[detected]
    dest = folder / (stem + ext)
    if dest.exists() and dest.read_bytes() != data:
        dest = folder / (stem + '_' + hashlib.sha256(data).hexdigest()[:10] + ext)
    result = dict(url=url, ok=True, kind=detected, bytes=len(data), saved_as=dest.name,
                  sha256=hashlib.sha256(data).hexdigest())
    if kind != 'pdf':
        with Image.open(io.BytesIO(data)) as im:
            result['pixels'] = '%dx%d' % im.size
            im.verify()
    else:
        pdf = PdfReader(io.BytesIO(data))
        if not pdf.pages:
            raise ValueError('PDF has no pages')
        result['pages'] = len(pdf.pages)
    if not dest.exists():
        dest.write_bytes(data)
    return result


def collect(row):
    pzn = row['pzn']
    folder = ROOT / pzn
    folder.mkdir(exist_ok=True)
    raw_path = folder / 'raw_extract.json'
    if raw_path.exists():
        prior = json.loads(raw_path.read_text(encoding='utf-8'))
        if prior.get('expanded_collection_complete'):
            return pzn, prior
    rec = dict(pzn=pzn, list_row=row, retrieved_at=datetime.now(timezone.utc).isoformat(),
               downloads={}, issues=[])
    with session() as s:
        try:
            response = get(s, row['product_source_url'])
            rec['page_http_status'] = response.status_code
            page = legacy.parse_page(response.text, response.url)
            rec['page'] = page
            if page['pzn_on_page'] != pzn:
                raise ValueError(f"PZN mismatch: requested {pzn}, page {page['pzn_on_page']}")
            soup = BeautifulSoup(response.text, 'html.parser')
            for a in soup.select('a[href]'):
                if a.find_parent('dd') and a.get('href', '').startswith(('https://', 'http://')):
                    page['provider_website'] = a['href']
            for n, url in enumerate(page['original_image_urls'], 1):
                try:
                    rec['downloads'][f'image_{n}'] = asset(s, url, folder, f'{pzn}_tablet_{n}', 'image')
                except Exception as exc:
                    rec['issues'].append(f'image_{n}: {exc}')
            if not page['original_image_urls']:
                rec['issues'].append('No original pill images linked on page')
            # The site's packshot metadata can point at the very same pill photo.
            pack = page.get('packshot_url')
            if pack and urljoin(response.url, pack) not in page['original_image_urls']:
                try:
                    rec['downloads']['package'] = asset(s, urljoin(response.url, pack), folder,
                                                         f'{pzn}_package', 'image')
                except Exception as exc:
                    rec['issues'].append(f'package: {exc}')
            links = [p for p in page['pdf_links'] if 'Gebrauchsinformation' in p['label']]
            links = links or page['pdf_links'][:1]
            if not links:
                rec['issues'].append('No leaflet PDF linked on page')
            for link in links[:1]:
                try:
                    response = get(s, link['url'])
                    if legacy.sniff(response.content) == 'pdf':
                        pdf_url = response.url
                    else:
                        urls = set(re.findall(r'https://image\.wub-service\.de/[^\s\"\'<>]+\.pdf', response.text))
                        if len(urls) != 1:
                            raise ValueError(f'Expected one embedded leaflet PDF, found {len(urls)}')
                        pdf_url = urls.pop()
                    d = asset(s, pdf_url, folder, f'{pzn}_leaflet', 'pdf')
                    d.update(viewer_page=link['url'], label=link['label'], doc_date=link['doc_date'])
                    rec['downloads']['leaflet'] = d
                    path = folder / d['saved_as']
                    text = legacy.pdf_text(path)
                    (folder / 'leaflet_text.txt').write_text(text, encoding='utf-8')
                    rec['leaflet_fields'] = legacy.pdf_fields(path)
                except Exception as exc:
                    rec['issues'].append(f'leaflet: {exc}')
        except Exception as exc:
            rec['issues'].append(str(exc))
    rec['expanded_collection_complete'] = bool(
        rec.get('page', {}).get('pzn_on_page') == pzn
        and rec['downloads'].get('leaflet', {}).get('ok')
        and rec['downloads'].get('image_1', {}).get('ok')
        and len([k for k in rec['downloads'] if k.startswith('image_')])
        == len(rec.get('page', {}).get('original_image_urls', [])))
    save_json(raw_path, rec)
    return pzn, rec


def brand_from_product_name(name):
    """Product trade name without strength, dosage form or pack descriptors."""
    name = re.sub(r'\s+\d+(?:[,.]\d+)?-1A\s+Pharma\b', '-1A Pharma', name)
    name = re.split(r'\s+(?:Filmtabletten|Retardtabletten|Retardtabl\.?|Tabletten|'
                    r'Hartkapseln|Schmelztabletten|überzogene|Tabs)\b', name, flags=re.I)[0]
    name = re.sub(r'(?<![\w])\d+(?:[,.]\d+)?\s*(?:mg|µg|mcg|g)?(?![\w])', '', name)
    name = re.sub(r'\s*[/]\s*', ' ', name)
    name = legacy.clean(name).strip(' -/')
    if re.match(r'^BELOC-ZOK\b', name, re.I):
        name = re.sub(r'\s+(?:mite|forte|Herz)\b', '', name, flags=re.I)
    return name


def record_from_raw(row, raw, columns):
    rec = dict.fromkeys(columns, '')
    pzn = row['pzn']
    page = raw.get('page', {})
    verified = page.get('pzn_on_page') == pzn
    rec.update(pzn=pzn, market=row['market'], product_name=row['example_product_name'],
               active_ingredient=row['active_ingredient'],
               strength_as_stated=row['example_strength_as_stated'],
               product_source_url=row['product_source_url'], retrieved_at=raw.get('retrieved_at', ''))
    if verified:
        rec['product_name'] = re.sub(r'^Beipackzettel von\s+', '', page['page_title'])
        rec['dosage_form'] = page.get('dosage_form_on_page', '')
        match = re.match(r'(\d+)\s', page.get('pack_size_on_page', ''))
        rec['pack_size'] = match.group(1) if match else ''
        substances = page.get('substances_on_page', [])
        rec['ingredient_form_as_stated'] = '; '.join(x for x in substances if not x.startswith('='))
        # A provider is not necessarily the authorisation holder or manufacturer.
        physical = page.get('physical_block_raw', '')
        match = re.search(r'Angaben zu Kerben (.+?)(?:Mehr Infos|Sonstige Merkmale|$)', physical)
        rec['score_line'] = match.group(1).strip() if match else ''
        for key, field in [('image_1', 'tablet_image_1'), ('image_2', 'tablet_image_2'),
                           ('leaflet', 'leaflet_path'), ('package', 'package_image')]:
            dl = raw.get('downloads', {}).get(key, {})
            if dl.get('ok'):
                rec[field] = f"{pzn}/{dl['saved_as']}"
    dl = raw.get('downloads', {}).get('leaflet', {})
    date = re.search(r'(\d{2})\.(\d{4})', dl.get('doc_date', ''))
    if date:
        rec['leaflet_revision_date'] = f'{date[2]}-{date[1]}'
    text_path = ROOT / pzn / 'leaflet_text.txt'
    if text_path.exists() and verified:
        rec.update(leaflet_details(text_path.read_text(encoding='utf-8'), raw))
    rec['verification_status'] = ('PZN verified against product page; assets file-validated; '
                                  'automatically extracted; appearance and clinical details require review.'
                                  if verified else 'Product page not verified; catalogue data only; see sources.json.')
    rec['brand'] = brand_from_product_name(rec['product_name'])
    rec['notes'] = ('Source catalogue strength preserved verbatim. Provider is recorded separately from '
                    'manufacturer. Appearance/company details are automatically extracted from the leaflet; '
                    'ambiguous multi-strength appearance fields are left blank. '
                    'Leaflet document date is taken from the product page. ' + '; '.join(raw.get('issues', [])))
    return rec


def leaflet_details(text, raw):
    """Conservative extracts: no visual guesses and no mixing different strengths."""
    out = {}
    flat = legacy.clean(text)
    section = raw.get('leaflet_fields', {}).get('section6_raw', '')
    # Leaflets can describe several strengths. Do not assign their combined appearance.
    if section and len(set(re.findall(r'\b\d+(?:[,.]\d+)?\s*mg\b', section))) <= 1:
        appearance = re.split(r'\b(?:Blisterpackungen|Packungsgrößen|Packungen mit|'
                              r'Die (?:Film)?tabletten sind in|Es werden|Zur Teilung|'
                              r'(?:[A-Z][\w-]*\s+){0,3}ist in Packungen)\b', section)[0].strip()
        if len(appearance) < 1000 and re.search(r'tablett|kapsel', appearance, re.I):
            out['appearance_description_original'] = appearance
            colors = [(r'gelblichweiß|gelblich-weiß', 'yellowish white'),
                      (r'beige.braun', 'beige-brown'), (r'apricot', 'apricot'),
                      (r'weiß|weiss', 'white'), (r'gelb', 'yellow'), (r'rosa', 'pink'),
                      (r'orange', 'orange'), (r'braun', 'brown'), (r'blau', 'blue'),
                      (r'grün', 'green'), (r'rot(?:e|en)?\b', 'red')]
            found = []
            lowered = appearance.lower()
            for pattern, english in colors:
                if re.search(pattern, lowered) and not (english in ['white', 'yellow'] and 'yellowish white' in found):
                    if english == 'brown' and 'beige-brown' in found:
                        continue
                    found.append(english)
            out['color'] = ', '.join(found)
            shapes = [(r'herzförmig', 'heart-shaped'), (r'\brund', 'round'),
                      (r'\boval', 'oval'), (r'länglich', 'oblong'),
                      (r'bikonvex|beidseits gewölbt', 'biconvex'), (r'\bkonvex', 'convex')]
            out['shape'] = ', '.join(english for pattern, english in shapes if re.search(pattern, lowered))
            imprints = re.findall(r'(?:Prägung|prägung|Aufdruck)\s*(?:[„“"\'])([^„“"\']+)', appearance)
            out['imprint'] = '; '.join(imprints)
    headings = list(re.finditer(r'Pharmazeutischer Unternehmer(?:\s+und Hersteller)?\s*', flat))
    if headings:
        match = headings[-1]
        tail = flat[match.end():]
        tail = re.split(r'Diese (?:Packungsbeilage|Gebrauchsinformation)|Dieses Arzneimittel|'
                        r'Weitere Informationsquellen|Versionscode', tail)[0]
        maker = re.search(r'\bHersteller\s+', tail)
        company = tail[:maker.start()] if maker else tail
        company = re.split(r'\bMitvertrieb\b', company)[0].strip()
        if 5 < len(company) < 1500 and re.search(r'GmbH|AG\b|B\.V\.|Ltd|S\.A\.|Pharma|Healthcare', company):
            out['pharmaceutical_company'] = company
        if maker:
            manufacturer = tail[maker.end():].strip()
        elif 'und Hersteller' in match[0]:
            manufacturer = company
        else:
            manufacturer = ''
        if 5 < len(manufacturer) < 2500 and re.search(r'GmbH|AG\b|B\.V\.|Ltd|S\.A\.|Pharma|Healthcare|S\.r\.l|S\.R\.L', manufacturer):
            out['manufacturer'] = manufacturer
    return out


def merge(rows, raws, backup):
    columns, existing_rows = read_csv(ROOT / 'medicines.csv')
    columns += [x for x in EXTRA if x not in columns]
    existing = {r['pzn']: r for r in existing_rows}
    for row in rows:
        pzn = row['pzn']
        new = pzn not in existing
        raw = raws.get(pzn)
        if new and raw is None:
            continue
        automatic = bool(raw and not new and existing[pzn].get('verification_status', '').startswith(
            ('PZN verified against product page; assets file-validated;', 'Product page not verified; catalogue data only;')))
        rec = record_from_raw(row, raw, columns) if new or automatic else existing[pzn]
        if not new and raw:
            candidate = record_from_raw(row, raw, columns)
            for key in legacy.COLUMNS:
                if not rec.get(key) and candidate.get(key):
                    rec[key] = candidate[key]
        rec.update({k: row.get(k, '') for k in EXTRA})
        if not rec.get('brand', '').strip():
            rec['brand'] = brand_from_product_name(rec['product_name'])
        existing[pzn] = rec
        if new or automatic or (raw and not (ROOT / pzn / 'sources.json').exists()):
            sources = dict(pzn=pzn, input_file=INPUT.name, collection_row=row,
                           product_page=raw.get('page', {}), downloads=raw.get('downloads', {}),
                           unresolved_issues=raw.get('issues', []),
                           field_sources={k: {'value': rec.get(k, ''), 'source':
                              ('product_name with strength and dosage form removed' if k == 'brand' else
                               'leaflet PDF, automatic extract; requires review' if k in ['color', 'shape', 'imprint',
                               'appearance_description_original', 'pharmaceutical_company', 'manufacturer'] else
                               'expanded input catalogue, not independently clinically reviewed' if k in EXTRA
                               or k in ['active_ingredient', 'strength_as_stated', 'market'] else
                               'PZN-verified product page/download metadata; see raw_extract.json')}
                              for k in columns if rec.get(k)},
                           reuse_permission='Not checked; original downloaded files and watermarks preserved.')
            save_json(ROOT / pzn / 'sources.json', sources)
        elif (ROOT / pzn / 'sources.json').exists():
            path = ROOT / pzn / 'sources.json'
            sources = json.loads(path.read_text(encoding='utf-8'))
            sources['expanded_catalogue'] = dict(input_file=INPUT.name, row=row)
            save_json(path, sources)
    records = list(existing.values())
    temp_csv = ROOT / 'medicines.updated.csv'
    with temp_csv.open('w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=columns, quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows({k: r.get(k, '') for k in columns} for r in records)
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = openpyxl.load_workbook(ROOT / 'medicines.xlsx')
    ws = wb['Medicines']
    for ci, name in enumerate(columns, 1):
        cell = ws.cell(1, ci, name)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = PatternFill('solid', fgColor='1F4E79')
    for ri, record in enumerate(records, 2):
        for ci, name in enumerate(columns, 1):
            cell = ws.cell(ri, ci)
            cell.value = record.get(name, '')
            cell.data_type = 's'
            cell.alignment = Alignment(vertical='top', wrap_text=True)
            if name == 'pzn':
                cell.number_format = '@'
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = 'A2'
    for ci in range(27, len(columns) + 1):
        ws.column_dimensions[openpyxl.utils.get_column_letter(ci)].width = 45
    temp_xlsx = ROOT / 'medicines.updated.xlsx'
    wb.save(temp_xlsx)
    temp_csv.replace(ROOT / 'medicines.csv')
    temp_xlsx.replace(ROOT / 'medicines.xlsx')
    report = []
    for row in rows:
        pzn = row['pzn']
        rec = existing.get(pzn, {})
        raw = raws.get(pzn, {})
        report.append(dict(pzn=pzn, product_name=rec.get('product_name', row['example_product_name']),
                           status='complete' if rec.get('leaflet_path') and rec.get('tablet_image_1') else 'incomplete',
                           image_count=sum(bool(rec.get(k)) for k in ['tablet_image_1', 'tablet_image_2']),
                           leaflet_path=rec.get('leaflet_path', ''),
                           issues='; '.join(raw.get('issues', []))))
    with (ROOT / 'expanded_collection_status.csv').open('w', encoding='utf-8-sig', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=list(report[0]))
        w.writeheader()
        w.writerows(report)
    print(f'Saved {len(records)} medicines; {sum(r["status"] == "complete" for r in report)}/{len(rows)} catalogue entries have PDF and pill images.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--build-only', action='store_true', help='Rebuild from local extracts without network requests')
    args = parser.parse_args()
    _, rows = read_csv(INPUT)
    if len({r['pzn'] for r in rows}) != len(rows) or any(not re.fullmatch(r'\d{8}', r['pzn']) for r in rows):
        raise ValueError('Catalogue requires unique eight-digit PZNs')
    _, old = read_csv(ROOT / 'medicines.csv')
    backup = ROOT / 'backups' / datetime.now().strftime('%Y%m%d_%H%M%S')
    backup.mkdir(parents=True)
    for name in ['medicines.csv', 'medicines.xlsx']:
        shutil.copy2(ROOT / name, backup / name)
    if args.build_only:
        raws = {}
        for row in rows:
            path = ROOT / row['pzn'] / 'raw_extract.json'
            if path.exists():
                raws[row['pzn']] = json.loads(path.read_text(encoding='utf-8'))
        merge(rows, raws, backup)
        return
    old_pzns = {r['pzn'] for r in old}
    needed = [r for r in rows if r['pzn'] not in old_pzns or
              not next((m.get('leaflet_path') and m.get('tablet_image_1') for m in old if m['pzn'] == r['pzn']), False)]
    if args.limit:
        needed = needed[:args.limit]
    raws = {}
    print(f'Catalogue: {len(rows)}; existing: {len(old)}; fetching: {len(needed)}; backup: {backup}', flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(collect, row): row['pzn'] for row in needed}
        for n, future in enumerate(as_completed(futures), 1):
            pzn, raw = future.result()
            raws[pzn] = raw
            print(f'[{n}/{len(needed)}] {pzn}: {list(raw["downloads"])} issues={raw["issues"]}', flush=True)
    merge(rows, raws, backup)


if __name__ == '__main__':
    main()
