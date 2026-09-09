工具名：论文摘要
输入：数据集名{dataset}，摘要长度{n}
过程：
1）通过【获取数据集信息】获取数据集{dataset}的目录路径{data_path}
2）对目录{data_path}中的每个pdf文件，抽取文件的内容，调用系统统一配置的大模型（LLM）得到一段不超过{n}个字的中文摘要。所有文件的中文摘要合并成一个大的摘要。

输出：摘要文本。
注：LLM 调用由系统统一处理（跟随全局 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL 配置，使用工具模板提供的 _llm_chat 辅助函数），无需自行获取 API KEY。