# 思维架构库索引

> 存放**可复用的"如何思考"的元知识**。Agent 推理、想象空间、研究流水线可加载对应架构作为思维提示。
> 用户可自由增删改：新增条目 = 在本文档所在目录新建 `<name>.md`（带 frontmatter，可用 `order` 控制排列），重启后自动生效。

| order | name | 标题 | 标签 |
|---|---|---|---|
| 1 | react_loop | ReAct 推理循环 | 推理/agent/循环 |
| 2 | scientific_pipeline | 科研七步流水线（量纲分析范式） | 科研/方法论/因果 |
| 3 | multi_world_representation | 多世界重表示推理 | 推理/重表示/矛盾消解 |
| 4 | contradiction_classification | 矛盾三级分类 | 推理/矛盾/校验 |
| 5 | reflect_verify | 反思校验 | 反思/校验/经验 |
| 6 | debate_consensus | 讨论与辩论收敛 | 辩论/共识/多角色 |
| 7 | calculus_thinking | 微积分思想 | 数学/变化率/累积/优化 |
| 8 | approximation_estimation | 近似与量级估计 | 估算/量级/极限/误差 |
| 9 | interpolation_fitting | 插值与拟合 | 数据/模型/预测/回归 |
| 10 | divide_and_conquer | 分治与系统分解 | 分解/模块化/复杂度 |
| 11 | state_machine_modeling | 状态机建模 | 建模/状态/流程/转移 |
| 12 | feedback_control | 反馈与闭环修正 | 控制/闭环/纠错/收敛 |
| 13 | bayesian_update | 贝叶斯更新 | 概率/不确定性/证据 |
| 14 | falsification_test | 证伪检验 | 科学/验证/反例 |
| 15 | analogy_mapping | 类比迁移 | 类比/迁移/映射/保结构 |
| 16 | reverse_reasoning | 逆向推理 | 推理/规划/回溯 |

## 条目格式
```markdown
---
name: <唯一标识>
title: <标题>
tags: [<标签>]
desc: <一句话描述>
order: <排序号，可选>
---
正文（供 Agent 读取的可执行方法）
```
