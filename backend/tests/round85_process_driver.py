"""Fresh worker process with actual SDK over loopback HTTP; no provider access."""
import asyncio,json,os,sys,threading
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool
from app.config import settings
from app.services.agent_jobs import process_agent_run
from round85_support import configure,install_route,reply

schema,rid,config_file,destination=sys.argv[1:]
D=Path(destination);D.mkdir(parents=True)
command=json.loads(Path(config_file).read_text());contexts=[];requests=[]
class Provider(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self,*args):pass
    def do_POST(self):
        requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        body={'error':{'message':'synthetic R85 failure','type':'service_unavailable'}} if command.get('fail') else reply()
        raw=json.dumps(body).encode();self.send_response(503 if command.get('fail') else 200)
        self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw);self.wfile.flush()
configure();server=ThreadingHTTPServer(('127.0.0.1',0),Provider);threading.Thread(target=server.serve_forever,daemon=True).start()
settings.DEEPSEEK_BASE_URL=f'http://127.0.0.1:{server.server_port}'
patch=pytest.MonkeyPatch();install_route(patch,command,contexts)
async def main():
    engine=create_async_engine(os.environ['AGENT_QUERY_EVAL_DATABASE_URL'],poolclass=NullPool,connect_args={'server_settings':{'search_path':schema+',public'}})
    try:await process_agent_run(async_sessionmaker(engine,expire_on_commit=False),rid)
    finally:await engine.dispose()
try:asyncio.run(main())
finally:
    (D/'observed.json').write_text(json.dumps(dict(pid=os.getpid(),contexts=contexts,requests=requests,scripted_only=True),ensure_ascii=False,indent=2));patch.undo();server.shutdown()
