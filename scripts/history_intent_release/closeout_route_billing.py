"""Freeze the harness-only repair and its offline evidence before a PR."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/agent-dev-notes/release-route-billing-20261108'
SOURCE = ROOT / '.tmp/agent-release-route-billing-20261108'
OLD = ROOT / '.tmp/agent-history-intent-20261108'
NOTES = ROOT / 'docs/agent/dev-notes'

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

paths = subprocess.check_output(['git', 'ls-files', 'backend/app'], cwd=SOURCE, text=True).splitlines()
assert all((SOURCE / p).read_bytes() == (OLD / p).read_bytes() for p in paths)
old_code = (OUT / 'before/evaluate_agent_routing_real.py').read_text(encoding='utf-8')
new_code = (SOURCE / 'backend/scripts/evaluate_agent_routing_real.py').read_text(encoding='utf-8')
def cases(code):
    return next(ast.dump(n.value, include_attributes=False) for n in ast.parse(code).body
                if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'CASES' for t in n.targets))
assert cases(old_code) == cases(new_code)
def verdict(code):
    function = next(n for n in ast.parse(code).body if isinstance(n, ast.AsyncFunctionDef) and n.name == 'evaluate')
    returned = next(n.value for n in ast.walk(function) if isinstance(n, ast.Return))
    return ast.dump(next(v for k, v in zip(returned.keys, returned.values) if isinstance(k, ast.Constant) and k.value == 'passed'), include_attributes=False)
assert verdict(old_code) == verdict(new_code)
evidence = {'base_sha': 'a65a3ff42e4f9084f2dda09c1db6f401cd0174d5', 'runtime_files_unchanged': len(paths),
            'runtime_files': {p: sha(SOURCE / p) for p in paths}, 'cases_ast_unchanged': True, 'verdict_ast_unchanged': True,
            'before_failures': 5, 'targeted_passed': 50, 'local_provider_calls': 0,
            'source_files': {str(p.relative_to(SOURCE)).replace('\\', '/'): sha(p) for p in [SOURCE / 'backend/scripts/evaluate_agent_routing_real.py', SOURCE / 'backend/tests/test_route_eval_billing.py']}}
(OUT / 'source-verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

append = f'''
## 本地修复结果

仅修改backend/scripts/evaluate_agent_routing_real.py，并新增test_route_eval_billing.py。脚本拒绝非本地、非明确评测名称的PostgreSQL库；先提交合成用户，再对整套题共享该账号，逐次进入既有billing_user。新增脱敏账本汇总，未知费用为null，保留预留和阻断，不重置额度。

新增5项改前全部失败：真实BudgetTransport因缺计费账号拒绝、后续场景无法形成可计费请求，3项新隔离库前置约束尚不存在。这是一个评测集成问题及新增边界，不能说是5个模型错误。改后本文件5项及既有路由/饮食评测/计费合计50项通过、5条既有warning。已知费用正确结算；缺usage后共享账号继续阻断，provider模拟层只收到1次请求，第二题不再发送。没有真实模型费用。

{len(paths)}份backend/app与a65a3ff（对应f4d388f实测运行代码）逐字一致；33题CASES AST和passed判分表达式AST完全一致。命令为`.venv/Scripts/python.exe scripts/history_intent_release/run_route_billing_regression.py before`与`after`；before.log/xml、after.log/xml及source-verification.json保留。首次本地git clone受限MSYS初始化失败，正常权限的本地克隆成功，无源码或数据丢失。

完整GitHub CI与新SHA的正式真实门禁待执行。旧a65a3ff正式门禁仍记失败，原62项饮食通过不跨SHA冒充新候选验收。原正式报告没有完整费用，不推断账单。

素材：[本地草稿](release-route-billing-20261108-wechat-draft.md)，配图引用已留原文的[上一阶段指标示意](assets/history-intent-release-20261108/metrics.png)，明确只表示历史查询本地结果；本次门禁故障另用流程图示意，非产品截图。未发布。
'''
p = NOTES / 'release-route-billing-20261108.md'
value = p.read_text(encoding='utf-8').split('\n## 本地修复结果')[0]
p.write_text(value.rstrip() + '\n' + append, encoding='utf-8')
draft = '''# 开发笔记：30个失败，不一定是模型答错了

历史查询修复合并后，正式发布门禁给出了3/33。另一组全天饮食任务却两轮都31/31。先不能急着调提示词：30项共同的错误都是ModelBudgetError。

沿调用链检查，路由评测器直接进入模型，而生产运行时会先为当前账号建立计费上下文。旧评测脚本缺了这一步。费用保护按设计在发出请求前阻止了它，三项规则捷径没有触发模型，所以留下3/33。这是评测器没有接上计费边界，不能当成模型真实准确率。

修复只为隔离评测库创建合成账号，让整套题共享同一份额度，并通过既有传输层预留和结算。没有关费用保护，也没有给每道题重置额度。5项新回归在旧脚本全部失败，修复后连同现有计费和评测测试50项通过；缺少用量时仍保留预留并阻断后续请求。

```mermaid
flowchart LR
  A[旧路由评测：直接调用] --> B[缺计费账号]
  B --> C[费用保护拒绝发送]
  D[隔离库合成账号] --> E[既有 billing_user 上下文]
  E --> F[预留 → 模型 → 结算]
  F --> G[同一账号继续评测]
```

图为代码流程示意，不是小程序截图。原失败报告、修改前源码与离线传输层证据都有保存。生产Agent运行文件和33道题的预期、通过条件不变，后续还必须对新的完整SHA重新完成正式门禁，才能发布。

还有一处记录错误也要承认：工作流允许失败后继续执行，中间步骤显示success被误读为路由已通过。最终以工作流裁决和报告更正，不用一个绿色状态替代任务结果。

本稿仅本地准备。手机体验仍未采集，公众号未发布。来源：release-route-billing-20261108轮次记录、正式工作流42577336410、before/after离线回归与source-verification.json。
'''
(NOTES / 'release-route-billing-20261108-wechat-draft.md').write_text(draft, encoding='utf-8')
line = f'**2026-11-08｜EVAL-20261108-01本地修复完成。** 评测器补隔离账号和既有计费上下文，整批共用额度；新增5项改前失败，改后相关50通过。{len(paths)}份Agent运行源码、33题及判分表达式保持。原正式路由门禁3/33为计费上下文受阻，旧失败保留；待GitHub CI及新SHA一次完整正式复测。[记录](release-route-billing-20261108.md)。'
for folder in (NOTES, SOURCE / 'docs/agent/dev-notes'):
    path = folder / 'issues.md'
    text = path.read_text(encoding='utf-8')
    if line not in text:
        first, rest = text.split('\n', 1)
        path.write_text(first + '\n\n' + line + '\n' + rest, encoding='utf-8')
    path = folder / 'README.md'
    text = path.read_text(encoding='utf-8')
    entry = '- [正式路由评测接入计费上下文：5项改前失败，修复后相关50项通过，运行代码与判分不变](release-route-billing-20261108.md)'
    if entry not in text:
        first, rest = text.split('\n', 1)
        path.write_text(first + '\n\n' + entry + '\n' + rest, encoding='utf-8')
for name in ('release-route-billing-20261108.md', 'release-route-billing-20261108-wechat-draft.md'):
    (SOURCE / 'docs/agent/dev-notes' / name).write_bytes((NOTES / name).read_bytes())
print(json.dumps({k: v for k, v in evidence.items() if k not in {'runtime_files', 'source_files'}}))
