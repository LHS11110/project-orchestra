"""Compute request-weighted availability and one-second fault-aligned estimates."""
import argparse
import collections
import csv
import json
import math
from pathlib import Path
import statistics


def probability(rows):
    n=len(rows);s=sum(bool(r['ok']) for r in rows)
    if not n:return {'attempts':0,'successes':0,'p':None,'wilson95':None}
    p=s/n;z=1.959963984540054;den=1+z*z/n
    middle=(p+z*z/(2*n))/den;spread=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/den
    return {'attempts':n,'successes':s,'p':p,'wilson95':[max(0,middle-spread),min(1,middle+spread)]}


def summarize(directory):
    rows=[json.loads(line) for line in (directory/'requests.jsonl').read_text().splitlines() if line]
    adjusted=0
    for row in rows:
        for stage in row['stages']:
            if stage['ok'] and stage['ms']>2000:
                stage['ok']=False;stage['error']='DEADLINE_EXCEEDED'
                if row['ok']:adjusted+=1
                row['ok']=False;row['failed']=stage['name']
    healthy=[r for r in rows if r['scenario']=='healthy-control']
    rows=[r for r in rows if r['scenario'] not in ['preflight','healthy-control']]
    events=json.loads((directory/'events.json').read_text());eventmap={(e['scenario'],e['round']):e for e in events}
    for r in rows:
        e=eventmap[r['scenario'],r['round']];r['second']=math.floor((r['start']-e['fault_at'])/1000)
    scenarios={}
    for name in sorted(set(r['scenario'] for r in rows)):
        selected=[r for r in rows if r['scenario']==name]
        scenarios[name]={'overall':probability(selected),'phases':{p:probability([r for r in selected if r['phase']==p]) for p in ['baseline','fault','recovery']},'timeline':{str(t):probability([r for r in selected if r['second']==t]) for t in sorted(set(r['second'] for r in selected))},'rounds':{str(k):probability([r for r in selected if r['round']==k]) for k in sorted(set(r['round'] for r in selected))},'failures':dict(collections.Counter(r['failed'] for r in selected if not r['ok']))}
    stages={name:probability([s for r in rows for s in r['stages'] if s['name']==name]) for name in ['login','search','connect','save']}
    outcome={'deadline_adjusted_flows':adjusted,'healthy_control':probability(healthy),'definition':'Login -> search returns target -> access and authenticated initial WS snapshot -> Redis save ACK and canonical commit; 2000ms per stage, no automatic retry. Cohorts assigned by flow start, not completion.','overall':probability(rows),'timeline':{str(t):probability([r for r in rows if r['second']==t]) for t in sorted(set(r['second'] for r in rows))},'phases':{p:probability([r for r in rows if r['phase']==p]) for p in ['baseline','fault','recovery']},'conditional_stages':stages,'scenarios':scenarios,'mean_scenario_probability':statistics.mean(v['overall']['p'] for v in scenarios.values()),'limitations':['Artificial fault mix; not production uptime/SLA or annual availability.','Repeated requests during the same outage are correlated; Wilson intervals are binomial reference intervals, not independent trial guarantees.','A timeout may still be saved later; timeout is not proof of data loss.','Stage success rates condition on reaching that stage; end-to-end rate includes failures at any earlier stage.']}
    (directory/'summary.json').write_text(json.dumps(outcome,indent=2))
    with (directory/'timeline.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['scenario','second_from_fault','successes','attempts','p','wilson95_low','wilson95_high'])
        for name,values in scenarios.items():
            for t,value in values['timeline'].items():writer.writerow([name,t,value['successes'],value['attempts'],value['p'],*value['wilson95']])
    with (directory/'requests.csv').open('w') as f:
        writer=csv.writer(f);writer.writerow(['id','scenario','round','phase','start_ms','end_ms','fault_aligned_second','success','failed_stage'])
        for r in rows:writer.writerow([r['id'],r['scenario'],r['round'],r['phase'],r['start'],r['end'],r['second'],r['ok'],r.get('failed','')])
    return outcome


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('directory',type=Path);a=parser.parse_args()
    result=summarize(a.directory);print(json.dumps({'overall':result['overall'],'phases':result['phases']},indent=2))
