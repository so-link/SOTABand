工具名：编辑图片。
输入：1）数据集名{dataset}。 2）编辑的要求{req}   3）合成图片的数据集名{output_dataset} 
输出：编辑好的第1张图片，数据集名{output_dataset} 
过程：
1）调用【获取豆包API KEY】获取{api_key}
2) 调用 【获取数据集信息】获取数据集{dataset}的目录{data_path}
3）新建项目目录下的子目录: ’./data/download/{xxxx}/‘     ,{xxxx}  为当前时间戳，
4) 根据用户的输入的要求{req} ，使用{api_key}，调用doubao大模型，对目录{data_path}中的每张图片进行 编辑， 所有生成的图片下载到 子目录中’./data/download/{xxxx}/‘ 
5)通过【数据集注册API】API将目录’./data/download/{xxxx}/‘ 注册为合成图片数据集{output_dataset}。
