# RuoYi 舆情系统爬虫代码模式分析

## 文件清单

| 文件 | 用途 | 行数 |
|------|------|------|
| `common_db.py` | 共享数据库连接、保存函数、日志更新 | 214 |
| `proxy_config.py` | 统一代理配置（链式代理） | 119 |
| `content_utils.py` | 内容提取与清洗工具 | 185 |
| `hrw_spider.py` | HRW (Human Rights Watch) 新闻爬虫 (Playwright) | 203 |
| `cnn_spider.py` | CNN 新闻爬虫 (requests + RSS) | 205 |
| `bluesky_spider.py` | Bluesky 社交媒体爬虫 (atproto API) | 245 |
| `treasury_spider.py` | 美国财政部新闻爬虫 (requests + Playwright fallback) | 368 |

---

## 一、common_db.py — 共享数据库模块

### 核心功能
所有爬虫共用的数据库连接、保存函数和日志更新函数。

### 数据库配置
```python
DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "200422",
    "database": "ry-vue",
    "charset": "utf8mb4",
}
```

### 函数签名

#### `get_db()`
- 无参数，返回 `pymysql.connect(**DB_CONFIG)`

#### `clean(text)`
- 参数：`text` (str)
- 返回：合并空白后的字符串

#### `save_news_article(cursor, article)`
- 参数1：`cursor` — 已有的数据库 cursor
- 参数2：`article` — dict，字段：
  - `title` (必填)
  - `url` (必填，唯一索引)
  - `publish_date` 或 `date` (可选)
  - `keywords` (可选)
  - `cover_image` (可选)
  - `content` (可选)
  - `source` (可选)
- 返回：`(is_new: bool, is_updated: bool)`
- SQL：`INSERT ... ON DUPLICATE KEY UPDATE`，基于 `url` 唯一索引去重
- 更新字段：title, publish_date, keywords, cover_image, content（不更新 url, source）

#### `save_social_post(cursor, post)`
- 参数1：`cursor`
- 参数2：`post` — dict，字段：
  - `uuid` (必填)
  - `site_name` (必填)
  - `trigger_keyword` (可选)
  - `source_board` (可选)
  - `post_id` (必填，唯一索引)
  - `title` (可选)
  - `author` (可选)
  - `publish_time` (可选)
  - `like_count` (可选，默认0)
  - `comment_count` (可选，默认0)
  - `content` (可选)
  - `original_url` (可选)
  - `image_url` (可选)
- 返回：`(is_new: bool, is_updated: bool)`
- SQL：基于 `post_id` 唯一索引去重
- 更新字段：like_count, comment_count, title, content, image_url

#### `save_social_comment(cursor, comment)`
- 参数1：`cursor`
- 参数2：`comment` — dict，字段：
  - `post_id` (必填)
  - `title` (可选)
  - `comment_id` (必填，唯一索引)
  - `commenter` (可选)
  - `comment_content` (可选)
  - `like_count` (可选，默认0)
  - `comment_time` (可选)
- 返回：`(is_new: bool, is_updated: bool)`
- SQL：基于 `comment_id` 唯一索引去重
- 更新字段：like_count, comment_content

#### `update_crawl_log_start(log_id)`
- 参数：`log_id` (int 或 None)
- 操作：UPDATE crawl_log SET start_time=NOW() WHERE id=%s
- 使用独立连接，自动关闭

#### `update_crawl_log(log_id, items_found, items_new, items_updated)`
- 参数：
  - `log_id` (int 或 None)
  - `items_found` (int) — 爬取发现的总条目数
  - `items_new` (int) — 新增条目数（rowcount=1 累计）
  - `items_updated` (int) — 更新条目数（rowcount=2 累计）
- 操作：UPDATE crawl_log SET status='success', end_time=NOW(), items_found=%s, items_new=%s, items_updated=%s

#### `update_crawl_log_error(log_id, error_msg)`
- 参数：`log_id`, `error_msg` (截断到2000字符)
- 操作：UPDATE crawl_log SET status='failed', end_time=NOW(), error_msg=%s

