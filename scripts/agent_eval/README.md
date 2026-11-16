# 评测公共脚本

`metrics.py` 负责离线指标计算，`capture.py` 负责模型请求归属、预算限制和原始事件采集。各轮次目录中的同名文件只保留兼容入口，公共逻辑统一在此维护；任务、夹具和验收条件仍留在各轮次目录。

旧路径的命令行与导入方式继续可用，无需先切换工作目录。离线检查：

```powershell
.venv/Scripts/python.exe -B -m unittest discover -s scripts/agent_experience -p 'test_*.py' -v
.venv/Scripts/python.exe -B -m unittest discover -s scripts/agent_eval/tests -p 'test_*.py' -v
```

单独挂载观察器的容器同时需要将本目录只读挂载到 `/eval_support/agent_eval`，并将 `/eval_support` 加入 `PYTHONPATH`。相关启动器已配置这两个条件。

新评测的 `freeze.json` 或预登记记录使用 `shared_observer_files` 保存 `agent_eval.source_manifest()` 返回的指纹；已有预冻结校验的启动器同步核对这个字段。历史冻结记录不应为适配新代码而改写，使用新代码重跑前需要重新冻结。

合并前的脚本已按字节校验并保存在 `artifacts/_archive/scripts-before-dedup-20261112.zip`（内含 SHA-256 清单）。需按旧指纹复核历史报告时，在独立目录使用该快照及原始评测产物。
