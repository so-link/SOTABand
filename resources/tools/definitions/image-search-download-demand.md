输入：用户需求{req}, 图片数目{n}，数据集名称{dataset}
过程：
1）新建项目目录下的子目录: ’./data/download/{xxxx}/‘     ,{xxxx}  为当前时间戳，
2) 根据用户需求{req},使用bing搜索引擎搜索相关图片，把前{n}张图片下载到本地到子目录中’./data/download/{xxxx}/‘ 
3)通过【数据集注册API】API将目录’./data/download/{xxxx}/‘ 注册为图片数据集{dataset}。

输出：搜索到的第一张图片