#### `update_config_last_crawl(config_id)`
- 参数：`config_id`
- 操作：UPDATE crawl_config SET last_crawl_time=NOW()

### rowcount 判断规则
- `rowcount=1` → INSERT 新增
- `rowcount=2` → UPDATE 更新（ON DUPLICATE KEY UPDATE 命中）
- `rowcount=0` → 值未变化（既非新增也更新）

---

## 二、proxy_config.py — 代理配置

### 代理架构
```
本地 → HTTP代理(192.168.0.14:7890) → SOCKS5代理(zkapeteraaa@203.166.136.112:443) → 目标网站
```
通过 gost 实现链式代理中继，监听 127.0.0.1:1080

### 导出变量
| 变量 | 类型 | 说明 |
|------|------|------|
| `LAYER1_PROXY` | str | `http://192.168.0.14:7890/` |
| `LAYER2_PROXY` | str | `socks5://zkapeteraaa:zkapeteraaa@203.166.136.112:443` |
| `GOST_LOCAL_PORT` | int | `1080` |
| `CHAIN_PROXY` | str | `socks5://127.0.0.1:1080` |
| `PROXIES` | dict | `{"http": CHAIN_PROXY, "https": CHAIN_PROXY}` — requests/urllib 使用 |
| `SINGLE_PROXY` | str | 仅第一层（备用） |
| `SINGLE_PROXIES` | dict | 仅第一层的 proxies dict |
| `PLAYWRIGHT_PROXY` | dict | `{"server": CHAIN_PROXY}` — Playwright 使用 |

### 函数

#### `ensure_gost_running()`
- 检查 1080 端口是否已有服务
- 查找 gost 可执行文件（/usr/local/bin/gost 或 /usr/bin/gost）
- 自动启动 gost 后台进程
- 返回 True/False

#### `get_proxies(use_chain=True)`
- 返回 `PROXIES` 或 `SINGLE_PROXIES`

#### `get_playwright_proxy(use_chain=True)`
- 返回 `PLAYWRIGHT_PROXY` 或 `{"server": SINGLE_PROXY}`

### 使用方式
- **requests**: `requests.get(url, proxies=PROXIES, ...)`
- **Playwright**: `p.chromium.launch(proxy=get_playwright_proxy(), ...)`
- **Bluesky atproto**: `os.environ["HTTP_PROXY"] = PROXIES["http"]` + `os.environ["HTTPS_PROXY"] = PROXIES["https"]`

---

## 三、content_utils.py — 内容工具

### 功能
提供 HTML 内容清洗和 Playwright 页面内容提取。

### 常量
- `BOILERPLATE_RE` — 正则匹配无用文本（版权、分享按钮、QR码等）
- `REMOVE_TAGS` — 需要删除的 HTML 标签
- `FOOTER_SELECTORS` — 需要删除的页脚 CSS 选择器

### 函数

#### `clean_content_html(html)`
- 参数：`html` (str) — 原始HTML
- 返回：清洗后的HTML字符串
- 逻辑：
  1. 移除 script/style/nav/aside/form/iframe
  2. 移除含 footer/share/social/newsletter 类名的元素
  3. 移除 `<figure>` 标签
  4. 提取所有 `<p>` 标签文本，跳过boilerplate和过短内容
  5. 如果无 `<p>` 标签，fallback到文本分割

#### `scroll_to_bottom(page)`
- Playwright页面滚动到底部，触发lazy load
- 每次滚动800px，间隔400ms

#### `extract_content_playwright(page, selector="article", base_url="")`
- 从Playwright页面提取清洗后的内容
- 先滚动到底部
- 依次尝试 `div.entry-content` 和传入的 selector
- 通过 `inner_html()` 获取HTML再清洗

#### `remove_boilerplate_text(text)`
- 纯文本清洗，返回带 `<p>` 标签的HTML
- 长文本按句子边界分段，每段不超过300字符

---

## 四、hrw_spider.py — HRW 新闻爬虫

### 类型
新闻爬虫（News Spider），使用 Playwright 渲染页面

