# 低氧血症文档入口

只使用平台功能、不运行低氧预测时，从[平台独立安装](hypoxemia-runtime-and-delivery.md#只安装平台不使用低氧预测)开始。需要低氧处理、训练和评测时，再阅读[从零安装与完整操作指南](hypoxemia-operation-guide.md)。该指南包括双环境安装、API 配置、前后端启动、逐项按钮与参数说明，以及三个可以照着操作的合成数据示例。

| 文档 | 适合谁、解决什么问题 |
| --- | --- |
| [从零安装与完整操作指南](hypoxemia-operation-guide.md) | 使用者：从下载源码到处理、训练、保存、重载评测及 Excel 下载 |
| [架构与扩展说明](hypoxemia-admin-guide.md) | 管理员和开发者：模块边界、运行组件、来源与扩展方式 |
| [运行依赖与交付说明](hypoxemia-runtime-and-delivery.md) | 只使用平台的用户与部署人员：平台独立安装、低氧双环境依赖、可选 GPU 和交付限制 |
| [验收记录](pr-readiness.md) | 审查者：实际命令、结果、证据与未验证范围 |

运行目录、前后端端口及环境隔离的配置见[运行依赖与交付说明](hypoxemia-runtime-and-delivery.md)。通用平台设计文档仍在 `docs/`。
