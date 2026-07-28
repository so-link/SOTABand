工具名：生成图片。

输入：1）合成图片的要求{req}。 2）合成图片的数量{n} 3）合成图片数据集的名称{dataset}
输出：合成的第一个图片。
过程：根据用户的输入的要求{req}，调用doubao大模型生成{n}张图片，其中doubao大模型的访问方法通过调用【获取豆包API KEY】获得{DOUBAO_API_KEY}，所有生成的图片下载到项目目录下的子目录中/data/download/xxxx/，xxxx为当前时间戳，通过【数据集注册API】API注册为合成图片数据集{dataset}。
 
调用doubao大模型的代码参考如下：
import os
from volcenginesdkarkruntime import Ark

client = Ark(
    base_url="https://ark.cn-beijing.volces.com/api/v3",
    api_key={DOUBAO_API_KEY},
)

imagesResponse = client.images.generate(
    # Replace with Model ID
    model="doubao-seedream-5-0-lite-260128",
    prompt="充满活力的特写编辑肖像，模特眼神犀利，头戴雕塑感帽子，色彩拼接丰富，眼部焦点锐利，景深较浅，具有Vogue杂志封面的美学风格，采用中画幅拍摄，工作室灯光效果强烈。",
    size="2K",
    output_format="png",
    response_format="url",
    watermark=False
)