### 导入
```python
import os, sys, re, time, random, argparse
from playwright.sync_api import sync_playwright
import pymysql
from content_utils import extract_content_playwright, remove_boilerplate_text
from common_db import save_news_article, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
import hashlib, requests as req_lib
from proxy_config import PROXIES, get_playwright_proxy
```

### 常量
```python
IMAGE_DIR = "/home/ruoyi/uploadPath/sentiment/images"
SITE_NAME = "HRW"
BASE_URL = "https://www.hrw.org"
DEFAULT_MAX_PAGES = 2
DEFAULT_MAX_ARTICLES = 2
MAIN_KEYWORDS = ["china", "taiwan"]
SUB_KEYWORDS = ["trade", "technology", "military", "sanctions", "indo-pacific", ...]
```

### CLI参数（argparse）
```python
parser.add_argument("--config-id", type=int, default=None)
parser.add_argument("--keyword", type=str, default=None)
parser.add_argument("--max", type=int, default=None)
parser.add_argument("--log-id", type=int, default=None)
```

### 图片下载
```python
def download_image(url, article_id, idx=0):
```
- 文件名：`md5(str(article_id))[:16]_{idx}.jpg`
- 保存到：`/home/ruoyi/uploadPath/sentiment/images/`
- 使用 `requests.get(url, proxies=PROXIES, timeout=30)` 下载
- 数据库存储：`"sentiment/images/" + cover_image`（相对路径）

### 关键词匹配
- `contains_keywords(text)` — 检查是否包含任何 MAIN_KEYWORDS
- `extract_keywords(text)` — 提取所有匹配的关键词（MAIN + SUB），逗号分隔
- 使用 `re.search(rf"\b{re.escape(k)}\b", t)` 进行全词匹配

### Cover Image提取
```python
def extract_cover_image(page):
```
- 优先 `meta[property='og:image']`
- 备选 `meta[name='twitter:image']`

### Playwright浏览器启动
```python
browser = p.chromium.launch(
    headless=True,
    proxy=get_playwright_proxy(),
    args=["--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions", "--no-sandbox"]
)
page = browser.new_page()
```

### 爬取流程 (`crawl()`)
1. 打开列表页，收集所有文章链接
2. 逐个访问详情页
3. 提取 h1 标题、日期、封面图、正文内容
4. 关键词过滤（必须包含 MAIN_KEYWORDS）
5. 保存到数据库
6. 返回 `(items_found, items_new, items_updated)`

### main() 流程
```python
def main():
    args = parse_args()
    max_articles = args.max if args.max is not None else DEFAULT_MAX_ARTICLES
    update_crawl_log_start(args.log_id)
    try:
        items_found, items_new, items_updated = crawl(DEFAULT_MAX_PAGES, max_articles)
        update_crawl_log(args.log_id, items_found, items_new, items_updated)
        update_config_last_crawl(args.config_id)
    except Exception as e:
        update_crawl_log_error(args.log_id, str(e))
        raise
```

### 错误处理模式
- 列表页失败：`page_failures += 1`，继续下一页
- 所有列表页失败：`raise Exception("所有列表页访问失败...")`
- 详情页异常：`print(f"详情错误：{e}")`，跳过继续
- 每个详情页后 `time.sleep(random.uniform(1, 2))`

---

## 五、cnn_spider.py — CNN 新闻爬虫

### 类型
新闻爬虫（News Spider），使用 requests + RSS

### 导入
```python
import os, sys, re, time, random, argparse, requests
from bs4 import BeautifulSoup
import pymysql
from content_utils import clean_content_html
from proxy_config import PROXIES
from common_db import save_news_article, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
```

### 特点
- **不使用 Playwright**，纯 requests
- 通过 RSS 订阅源获取文章列表
- 内容提取：从嵌入的 script 中提取 `articleBody` JSON 字段

### 常量
```python
HEADERS = {"User-Agent": "Mozilla/5.0 ...", "Referer": "https://edition.cnn.com/"}
DEFAULT_MAX_ARTICLES = 3
CNN_RSS_FEEDS = ["http://rss.cnn.com/rss/edition.rss", "http://rss.cnn.com/rss/edition_world.rss"]
```

