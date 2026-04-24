# audit-system

行政审批材料审核系统，当前采用 `规则引擎优先 + LLM 辅助` 的混合审核架构。

## 快速开始

在项目目录下运行：

```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

默认访问地址：

`http://127.0.0.1:8501`

## 项目入口

- [project.md](</d:/My Project/claude test/audit-system/project.md>)：项目总上下文，适合新会话完整接手
- [handoff.md](</d:/My Project/claude test/audit-system/handoff.md>)：会话交接摘要，适合快速热启动
- [AGENTS.md](</d:/My Project/claude test/audit-system/AGENTS.md>)：仓库级 AI 开发规范，约束后续 Codex 会话默认遵守的规则
- [VERSIONING.md](</d:/My Project/claude test/audit-system/VERSIONING.md>)：版本管理说明，约定如何提交、查看历史和打标签

## 核心文件

- [app.py](</d:/My Project/claude test/audit-system/app.py>)
- [core/workflow.py](</d:/My Project/claude test/audit-system/core/workflow.py>)
- [core/rule_engine.py](</d:/My Project/claude test/audit-system/core/rule_engine.py>)
- [core/auditor.py](</d:/My Project/claude test/audit-system/core/auditor.py>)
- [core/pdf_parser.py](</d:/My Project/claude test/audit-system/core/pdf_parser.py>)
- [config/rules.yaml](</d:/My Project/claude test/audit-system/config/rules.yaml>)

## 当前开发方向

- 继续把可程序判定规则迁移到 `core/rule_engine.py`
- 持续压低 LLM 误判
- 让最终 `summary` 与 `issues` 更一致
