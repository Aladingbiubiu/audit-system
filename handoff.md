# 会话交接摘要

## 当前项目状态

- 项目：行政审批材料审核系统
- 路径：`d:\My Project\claude test\audit-system`
- 架构：`规则引擎优先 + LLM 辅助`
- 全量背景请先看：[project.md](</d:/My Project/claude test/audit-system/project.md>)

## 当前主线

系统已经从“主要靠大模型审核”转成“程序先判确定性规则，再让大模型处理语义理解类问题”。
当前已开始把规则从散落代码迁入结构化规则骨架：`DocumentFacts` 负责事实层，`GroupProfile` 负责团组画像，`AuditPolicy` / `PolicySelector` 负责团组策略，`RuleMetadata` / `RuleRunner` 负责规则身份和执行。

目前主线目标不是继续堆 prompt，而是：

1. 把更多可程序判定规则迁移到 `core/rule_engine.py`
2. 持续压低 LLM 误判
3. 让最终 summary 和 issues 更一致
4. 逐步减少规则隐形耦合，让新增规则优先进入元数据执行流程

## 最近已经完成的关键点

- 已接入 OCR，扫描页可识别
- 已支持程序判断：
  - 禁用词
  - 企业团组识别
  - 人员名单识别
  - 公示情况识别
  - 交通班次存在性识别
  - 邀请单位中文名称初步比对
  - 邀请函中文邀请单位高置信句式抽取、噪声截断和核心名称归一化
  - 跨材料停留天数一致性校验
- 已新增 `core/rule_model.py`：
  - `DocumentFacts`：材料事实层
  - `GroupProfile`：团组类型和任务类型画像
  - `AuditPolicy` / `PolicySelector`：按团组类型显式选择启用规则 ID
  - `RuleMetadata` / `Rule` / `RuleRunner`：规则元数据和执行器
- 第一批已通过规则元数据执行器接入：
  - 禁用词审核
  - 跨材料停留天数一致性
  - 邀请单位中文名称一致性
  - 高校科研院所团组策略审核
- 已做问题排序：
  - 严重 -> 一般 -> 提示
  - 同级按类别
  - 再按页码
- 已将最终 summary 改为基于最终 issues 重新生成，避免保留已被过滤的问题描述
- 已关闭一批不稳定规则：
  - 签发人相关
  - 文号相关
  - 团长（级别）
  - 落款 / 印章日期 OCR 不清类问题

## 当前最重要的已落地规则

### 1. 跨材料停留天数一致性

这条是目前最关键的确定性校验，已成功落地。

已能从以下材料提取天数并对比：

- 呈报表：`停留时间 XX 天`
- 邀请函翻译件：`在国外停留时间 XX 天`
- 预算审批意见表：`出访时间（天数）XX 天`

若不一致，程序直接报严重问题。

### 2. 禁用词

- `参观 / 考察 / 调研`：按严重问题处理
- `学习`：
  - 在 `培训 / 访学 / 学术交流` 场景放过
  - 其他场景可作为禁用词
- 邀请函译文中的禁用词不判断
- 同类禁用词只报一条

### 3. 企业团组口径

组团单位出现 `集团 / 公司 / 有限公司 / 股份公司` 等时，优先按企业团组处理。

企业团组不直接因缺少以下内容判错：

- 列入计划情况
- 周末公务情况
- 翻译情况
- 是否学术交流团

但仍应关注公示情况。

### 4. 材料顺序与访学附件范围

- 常见材料顺序默认按：
  - `呈报表`
  - `人员名单`
  - `日程安排`
  - `预算表`
  - `情况说明`
- 对访学团组后附材料：
  - 默认只识别附件第一页
  - 不做附件通篇逐页扫描
  - 不拿附件正文做禁用词和细碎语义判断

## 当前明确不要自动判断的内容

以下内容目前不要再加回自动审核，除非用户再次明确要求：

- 呈报表签发人
- 呈报表文号
- 预算审批意见表中的 `团长（级别）`
- 盖章 / 落款 / 手写附近的模糊日期识别

## 下一步建议优先级

### P1

- 用真实材料回放邀请单位中文名称抽取效果，继续小步调参
- 避免为少数样例扩大邀请单位误报面

### P2

- 把交通班次信息从“存在性识别”升级为“逐段结构化校验”

### P3

- 继续把其他确定性规则迁入 `RuleMetadata` / `PolicySelector` / `RuleRunner`，减少规则隐形耦合

## 建议新会话先看的文件

1. [project.md](</d:/My Project/claude test/audit-system/project.md>)
2. [core/rule_engine.py](</d:/My Project/claude test/audit-system/core/rule_engine.py>)
3. [core/rule_model.py](</d:/My Project/claude test/audit-system/core/rule_model.py>)
4. [core/workflow.py](</d:/My Project/claude test/audit-system/core/workflow.py>)
5. [core/auditor.py](</d:/My Project/claude test/audit-system/core/auditor.py>)
6. [config/rules.yaml](</d:/My Project/claude test/audit-system/config/rules.yaml>)
