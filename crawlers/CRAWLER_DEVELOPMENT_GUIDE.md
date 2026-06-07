# RuoYi舆情系统 - 爬虫开发范式

## 目录

- [一、开发流程概览](#一开发流程概览)
- [二、文件清单与分类](#二文件清单与分类)
- [三、配置文件说明](#三配置文件说明crawler_configpy)
- [四、数据库表结构](#四数据库表结构sentiment_tablessql)
- [五、共享模块说明](#五共享模块说明已写好的不用改)
- [六、模板说明](#六模板说明需要开发的部分)
- [七、案例：新闻类爬虫（HRW）](#七案例新闻类爬虫hrw)
- [八、案例：社媒类爬虫（Bluesky）](#八案例社媒类爬虫bluesky)
- [九、接入系统步骤](#九接入系统步骤)
- [十、CLI参数规范](#十cli参数规范)
- [十一、注意事项](#十一注意事项)

---

## 一、开发流程概览

```
1. 搭建环境 → pip install pymysql requests beautifulsoup4 lxml playwright
2. 改配置   → 改 crawler_config.py 中的 DB、PROXY、IMAGE_DIR
3. 建库建表 → mysql -u root -p < sentiment_tables.sql
4. 复制模板 → cp template_news_spider.py xxx_spider.py（或社媒模板）
5. 开发爬虫 → 只改模板中标注【需要开发】的部分
6. 测试     → python xxx_spider.py --max 3
7. 接入     → 在 crawl_config 表插入配置，由 Java 调度器自动运行
```

---

## 二、文件清单与分类

### 🔧 开发者需要修改的文件

| 文件 | 说明 |
|------|------|
| `crawler_config.py` | **改这里配置**：DB地址、代理地址、图片路径 |
| `xxx_spider.py` | **新开发的爬虫**：从模板复制，实现数据提取函数 |
| `sentiment_tables.sql` | 新环境需要执行建表SQL |

### 🏗️ 已写好的共享代码（不要改）

| 文件 | 功能 |
|------|------|
| `common_db.py` | 数据库操作：`get_db()`、`save_news_article()`、`save_social_post()`、`save_social_comment()`、`update_crawl_log()` 等 |
| `proxy_config.py` | 读取 `crawler_config.py` 中的 `PROXY`，导出 `PROXIES` 和 `get_playwright_proxy()` |
| `content_utils.py` | 内容清洗：`clean_content_html()`（去样板文本+保留段落HTML）、`remove_boilerplate_text()`（纯文本转HTML）、`extract_content_playwright()`（浏览器提取） |

### 📄 模板文件（复制后改）

| 文件 | 类型 | 说明 |
|------|------|------|
| `template_news_spider.py` | 新闻类 | 复制为 `xxx_spider.py`，实现 `fetch_article_list()` + `fetch_article_detail()` |
| `template_social_spider.py` | 社媒类 | 复制为 `xxx_spider.py`，实现 `search_posts()` |

### 📖 文档

| 文件 | 说明 |
|------|------|
| `CRAWLER_DEVELOPMENT_GUIDE.md` | 本文件，开发指南 |
| `CRAWLER_PATTERN_ANALYSIS.md` | 现有爬虫代码的模式分析 |

---

## 三、配置文件说明（crawler_config.py）

开发者唯一需要改的文件，部署时改这里就行：

```python
# 数据库
DB = {
    "host":     "localhost",      # ← 改成你的MySQL地址
    "user":     "root",
    "password": "200422",         # ← 改成你的密码
    "database": "ry-vue",
    "charset":  "utf8mb4",
}

# 代理（只连本地代理端口）
PROXY = "http://127.0.0.1:7890"  # ← Windows用Clash Verge端口
# PROXY = "socks5://127.0.0.1:1080"  # ← Linux用gost端口

# 图片存储路径
IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_images")  # Windows
# IMAGE_DIR = "/home/ruoyi/uploadPath/sentiment/images"  # Linux
```

其他参数（默认值通常不需要改）：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `MAX_ARTICLES` | 10 | 每次最多爬取文章数 |
| `MAX_PAGES` | 3 | 列表页最多翻几页 |
| `MAX_PER_KEYWORD` | 2 | 每个关键词最多爬几条（社媒） |
| `REQUEST_TIMEOUT` | 30 | HTTP请求超时（秒） |
| `REQUEST_DELAY` | (1, 3) | 请求间隔随机范围（秒） |
| `MAIN_KEYWORDS` | ["china", "taiwan"] | 关键词列表 |

---

## 四、数据库表结构（sentiment_tables.sql）

执行 `mysql -u root -p < sentiment_tables.sql` 建表。共6张表：

| 表名 | 用途 | 唯一索引 |
|------|------|---------|
| `crawl_config` | 爬取配置（Java调度器自动管理） | `(site_name, keyword)` |
| `crawl_log` | 爬取日志（爬虫通过`--log-id`回写） | — |
| `news_article` | 新闻文章 | `(url)` |
| `social_post` | 社交帖子 | `(post_id)` |
| `social_comment` | 社交评论 | `(post_id, comment_id)` |
| `social_post_image` | 帖子图片 | — |

### 关键字段说明

**news_article**
```sql
title       varchar(500)  -- 标题
url         varchar(500)  -- 原始URL（唯一索引，去重用）
publish_date varchar(50)  -- 发布日期（字符串，格式不限）
keywords    varchar(200)  -- 关键词（逗号分隔，如 "china,military"）
cover_image varchar(500)  -- 封面图本地路径
content     longtext      -- 文章正文（完整保存，不限长度）
source      varchar(100)  -- 来源站点名（如 "HRW"、"CNN"）
```

**social_post**
```sql
uuid              varchar(100)  -- 生成的UUID
site_name         varchar(100)  -- 站点名（如 "Bluesky"）
trigger_keyword   varchar(200)  -- 触发的关键词
post_id           varchar(200)  -- 帖子原始ID（唯一索引）
title             varchar(500)  -- 标题/内容前200字
author            varchar(100)  -- 作者
publish_time      varchar(50)   -- 发布时间
like_count        int           -- 点赞数
comment_count     int           -- 评论数
content           longtext      -- 完整正文（不限长度）
original_url      varchar(500)  -- 原始链接
image_url         varchar(500)  -- 封面图本地路径
```

**social_comment**
```sql
post_id           varchar(200)  -- 关联帖子的post_id
comment_id        varchar(200)  -- 评论原始ID（唯一索引）
commenter         varchar(100)  -- 评论者
comment_content   longtext      -- 评论内容（完整保存）
like_count        int           -- 点赞数
comment_time      varchar(50)   -- 评论时间
```

---

## 五、共享模块说明（已写好的，不用改）

### common_db.py

```python
from common_db import get_db, save_news_article, save_social_post, \
    save_social_comment, save_social_post_image, \
    update_crawl_log_start, update_crawl_log, update_crawl_log_error, \
    update_config_last_crawl
```

- `get_db()` → 返回 pymysql.Connection
- `save_news_article(cursor, article_dict)` → 返回 `(is_new, is_updated)`
- `save_social_post(cursor, post_dict)` → 返回 `(is_new, is_updated)`
- `save_social_comment(cursor, comment_dict)` → 返回 `(is_new, is_updated)`
- `save_social_post_image(cursor, image_dict)` → 返回 `is_new`
- `update_crawl_log_start(log_id)` → 标记开始时间
- `update_crawl_log(log_id, found, new, updated)` → 标记成功
- `update_crawl_log_error(log_id, msg)` → 标记失败
- `update_config_last_crawl(config_id)` → 更新最后爬取时间

### proxy_config.py

```python
from proxy_config import PROXIES, get_playwright_proxy
# PROXIES = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
# get_playwright_proxy() = {"server": "http://127.0.0.1:7890"}
```

### content_utils.py

```python
from content_utils import clean_content_html, remove_boilerplate_text, extract_content_playwright
# clean_content_html(html) → 去样板文本，保留<p>段落格式
# remove_boilerplate_text(text) → 纯文本转HTML段落
# extract_content_playwright(page, selector) → 浏览器页面提取
```

---

## 六、模板说明（需要开发的部分）

### 新闻类模板 template_news_spider.py

**需要改的地方**：

```python
# ===== 配置区（改这5行）=====
SITE_NAME = "BBC"                        # 站点名
BASE_URL = "https://www.bbc.com/news"    # 列表页URL
KEYWORDS = MAIN_KEYWORDS                  # 关键词
```

**需要实现的函数**：

```python
def fetch_article_list(session, max_pages):
    """收集文章链接。返回: [{"title": str, "url": str}, ...]"""
    # 在此实现列表页解析逻辑

def fetch_article_detail(session, url, title):
    """获取文章详情。返回 dict 或 None"""
    # 在此实现正文、日期、封面图提取逻辑
    # 必须返回: {"title", "url", "date", "keywords", "content", "cover_image", "source"}
```

**数据保存流程**（模板已内置，不需要改）：

```
fetch_article_list() → 收集文章链接列表
  ↓ 逐篇
fetch_article_detail() → 提取正文、日期、封面
  ↓ 关键词过滤
save_news_article(cursor, detail) → 入库（自动去重）
```

### 社媒类模板 template_social_spider.py

**需要改的地方**：

```python
# ===== 配置区 =====
SITE_NAME = "Reddit"    # 站点名
```

**需要实现的函数**：

```python
def search_posts(keyword, max_count):
    """搜索帖子。返回: [post_dict, ...]"""
    # 在此实现搜索/列表逻辑
```

返回格式（每条帖子）：

```python
{
    "post_id": str,          # 帖子唯一ID（必填，有唯一索引）
    "title": str,            # 标题/摘要
    "author": str,           # 作者
    "content": str,          # 完整内容（不限长度，全量保存）
    "publish_time": str,     # "YYYY-MM-DD HH:MM:SS"
    "like_count": int,
    "comment_count": int,
    "original_url": str,
    "image_urls": list,      # 图片URL列表
    "comments": [            # 评论列表
        {"comment_id": str, "commenter": str, "comment_content": str,
         "like_count": int, "comment_time": str}
    ]
}
```

**数据保存流程**（模板已内置，不需要改）：

```
search_posts() → 搜索帖子列表
  ↓ 逐条
download_image() → 下载封面图到本地
save_social_post(cursor, post_data) → 入库帖子（自动去重）
save_social_comment(cursor, comment_data) → 入库评论
```

---

## 七、案例：新闻类爬虫（HRW）

以 HRW（Human Rights Watch）为例，展示一个完整的新闻爬虫是如何工作的。

**文件**：`hrw_spider.py`（已实现，可参考）

### 核心逻辑

```python
# 1. 访问列表页，收集文章链接
url = "https://www.hrw.org/news?country%5B0%5D=9545"
page.goto(url)
articles = page.locator("article")  # 每个<article>标签包含一篇文章
for article in articles:
    link = article.locator("a").first.get_attribute("href")
    title = article.locator("a").first.inner_text()
    news_list.append({"title": title, "url": href})

# 2. 逐篇访问详情页，提取正文
page.goto(news["url"])
h1 = page.locator("h1").first.inner_text()          # 标题
date = page.locator("time").get_attribute("datetime") # 日期
cover = page.locator("meta[property='og:image']").get_attribute("content")  # 封面
content = article_el.first.inner_text()               # 正文

# 3. 关键词过滤（标题+正文必须包含至少一个主关键词）
if not any(kw in content.lower() for kw in ["china", "taiwan"]):
    continue

# 4. 下载封面图并入库
cover_file = download_image(cover_url, news["url"])
article_data = {"title": title, "url": url, "date": date,
                "content": content, "keywords": keywords,
                "cover_image": cover_file, "source": "HRW"}
is_new, is_updated = save_news_article(cursor, article_data)
```

### 关键技术点

- **Playwright浏览器**：HRW页面需要JS渲染，用Playwright
- **内容提取**：直接取 `<article>` 标签的 `inner_text()`
- **封面图**：从 `<meta property="og:image">` 获取
- **代理**：`proxy=get_playwright_proxy()`

---

## 八、案例：社媒类爬虫（Bluesky）

以 Bluesky 为例，展示一个完整的社媒爬虫是如何工作的。

**文件**：`bluesky_spider.py`（已实现，可参考）

### 核心逻辑

```python
# 1. 搜索帖子
from atproto import Client
client = Client()
client.login(BSKY_USERNAME, BSKY_PASSWORD)
result = client.app.bsky.feed.search_posts({"q": keyword, "limit": 20, "sort": "latest"})

# 2. 遍历帖子，获取完整内容+评论
for post in result.posts:
    uri = post.uri
    # 获取帖子线程（帖子+回复）
    thread = client.app.bsky.feed.get_post_thread({"uri": uri, "depth": 3})

    # 主帖信息
    post_data = {
        "post_id": uri,           # AT Protocol URI 作为唯一ID
        "title": post.record.text[:100],
        "author": post.author.handle,
        "content": post.record.text,  # 完整文本
        "publish_time": "2026-01-01 12:00:00",
        "like_count": post.like_count,
        "comment_count": post.reply_count,
        "original_url": f"https://bsky.app/profile/{author}/post/{id}",
        "image_urls": [...],      # 提取图片URL
    }

    # 3. 保存帖子
    is_new, is_updated = save_social_post(cursor, post_data)

    # 4. 保存评论（从thread.replies中提取）
    for reply in thread.replies:
        comment_data = {
            "post_id": uri,
            "comment_id": reply.post.uri,
            "commenter": reply.post.author.handle,
            "comment_content": reply.post.record.text,
            "like_count": reply.post.like_count,
        }
        save_social_comment(cursor, comment_data)
```

### 关键技术点

- **atproto库**：Bluesky API 客户端，需要先 `login()`
- **代理**：通过环境变量设置 `os.environ["HTTPS_PROXY"] = PROXIES["https"]`
- **图片**：从 `record.embed.images[].image.ref.link` 提取 CID，拼接下载URL
- **评论嵌套**：帖子回复是树形结构，用递归 `parse_thread()` 遍历

---

## 九、接入系统步骤

开发完成后，需要在数据库注册配置，Java调度器会自动调用你的爬虫。

```sql
-- 1. 在 crawl_config 表插入配置
INSERT INTO crawl_config (site_name, keyword, interval_minutes, max_results, enabled)
VALUES ('YourSite', 'china,taiwan', 120, 10, 1);
-- site_name: 站点名（必须与脚本中 SITE_NAME 一致）
-- keyword: 搜索关键词
-- interval_minutes: 爬取间隔（分钟）
-- max_results: 每次最多爬取条数
-- enabled: 是否启用

-- 2. Java调度器会自动：
--    - 每60秒检查到期的配置
--    - 创建 crawl_log 记录
--    - 调用: python3 your_spider.py --config-id N --keyword "xxx" --max 10 --log-id M
--    - 爬虫完成后 crawl_log 自动更新状态
```

---

## 十、CLI参数规范

所有爬虫必须支持以下4个参数（模板已内置，不需要改）：

```bash
python3 your_spider.py --config-id 1 --keyword "china" --max 10 --log-id 1001
```

| 参数 | 来源 | 用途 |
|------|------|------|
| `--config-id` | crawl_config.id | 成功后更新 last_crawl_time |
| `--keyword` | crawl_config.keyword | 传给爬虫的搜索关键词 |
| `--max` | crawl_config.max_results | 最多爬取条数 |
| `--log-id` | crawl_log.id | 脚本负责回写 status/items 等 |

---

## 十一、注意事项

1. **内容完整保存，不要截断**：`content` 字段是 `longtext`（不限长度），所有正文完整入库
2. **所有请求走代理**：`requests.get(url, proxies=PROXIES)`，Playwright 用 `proxy=get_playwright_proxy()`
3. **Playwright启动参数**：`args=["--disable-dev-shm-usage", "--disable-gpu", "--no-sandbox"]`
4. **图片下载失败不中断**：返回空字符串即可
5. **请求间隔**：`time.sleep(random.uniform(1, 3))`，避免被封
6. **DB连接**：Windows开发需要能连通MySQL（本地或远程）
7. **返回值约定**：`crawl()` 函数必须返回 `(items_found, items_new, items_updated)` 三元组
