"""Prepare evidence, visually review missing appearance, then normalize CSV/XLSX.

python normalize_medicines.py prepare
python normalize_medicines.py apply
"""
import csv
import json
import re
import shutil
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
from openpyxl import load_workbook
from openpyxl.styles import Alignment, Font, PatternFill

import collect_expanded as collector
from collect_medications import clean, parse_page, pdf_text

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'normalization'
OUT.mkdir(exist_ok=True)
NEW = ['ingredient_key', 'strength_label', 'strength_value', 'strength_value_2',
       'strength_values', 'strength_unit', 'strength_source', 'form_class', 'form_class_source',
       'color_primary', 'color_secondary', 'color_primary_source', 'color_secondary_source',
       'shape_base', 'shape_base_source', 'profile', 'profile_source',
       'diameter_mm', 'length_mm', 'width_mm', 'height_mm', 'mass_mg', 'dimensions_source',
       'imprint_clean', 'imprint_conflict', 'imprint_clean_source',
       'score_type', 'score_sides', 'splittable', 'score_type_source', 'score_sides_source',
       'splittable_source', 'pharmaceutical_company_source', 'exclude', 'needs_review',
       'review_reasons', 'demo_ready']


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def read():
    with (ROOT / 'medicines.csv').open(encoding='utf-8-sig', newline='') as f:
        rd = csv.DictReader(f)
        return list(rd.fieldnames), list(rd)


def evidence(row):
    pzn = row['pzn']
    path = OUT / 'page_evidence' / (pzn + '.json')
    if path.exists():
        cached = json.loads(path.read_text(encoding='utf-8'))
        if cached.get('verified'):
            return pzn, cached
    ev = {'url': row['product_source_url'], 'verified': False}
    try:
        with collector.session() as s:
            response = collector.get(s, row['product_source_url'])
        soup = BeautifulSoup(response.text, 'html.parser')
        page = parse_page(response.text, response.url)
        if page['pzn_on_page'] != pzn:
            raise ValueError('PZN mismatch')
        text = clean(soup.get_text(' '))
        match = re.search(r'Informationen zu Teilbarkeit und Zubereitung (.+?)(?:Video:|Gegenanzeigen)', text)
        ev.update(verified=True, page=page, divisibility=match[1] if match else '')
    except Exception as exc:
        ev['error'] = str(exc)
    dump(path, ev)
    return pzn, ev


def pdf_appearance(row):
    p = ROOT / row['pzn'] / 'leaflet_text.txt'
    text = p.read_text(encoding='utf-8') if p.exists() else ''
    if not text and row.get('leaflet_path'):
        text = pdf_text(ROOT / row['leaflet_path'])
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'([a-zäöüß])-\s*\n\s*([a-zäöüß])', r'\1\2', text)
    flat = clean(text)
    flat = re.sub(r'wei\s+ß\s*e', 'weiße', flat)
    flat = re.sub(r'w\s+eiße', 'weiße', flat, flags=re.I)
    # Include hyphenated PDF headings and extraction gaps such as Packun g.
    heads = list(re.finditer(r'Wie\s+[^.!?]{1,180}?auss(?:ieht|ehen)\s+und\s+Inhalt\s+der\s+Pa\s*-?\s*ckun\s*g', flat, re.I))
    section = ''
    if heads:
        section = flat[heads[-1].end():]
        section = re.split(r'Pharmazeutisch(?:er|e)\s+Unternehmer|Zulassungsinhaber', section)[0]
    if not section:
        raw_path = ROOT / row['pzn'] / 'raw_extract.json'
        raw = json.loads(raw_path.read_text(encoding='utf-8')) if raw_path.exists() else {}
        section = raw.get('leaflet_fields', {}).get('section6_raw', '')
    if not section:
        return '', flat
    section = section.strip()
    # Product paragraphs start with a trade name and a strength. Isolate exactly
    # the target strength, never descriptions of other strengths in the same PDF.
    starts = list(re.finditer(r'(?:^|(?<=[.!?])\s+)([A-ZÄÖÜ][A-Za-zÄÖÜäöüß®0-9 ,/–—\-]{1,85}?)'
                             r'\s+(\d+(?:[,.]\d+)?)\s*mg(?:\s*/\s*(\d+(?:[,.]\d+)?)\s*mg)?'
                             r'\s+(?:(?:Film|Retard)?tabletten\s+)?(?:sind|ist)\b', section))
    target = re.findall(r'(\d+(?:[,.]\d+)?)\s*mg', row['product_name'])
    target = [float(x.replace(',', '.')) for x in target]
    if starts:
        matches = []
        for i, m in enumerate(starts):
            values = [float(v.replace(',', '.')) for v in m.groups()[1:] if v]
            if target and values == target[:len(values)]:
                if re.match(r'\s+(?:in|erhältlich|Packungen)',section[m.end():]):
                    continue
                end = starts[i+1].start() if i+1 < len(starts) else len(section)
                matches.append(section[m.start(1):end])
        if matches:
            section = matches[0]
        elif len(starts) > 1:
            return '', flat
    # If multiple strength values remain before packaging, do not use the block.
    section = re.split(r'\b(?:Blisterpackungen|Packungsgrößen|Packungen mit|'
                       r'Die (?:Film)?tabletten (?:sind|werden) in|Es werden|Zur Teilung|'
                       r'[A-Z][\w-]*(?:\s+[\w-]+){0,2}\s+ist in|Pharmazeutischer)\b', section)[0]
    strengths = set(re.findall(r'\b\d+(?:[,.]\d+)?\s*mg\b', section))
    if len(strengths) > (2 if len(target) == 2 else 1):
        return '', flat
    if len(section) > 1400:
        return '', flat
    return clean(section), flat


