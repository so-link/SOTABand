"""Domain workflow adapters mounted by the platform Agent."""

def create_workflow_registry(host):
    from core.workflow.registry import WorkflowRegistry
    from .hypoxemia.adapter import HypoxemiaWorkflowAdapter
    from .host import AgentWorkflowHost
    return WorkflowRegistry([HypoxemiaWorkflowAdapter(host=AgentWorkflowHost(host))])
