"""Real Agora images, disposable data/network, request-level chaos availability.

Existing containers are read-only configuration/image/certificate references.
No published ports and no production data volumes are attached. Faults are
executed only through Orchestra's inventory/label/network guarded chaos runner.
"""
import argparse
import collections
import ipaddress
import json
import math
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from orchestra.chaos import plan, run
from orchestra.docker import Docker


def call(*args,input=None,timeout=180):
    r=subprocess.run(['docker',*map(str,args)],input=input,text=True,capture_output=True,timeout=timeout)
    if r.returncode:
        detail=r.stderr
        if '--env-file' in args:
            path=Path(args[args.index('--env-file')+1])
            for line in path.read_text().splitlines():
                if '=' in line:
                    key,value=line.split('=',1)
                    if any(word in key.upper() for word in ['PASSWORD','SECRET','TOKEN']) and value:detail=detail.replace(value,'[redacted]')
        raise RuntimeError(f'Docker operation {args[0]} failed: {detail[-1200:]}')
    return (r.stdout+r.stderr if args[0]=='logs' else r.stdout).strip()


def inspect(name): return json.loads(call('inspect',name))[0]
def env(raw): return dict(v.split('=',1) for v in raw['Config']['Env'])


class Lab:
    def __init__(self):
        self.tag='orchestra-chaos-flow-'+uuid.uuid4().hex[:10]
        self.directory=ROOT/'results'/self.tag
        self.directory.mkdir(parents=True,mode=0o700)
        self.containers=[];self.volumes=[];self.network=None
        self.names={};self.raw={name:inspect('agora-'+name) for name in ['mssql','elasticsearch','redis-primary','redis-replica-1','redis-replica-2','redis-sentinel-1','redis-sentinel-2','redis-sentinel-3','cpp','spring','broker','nginx','storage-broker']}
        self.ips={};self.production_ids={name:value['Id'] for name,value in self.raw.items()}
    def state(self,**values):
        p=self.directory/'control.json';tmp=p.with_suffix('.tmp');tmp.write_text(json.dumps(values));tmp.replace(p)
    def mount(self,m):
        source=m.get('Name') if m['Type']=='volume' else m['Source']
        return ['--mount',f'type={m["Type"]},src={source},dst={m["Destination"]},readonly']
    def create(self,key,source=None,overrides=None,aliases=(),number=None,extra=(),command=None,entrypoint=None,data=()):
        name=self.tag+'-'+key;self.names[key]=name
        args=['run','-d','--name',name,'--label','com.docker.compose.project='+self.tag,'--label','orchestra.chaos.run='+self.tag,'--network',self.network]
        if number:
            self.ips[key]=str(ipaddress.ip_network(self.subnet).network_address+number);args+=['--ip',self.ips[key]]
        for alias in aliases:args+=['--network-alias',alias]
        if source:
            raw=self.raw[source];settings=env(raw);settings.update(overrides or {})
            # Read-only config and certificate mounts only, never existing data.
            for m in raw['Mounts']:
                if not m['RW'] and m['Destination'] not in ['/run/secrets/redis-tls','/run/certs/redis-ca.crt']:
                    if source=='nginx' and not m['Destination'].startswith('/etc/nginx/ssl/'):continue
                    args+=self.mount(m)
            ep=raw['Config']['Entrypoint'];cmd=raw['Config']['Cmd'] or []
            if entrypoint is None and ep:entrypoint=ep[0];command=ep[1:]+cmd if command is None else command
            elif command is None:command=cmd
            image=raw['Config']['Image']
        else:settings=overrides or {};image=extra[0];extra=extra[1:]
        config=self.directory/(key+'.env');config.write_text(''.join(f'{k}={v}\n' for k,v in settings.items() if '\n' not in v));config.chmod(0o600)
        args+=['--env-file',str(config)]
        for destination in data:
            volume=name+'-data-'+str(len(self.volumes));call('volume','create','--label','orchestra.chaos.run='+self.tag,volume);self.volumes.append(volume)
            args+=['--mount',f'type=volume,src={volume},dst={destination}']
        if source in ['cpp','spring']:
            args+=['--mount',f'type=bind,src={self.directory}/redis-ca.crt,dst=/run/certs/redis-ca.crt,readonly']
        if source and source.startswith('redis-'):
            args+=['--mount',f'type=bind,src={self.directory}/redis-tls,dst=/run/secrets/redis-tls,readonly']
        args+=list(extra)
        if entrypoint:args+=['--entrypoint',entrypoint]
        self.containers.append(name)
        call(*args,image,*(command or []))
        return name
    def wait(self,fn,title,seconds=180):
        until=time.monotonic()+seconds
        while time.monotonic()<until:
            try:
                if fn():print('Ready:',title,flush=True);return
            except (RuntimeError,subprocess.TimeoutExpired):pass
            time.sleep(2)
        raise TimeoutError('Readiness failed: '+title)
    def sql(self,text):
        return call('exec','-i',self.names['sql'],'bash','-lc','export SQLCMDPASSWORD="$MSSQL_SA_PASSWORD"; exec /opt/mssql-tools18/bin/sqlcmd -S tcp:127.0.0.1,1433 -U sa -C -b -I -h -1',input=text,timeout=30)
    def worker(self,mode,timeout=60):
        return call('exec',self.names['worker'],'node','/lab/worker.mjs',mode,timeout=timeout)
    def bootstrap(self):
        existing=json.loads(call('network','inspect',*call('network','ls','-q').split()))
        used=[ipaddress.ip_network(c['Subnet']) for n in existing for c in (n.get('IPAM',{}).get('Config') or []) if 'Subnet' in c]
        self.subnet=next(f'172.29.{n}.0/24' for n in range(200,250) if not any(ipaddress.ip_network(f'172.29.{n}.0/24').overlaps(x) for x in used if x.version==4))
        self.network=self.tag+'-net';call('network','create','--internal','--subnet',self.subnet,'--label','orchestra.chaos.run='+self.tag,self.network)
        self.state(enabled=False)
        tls=self.directory/'redis-tls';tls.mkdir()
        openssl=lambda *a:subprocess.run(['openssl',*a],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        openssl('req','-x509','-newkey','rsa:2048','-nodes','-days','1','-keyout',str(tls/'ca.key'),'-out',str(tls/'ca.crt'),'-subj','/CN=Orchestra disposable Redis CA')
        openssl('req','-new','-newkey','rsa:2048','-nodes','-keyout',str(tls/'server.key'),'-out',str(tls/'server.csr'),'-subj','/CN=Orchestra disposable Redis')
        addresses=[str(ipaddress.ip_network(self.subnet).network_address+n) for n in [10,11,12,20,21,22]]
        (tls/'extensions').write_text('subjectAltName='+','.join('IP:'+x for x in addresses)+',DNS:redis-primary,DNS:agora-redis-primary,DNS:localhost,IP:127.0.0.1\nextendedKeyUsage=serverAuth,clientAuth\n')
        openssl('x509','-req','-in',str(tls/'server.csr'),'-CA',str(tls/'ca.crt'),'-CAkey',str(tls/'ca.key'),'-CAcreateserial','-out',str(tls/'server.crt'),'-days','1','-extfile',str(tls/'extensions'))
        shutil.copy(tls/'ca.crt',self.directory/'redis-ca.crt')
        # Container users need directory traversal; private files remain 0600.
        self.directory.chmod(0o755)
        self.create('sql','mssql',aliases=['sql-direct'],number=2,data=['/var/opt/mssql'])
        self.create('es','elasticsearch',overrides={'ES_JAVA_OPTS':'-Xms512m -Xmx1g'},aliases=['es-direct'],number=3,data=['/usr/share/elasticsearch/data','/usr/share/elasticsearch/snapshots'])
        primary=str(ipaddress.ip_network(self.subnet).network_address+10)
        for key,n in [('redis-primary',10),('redis-replica-1',11),('redis-replica-2',12)]:
            address=str(ipaddress.ip_network(self.subnet).network_address+n)
            self.create(key,key,overrides={'REDIS_NODE_ANNOUNCE_IP':address,'REDIS_NODE_PRIMARY_HOST':'' if n==10 else primary},aliases=[key],number=n,data=['/data'])
        for index in range(1,4):
            key='redis-sentinel-'+str(index);n=19+index
            self.create(key,key,overrides={'REDIS_SENTINEL_MASTER_HOST':primary,'REDIS_SENTINEL_ANNOUNCE_IP':str(ipaddress.ip_network(self.subnet).network_address+n)},aliases=[key],number=n,data=['/data'])
        self.wait(lambda:'1' in self.sql('SELECT 1;'),'isolated SQL')
        schema=(ROOT.parent/'project-agora-DB/mssql/init-mssql.sql').read_text();db=env(self.raw['mssql'])
        variables={'DB_NAME':db.get('MSSQL_DB','agora_db'),'DB_USER':db.get('MSSQL_USER','agora_user'),'DB_PASSWORD':db['MSSQL_PASSWORD'],'TABLE_USERS':'users','TABLE_REDIS_SERVER':'redis_server','TABLE_CANVAS_INFO':'canvas_info','TABLE_CANVAS_CACHE':'canvas_info','TABLE_CPP_SERVER':'cpp_server'}
        for k,v in variables.items():schema=schema.replace('$('+k+')',v)
        self.sql(schema)
        self.sql(f"USE agora_db; INSERT INTO redis_server(redis_ip,redis_port,is_activated) VALUES('{primary}','6379',1);")
        upstream={'STORAGE_MSSQL_UPSTREAM':'sql-direct:1433','STORAGE_ES_UPSTREAM':'es-direct:9200'}
        for key,variable in [('redis-primary','PRIMARY'),('redis-replica-1','REPLICA_1'),('redis-replica-2','REPLICA_2')]:upstream['STORAGE_REDIS_'+variable+'_UPSTREAM']=self.ips[key]+':6379'
        for i in range(1,4):upstream['STORAGE_SENTINEL_'+str(i)+'_UPSTREAM']=self.ips['redis-sentinel-'+str(i)]+':26379'
        self.create('storage','storage-broker',overrides=upstream,aliases=['agora-storage-broker','agora-mssql','agora-elasticsearch'],number=30)
        routes={}
        for key,port in [('redis-primary',16379),('redis-replica-1',16380),('redis-replica-2',16381)]:routes[self.ips[key]+':6379']={'host':'agora-storage-broker','port':port}
        for i in range(1,4):routes[self.ips['redis-sentinel-'+str(i)]+':26379']={'host':'agora-storage-broker','port':26378+i}
        app={'REDIS_SENTINELS':','.join(self.ips['redis-sentinel-'+str(i)]+':26379' for i in range(1,4)),'REDIS_BROKER_ROUTES':json.dumps(routes,separators=(',',':')),'ES_SEMANTIC_SEARCH_ENABLED':'false'}
        # Existing least-privilege ES application roles are recreated below.
        worker_env={'NODE_EXTRA_CA_CERTS':'/lab/ca-bundle.pem'}
        ca=''
        for raw,destination in [(self.raw['nginx'],'/etc/nginx/ssl/agora/ca.pem'),(self.raw['cpp'],'/run/certs/elasticsearch-ca.crt')]:
            m=next(m for m in raw['Mounts'] if m['Destination']==destination);ca+=Path(m['Source']).read_text()+'\n'
        (self.directory/'ca-bundle.pem').write_text(ca)
        shutil.copy(ROOT/'tests/chaos_userflow_worker.mjs',self.directory/'worker.mjs')
        # Worker uses the same Node image as the FE, without serving any ports.
        image=inspect('project-agora-frontend:local')['Id']
        self.create('worker',overrides=worker_env,aliases=['load-worker'],number=50,extra=[image,'--user','0','--mount',f'type=bind,src={self.directory},dst=/lab'],entrypoint='sleep',command=['3600'])
        esenv=env(self.raw['elasticsearch']);private={'email':'chaos@example.invalid','password':'Chaos-'+uuid.uuid4().hex,'esAdmin':esenv['ELASTIC_PASSWORD'],'esUser':esenv['ES_USER_NAME'],'esPassword':esenv['ES_USER_PASSWORD'],'esLogUser':esenv['ES_LOG_USER_NAME'],'esLogPassword':esenv['ES_LOG_USER_PASSWORD']}
        (self.directory/'private.json').write_text(json.dumps(private));(self.directory/'private.json').chmod(0o600)
        (self.directory/'init-es.mjs').write_text('''import fs from 'node:fs';const c=JSON.parse(fs.readFileSync('/lab/private.json'));async function put(path,body){let r=await fetch('https://agora-elasticsearch:9200/'+path,{method:'PUT',headers:{Authorization:'Basic '+Buffer.from('elastic:'+c.esAdmin).toString('base64'),'Content-Type':'application/json'},body:JSON.stringify(body),signal:AbortSignal.timeout(3000)});if(!r.ok)throw new Error('ES_INIT_'+r.status);}await put('_security/role/agora-lab',{cluster:[],indices:[{names:['canvas','canvas-search'],privileges:['read','write','view_index_metadata','create_index']}]});await put('_security/user/'+c.esUser,{password:c.esPassword,roles:['agora-lab']});await put('_security/role/agora-lab-log',{cluster:[],indices:[{names:['agora-logs','agora-logs-*'],privileges:['auto_configure','create_doc']}]});await put('_security/user/'+c.esLogUser,{password:c.esLogPassword,roles:['agora-lab-log']});await put('canvas',{settings:{number_of_shards:1,number_of_replicas:0},mappings:{properties:{'canvas-name':{type:'text',fields:{keyword:{type:'keyword'}}},'canvas-id':{type:'long'},'admin-user-id':{type:'long'},people:{type:'long'},'init-group':{type:'keyword'},items:{type:'object'}}}});''')
        def init_es():
            call('exec',self.names['worker'],'node','/lab/init-es.mjs',timeout=15);return True
        self.wait(init_es,'isolated ES roles and index')
        self.create('spring','spring',overrides=app,aliases=['agora-spring'],number=40,data=['/home/agora/project-agora/canvas-resource'])
        self.create('cpp','cpp',overrides=app,aliases=['agora-cpp'],number=41)
        self.create('broker','broker',overrides={'BROKER_TRUSTED_PROXY_IPS':str(ipaddress.ip_network(self.subnet).network_address+43)},aliases=['agora-broker'],number=42)
        config='''server { listen 8444 ssl; ssl_certificate /etc/nginx/ssl/agora/fullchain.pem; ssl_certificate_key /etc/nginx/ssl/agora/privkey.pem;
resolver 127.0.0.11 valid=1s; proxy_ssl_trusted_certificate /etc/nginx/ssl/agora/ca.pem; proxy_ssl_verify on; proxy_ssl_server_name on;
location /api/ { set $target agora-spring; proxy_ssl_name agora-spring; proxy_pass https://$target:8080; proxy_set_header X-Forwarded-For $remote_addr; proxy_set_header X-Real-IP $remote_addr; proxy_set_header Host $host; }
location ~ ^/wss/port/(?<wsport>[0-9]+)/canvas/(?<canvas>[0-9]+)$ { set $broker agora-broker; proxy_ssl_name agora-broker; proxy_pass https://$broker:4443/broker/websocket?canvas_id=$canvas&ws_port=$wsport&rtc=false&token=$arg_token&client_id=$arg_client_id; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection Upgrade; proxy_set_header X-Real-IP $remote_addr; proxy_set_header Host $host; access_log off; }
location /api/port/ { return 404; }
location ~ ^/internal/cpp/servers/[0-9]+/(?<cpppath>api/.*|health)$ { set $cpp agora-cpp; proxy_ssl_name agora-cpp; proxy_pass https://$cpp:8000/$cpppath$is_args$args; proxy_set_header X-Agora-Internal-Token $http_x_agora_internal_token; proxy_set_header Host agora-cpp; }
}'''
        # Spring calls the deployed internal C++ routing path; use the repo's map.
        config=config.replace('proxy_ssl_name agora-broker;', 'proxy_ssl_name '+env(self.raw['nginx']).get('BROKER_TLS_SERVER_NAME','agora-broker')+';')
        (self.directory/'nginx.conf').write_text(config)
        self.create('gateway','nginx',aliases=['agora-nginx'],number=43,entrypoint='nginx',command=['-g','daemon off;'],extra=['--mount',f'type=bind,src={self.directory}/nginx.conf,dst=/etc/nginx/conf.d/default.conf,readonly'])
        def health():
            code="const r=await fetch('https://agora-nginx:8444/api/auth/health',{signal:AbortSignal.timeout(2000)});if(!r.ok)process.exit(1);"
            call('exec',self.names['worker'],'node','--input-type=module','-e',code,timeout=8);return True
        self.wait(health,'isolated Spring through TLS gateway')
        print(self.worker('setup'),flush=True)
        print(self.worker('probe'),flush=True)
        first=json.loads((self.directory/'requests.jsonl').read_text().splitlines()[-1])
        if not first['ok']:
            import re
            try:
                diagnostic='IO.inspect(AgoraBroker.Backend.connect(%{"principal" => %{"canvas_id" => 1, "nickname" => "Chaos laboratory", "tag_number" => 1, "settings_revision" => 0}, "include_items" => false, "client_id" => "diagnostic", "ws_port" => 8002, "client_ip" => "'+self.ips['worker']+'", "route" => %{"canvas_id" => 1, "connection_id" => "diagnostic", "request_id" => "diagnostic"}}))'
                print(call('exec',self.names['broker'],'/app/bin/agora_broker','rpc',diagnostic),flush=True)
            except RuntimeError:print('Backend diagnostic unavailable',flush=True)
            for key in ['gateway','broker','cpp']:
                logs=call('logs','--tail','18',self.names[key])
                logs=re.sub(r'eyJ[A-Za-z0-9_.-]+','[redacted-jwt]',logs)
                print(key+' diagnostic: '+logs,flush=True)
            raise RuntimeError('Valid user flow preflight failed: '+json.dumps(first))
        print('Real login/search/connect/save preflight passed.',flush=True)
    def cleanup(self):
        for name in reversed(self.containers):
            try:
                d=inspect(name)
                if d['Config']['Labels'].get('orchestra.chaos.run')!=self.tag:raise RuntimeError('Cleanup label mismatch')
                if d['State'].get('Paused'):call('unpause',name)
                call('rm','-f',name)
            except Exception as e:print('Cleanup error:',name,type(e).__name__,flush=True)
        for name in self.volumes:
            call('volume','rm',name)
        if self.network:call('network','rm',self.network)
        # Never retain app credentials or Redis CA private keys in results.
        for path in self.directory.glob('*.env'):path.unlink()
        (self.directory/'private.json').unlink(missing_ok=True)
        for name in ['ca.key','server.key']:(self.directory/'redis-tls'/name).unlink(missing_ok=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--rounds',type=int,default=3);parser.add_argument('--targets',default='gateway,spring,broker,cpp,storage,sql,es,redis-primary');args=parser.parse_args()
    allowed={'gateway','spring','broker','cpp','storage','sql','es','redis-primary'}
    selected=args.targets.split(',')
    if not 1<=args.rounds<=10 or len(set(selected))!=len(selected) or not set(selected)<=allowed:
        parser.error('Use 1–10 rounds and unique targets from '+','.join(sorted(allowed)))
    lab=Lab();print('Disposable run:',lab.tag,flush=True)
    try:
        lab.bootstrap()
        call('exec','-d',lab.names['worker'],'node','/lab/worker.mjs','run')
        targets=args.targets.split(',');rng=random.Random(20261015);events=[]
        config={'recovery_timeout':60,'nodes':{k:{'container':lab.names[k],'compose_project':lab.tag,'networks':[lab.network],'faults':['pause']} for k in targets}}
        docker=Docker();original_fault=docker.fault;original_restore=docker.restore
        for repeat in range(1,args.rounds+1):
            order=targets[:];rng.shuffle(order)
            for key in order:
                origin=int(time.time()*1000);meta={'enabled':True,'scenario':key,'round':repeat,'origin':origin}
                lab.state(**meta,phase='baseline');time.sleep(5)
                event={'scenario':key,'round':repeat,'origin':origin}
                def inject(state,kind,network,cpus):
                    original_fault(state,kind,network,cpus);event['fault_at']=int(time.time()*1000);lab.state(**meta,phase='fault')
                def restore(state,kind,network):
                    original_restore(state,kind,network);event['restored_at']=int(time.time()*1000);lab.state(**meta,phase='recovery')
                docker.fault=inject;docker.restore=restore
                run(config,plan(config,key,'pause',5),lab.directory/'journals',docker)
                time.sleep(15);lab.state(enabled=False);time.sleep(8)
                event['end']=int(time.time()*1000);events.append(event)
                (lab.directory/'events.json').write_text(json.dumps(events,indent=2))
                rows=[json.loads(v) for v in (lab.directory/'requests.jsonl').read_text().splitlines() if v]
                current=[v for v in rows if v['scenario']==key and v['round']==repeat]
                print(json.dumps({'scenario':key,'round':repeat,'successes':sum(v['ok'] for v in current),'attempts':len(current)}),flush=True)
        lab.state(stop=True,enabled=False);time.sleep(9)
        print(lab.worker('audit'),flush=True)
        (lab.directory/'manifest.json').write_text(json.dumps({'run':lab.tag,'subnet':lab.subnet,'rate':4,'stage_deadline_ms':2000,'baseline_s':5,'fault_s':5,'recovery_s':15,'rounds':args.rounds,'targets':targets,'images':{k:inspect(v)['Image'] for k,v in lab.names.items()},'isolated':True,'published_ports':False},indent=2))
        print('Experiment complete:',lab.directory,flush=True)
    finally:lab.cleanup()

if __name__=='__main__':main()
