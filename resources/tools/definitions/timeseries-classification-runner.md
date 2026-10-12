# 通用时序分类训练评测

内置五通道低氧预测所需的时序分类模型。支持查看模型目录、选择模型、
保存结构化模型与训练参数，并由模型资源驱动后台训练。

支持数据检查、数据注册、模型目录、模型资源创建、后台训练、任务进度、模型导入和评测报告读取。
训练由平台任务层托管，不在 Agent 请求进程中运行。

详细输入输出规范见：

```text
resources/tools/implementations/timeseries-classification-runner/spec.md
```
