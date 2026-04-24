# 版本管理说明

本项目已使用 `git` 做版本管理。

## 日常使用

查看当前改动：

```powershell
git status
```

提交一次开发进度：

```powershell
git add .
git commit -m "规则引擎：新增跨材料停留天数一致性校验"
```

查看历史版本：

```powershell
git log --oneline --decorate --graph
```

## 建议的提交节奏

建议按“一个可回滚的小里程碑”提交一次，例如：

- 修复一类误判
- 新增一条确定性规则
- 调整一组审核口径
- 完成一轮上下文文档整理

不要等很多改动堆在一起再提交。

## 建议的版本标签

当某一轮功能比较完整、可作为阶段版本时，打一个标签：

```powershell
git tag v0.1.0
```

查看所有标签：

```powershell
git tag
```

查看某个历史版本：

```powershell
git show v0.1.0
```

## 当前建议

建议后续按下面的方式管理：

- 日常开发：频繁 commit
- 阶段稳定版：打 tag
- 若需要版本说明：同步更新 `project.md` 或 `handoff.md`