def number(value):
    return float(value.replace(',', '.'))


def normalized(row, ev):
    r = dict(row)
    for c in NEW:
        r[c] = ''
    reasons = []
    def review(reason):
        if reason not in reasons:
            reasons.append(reason)
    ingredient = r['active_ingredient'].strip()
    if ingredient.lower().replace(' ', '') in ['bisoprololfumarat', 'bisoprololfumarate']:
        ingredient = 'Bisoprolol'
    if 'sacubitril' in ingredient.lower() and 'valsartan' in ingredient.lower():
        ingredient = 'Sacubitril / Valsartan'
    r['active_ingredient'] = ingredient
    r['ingredient_key'] = re.sub(r'\s+', '', ingredient).lower()
    r['strength_label'] = r['strength_as_stated']
    strength_text = r['strength_as_stated']
    if ingredient == 'Sacubitril / Valsartan':
        strength_text = r['product_name']
    matches = re.findall(r'(\d+(?:[,.]\d+)?)\s*(mg|µg|mcg|g)\b', strength_text, re.I)
    if matches:
        values = [number(n) for n,u in matches]
        r['strength_value'] = values[0]
        r['strength_value_2'] = values[1] if len(values)>1 else ''
        r['strength_values'] = json.dumps(values, separators=(',', ':'))
        r['strength_unit'] = matches[0][1].lower()
        r['strength_source'] = 'text'
        if len(set(u.lower() for n,u in matches))>1:
            review('mixed_strength_units')
    else:
        review('strength_unparsed')
    form = (r['dosage_form'] or r['dosage_form_as_listed']).lower()
    r['form_class'] = ('口腔崩解片' if 'schmelz' in form else '缓释片' if 'retard' in form
                       else '胶囊' if 'kapsel' in form else '薄膜片' if 'film' in form
                       else '普通片' if form == 'tabletten' else '')
    if 'überzogene' in form:
        review('coated_tablet_unspecified_coating')
    if r['form_class']:
        r['form_class_source'] = 'text'
    r['exclude'] = 'true' if 'AQUMELDI' in r['product_name'].upper() else 'false'
    if r['form_class'] == '胶囊':
        review('capsule_requires_review')
    if not r['form_class']:
        review('form_class_unmapped')
    appearance = r['appearance_description_original']
    extracted, pdf = pdf_appearance(r)
    # Improved strength-specific text supersedes prior incomplete automated extracts.
    if extracted and re.search(r'weiß|weiss|gelb|rosa|rund|oval|länglich|konvex|tablett',extracted,re.I) and (not appearance or r['verification_status'].startswith('PZN verified')):
        appearance = extracted
        r['appearance_description_original'] = appearance
    description = appearance.lower()
    colors = parse_colors(description) if appearance else []
    if not colors:
        colors = parse_colors(r['color'].lower())
        description = r['color'].lower()
    if colors:
        r['color_primary'] = colors[0]
        r['color_secondary'] = colors[1] if len(colors)>1 else ''
        r['color_primary_source'] = 'text'
        if r['color_secondary']:
            r['color_secondary_source'] = 'text'
        if len(colors)>1 or re.search(r'hellrot|blassrot|light red|apricot|pfirsich|ocker|creme|cream|beige.braun|gesprenkelt|marmoriert|mottled|speckled', description):
            review('ambiguous_or_multicolor')
    shape_text = appearance.lower()
    if not re.search(r'herzförmig|heart|rautenförmig|diamond|kapsel|capsule|\brund|\bround|oval|länglich|oblong|konvex|convex|gewölbt|flach|flat', shape_text):
        shape_text = r['shape'].lower()
    for pattern, shape in [(r'herzförmig|heart', 'heart'), (r'rautenförmig|diamant|diamond', 'diamond'),
                           (r'kapsel(?:förmig)|capsule.shaped|hartgelatinekapsel', 'capsule'),
                           (r'\brund|\bround|zylindrisch', 'round'), (r'\boval', 'oval'),
                           (r'länglich|längliche|oblong|langgestreckt', 'oblong')]:
        if re.search(pattern, shape_text):
            r['shape_base'] = shape; r['shape_base_source'] = 'text'; break
    if re.search(r'bikonvex|biconvex|beidseits gewölbt|beidseitig gewölbt', shape_text):
        r['profile'] = 'biconvex'
    elif re.search(r'konvex|convex|gewölbt', shape_text):
        r['profile'] = 'convex'
    elif re.search(r'\bflach|\bflat|planparallel', shape_text):
        r['profile'] = 'flat'
    if r['profile']:
        r['profile_source'] = 'text'
    physical = ev.get('page', {}).get('physical_block_raw', '')
    for label, field, unit in [('Durchmesser','diameter_mm','mm'), ('Länge','length_mm','mm'),
                               ('Breite','width_mm','mm'), ('Höhe','height_mm','mm'), ('Masse','mass_mg','mg')]:
        m = re.search(label+r'\s*:\s*(\d+(?:[,.]\d+)?)\s*'+unit, physical)
        if m:
            r[field] = number(m[1]); r['dimensions_source'] = 'text'
    if not r['length_mm']:
        m = re.search(r'(\d+(?:[,.]\d+)?)\s*mm\s*[x×]\s*(\d+(?:[,.]\d+)?)\s*mm', appearance)
        if m:
            r['length_mm'],r['width_mm'] = number(m[1]),number(m[2]);r['dimensions_source']='text'
    im = r['imprint'].strip()
    bad = bool(re.search(r'CONFLICT|auf einer|auf der|Tablette|Bruchkerbe|geteilt|Seite|Dosen', im, re.I))
    r['imprint_conflict'] = 'true' if bad else 'false'
    quoted = re.findall(r'[„“"«]([^„“"”«»]{1,25})[“”"»]', appearance)
    quoted = [clean(q) for q in quoted if re.fullmatch(r'[A-Za-z0-9 ,./+\-]+',clean(q))]
    page_im = re.search(r'Sonstige Merkmale \(Prägungen/Aufschrift\) (.+?)(?:Besonderheiten|Mehr Infos|$)',physical)
    if page_im:
        candidate = page_im[1].strip(' "„“”')
        if len(candidate)<50 and not re.search(r'Tablette|auf der',candidate):
            r['imprint_clean'] = candidate; r['imprint_clean_source']='text'
    if not r['imprint_clean'] and quoted:
        r['imprint_clean'] = ' / '.join(dict.fromkeys(quoted));r['imprint_clean_source']='text'
    if not r['imprint_clean'] and im and not bad:
        if 'on one side' in im or 'on the other' in im:
            tokens=re.findall(r'"([A-Za-z0-9 /]+)"', im)
            r['imprint_clean']=' / '.join(tokens)
            if 'logo' in im.lower():
                r['imprint_clean'] += ' / logo'
        elif re.fullmatch(r'[A-Za-z0-9 /.,+\-"“”„]+',im):
            r['imprint_clean']=im.strip(' "„“”')
        if r['imprint_clean']: r['imprint_clean_source']='text'
    if bad:
        review('imprint_conflict_or_parse_residue')
    score = physical or r['score_line']
    score_lower=score.lower()
    if 'ohne kerbe' in score_lower or score_lower.strip()=='none':
        r['score_type']='none'
    elif re.search(r'parallelkerbe',score_lower):
        review('parallel_score_outside_requested_enum')
    elif re.search(r'kreuzkerbe|cross',score_lower):
        r['score_type']='cross'
    elif re.search(r'einzelkerbe|single|one score|score groove|score line on',score_lower):
        r['score_type']='single'
    if r['score_type']:
        r['score_type_source']='text'
    if r['score_type']!='none':
        if re.search(r'beidseitig|both sides',score_lower):r['score_sides']='both'
        elif re.search(r'einseitig|one side|opposite|imprinted side',score_lower):r['score_sides']='one'
        if r['score_sides']:r['score_sides_source']='text'
    division=ev.get('divisibility','')+' '+appearance
    if re.search(r'nicht (?:in .*?)?(?:dosisgleich|gleiche)|nicht zur (?:Teilung|Aufteilung)|not for dividing',division,re.I):
        r['splittable']='no'
    elif re.search(r'(?:in\s+\d+\s+dosisgleiche\s+\w+\s+teilbar|in\s+gleiche\s+(?:Dosen|Hälften|Viertel)\s+geteilt)',division,re.I):
        r['splittable']='yes'
    elif re.search(r'nicht teilbar|nicht geteilt werden|nicht (?:zerbrochen|geteilt)',division,re.I):
        r['splittable']='no'
    if r['splittable']:r['splittable_source']='text'
    if not r['pharmaceutical_company'].strip() and r['pharmaceutical_company_as_listed'].strip():
        r['pharmaceutical_company']=r['pharmaceutical_company_as_listed']
        r['pharmaceutical_company_source']='text'
        review('company_filled_from_provider_listing')
    elif r['pharmaceutical_company']:
        r['pharmaceutical_company_source']='text'
    r['review_reasons']='; '.join(reasons)
    return r


