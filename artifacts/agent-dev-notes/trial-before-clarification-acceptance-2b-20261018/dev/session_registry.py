"""Independent append-once identity records captured before a Run submission."""
import hashlib,json,os,re
from pathlib import Path
from session_recovery import Contract,Scope

def digest(raw):return hashlib.sha256(raw).hexdigest()

def exclusive_json(path,value):
    with path.open('x',encoding='utf-8') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())


class Registry:
    def __init__(self,root,*,freeze,limits,prefix,expected_turns):
        self.root=Path(root);self.directory=self.root/'identity-registry';self.directory.mkdir(exist_ok=True)
        self.header=dict(version=1,freeze=freeze,limits=limits,schema_prefix=prefix,expected_turns=expected_turns)
        Contract(prefix,freeze,{k:Scope(None,None,None,v) for k,v in expected_turns.items()})
        path=self.directory/'header.json'
        if path.exists():
            if json.loads(path.read_text(encoding='utf-8'))!=self.header:raise RuntimeError('Registry contract mismatch')
        else:exclusive_json(path,self.header)

    def register(self,budget,instance,*,schema,user_id,conversation_id):
        if instance not in self.header['expected_turns']:raise ValueError('Unregistered task')
        if not re.fullmatch(re.escape(self.header['schema_prefix'])+r'[0-9a-f]{32}',schema):raise ValueError('Schema prefix mismatch')
        path=self.directory/(instance+'.json')
        value=dict(instance=instance,schema=schema,user_id=user_id,conversation_id=conversation_id,
                   expected_turns=self.header['expected_turns'][instance],freeze_sha256=self.header['freeze'])
        exclusive_json(path,value)
        budget.register_identity(instance,digest(path.read_bytes()))

    def contract(self,budget_bytes):
        state=json.loads(budget_bytes);scopes={}
        if state['freeze']!=self.header['freeze'] or state['limits']!=self.header['limits']:raise RuntimeError('Registry budget mismatch')
        for instance,turns in self.header['expected_turns'].items():
            if state['instances'].get(instance)!='started':continue
            expected=state.get('identities',{}).get(instance)
            if not expected:
                scopes[instance]=Scope(None,None,None,turns)
                continue
            raw=(self.directory/(instance+'.json')).read_bytes()
            if digest(raw)!=expected:raise RuntimeError('Identity registry fingerprint mismatch')
            row=json.loads(raw)
            if row['instance']!=instance or row['freeze_sha256']!=self.header['freeze'] or row['expected_turns']!=turns:raise RuntimeError('Identity registry scope mismatch')
            scopes[instance]=Scope(row['schema'],row['user_id'],row['conversation_id'],turns)
        return Contract(self.header['schema_prefix'],self.header['freeze'],scopes)
