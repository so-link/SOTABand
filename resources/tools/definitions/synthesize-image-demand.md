工具名：合成图片。

输入：1）合成图片的要求{req}。 2）合成图片的数量{n} 3）合成图片数据集的名称{dataset}
输出：合成的第一个图片。
过程：
1)通过调用【获取豆包API KEY】获得{api_key}
2）新建项目目录下的子目录: ’./data/download/{xxxx}/‘     ,{xxxx}  为当前时间戳，
3) 根据用户的输入的要求{req}，调用doubao大模型生成{n}张图片(doubao的API_KEY是{api_key})
4）所有生成的图片下载到 子目录中’./data/download/{xxxx}/‘ 
5)通过【数据集注册API】API将目录’./data/download/{xxxx}/‘ 注册为合成图片数据集{dataset}。
