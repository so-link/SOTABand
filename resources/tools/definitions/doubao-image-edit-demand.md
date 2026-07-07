编辑图片。根据用户的输入的要求，调用doubao大模型编辑图片，其中doubao大模型的apikey以及模型名通过调用【LLM配置获取API】API获得，所有生成的图片下载到项目目录下的子目录中/data/download/xxxx/，xxxx为当前时间戳。

输入：1）输入的图片。 2）编辑的要求 
输出：编辑好的图片
注：调用doubao进行图片编辑的代码示例： 
import base64
from volcengine.ark import ArkService

# ===================== 配置修改这里 =====================
AK = "你的火山引擎API Key"
ENDPOINT_ID = "ep-xxxxxx"  # doubao-seedream-5-0-260128 接入点
INPUT_FILE = "input.jpg"
OUT_FILE = "output_2048.jpg"
PROMPT = "高清优化，细节拉满，写实画质"
NEG_PROMPT = "模糊，水印，文字，畸形"
# ======================================================

# 本地图片转base64
def file_to_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")

# 初始化方舟SDK客户端
ark_service = ArkService()
ark_service.set_ak(AK)

# 构造请求
resp = ark_service.images_edit(
    model=ENDPOINT_ID,
    image=file_to_b64(INPUT_FILE),
    prompt=PROMPT,
    negative_prompt=NEG_PROMPT,
    strength=0.7,
    size="2048x2048",
    n=1
)

# 处理结果
if resp.data:
    img_bytes = base64.b64decode(resp.data[0].b64_image)
    with open(OUT_FILE, "wb") as f:
        f.write(img_bytes)
    print(f"图片生成成功，保存至 {OUT_FILE}")
else:
    print("生成失败：", resp.error)

需要用到的API： 【LLM配置获取API】