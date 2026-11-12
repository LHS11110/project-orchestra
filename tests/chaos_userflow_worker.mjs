// Runs only inside the disposable network made by chaos_userflow_lab.py.
import fs from 'node:fs';
import {randomUUID} from 'node:crypto';
const cfg=JSON.parse(fs.readFileSync('/lab/private.json'));
const base='https://agora-nginx:8444', deadline=2000;
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function http(path,body,token,method=body===undefined?'GET':'POST',limit=deadline){
 const r=await fetch(base+path,{method,headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(limit)});
 const text=await r.text();if(!r.ok)throw new Error('HTTP_'+r.status);
 return text?JSON.parse(text):{};
}
const login=()=>http('/api/auth/login',{email:cfg.email,password:cfg.password});
function socket(token,canvas,client){
 const ws=new WebSocket(`wss://agora-nginx:8444/wss/port/8002/canvas/${canvas}?token=${encodeURIComponent(token)}&client_id=${client}`);
 const inbox=[],waiters=[];let ended=false;
 ws.addEventListener('message',event=>{let data;try{data=JSON.parse(event.data);}catch{return;}inbox.push(data);for(const fn of [...waiters])fn();});
 ws.addEventListener('error',()=>{ended=true;for(const fn of [...waiters])fn();});
 ws.addEventListener('close',()=>{ended=true;for(const fn of [...waiters])fn();});
 const read=(predicate,limit=deadline)=>new Promise((resolve,reject)=>{
  let timer;const done=(error,data)=>{clearTimeout(timer);const i=waiters.indexOf(check);if(i>=0)waiters.splice(i,1);error?reject(error):resolve(data);};
  const check=()=>{const i=inbox.findIndex(predicate);if(i>=0)done(null,inbox.splice(i,1)[0]);else if(ended)done(new Error('WS_CLOSED'));};
  timer=setTimeout(()=>done(new Error('WS_TIMEOUT')),limit);waiters.push(check);check();
 });
 return {ws,read};
}
async function setup(){
 await http('/api/auth/signup',{email:cfg.email,password:cfg.password,nickname:'Chaos laboratory',emailVerificationToken:cfg.emailVerificationToken},undefined,'POST',15000);
 const auth=await login();
 const canvas=await http('/api/canvases',{canvasName:'Chaos userflow lab',description:'Disposable availability experiment'},auth.accessToken,'POST',15000);
 fs.writeFileSync('/lab/canvas.json',JSON.stringify(canvas));
 await sleep(2000);
 console.log('Fixture account and canvas created.');
}
async function journey(meta){
 const start=Date.now(),id=randomUUID(), stages=[];let connection,failed;
 async function stage(name,fn){const at=Date.now();try{const value=await fn();if(Date.now()-at>deadline)throw new Error('DEADLINE_EXCEEDED');stages.push({name,at,ms:Date.now()-at,ok:true});return value;}catch(e){stages.push({name,at,ms:Date.now()-at,ok:false,error:e.name==='TimeoutError'?'TIMEOUT':e.message.replace(/wss?:\S+/g,'[redacted]')});throw e;}}
 try{
  const auth=await stage('login',login);
  await stage('search',async()=>{const list=await http('/api/canvases/search?name=Chaos',undefined,auth.accessToken);if(!Array.isArray(list)||!list.some(c=>c.canvas_id===cfg.canvas))throw new Error('SEARCH_CANVAS_MISSING');});
  const client=randomUUID();
  await stage('connect',async()=>{
   const at=Date.now(),access=await http(`/api/canvases/${cfg.canvas}/access`,{},auth.accessToken);
   connection=socket(access.canvas_access_token,cfg.canvas,client);
   const init=await connection.read(d=>d.type==='init_items',Math.max(1,deadline-(Date.now()-at)));
   if(init.sync_protocol!==2||init.client_id!==client)throw new Error('INVALID_INITIAL_SNAPSHOT');
  });
  await stage('save',async()=>{
   const at=Date.now(),request=randomUUID();
   connection.ws.send(JSON.stringify({type:'canvas_ops',client_id:client,request_count:1,request_id:request,operations:[{action:'create',id:'flow-'+id,patch:{kind:'shape',shapeType:'rectangle',x:0.1,y:0.1,width:0.1,height:0.1,color:'#123456',fill:'#ffffff',permission:'default'}}]}));
   const result=await connection.read(d=>d.request_id===request&&(d.type==='canvas_result'||d.type==='error'));
   if(!result.ok)throw new Error(result.code||'SAVE_REJECTED');
   const commit=await connection.read(d=>d.type==='canvas_commit'&&d.request_id===request,Math.max(1,deadline-(Date.now()-at)));
   if(!commit.items?.['flow-'+id])throw new Error('COMMIT_ITEM_MISSING');
  });
 }catch(e){failed=stages.at(-1)?.name||'client';}
 finally{if(connection)connection.ws.close();}
 const record={...meta,id,start,end:Date.now(),ok:!failed,failed,stages};
 fs.appendFileSync('/lab/requests.jsonl',JSON.stringify(record)+'\n');
}
async function run(){
 cfg.canvas=JSON.parse(fs.readFileSync('/lab/canvas.json')).canvas_id;
 const active=new Set();let last=0;
 while(true){
  const control=JSON.parse(fs.readFileSync('/lab/control.json'));
  if(control.stop)break;
  if(control.enabled&&Date.now()>=last+250){
   last=Date.now();const task=journey({scenario:control.scenario,round:control.round,origin:control.origin,phase:control.phase});active.add(task);task.finally(()=>active.delete(task));
  }
  await sleep(10);
 }
 await Promise.allSettled([...active]);console.log('Load generator drained.');
}
async function features(){
 const {textDelta}=await import('/lab/itemDelta.mjs');
 cfg.canvas=JSON.parse(fs.readFileSync('/lab/canvas.json')).canvas_id;
 const auth=await login(),access=await http(`/api/canvases/${cfg.canvas}/access`,{},auth.accessToken),client=randomUUID(),connection=socket(access.canvas_access_token,cfg.canvas,client);let count=0;
 async function rpc(service){const request_id=randomUUID();connection.ws.send(JSON.stringify({type:'runtime_execute',action:'service',request_id,service}));const response=await connection.read(e=>e.request_id===request_id&&(e.type==='runtime_result'||e.type==='error'),20000);if(!response.ok)throw Error('SERVICE_'+(response.code||response.message));return response.result;}
 async function ops(operations){const request_id=randomUUID();connection.ws.send(JSON.stringify({type:'canvas_ops',client_id:client,request_count:++count,request_id,operations}));const result=await connection.read(e=>e.request_id===request_id&&e.type==='canvas_result',10000);if(!result.ok)throw Error('FEATURE_SAVE_'+result.code);return connection.read(e=>e.request_id===request_id&&e.type==='canvas_commit',10000);}
 try{
  await connection.read(e=>e.type==='init_items',10000);connection.ws.send(JSON.stringify({type:'canvas_sync',delta_commits:true,client_id:client,request_id:randomUUID()}));await connection.read(e=>e.type==='canvas_snapshot',10000);
  const id='feature-notebook';const before='x'.repeat(20000);
  await ops([{action:'create',id,patch:{kind:'shape',shapeType:'rectangle',runtimeRole:'notebook',x:0,y:0,width:.5,height:.6,permission:'default',notebookCells:[{id:'a',cellType:'code',source:before},{id:'b',cellType:'code',source:'untouched'}],notebookResults:{}}}]);
  const commit=await ops([{action:'patch',id,patch:{},notebook_edits:[{id:'a',patch:{},source_delta:textDelta(before,before+'!')}]}]);
  if(commit.items?.[id]||!commit.patches?.[id]||JSON.stringify(commit).includes(before))throw Error('NOTEBOOK_DELTA_NOT_COMPACT');
  await ops([{action:'patch',id,patch:{},notebook_structure:{order:['a','b','c'],insert:[{id:'c',cellType:'code',source:'new cell'}],remove:[]}}]);
  await ops([{action:'patch',id,patch:{},notebook_results:{set:{a:{outputs:[{output_type:'stream',text:'first output'}],exitCode:0}},remove:[]}}]);
  await ops([{action:'patch',id,patch:{},notebook_results:{set:{b:{outputs:[{output_type:'stream',text:'second output'}],exitCode:0}},remove:[]}}]);
  await ops([{action:'create',id:'feature-code',patch:{kind:'text',renderMode:'code',language:'python',text:before,x:0,y:0,width:.3,height:.2,permission:'default'}}]);
  await ops([{action:'patch',id:'feature-code',patch:{},text_edits:{text:textDelta(before,before+'!')}}]);
  const staleId=randomUUID();connection.ws.send(JSON.stringify({type:'canvas_ops',client_id:client,request_count:count+1,request_id:staleId,operations:[{action:'patch',id:'feature-code',patch:{},text_edits:{text:textDelta(before,before+'?')}}]}));
  const stale=await connection.read(e=>e.request_id===staleId&&(e.type==='error'||e.type==='canvas_result'),10000);if(stale.code!=='CANVAS_TEXT_CONFLICT')throw Error('STALE_TEXT_ACCEPTED');
  await ops([{action:'patch',id:'feature-code',patch:{},text_edits:{text:textDelta(before+'!',before+'!?')}}]);
  connection.ws.send(JSON.stringify({type:'canvas_sync',delta_commits:true,client_id:client,request_id:randomUUID()}));const current=await connection.read(e=>e.type==='canvas_snapshot',10000);
  if(current.items[id].notebookCells.length!==3||current.items[id].notebookCells[1].source!=='untouched'||current.items[id].notebookResults.a.outputs[0].text!=='first output'||current.items[id].notebookResults.b.outputs[0].text!=='second output'||current.items['feature-code'].text!==before+'!?')throw Error('NOTEBOOK_DELTA_STATE_MISMATCH');
  const bots=await rpc({operation:'api',method:'GET',path:'/bots'});if(!Array.isArray(bots))throw Error('BOT_LIST_INVALID');
  const created=await rpc({operation:'api',method:'POST',path:'/bots',body:{name:'Chaos service fixture'}});if(!created.bot?.user_id)throw Error('BOT_CREATE_INVALID');await rpc({operation:'api',method:'DELETE',path:'/bots/'+created.bot.user_id});
  const context=await rpc({operation:'api',method:'GET',path:'/github'});if(!context||typeof context!=='object')throw Error('GITHUB_CONTEXT_INVALID');
  const gif=Buffer.from('R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7','base64');
  const upload=await rpc({operation:'upload_begin',path:'/media',files:[{field:'file',name:'fixture.gif',type:'image/gif',size:gif.length}]});
  const chunk={operation:'upload_chunk',path:'/media',uploadId:upload.uploadId,index:0,offset:0,data:gif.toString('base64')};await rpc(chunk);await rpc(chunk);
  const file=await rpc({operation:'upload_finish',path:'/media',uploadId:upload.uploadId});const resource=await rpc({operation:'resource',path:file.url.replace(`/api/canvases/${cfg.canvas}`,''),offset:0});if(!Buffer.from(resource.data,'base64').equals(gif))throw Error('FILE_ROUNDTRIP_MISMATCH');
  console.log('Features passed: compact notebook edit/commit, bot list/create/revoke, repository context, chunk retry and binary file roundtrip.');
 }finally{connection.ws.close();}
}
if(process.argv[2]==='features')await features();else if(process.argv[2]==='setup')await setup();else if(process.argv[2]==='audit'){
 cfg.canvas=JSON.parse(fs.readFileSync('/lab/canvas.json')).canvas_id;
 // A separate fault-free control after all injected faults have recovered.
 const healthyTasks=[], healthyOrigin=Date.now();
 for(let i=0;i<40;i++){healthyTasks.push(journey({scenario:'healthy-control',round:0,origin:healthyOrigin,phase:'healthy'}));await sleep(250);}
 await Promise.allSettled(healthyTasks);
 const auth=await login(), access=await http(`/api/canvases/${cfg.canvas}/access`,{},auth.accessToken), client=randomUUID();
 const connection=socket(access.canvas_access_token,cfg.canvas,client);
 try {
  const init=await connection.read(d=>d.type==='init_items',10000);
  const rows=fs.readFileSync('/lab/requests.jsonl','utf8').trim().split('\n').map(v=>JSON.parse(v));
  const confirmed=rows.filter(r=>r.ok),missing=confirmed.filter(r=>!init.items['flow-'+r.id]);
  const malformed=confirmed.filter(r=>{const i=init.items['flow-'+r.id];return i&&(i.kind!=='shape'||i.x!==0.1||i.width!==0.1);});
  const lateSaved=rows.filter(r=>!r.ok&&init.items['flow-'+r.id]).length;
  const audit={confirmed:confirmed.length,missing:missing.length,malformed:malformed.length,timed_out_but_saved:lateSaved,stored_objects:Object.keys(init.items).length,revision:init.revision};
  fs.writeFileSync('/lab/audit.json',JSON.stringify(audit,null,2)); console.log(JSON.stringify(audit));
  if(missing.length||malformed.length)process.exitCode=1;
 }finally{connection.ws.close();}
}else if(process.argv[2]==='probe'){
 cfg.canvas=JSON.parse(fs.readFileSync('/lab/canvas.json')).canvas_id;await journey({scenario:'preflight',round:0,origin:Date.now(),phase:'baseline'});
}else await run();
