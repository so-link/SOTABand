"""
调度与算力管理子系统 (Scheduling & Compute Management)
=======================================================

将任务分发至异构算力集群（CPU/GPU/NPU），动态分配计算资源，
均衡集群负载。

组件：
- sysinfo       : 主机指标采集 — CPU / 内存 / 外存（psutil）
- gpu           : GPU 采集 — GpuProvider 抽象，NVIDIA(NVML) / Mock / 无 GPU
- monitor       : 设备监控器 — 共享采样、历史环形缓冲、WebSocket 推送
- requirements  : 资源需求 — spec front-matter 声明 / 代码静态推断
- allocator     : 资源分配器 — 为工具 / Agent 匹配设备、预留记账、准入检查
- heterogeneous : 异构调度器 — 将任务分发至 CPU/GPU/NPU 异构算力集群
- balancer      : 负载均衡器 — 均衡集群各节点负载
"""
