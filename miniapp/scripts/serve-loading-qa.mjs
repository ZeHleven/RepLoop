// Loopback-only static build server. No accounts, backend proxy or mutation API.
import http from 'node:http'
import path from 'node:path'
import { readFile } from 'node:fs/promises'
const [directory, port] = process.argv.slice(2)
const root = path.resolve(directory)
const types = {'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml','.json':'application/json'}
http.createServer(async (req,res) => {
  try {
    const file = path.resolve(root,'.'+decodeURIComponent(new URL(req.url,'http://localhost').pathname))
    if (file !== root && !file.startsWith(root + path.sep)) { res.writeHead(403).end(); return }
    const target = file === root ? path.join(root,'index.html') : file
    const content = await readFile(target)
    res.writeHead(200,{'Content-Type':types[path.extname(target)] || 'application/octet-stream','Cache-Control':'no-store'}).end(content)
  } catch { res.writeHead(404).end('Not found') }
}).listen(Number(port),'127.0.0.1',() => console.log(`Loading QA static host http://127.0.0.1:${port}; API must be supplied by a fresh fixture-only browser`))
