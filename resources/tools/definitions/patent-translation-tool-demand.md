输入：用户需求{req}，数量{n}，发表年限{year}，数据集名称{dataset}
过程：
1）调用【【Lens专利检索与注册工具】】，所采用的参数：用户需求{req}，数量{n}，发表年限{year}，数据集名称{dataset}
2）调用【获取数据集信息】获取数据集{dataset}的目录的路径{data_path}
3）调用系统统一配置的大模型（LLM），对目录{data_path}下的所有md文件的内容全文翻译为中文，替换原来的md文件的内容
输出：{data_path}

注：LLM 调用由系统统一处理（跟随全局 LLM_PROVIDER / LLM_API_KEY / LLM_MODEL 配置，使用工具模板提供的 _llm_chat 辅助函数），无需自行获取 API KEY。