### CLI参数
```python
parser.add_argument("--config-id", type=int, default=None)
parser.add_argument("--keyword", type=str, default=None)
parser.add_argument("--max", type=int, default=None)
parser.add_argument("--log-id", type=int, default=None)
parser.add_argument("max_legacy", nargs="?", type=int, default=None)  # 向后兼容
```

### 内容提取策略
```python
def get_article_content(url):
```
1. 检查URL是否为无效类型（/collections/, /video/, /interactive/, live-news）
2. 3次重试，使用 requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=25, verify=False)
3. 方法1：从HTML中查找 `articleBody` 字段（CNN嵌入的JSON数据），提取全文
4. 方法2：fallback到 meta description
5. 返回清洗后的HTML，截断到8000字符

### 关键词匹配
```python
def extract_keywords(text):
```
- 简单的 `re.search(rf"\b{k}\b", t)` 匹配
- 关键词列表：china, taiwan, trade, technology, military, economy, politics, health, climate
- 支持 `--keyword` 参数过滤标题

### 代理使用
- 使用 `requests.get(url, proxies=PROXIES, ...)` + `verify=False`

### 错误处理
- RSS抓取失败：打印错误，继续下一个feed
- 正文抓取失败：3次重试，间隔2秒
- 详情页异常：`continue` 到下一项

### DB_CONFIG
- 重复定义（与 common_db.py 相同），但实际未使用 `from common_db import get_db`（CNN爬虫自己定义了）

---

## 六、bluesky_spider.py — Bluesky 社交媒体爬虫

### 类型
社交媒体爬虫（Social Spider），使用 atproto API

### 导入
```python
import os, sys, uuid, hashlib, argparse
from datetime import datetime
from proxy_config import PROXIES
os.environ["HTTP_PROXY"] = PROXIES["http"]
os.environ["HTTPS_PROXY"] = PROXIES["https"]
from atproto import Client
import pymysql, time, requests
from common_db import save_social_post, save_social_comment, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
```

### 特点
- **不使用 Playwright 或 requests 做页面爬取**
- 使用 atproto Client API 直接与 Bluesky 交互
- 代理通过环境变量传递（`HTTP_PROXY` / `HTTPS_PROXY`）

### 常量
```python
BSKY_USERNAME = os.environ.get("BSKY_USERNAME", "zao-17.bsky.social")
BSKY_PASSWORD = os.environ.get("BSKY_PASSWORD", "3ORI6-VJAFI")
ALL_KEYWORDS = ["china", "taiwan"]
DEFAULT_MAX_PER_KW = 2
DEPTH = 3
IMAGE_DIR = "/home/ruoyi/uploadPath/sentiment/images"
```

### CLI参数
```python
parser.add_argument("--config-id", type=int, default=None)
parser.add_argument("--keyword", type=str, default=None)
parser.add_argument("--max", type=int, default=None)
parser.add_argument("--log-id", type=int, default=None)
parser.add_argument("max_legacy", nargs="?", type=int, default=None)
```

### post_id 格式
- Bluesky 的 `post.uri` 是 AT Protocol URI，格式如：
  `at://did:plc:xxxxx/app.bsky.feed.post/xxxxx`
- 该 URI **包含斜杠 `/`**，直接存入数据库的 `post_id` 字段
- 构造原始URL时取最后一段：`cid.split('/')[-1]`

### 图片处理
```python
def get_image_urls(record, did=None):
```
- 从帖子的 `embed.images` 或 `embed.external.thumb` 提取图片BlobRef
- 构造下载URL：`https://bsky.social/xrpc/com.atproto.sync.getBlob?did={did}&cid={cid}`

```python
def download_image(url, post_id, idx):
```
- 文件名：`md5(post_id)[:16]_{idx}.jpg`
- 保存到：`/home/ruoyi/uploadPath/sentiment/images/`

