"""
调度与算力管理子系统 (Scheduling & Compute Management)
=======================================================

将任务分发至异构算力集群（CPU/GPU/NPU），动态分配计算资源，
均衡集群负载。

组件：
- heterogeneous : 异构调度器 — 将任务分发至 CPU/GPU/NPU 异构算力集群
- allocator     : 资源分配器 — 动态分配 CPU核心数/GPU显存/内存大小
- balancer      : 负载均衡器 — 均衡集群各节点负载
"""
