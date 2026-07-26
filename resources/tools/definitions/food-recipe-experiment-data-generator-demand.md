输入：数据条目{n}，数据集名{output_dataset}
过程：
1）新建项目目录下的子目录: ’./data/download/{xxxx}/‘     ,{xxxx}  为当前时间戳，
2）模拟生成一个食品配方实验的表格数据，每一行是一个样本的实验数据，保存为csv文件，
3）所有生成的csv文件保存到 子目录中’./data/download/{xxxx}/‘ 
4) 通过【数据集注册API】API将目录’./data/download/{xxxx}/‘ 注册为数据集{output_dataset}。

输出： 
      合成的表格数据（输出为表格类型）