```python
def save_images(cur, conn, post_id, image_urls):
```
- 保存到 `social_post_image` 表
- 字段：`post_id`, `image_url` (原始URL), `local_path` (如 `sentiment/images/xxx.jpg`), `idx`
- **先检查是否已有图片记录**（去重）

### 爬取流程
1. 登录 Bluesky
2. 逐关键词搜索帖子（`client.app.bsky.feed.search_posts`）
3. 按媒体内容排序（有图片的优先）
4. 对每个帖子：
   - 检查是否已存在（`SELECT 1 FROM social_post WHERE post_id=%s`）
   - 已存在：只更新互动数据（点赞/评论数）+ 更新评论
   - 新帖子：完整保存（帖子 + 图片 + 评论）
5. 递归解析评论线程（`parse_thread`，最大深度3层）

### DB_CONFIG
- 重复定义（与 common_db.py 相同）

---

## 七、treasury_spider.py — 美国财政部新闻爬虫

### 类型
新闻爬虫（News Spider），使用 requests + BeautifulSoup + Playwright fallback

### 导入
```python
import sys, re, time, random, argparse, warnings
from content_utils import clean_content_html
import requests
from bs4 import BeautifulSoup
import pymysql
from common_db import save_news_article, update_crawl_log, update_crawl_log_error, update_config_last_crawl, update_crawl_log_start
from proxy_config import PROXIES, get_playwright_proxy
```

### 特点
- **混合模式**：主要用 requests + BeautifulSoup，content提取时 fallback 到 Playwright
- 内容提取策略最复杂（3种方法递进）

### 常量
```python
HEADERS = {"User-Agent": "Mozilla/5.0 ..."}
DEFAULT_MAX_PAGES = 3
DEFAULT_MAX_ARTICLES = 2
BASE_URL = "https://home.treasury.gov/news/press-releases"
MAIN_KEYWORDS = ["china", "taiwan"]
SUB_KEYWORDS = ["trade", "technology", "military", "sanctions", "tariff", "investment", "financial"]
```

### CLI参数
```python
parser.add_argument("--config-id", type=int, default=None)
parser.add_argument("--keyword", type=str, default=None)
parser.add_argument("--max", type=int, default=None)
parser.add_argument("--log-id", type=int, default=None)
parser.add_argument("max_pages_legacy", nargs="?", type=int, default=None)  # 向后兼容
parser.add_argument("max_articles_legacy", nargs="?", type=int, default=None)  # 向后兼容
```

### 内容提取策略（3层递进）
```python
def extract_og_description(soup, url=None):
```
1. **方法1**：从 main/article/section 标签提取文本，过滤导航内容
2. **方法2**：Playwright 渲染页面后提取 body.innerText，找 WASHINGTON 开头的段落
3. **方法3**：meta description fallback

### 日期提取
```python
def extract_article_date(soup):
```
- 查找 `<time class='datetime'>` 元素
- 排除 header/nav/footer/banner 区域的 `<time>`
- 跳过 America 250th Anniversary 横幅日期（July 4, 2026）

### Playwright使用（仅在content提取时）
```python
browser = pw.chromium.launch(
    headless=True,
    proxy=get_playwright_proxy(),
    args=["--disable-dev-shm-usage", "--disable-gpu", "--disable-extensions", "--no-sandbox"]
)
page = browser.new_page()
page.goto(url, wait_until="domcontentloaded", timeout=20000)
page.wait_for_timeout(3000)
body_text = page.evaluate("() => document.body.innerText")
browser.close()
```

### 关键词匹配
```python
def matches_main_keywords(title, content):
    combined = (title + " " + content).lower()
    return any(kw in combined for kw in MAIN_KEYWORDS)
```
- 使用简单 `in` 检查（非正则），更宽松

### 错误处理
- 列表页失败：`print(f"  [ERR] Page {pg} failed: {e}")`，继续下一页
- 详情页失败：content="" 和 date=""，跳过保存
- 无 `raise` 在详情页错误中

---

## 八、通用模式总结

