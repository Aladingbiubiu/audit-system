# 历史出访知识库模块记录

## 当前定位

历史出访知识库放在 `audit-system` 仓库内，但作为独立模块运行。它不接入现有自动审核流程，不在审核完成后自动归档，也不写入 `data/audit.db`。

当前设计边界：

- 独立入口：`history_app.py`
- 独立模块：`history/`
- 独立数据库：`data/history.db`
- 复用方式：通过 `history/audit_adapter.py` 调用现有 PDF/OCR 与事实抽取能力
- 审核系统依赖方向：历史模块可以读取 `core/pdf_parser.py`、`core/rule_engine.py`；审核主流程不依赖历史模块

## 已实现能力

### 批量建库

- 用户输入一个历史材料根目录。
- 系统递归扫描目录下所有 PDF。
- 每个 PDF 作为一次独立出访任务入库。
- 单个 PDF 失败不影响整个批次。
- 记录导入任务、来源文件、成功/失败/需复核状态。

### 字段抽取

已优先复用现有 `DocumentFacts` / `PresentmentFacts` / `BudgetFacts`，抽取字段包括：

- 国家 / 地区 / 城市
- 国外单位中文名、外文名
- 机构类型
- 业务领域
- 出访事由与摘要
- 停留天数
- 组团单位
- 团组类型
- 团组人数
- 经费来源
- 出访日期
- 来源 PDF 路径和 hash

### 重复处理

- 用文件 hash 标记精确重复。
- 用国家、出访日期、组团单位、国外单位、停留天数等字段识别疑似重复。
- 重复材料只标记为需复核，不自动合并、不自动删除。

### 查询与推荐

`history_app.py` 当前包含三个页面：

1. `批量建库`：递归导入 PDF，查看导入任务和结果。
2. `出访档案`：按国家、行业、国外单位、组团单位、重复状态筛选，支持人工修正。
3. `智能推荐`：输入目标国家后展示历史出访、常见国外单位和相似任务，并显示推荐原因。

## 主要文件

- `history_app.py`：独立 Streamlit 入口。
- `history/models.py`：独立 SQLAlchemy 模型和 `history.db` 连接。
- `history/repository.py`：历史库读写、查询、人工修正。
- `history/audit_adapter.py`：复用现有 PDF/OCR 与事实抽取的适配器。
- `history/extractor.py`：从 PDF 和 `DocumentFacts` 生成历史出访字段。
- `history/importer.py`：递归扫描、批量导入、重复标记。
- `history/recommender.py`：国家查询与相似任务推荐。
- `core/rule_engine.py`：新增只读公共方法 `extract_facts()`，用于复用事实抽取，不改变审核行为。

## 启动方式

```powershell
python -m streamlit run history_app.py --server.address 127.0.0.1 --server.port 8502
```

默认访问：

`http://127.0.0.1:8502`

## 已验证

- `python -m compileall core history history_app.py app.py` 通过。
- 历史模块核心服务可正常 import。
- 使用现有 PDF 做过一次非 OCR 烟测导入，能生成历史记录；测试后已清空 `history.db` 中的测试行。
- `history_app.py` 可在 `8502` 端口启动并返回 HTTP 200。

## 后续建议

- 历史库先保持独立，不要急着接入审核完成流程。
- 批量导入真实历史材料前，先用 5-10 个 PDF 小批量试跑，观察字段质量和重复标记效果。
- Word 材料暂未接入，后续可增加 doc/docx 解析适配器。
- 业务领域目前以关键词和可选 LLM 辅助为主，后续可沉淀固定行业字典。

