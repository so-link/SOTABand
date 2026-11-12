"""Expose the platform Agent through the workflow host contract."""


class AgentWorkflowHost:
    def __init__(self, agent):
        self.agent = agent

    @property
    def llm(self):
        return self.agent.llm

    def resolve_dataset_paths(self, attachments):
        return self.agent._resolve_dataset_paths(attachments)

    async def execute_tool(self, tool, query, paths=None, params=None):
        return await self.agent._execute_tool(tool, query, paths, params)

    async def check_missing_params(self, tool_id, params, mode=None):
        return await self.agent._check_missing_params(tool_id, params, mode)

    def get_tool_modes(self, tool_id):
        return self.agent._get_tool_modes(tool_id)

    def resolve_mode_selection(self, answer, modes):
        return self.agent._resolve_mode_selection(answer, modes)

    def resolve_tool_selection(self, answer, candidates):
        return self.agent._resolve_tool_selection(answer, candidates)

    def get_path_params(self, tool_id, mode=None):
        return self.agent._get_path_params(tool_id, mode)

    async def extract_params(self, query, tool_id, paths=None):
        return await self.agent._extract_params(query, tool_id, paths)

    def submit_task_result(self, result, tool):
        return self.agent.submit_task_result(result, tool)

    def respond(self, ctx, content, attachments, workspace_tool_ids=None, tool_result=None):
        return self.agent.respond(ctx, content, attachments, workspace_tool_ids, tool_result)
