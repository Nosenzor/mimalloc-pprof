"""Verify archived bytes and original cohort coverage; never infer merge acceptance."""
from pathlib import Path
import argparse,collections,dataclasses,hashlib,io,json,math,sys,tarfile,zipfile
sys.path.insert(0,str(Path.cwd()/'ci'))
import perf_ab,perf_ab_ledger,perf_ab_stats
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('directory',type=Path)
parser.add_argument('--allow-pending',action='store_true',help='checkpoint only; report missing live measurements explicitly')
args=parser.parse_args();directory=args.directory
manifest=json.loads((directory/'manifest.json').read_text());archive_bytes=(directory/'measurements.zip').read_bytes()
assert len(archive_bytes)==manifest['archive']['bytes']
assert hashlib.sha256(archive_bytes).hexdigest()==manifest['archive']['sha256']
entries={x['path']:x for x in manifest['files']};assert len(entries)==len(manifest['files'])
report={'archive_sha256':manifest['archive']['sha256'],'verified_inputs':len(entries),'runs':[],'pending':[],'metric_verdicts':{}}
with zipfile.ZipFile(directory/'measurements.zip') as archive:
    for item in entries.values():
        assert not Path(item['path']).is_absolute() and '..' not in Path(item['path']).parts
        data=archive.read(item['member'])
        assert len(data)==item['bytes'] and hashlib.sha256(data).hexdigest()==item['sha256'],item['path']
    def read(path):
        item=entries[path];return archive.read(item['member'])
    def doc(path): return json.loads(read(path))
    analysis_paths=['ci/perf_ab.py','ci/perf_ab.c','ci/perf_ab_ledger.py','ci/perf_ab_stats.py']
    report['analysis_code_sha256']={name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in analysis_paths}
    for ref in [manifest['v1_rejected'],manifest['v2'],manifest['v3']]:
        with tarfile.open(fileobj=io.BytesIO(read('source/'+ref+'.tar'))) as source:
            for name in analysis_paths:
                archived=source.extractfile(name);assert archived is not None
                assert hashlib.sha256(archived.read()).hexdigest()==report['analysis_code_sha256'][name],(ref,name,'analysis source differs')
    seen=set()
    for prefix,label in [('', 'v1'),('v2/','v2'),('v3/','v3')]:
        cohort=doc(prefix+'evidence/dispatches.json')
        assert len(cohort['runs'])==13,(label,'incomplete dispatch coverage')
        statuses={x['run_id']:x for x in doc(prefix+'evidence/cohort-status.json')}
        counters=collections.Counter()
        for run in cohort['runs']:
            run_id=run['response']['workflow_run_id'];assert run_id not in seen;seen.add(run_id)
            name=run['workload'];status=statuses[run_id]
            folder=f'{prefix}downloads/{run_id}/';raw_path=folder+'samples/perf-ab-samples.json'
            if raw_path not in entries:
                assert args.allow_pending and status['status']!='completed',(label,name,'missing terminal original samples')
                report['pending'].append({'source':label,'workload':name,'run_id':run_id});continue
            raw=read(raw_path);data=json.loads(raw)
            assert data['base']['sha']==cohort['baseline'] and data['head']['sha']==cohort['candidate']
            assert data.get('requested_reps',data['reps'])==60 and data['max_reps']==120
            assert data['variants']==[{'arm':'head','env':{},'cppdefs':[]}]
            assert data['fields']==[*perf_ab.METRICS,'ideal RSS MiB']
            assert data['latency_protocol']['version']=='sampled-stream-operation-v1'
            assert data['arm_order_protocol']=={'version':perf_ab.ARM_ORDER_VERSION,'arms':['base','head','null']}
            kind,params={**perf_ab.WORKLOADS,**perf_ab.DIAGNOSTIC_WORKLOADS}[name]
            assert data['workloads']=={name:{'kind':kind,'params':params._asdict()}}
            hashes=doc(folder+'builds/sha256.json')
            assert set(hashes)=={f'{arm}/{kind}/{exe}' for arm in ['base','head'] for exe in ['perf_ab','perf_ab_latency']}
            for path,digest in hashes.items(): assert hashlib.sha256(read(folder+'builds/'+path)).hexdigest()==digest
            arms=data['samples'][name];assert set(arms)=={'base','head','null'}
            counts={arm:len(samples) for arm,samples in arms.items()};assert len(set(counts.values()))==1
            assert all(len(v)==len(data['fields']) and all(math.isfinite(x) and x>=0 for x in v) for values in arms.values() for v in values)
            result={'source':label,'workload':name,'run_id':run_id,'complete':data.get('complete'),'counts':counts,'raw_sha256':hashlib.sha256(raw).hexdigest()}
            if data.get('complete') is not True:
                assert data.get('failure') and 'cells' not in status and 'verdict' not in status
                result['failure']=data['failure'];report['runs'].append(result);continue
            n=counts['base'];assert 60<=n<=120 and (n-60)%5==0
            saved,cells=perf_ab.ledger_row(arms['base'],arms['head'],arms['null'])
            computed=json.loads(json.dumps([dataclasses.asdict(c) for c in cells]))
            original=doc(folder+'verified-ledger.json')
            assert original['raw_sha256']==result['raw_sha256']
            assert original['saved_percent']==saved and original['cells']==computed,(label,name,'canonical ledger differs')
            assert original['verdict']==perf_ab_ledger.row_verdict(cells)
            strict=[]
            for cell in cells:
                noise=perf_ab_stats.noise_floor(perf_ab_stats.paired_percent(perf_ab.column(arms['base'],cell.metric),perf_ab.column(arms['null'],cell.metric)))
                strict.append({'metric':cell.metric,'allowance':noise,'verdict':perf_ab_ledger.judge(cell.regression,noise)})
            assert original['zero_memory_credit_check']==strict
            index=perf_ab.METRICS.index('release ms');release=perf_ab.percentile([v[index] for v in arms['head']],perf_ab.RELEASE_PERCENTILE)
            assert original['p95_release_ms']==release and original['release_held']==(release<=data['release_bound_ms'])
            counters.update(c.verdict for c in cells);result['verdict']=original['verdict'];report['runs'].append(result)
        report['metric_verdicts'][label]=dict(counters)
    diagnostic='baseline-rss-diagnostic/'
    d=doc(diagnostic+'samples/perf-ab-samples.json');assert d['base']['sha']==d['head']['sha']==manifest['baseline']
    hashes=doc(diagnostic+'builds/sha256.json')
    for path,digest in hashes.items(): assert hashlib.sha256(read(diagnostic+'builds/'+path)).hexdigest()==digest
    for exe in ['perf_ab','perf_ab_latency']: assert hashes['base/plain/'+exe]==hashes['head/plain/'+exe]
    diagnostic_report=doc(diagnostic+'verified-diagnostic.json')
    assert diagnostic_report['raw_sha256']==hashlib.sha256(read(diagnostic+'samples/perf-ab-samples.json')).hexdigest()
    report['baseline_only_diagnostic']={'run_id':diagnostic_report['run_id'],'complete':d.get('complete'),'counts':diagnostic_report['counts'],'crossing_pairs':len(diagnostic_report['crossings'])}
    assert len(seen)==39
    assert len(report['runs'])+len(report['pending'])==39
    if not args.allow_pending: assert not report['pending']
report['note']='Byte/coverage/ledger verification only. Numeric verdicts and any incomplete or uncertain cells remain unchanged; this is not merge acceptance.'
print(json.dumps(report,indent=2))
