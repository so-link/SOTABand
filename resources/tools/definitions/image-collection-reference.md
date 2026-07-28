#使用https://unsplash.com/ 进行图片搜索和下载的参考代码

import requests
import os
from tqdm import tqdm
import re

SAVE_DIR = "online photo"
os.makedirs(SAVE_DIR, exist_ok=True)

queries = []
with open("input.txt", "r", encoding="utf-8") as f:
    for line in f:
        if line.startswith("场景"):
            queries = re.findall(r'[\"“](.*?)[\"”]', line)
# queries = [
#     "drone skyscraper city",
#     "aerial city skyline",
#     "drone aerial buildings",
#     "drone view downtown",
# ]

headers = {
    "User-Agent": "Mozilla/5.0"
}

def download(url, idx):
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            with open(f"{SAVE_DIR}/{idx}.jpg", "wb") as f:
                f.write(r.content)
    except:
        pass


def crawl_unsplash(query, start_idx):

    url = f"https://unsplash.com/napi/search/photos?query={query}&per_page=30"

    r = requests.get(url, headers=headers)
    data = r.json()

    idx = start_idx

    for img in data["results"]:

        img_url = img["urls"]["regular"]

        download(img_url, idx)

        idx += 1

    return idx


idx = 0

for q in queries:
    print("Searching:", q)
    idx = crawl_unsplash(q, idx)

print("done")