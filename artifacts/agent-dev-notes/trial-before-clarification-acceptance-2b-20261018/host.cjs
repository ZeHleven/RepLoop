const fs=require('fs'),p=require('path'),cp=require('child_process'),a=require('assert/strict'),crypto=require('crypto');
const R=__dirname,mode=process.argv[2],batch=process.argv[3],D=p.join(R,batch||''),TOP=p.resolve(R,'../../..');
const W='C:/Users/12521/.codex/worktrees/r87-trigger-consistency/FitnessAgent',PG='reploop-trialaccept2b-'+batch,DB='agent_trialaccept2b_'+batch+'_test',PASS='trialaccept2b-local-synthetic-only',IMAGE='sha256:08d37783879d9da29af3a0f81a93fee3445594c8c39e525117aae6789eaf513f';
a(['dev','holdout'].includes(batch));
function docker(args){const r=cp.spawnSync('docker',args,{encoding:'utf8',timeout:60000});a.equal(r.status,0,r.stderr);return r.stdout.trim();}
function pool(action,code){const r=cp.spawnSync(p.join(TOP,'.venv/Scripts/python.exe'),['-X','utf8',p.join(R,'pool.py'),action,batch,...(code===undefined?[]:[String(code)])],{encoding:'utf8',timeout:30000});console.log(r.stdout);if(r.status!==0)console.error(r.stderr);a.equal(r.status,0,'Pool reservation/settlement stopped');}
const hash=f=>crypto.createHash('sha256').update(fs.readFileSync(f)).digest('hex');
if(mode==='prepare'){
 a(!docker(['ps','-a','--filter','name=^/'+PG+'$','--format','{{.Names}}']));
 const image='sha256:c021e0b757cdfeaa1b8d302f6b74866814a3e51248bf26296a05856f6c9cd42a';
 docker(['run','-d','--pull','never','--name',PG,'--network','none','-e','POSTGRES_USER=agent_eval','-e','POSTGRES_PASSWORD='+PASS,'-e','POSTGRES_DB='+DB,image]);
 fs.writeFileSync(p.join(D,'environment.json'),JSON.stringify({source:W,postgres:PG,postgres_image:image,app_image:IMAGE,external_ports:0,synthetic_only:true},null,2),{flag:'wx'});
}else if(mode==='extension'){console.log(docker(['exec',PG,'psql','-U','agent_eval','-d',DB,'-c','CREATE EXTENSION IF NOT EXISTS vector;']));
}else if(mode==='stop'){console.log(docker(['stop','--timeout','10',PG]));
}else{
 a(['preflight','unit','live','usage-audit'].includes(mode));const stamp=Date.now(),name='trialaccept2b-'+batch+'-'+mode+'-'+stamp,url=`postgresql+asyncpg://agent_eval:${PASS}@127.0.0.1:5432/${DB}`;
 const env={...process.env};delete env.DEEPSEEK_API_KEY;
 if(mode==='live'){
  const freeze=JSON.parse(fs.readFileSync(p.join(D,'freeze.json'),'utf8'));
  a.equal(freeze.host_sha256,hash(__filename));a.equal(freeze.pool_sha256,hash(p.join(R,'pool.py')));
  for(const [file,digest] of Object.entries(freeze.files))a.equal(hash(p.join(D,file)),digest,'Frozen file changed: '+file);
  a.equal(freeze.holdout_gold_sha256,hash(p.join(R,'holdout-gold.json')));
  a.equal(freeze.authorization_sha256,hash(p.join(R,'../authorized-retests-60/authorization.json')));
  a.equal(freeze.round_authorization_sha256,hash(p.join(R,'authorization.json')));
  const src=JSON.parse(fs.readFileSync(p.join(D,'candidate-source.json'),'utf8'));
  for(const f of src.files)a.equal(hash(p.join(W,f.file)),f.sha256,f.file);
  pool('reserve'); // Before key read and external network; no automatic replay.
  const networks=JSON.parse(docker(['inspect',PG,'--format','{{json .NetworkSettings.Networks}}']));
  if(networks.none)docker(['network','disconnect','none',PG]);if(!networks.bridge)docker(['network','connect','bridge',PG]);docker(['start',PG]);
  const key=fs.readFileSync(p.join(TOP,'.env'),'utf8').match(/^DEEPSEEK_API_KEY\s*=\s*(.*?)\s*$/m);
  env.DEEPSEEK_API_KEY=key?.[1].replace(/^(["'])(.*)\1$/,'$2');a(/^sk-[A-Za-z0-9]{20,}$/.test(env.DEEPSEEK_API_KEY||''),'Missing key, never logged');
 }
 const args=['run','--pull','never','--name',name,'--network','container:'+PG,'--mount',`type=bind,source=${W},target=/workspace,readonly`,'--mount',`type=bind,source=${D},target=/evidence`,'-w','/workspace/backend'];
 const vars={DATABASE_URL:url,TEST_DATABASE_URL:url,AGENT_QUERY_EVAL_DATABASE_URL:url,SECRET_KEY:'round51-synthetic-local-only',PYTHONPATH:'/workspace/backend:/workspace/backend/tests:/evidence',PYTHONDONTWRITEBYTECODE:'1',PYTHONFAULTHANDLER:'1',PYTHONIOENCODING:'utf8',R31_MODE:mode,DEEPSEEK_BASE_URL:'https://api.deepseek.com',DEEPSEEK_CHAT_MODEL:'deepseek-v4-flash',AGENT_MODEL:'deepseek-v4-flash',AGENT_INTENT_MODEL:'deepseek-v4-flash',RAG_ENABLED:'false',EMBEDDING_API_KEY:'',WECHAT_APP_SECRET:'',OPENAI_API_KEY:'',AGENT_ASYNC_WORKER_ENABLED:'false'};
 for(const[k,v]of Object.entries(vars))args.push('-e',k+'='+v);args.push('-e',mode==='live'?'DEEPSEEK_API_KEY':'DEEPSEEK_API_KEY=',IMAGE,'python');
 const xml=mode==='unit'?'unit-'+stamp+'.xml':null;
 if(mode==='unit')args.push('-m','pytest','-q','--assert=plain','-p','no:cacheprovider','/evidence/test_continuation_budget.py','/evidence/test_usage_v2.py','/evidence/test_new_limits.py','--junitxml=/evidence/'+xml);
 else args.push('/evidence/'+(mode==='usage-audit'?'verify_usage_v2.py':'run.py'));
 const log=mode+'-'+stamp+'.log',out=fs.createWriteStream(p.join(D,log),{flags:'wx'}),child=cp.spawn('docker',args,{env,stdio:['ignore','pipe','pipe']});
 child.stdout.on('data',d=>{out.write(d);process.stdout.write(d)});child.stderr.on('data',d=>{out.write(d);process.stderr.write(d)});
 const timer=setTimeout(()=>docker(['stop','--timeout','1',name]),(mode==='live'?2399:600)*1000);
 child.on('close',(code,signal)=>{clearTimeout(timer);out.end(()=>{const state=JSON.parse(docker(['inspect',name,'--format','{{json .State}}']));fs.writeFileSync(p.join(D,mode+'-'+stamp+'.status.json'),JSON.stringify({mode,code,signal,container:name,container_state:state,xml,log,at:new Date().toISOString()},null,2),{flag:'wx'});console.log(JSON.stringify({mode,code,container:state}));if(mode==='live')pool('settle',code??1);process.exitCode=code??1;});});
}
