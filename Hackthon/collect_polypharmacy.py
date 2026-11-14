"""Collect all assets and fill the existing 26-column polypharmacy records.

python collect_polypharmacy.py fetch
python collect_polypharmacy.py build
"""
import csv
import json
import re
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw
from openpyxl import load_workbook

import collect_expanded as collect
import recheck_medicines as check
import finalize_medicines as final

ROOT = Path(__file__).resolve().parent
LIST = ROOT / 'polypharmacy_collection_list.csv'
WORK = ROOT / 'polypharmacy_collection'
WORK.mkdir(exist_ok=True)


def read(path):
    with path.open(encoding='utf-8-sig', newline='') as f:
        rd=csv.DictReader(f);return list(rd.fieldnames),list(rd)


def prepare():
    _,rows=read(LIST)
    assert len({r['pzn'] for r in rows})==len(rows)
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(collect.collect,r) for r in rows]
        for n,future in enumerate(as_completed(futures),1):
            pzn,raw=future.result()
            print(f'[{n}/{len(rows)}] {pzn}: {list(raw["downloads"])}; {raw["issues"]}',flush=True)
    derive()


def derive():
    columns,existing=read(ROOT/'medicines.csv');by_pzn={r['pzn']:r for r in existing}
    _,listing=read(LIST);output=[]
    for source in listing:
        pzn=source['pzn'];raw=json.loads((ROOT/pzn/'raw_extract.json').read_text(encoding='utf-8'))
        page=raw.get('page',{})
        assert page.get('pzn_on_page')==pzn,pzn
        r=dict(by_pzn.get(pzn,dict.fromkeys(columns,'')))
        r.update(pzn=pzn,market=source['market'],product_name=re.sub(r'^Beipackzettel von\s+','',page['page_title']),
                 active_ingredient=source['active_ingredient'],strength_as_stated=source['example_strength_as_stated'],
                 dosage_form=page['dosage_form_on_page'],product_source_url=source['product_source_url'],
                 retrieved_at=raw['retrieved_at'][:10])
        strength=re.search(r'\d+(?:[,.]\d+)?\s*(?:mg|µg|mcg)\b',source['example_strength_as_stated'])
        if strength:r['strength_as_stated']=strength[0]
        pack=re.match(r'(\d+)\s',page['pack_size_on_page'])
        r['pack_size']=pack[1] if pack else ''
        if not r['brand']:r['brand']=collect.brand_from_product_name(r['product_name'])
        for key,field in [('image_1','tablet_image_1'),('image_2','tablet_image_2'),('leaflet','leaflet_path'),('package','package_image')]:
            d=raw['downloads'].get(key,{})
            r[field]=f"{pzn}/{d['saved_as']}" if d.get('ok') else ''
        text=check.leaflet_text(r)
        company,maker,company_notes=check.derive_companies(text)
        if company:r['pharmaceutical_company']=final.company_names(company)
        else:r['pharmaceutical_company']=final.company_names(r['pharmaceutical_company'])
        r['manufacturer']=final.company_names(maker)
        ingredient,_=check.ingredient_form(text,page.get('substances_on_page',[]))
        if ingredient and not r['ingredient_form_as_stated']:r['ingredient_form_as_stated']=ingredient
        app,_=check.appearance(r,text)
        if app and not re.search(r'tablett|kapsel',app,re.I):app=''
        r['appearance_description_original']=app
        color,shape=check.colour_shape(app)
        r['color']=color;r['shape']=shape
        physical=page.get('physical_block_raw','')
        r['score_line']=check.score_from_page(physical)
        r['imprint']=check.page_imprint(physical)
        if not r['imprint'] and app:
            toks=re.findall(r'[„“"]([^„“"”]{1,25})[“”"]',app)
            r['imprint']=' / '.join(dict.fromkeys(x.strip() for x in toks if re.fullmatch(r'[A-Za-z0-9 ,/.-]+',x.strip())))
        revision=check.revision(text)
        if revision:r['leaflet_revision_date']=revision
        notes=[]
        if not app:notes.append('Appearance not extracted from leaflet.')
        notes.extend(raw['issues'])
        notes.extend(company_notes)
        r['notes']=' '.join(notes)
        r['verification_status']='Product page and leaflet checked; tablet photos reviewed; not pharmacist verified.'
        output.append(r)
    collect.save_json(WORK/'prepared_rows.json',output)
    for start in range(0,len(output),16):
        batch=output[start:start+16]
        canvas=Image.new('RGB',(1440,4*300),'white');draw=ImageDraw.Draw(canvas)
        for i,r in enumerate(batch):
            x=i%4*360;y=i//4*300
            draw.text((x+5,y+4),r['pzn']+' '+r['brand'][:23],fill='black')
            draw.text((x+5,y+20),'text: '+r['color'][:18]+'; '+r['shape'][:22],fill='black')
            for j,key in enumerate(['tablet_image_1','tablet_image_2']):
                if r[key]:
                    with Image.open(ROOT/r[key]) as im:
                        im=im.convert('RGB');im.thumbnail((176,250));canvas.paste(im,(x+j*180,y+45))
        canvas.save(WORK/f'review_{start//16+1}.jpg',quality=95)
    print('Prepared rows and image review sheets.',flush=True)


