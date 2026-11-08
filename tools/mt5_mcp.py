"""Local MT5 MCP client. Never prints connection credentials."""
from pathlib import Path
import json, re, urllib.request, sys
CONFIG=Path(__file__).resolve().parents[1]/'mt5-mcp.txt'
class Client:
 def __init__(self,port=22346):
  b=CONFIG.read_bytes();s=b.decode('utf-16') if b.startswith((b'\xff\xfe',b'\xfe\xff')) else b.decode('utf-8-sig')
  tokens=re.findall(r'Bearer\s+([^\s\"\'<>}]+)',s)
  if not tokens: raise RuntimeError('No usable MT5 bearer token in local configuration.')
  if port not in [22345,22346]: raise ValueError('Only existing local MT5 endpoints are allowed')
  self.endpoint=f'http://127.0.0.1:{port}/mcp';self.key=tokens[0];self.session=None;self.seq=0
  self.rpc('initialize',{'protocolVersion':'2025-06-18','capabilities':{},'clientInfo':{'name':'chart-dataset','version':'1.0'}})
  self.rpc('notifications/initialized',notification=True)
 def rpc(self,method,params=None,notification=False):
  self.seq+=1;body={'jsonrpc':'2.0','method':method,'params':params or {}}
  if not notification: body['id']=self.seq
  headers={'Authorization':'Bearer '+self.key,'Content-Type':'application/json','Accept':'application/json, text/event-stream'}
  if self.session: headers['Mcp-Session-Id']=self.session
  req=urllib.request.Request(self.endpoint,data=json.dumps(body).encode(),headers=headers)
  with urllib.request.urlopen(req,timeout=120) as response:
   self.session=response.headers.get('Mcp-Session-Id',self.session)
   raw=response.read().decode()
  if not raw: return {}
  if raw.startswith('event:') or raw.startswith('data:'):
   raw=next(s[5:].strip() for s in raw.splitlines() if s.startswith('data:'))
  data=json.loads(raw)
  if 'error' in data: raise RuntimeError(data['error'])
  return data.get('result',data)
 def call(self,name,args=None):
  if name.startswith('trade_'): raise RuntimeError('Trading is outside this data collection task.')
  return self.rpc('tools/call',{'name':name,'arguments':args or {}})
if __name__=='__main__':
 port=22345 if '--editor' in sys.argv else 22346
 if '--editor' in sys.argv: sys.argv.remove('--editor')
 c=Client(port)
 if sys.argv[1]=='list': result=c.rpc('tools/list')
 elif sys.argv[1]=='install': result=c.call('write_binary_file',{'path':sys.argv[3],'data':Path(sys.argv[2]).read_bytes().hex(),'overwrite':True})
 elif sys.argv[1]=='batch':
  result=[]
  for item in json.loads(Path(sys.argv[2]).read_text()):
   r=c.call(item['name'],item.get('arguments',{}));result.append(r)
   if r.get('isError'): break
 else: result=c.call(sys.argv[1],json.loads(sys.argv[2]) if len(sys.argv)>2 else {})
 print(json.dumps(result,ensure_ascii=False))