### 1. CLI参数规范（argparse）
所有爬虫统一使用以下参数：
```python
parser.add_argument("--config-id", type=int, default=None)  # crawl_config ID
parser.add_argument("--keyword", type=str, default=None)    # 关键词过滤
parser.add_argument("--max", type=int, default=None)        # 最大爬取数量
parser.add_argument("--log-id", type=int, default=None)     # crawl_log ID
```
部分爬虫保留向后兼容的位置参数：
```python
parser.add_argument("max_legacy", nargs="?", type=int, default=None)
```

### 2. main() 流程（所有爬虫统一）
```python
def main():
    args = parse_args()
    # 1. 解析参数
    # 2. update_crawl_log_start(args.log_id)  — 记录开始时间
    try:
        # 3. 执行爬取
        items_found, items_new, items_updated = crawl(...)
        # 4. update_crawl_log(args.log_id, items_found, items_new, items_updated)
        # 5. update_config_last_crawl(args.config_id)
    except Exception as e:
        # 6. update_crawl_log_error(args.log_id, str(e))
        raise
```

### 3. 数据库操作模式
- 使用 `pymysql` 连接
- 所有 save 函数使用 `INSERT ... ON DUPLICATE KEY UPDATE`
- 基于 `cursor.rowcount` 判断新增/更新/无变化
- 每次保存后 `conn.commit()`
- finally 块中关闭 cursor 和连接

### 4. 代理使用方式
| 场景 | 方式 |
|------|------|
| requests | `proxies=PROXIES` (dict) |
| Playwright | `proxy=get_playwright_proxy()` (dict with "server" key) |
| atproto/Bluesky | `os.environ["HTTP_PROXY"]` + `os.environ["HTTPS_PROXY"]` |

### 5. Playwright浏览器启动（统一参数）
```python
browser = p.chromium.launch(
    headless=True,
    proxy=get_playwright_proxy(),
    args=[
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-extensions",
        "--no-sandbox"
    ]
)
page = browser.new_page()
```

### 6. 图片下载模式
```python
IMAGE_DIR = "/home/ruoyi/uploadPath/sentiment/images"

def download_image(url, post_id/article_id, idx):
    filename = f"{md5(post_id)[:16]}_{idx}.jpg"
    local_path = os.path.join(IMAGE_DIR, filename)
    resp = requests.get(url, proxies=PROXIES, timeout=30)
    with open(local_path, "wb") as f:
        f.write(resp.content)
    return filename  # 仅返回文件名
```
- 数据库存储相对路径：`"sentiment/images/" + filename`
- Bluesky额外使用 `social_post_image` 表存储多图

### 7. 关键词匹配逻辑
- **main()** 关键词：`["china", "taiwan"]`
- **sub()** 关键词：各站点自定义
- 匹配方式：
  - HRW：`re.search(rf"\b{re.escape(k)}\b", t)` — 严格全词匹配
  - CNN：简单字符串匹配
  - Treasury：`in` 检查（更宽松）
- 一般先检查是否包含 main 关键词（过滤），再提取所有匹配的关键词（存储）

### 8. Content提取策略
| 爬虫 | 主要方式 | fallback |
|------|----------|----------|
| HRW | Playwright innerHTML + clean_content_html | 无 |
| CNN | requests + articleBody JSON提取 | meta description |
| Bluesky | atproto API 直接获取文本 | 无 |
| Treasury | requests + main/article/section标签 | Playwright渲染 → meta description |

### 9. 错误处理模式
- 所有爬虫的 main() 都用 try/except 包裹
- 异常时调用 `update_crawl_log_error(log_id, str(e))`
- 然后 `raise` 重新抛出
- 循环内部的异常通常 `print` 后 `continue`

### 10. DB_CONFIG 重复定义
注意：所有爬虫文件中都重复定义了 `DB_CONFIG`，但实际只通过 `from common_db import ...` 使用共享函数。`DB_CONFIG` 的重复定义是历史遗留，不影响功能。

### 11. 返回值规范
所有 `crawl()` 函数统一返回三元组：`(items_found, items_new, items_updated)`
