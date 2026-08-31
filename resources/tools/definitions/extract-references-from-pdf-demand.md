输入：pdf论文文件路径 {path}，
过程：
（1）调用系统统一配置的大模型（LLM）解析论文{path}中的参考文献，输出参考文献列表（LLM 调用由系统统一处理，使用工具模板提供的 _llm_chat 辅助函数，跟随全局 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL 配置，无需自行获取 API KEY）

输出：表格形式的参考文献列表，