def parse_colors(text):
    matches=[]
    patterns=[('white',r'weiß|weiss|\bwhite\b|off.white|gebrochen'),('yellow',r'gelb|yellow|ocker'),
              ('orange',r'orange|apricot|aprikos|pfirsich'),('pink',r'rosa|pink'),('red',r'\brot(?:e|en|es)?\b|hellrot|blassrot|rötlich|\bred\b'),
              ('brown',r'braun|brown'),('green',r'grün|green'),('blue',r'blau|blue'),
              ('violet',r'violett|violet|lila'),('grey',r'grau|grey|gray'),('beige',r'beige|creme|cream')]
    # Only the appearance paragraph enters here, never the composition/excipients.
    for color,pattern in patterns:
        m=re.search(pattern,text)
        if m:matches.append((m.start(),color))
    return [c for _,c in sorted(matches)]


def prepare():
    (OUT/'page_evidence').mkdir(exist_ok=True)
    _,rows=read(); pages={}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(evidence,r) for r in rows]
        for n,future in enumerate(as_completed(futures),1):
            pzn,ev=future.result();pages[pzn]=ev
            if n%25==0 or n==len(rows):print(f'Page evidence {n}/{len(rows)}',flush=True)
    prepared=[normalized(r,pages[r['pzn']]) for r in rows]
    dump(OUT/'prepared_rows.json',prepared)
    missing=[r for r in prepared if (not r['color_primary'] or not r['shape_base']) and r['tablet_image_1']]
    print('Need image review:',len(missing),flush=True)
    for start in range(0,len(missing),20):
        batch=missing[start:start+20]
        canvas=Image.new('RGB',(1200,5*260),'white');draw=ImageDraw.Draw(canvas)
        for i,r in enumerate(batch):
            x=(i%4)*300;y=(i//4)*260
            draw.text((x+5,y+3),r['pzn']+' '+r['brand'][:19],fill='black')
            draw.text((x+5,y+19),'need '+('color ' if not r['color_primary'] else '')+('shape' if not r['shape_base'] else ''),fill='black')
            for j,key in enumerate(['tablet_image_1','tablet_image_2']):
                if r[key]:
                    with Image.open(ROOT/r[key]) as im:
                        im=im.convert('RGB');im.thumbnail((146,210))
                        canvas.paste(im,(x+j*150+(146-im.width)//2,y+42))
        path=OUT/f'image_review_{start//20+1}.jpg';canvas.save(path,quality=94)
        print(path,flush=True)


def apply():
    columns,original=read()
    rows=json.loads((OUT/'prepared_rows.json').read_text(encoding='utf-8'))
    reviews=json.loads((OUT/'image_reviews.json').read_text(encoding='utf-8'))
    for r in rows:
        reasons=[x for x in r['review_reasons'].split('; ') if x]
        for field,value in reviews.get(r['pzn'],{}).items():
            if field=='note':
                reasons.append(value);continue
            if not r.get(field) and value:
                r[field]=value;r[field+'_source']='image'
        if not r['color_primary']:reasons.append('color_missing')
        if not r['shape_base']:reasons.append('shape_missing')
        if not r['tablet_image_1']:reasons.append('pill_image_missing')
        r['needs_review']='true' if reasons else 'false'
        r['review_reasons']='; '.join(dict.fromkeys(reasons))
        r['demo_ready']='true' if (r['tablet_image_1'] and (ROOT/r['tablet_image_1']).is_file()
            and r['color_primary'] and r['shape_base'] and r['strength_value']!='' and r['exclude']=='false') else 'false'
    backup=ROOT/'backups'/('normalized_'+datetime.now().strftime('%Y%m%d_%H%M%S'));backup.mkdir()
    for filename in ['medicines.csv','medicines.xlsx']:shutil.copy2(ROOT/filename,backup/filename)
    fields=columns+[c for c in NEW if c not in columns]
    with (ROOT/'medicines.normalized.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,quoting=csv.QUOTE_ALL);w.writeheader();w.writerows(rows)
    wb=load_workbook(ROOT/'medicines.xlsx');ws=wb['Medicines']
    for ci,c in enumerate(fields,1):
        cell=ws.cell(1,ci,c);cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='1F4E79')
    numeric={'strength_value','strength_value_2','diameter_mm','length_mm','width_mm','height_mm','mass_mg'}
    for ri,r in enumerate(rows,2):
        for ci,c in enumerate(fields,1):
            cell=ws.cell(ri,ci);cell.value=r[c]
            if c not in numeric:cell.data_type='s'
            if c=='pzn':cell.number_format='@'
            cell.alignment=Alignment(vertical='top',wrap_text=True)
    ws.auto_filter.ref=ws.dimensions;ws.freeze_panes='A2'
    for ci in range(len(columns)+1,len(fields)+1):
        ws.column_dimensions[__import__('openpyxl').utils.get_column_letter(ci)].width=24
    wb.save(ROOT/'medicines.normalized.xlsx')
    (ROOT/'medicines.normalized.csv').replace(ROOT/'medicines.csv')
    (ROOT/'medicines.normalized.xlsx').replace(ROOT/'medicines.xlsx')
    dump(OUT/'normalized_rows.json',rows)
    report={'rows':len(rows),'demo_ready':sum(r['demo_ready']=='true' for r in rows),
            'needs_review':sum(r['needs_review']=='true' for r in rows),
            'excluded':[r['pzn'] for r in rows if r['exclude']=='true'],
            'color_missing':[r['pzn'] for r in rows if not r['color_primary']],
            'shape_missing':[r['pzn'] for r in rows if not r['shape_base']],
            'image_filled':[r['pzn'] for r in rows if r['color_primary_source']=='image' or r['shape_base_source']=='image'],
            'backup':str(backup)}
    dump(OUT/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    {'prepare':prepare,'apply':apply}[sys.argv[1]]()