def build():
    fields,rows=read(ROOT/'medicines.csv');assert fields==final.COLUMNS
    prepared=json.loads((WORK/'prepared_rows.json').read_text(encoding='utf-8'))
    photos=json.loads((WORK/'photo_review.json').read_text(encoding='utf-8'))
    corrections=json.loads((WORK/'reviewed_corrections.json').read_text(encoding='utf-8'))
    existing={r['pzn']:r for r in rows}
    for r in prepared:
        notes=[];patch=photos.get(r['pzn'],{})
        correction=corrections.get(r['pzn'],{})
        for field,value in correction.items():
            r[field]=value
        for field,value in patch.items():
            if field=='notes':notes.append(value);continue
            if field=='appearance_description_original' or not r[field]:
                r[field]=value
                if field in ['color','shape','imprint']:notes.append(field)
        if any(x in notes for x in ['color','shape','imprint']):
            r['notes']='Appearance supplemented from tablet photos.'
        else:r['notes']=''
        extras=[x for x in notes if x not in ['color','shape','imprint']]
        if extras:r['notes']+=' '+' '.join(extras)
        r['notes']=r['notes'].strip()
        if not r['manufacturer']:r['notes']=(r['notes']+' Manufacturer not established.').strip()
        if len(r['appearance_description_original'])>1500:raise ValueError('Appearance extraction too long: '+r['pzn'])
        for field in ['pharmaceutical_company','manufacturer']:
            assert len(r[field])<400,(r['pzn'],field,r[field])
        for field in ['color','shape','imprint','score_line']:
            assert len(r[field])<200,(r['pzn'],field,r[field])
        raw=json.loads((ROOT/r['pzn']/'raw_extract.json').read_text(encoding='utf-8'))
        sources={'pzn':r['pzn'],'collection_file':LIST.name,'page':raw['page'],
                 'downloads':raw['downloads'],'issues':raw['issues'],'photo_review':patch,
                 'reviewed_text_corrections':correction,
                 'field_sources':{k:{'value':r[k],'source':('downloaded leaflet, reviewed text extract' if k in correction else
                   'tablet image, visually reviewed' if k in patch and k in ['color','shape','imprint']
                   else 'product page / leaflet; original collection fields retained where verified')} for k in fields}}
        collect.save_json(ROOT/r['pzn']/'sources.json',sources)
        existing[r['pzn']]=r
    backup=ROOT/'backups'/('polypharmacy_assets_'+datetime.now().strftime('%Y%m%d_%H%M%S'));backup.mkdir()
    for name in ['medicines.csv','medicines.xlsx']:shutil.copy2(ROOT/name,backup/name)
    updated=list(existing.values())
    with (ROOT/'medicines.polypharmacy.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,quoting=csv.QUOTE_ALL);writer.writeheader();writer.writerows(updated)
    wb=load_workbook(ROOT/'medicines.xlsx');ws=wb['Medicines']
    for ri,r in enumerate(updated,2):
        for ci,field in enumerate(fields,1):
            cell=ws.cell(ri,ci);cell.value=r[field];cell.data_type='s'
            if field=='pzn':cell.number_format='@'
    ws.auto_filter.ref=ws.dimensions
    wb.save(ROOT/'medicines.polypharmacy.xlsx')
    (ROOT/'medicines.polypharmacy.csv').replace(ROOT/'medicines.csv')
    (ROOT/'medicines.polypharmacy.xlsx').replace(ROOT/'medicines.xlsx')
    print(f'Updated {len(prepared)} polypharmacy entries; total {len(updated)} rows. Backup: {backup}')


if __name__=='__main__':
    {'fetch':prepare,'derive':derive,'build':build}[sys.argv[1]]()
