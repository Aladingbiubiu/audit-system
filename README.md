# audit-system

行政审批材料审核系统，当前采用 `规则引擎优先 + LLM 辅助` 的混合审核架构。

## 快速开始

在项目目录下运行：

```powershell
python -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
```

默认访问地址：

`http://127.0.0.1:8501`

## PaddleOCR 增强识别

系统默认使用 RapidOCR，并在项目内存在 `.paddle-ocr-venv` 时，对扫描页批量调用
PaddleOCR PP-OCRv5 mobile 模型进行二次识别，再自动选择文本更完整的结果。

首次安装：

```powershell
powershell -ExecutionPolicy Bypass -File tools/setup_paddle_ocr.ps1
```

首次识别会下载本地模型。PaddleOCR 不可用或识别失败时会自动回退 RapidOCR。
如需临时关闭增强识别，可设置环境变量 `PADDLE_OCR_ENABLED=0`。

## 历史出访知识库

历史出访知识库是仓库内的独立模块，不接入自动审核流程，使用独立数据库 `data/history.db`。

单独启动：

```powershell
python -m streamlit run history_app.py --server.address 127.0.0.1 --server.port 8502
```

默认访问地址：

`http://127.0.0.1:8502`

## 项目入口

- [project.md](</d:/My Project/claude test/audit-system/project.md>)：项目总上下文，适合新会话完整接手
- [handoff.md](</d:/My Project/claude test/audit-system/handoff.md>)：会话交接摘要，适合快速热启动
- [AGENTS.md](</d:/My Project/claude test/audit-system/AGENTS.md>)：仓库级 AI 开发规范，约束后续 Codex 会话默认遵守的规则
- [VERSIONING.md](</d:/My Project/claude test/audit-system/VERSIONING.md>)：版本管理说明，约定如何提交、查看历史和打标签
- [lishishuju.md](</d:/My Project/claude test/audit-system/lishishuju.md>)：历史出访知识库模块记录

## 核心文件

- [app.py](</d:/My Project/claude test/audit-system/app.py>)
- [core/workflow.py](</d:/My Project/claude test/audit-system/core/workflow.py>)
- [core/rule_engine.py](</d:/My Project/claude test/audit-system/core/rule_engine.py>)
- [core/rule_model.py](</d:/My Project/claude test/audit-system/core/rule_model.py>)
- [core/auditor.py](</d:/My Project/claude test/audit-system/core/auditor.py>)
- [core/pdf_parser.py](</d:/My Project/claude test/audit-system/core/pdf_parser.py>)
- [config/rules.yaml](</d:/My Project/claude test/audit-system/config/rules.yaml>)
- [history_app.py](</d:/My Project/claude test/audit-system/history_app.py>)
- [history/](</d:/My Project/claude test/audit-system/history>)

## 当前开发方向

- 继续把可程序判定规则迁移到 `core/rule_engine.py`
- 新增确定性规则优先挂到 `core/rule_model.py` 的事实模型、规则元数据和团组类型策略上
- 继续完善 `PresentmentFacts` / `BudgetFacts`，先稳定抽取字段，再做跨表一致性规则
- 持续压低 LLM 误判
- 让最终 `summary` 与 `issues` 更一致
