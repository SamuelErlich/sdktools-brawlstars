#!/usr/bin/env python3
"""Summarize an exported capture locally. Does not replay touches or infer game state."""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MAX_BYTES=12*1024*1024

def require(ok,message):
    if not ok:raise ValueError(message)

def unique_object(pairs):
    out={}
    for k,v in pairs:
        require(k not in out,'Duplicate JSON key');out[k]=v
    return out

def finite(v):return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)

def summarize(report,target,index):
    require(isinstance(report,dict),'Report must be an object')
    require(report.get('schema')=='SDKLaserCapture.Diagnostic' and report.get('schema_version')==1,'Unsupported schema')
    identity=report.get('identity')
    require(isinstance(identity,dict),'Identity missing')
    for key in ('bundle_identifier','version','build','main_uuid'):
        require(identity.get(key)==target[key],'Wrong identity: '+key)
    duration=report.get('duration_ms')
    require(finite(duration) and 0<=duration<=86400000,'Invalid observed duration')
    arrays={}
    for key,cap in [('events',2000),('snapshots',184),('markers',64)]:
        rows=report.get(key)
        require(isinstance(rows,list) and len(rows)<=cap,'Missing or oversized '+key)
        previous=-1
        for row in rows:
            require(isinstance(row,dict),'Invalid row')
            t=row.get('elapsed_ms')
            require(finite(t) and previous<=t<=duration+1 and t>=0,'Invalid/nonmonotonic timestamp')
            previous=t
        arrays[key]=rows
    contacts={}; phases=Counter(); windows=Counter()
    for event in arrays['events']:
        cid=event.get('touch_id');phase=event.get('phase')
        require(isinstance(cid,int) and not isinstance(cid,bool) and cid>0,'Invalid touch ID')
        require(phase in ('began','moved','ended','cancelled'),'Invalid phase')
        for key,n in [('point',2),('normalized',2),('window_bounds_points',4)]:
            xy=event.get(key);require(isinstance(xy,list) and len(xy)==n and all(finite(v) for v in xy),'Invalid geometry')
        require(event['window_bounds_points'][2]>0 and event['window_bounds_points'][3]>0,'Invalid window size')
        contact=contacts.setdefault(cid,{'began':0,'closed':False})
        require(not contact['closed'],'Event after ended contact')
        if phase=='began':
            contact['began']+=1;require(contact['began']==1,'Touch ID reused')
        if phase in ('ended','cancelled'):contact['closed']=True
        phases[phase]+=1;windows[str(event.get('window_class','unknown'))]+=1
    classes=Counter(); max_views=0
    for frame in arrays['snapshots']:
        frame_windows=frame.get('windows')
        require(isinstance(frame_windows,list) and len(frame_windows)<=8,'Invalid windows')
        count=0
        for window in frame_windows:
            require(isinstance(window,dict) and isinstance(window.get('views'),list),'Invalid view collection')
            count+=len(window['views'])
            for view in window['views']:
                require(isinstance(view,dict),'Invalid view')
                classes[str(view.get('class','unknown'))]+=1
        require(count<=96,'View budget exceeded');max_views=max(max_views,count)
    expected={r['relative_path']:r for r in index['assets']}
    exported=report.get('static_assets_exported',[])
    require(isinstance(exported,list) and len(exported)<=5,'Invalid asset list')
    seen=set(); assets=[]
    for item in exported:
        require(isinstance(item,dict),'Invalid asset')
        path=item.get('relative_path');require(path in expected and path not in seen,'Unexpected or duplicate asset')
        seen.add(path)
        match=item.get('status')=='exported' and item.get('sha256')==expected[path]['sha256'] and item.get('bytes')==expected[path]['bytes']
        assets.append({'relative_path':path,'status':item.get('status'),'metadata_matches_base':match,
                       'file_content_verified_by_this_summary':False})
    return {'schema':'SDKLaserCapture.Summary','schema_version':1,
        'identity':{k:identity.get(k) for k in ('bundle_identifier','version','build','main_uuid','module_version')},
        'duration_ms':duration,'stop_reason':report.get('stop_reason'),
        'counts':{**{k:len(v) for k,v in arrays.items()},'touch_contacts':len(contacts),
                  'complete_contacts':sum(v['began']==1 and v['closed'] for v in contacts.values()),'max_views_per_snapshot':max_views},
        'touch_phases':dict(phases),'touch_window_classes':dict(windows),'view_classes':dict(classes),
        'static_assets':assets,
        'coverage':{'live_players':False,'live_positions':False,'live_health':False,'live_projectiles':False,
                    'native_call_tracing':False,'autoplay':False},
        'notes_pt':['As tabelas são recursos estáticos do aplicativo.','Toques não comprovam comandos aceitos pelo jogo.',
                    'Este resumo compara os metadados dos CSV; confira também os hashes dos arquivos recebidos.']}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('capture',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();require(not args.output.exists(),'Output already exists')
    with args.capture.open('rb') as f:raw=f.read(MAX_BYTES+1)
    require(len(raw)<=MAX_BYTES,'Capture exceeds 12 MiB')
    def reject_constant(value):raise ValueError('Nonfinite JSON value')
    report=json.loads(raw,object_pairs_hook=unique_object,parse_constant=reject_constant)
    target=json.loads((ROOT/'resources/Target.json').read_text())['target']
    index=json.loads((ROOT/'resources/AssetIndex.json').read_text())
    result=summarize(report,target,index);result['input_sha256']=hashlib.sha256(raw).hexdigest()
    with args.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(result['counts']))

if __name__=='__main__':main()
