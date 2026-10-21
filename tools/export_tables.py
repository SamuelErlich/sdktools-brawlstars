#!/usr/bin/env python3
"""Export only five pinned static tables from the original IPA, without executing it."""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = frozenset('res/csv_logic/'+name+'.csv' for name in
    ('characters','skills','projectiles_logic','locations','game_mode_variations'))

def digest_file(path):
    h=hashlib.sha256()
    with path.open('rb') as source:
        for b in iter(lambda:source.read(1024*1024),b''): h.update(b)
    return h.hexdigest()

def summarize_csv(raw):
    if not isinstance(raw,bytes) or len(raw)>1024*1024: raise ValueError('CSV size limit')
    rows=list(csv.reader(io.StringIO(raw.decode('utf-8-sig'),newline='')))
    if len(rows)<2 or len(rows)>20000 or not 1<=len(rows[0])<=1024:
        raise ValueError('CSV row/column limit')
    if len(set(rows[0]))!=len(rows[0]): raise ValueError('Duplicate column')
    # Retain values and continuation rows verbatim; no default propagation.
    return {'column_count':len(rows[0]),'logical_rows':len(rows),
        'named_records':sum(bool(r and r[0].strip()) for r in rows[2:]),
        'continuation_or_empty_rows':sum(not r or not r[0].strip() for r in rows[2:]),
        'columns':rows[0],'declared_types':rows[1],
        'interpretation':'static_table_only; not live state; empty values not inferred'}

def select_assets(archive,index,app_path):
    specs=index.get('assets')
    if index.get('schema_version')!=1 or not isinstance(specs,list) or len(specs)!=5:
        raise ValueError('Invalid asset index')
    paths=[r.get('relative_path') for r in specs]
    if len(set(paths))!=5 or set(paths)!=ALLOWLIST: raise ValueError('Asset allowlist mismatch')
    out=[]; total=0
    for ref in specs:
        name=app_path+'/'+ref['relative_path']
        matches=[i for i in archive.infolist() if i.filename==name]
        if len(matches)!=1: raise ValueError('Missing/duplicate selected table')
        info=matches[0]
        if info.file_size!=ref['bytes'] or not 0<info.file_size<=1024*1024:
            raise ValueError('Unexpected table size')
        with archive.open(info) as stream: raw=stream.read(1024*1024+1)
        if len(raw)!=ref['bytes'] or hashlib.sha256(raw).hexdigest()!=ref['sha256']:
            raise ValueError('Table hash mismatch')
        total+=len(raw)
        if total>2*1024*1024: raise ValueError('Total table size limit')
        summary=summarize_csv(raw)
        for key in ('column_count','logical_rows','named_records'):
            if summary[key]!=ref[key]: raise ValueError('CSV metadata mismatch')
        out.append((ref,raw,summary))
    return out

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    target=json.loads((ROOT/'resources/Target.json').read_text())['target']
    index=json.loads((ROOT/'resources/AssetIndex.json').read_text())
    if args.output.exists(): raise ValueError('Output already exists')
    if args.base.stat().st_size!=target['ipa_size'] or digest_file(args.base)!=target['ipa_sha256']:
        raise ValueError('Base IPA identity mismatch')
    with zipfile.ZipFile(args.base) as z: selected=select_assets(z,index,target['app_path'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.tables-',suffix='.zip',dir=args.output.parent);os.close(fd)
    temporary=Path(temp)
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
            for ref,raw,summary in selected: z.writestr(Path(ref['relative_path']).name,raw)
            z.writestr('TABLES_REPORT.json',json.dumps({'schema_version':1,'scope':'static_bundle_tables_not_live_match_state',
                'base_sha256':target['ipa_sha256'],'assets':[dict(ref,**{'csv_summary':summary}) for ref,raw,summary in selected]},ensure_ascii=False,indent=2))
        with zipfile.ZipFile(temporary) as z:
            if z.testzip() is not None: raise ValueError('Output ZIP CRC failure')
        os.link(temporary,args.output)  # Atomic publication; never overwrite.
    finally: temporary.unlink(missing_ok=True)
    print(json.dumps({'output':args.output.name,'tables':len(selected),'bytes':args.output.stat().st_size,'sha256':digest_file(args.output)}))

if __name__=='__main__': main()
