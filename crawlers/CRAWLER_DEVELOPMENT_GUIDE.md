# 舆情爬虫开发指南

> 面向 RuoYi 舆情系统的爬虫开发者，从零开始写一个新爬虫的完整手册。

---

## 目录

1. [开发环境搭建](#1-开发环境搭建)
2. [配置文件说明](#2-配置文件说明)
3. [文件分类与职责](#3-文件分类与职责)
4. [数据库建表](#4-数据库建表)
5. [新建爬虫（模板用法）](#5-新建爬虫模板用法)
6. [数据保存范式](#6-数据保存范式)
7. [命令行参数](#7-命令行参数)
8. [手动测试](#8-手动测试)
9. [关键规则](#9-关键规则)
10. [案例参考](#10-案例参考)

---

## 1. 开发环境搭建

### Windows（本地开发）

```bash
# 1) 安装 Python 3.8+（推荐 3.10+）
#    下载安装包：https://www.python.org/downloads/
#    安装时勾选 "Add Python to PATH"

# 2) 进入爬虫目录
cd crawlers/

# 3) 安装依赖
pip install pymysql requests beautifulsoup4 lxml

# 4) 如需 Playwright 爬虫（如 hrw_spider），额外安装：
pip install playwright
playwright install chromium

# 5) 代理：本地运行 Clash Verge，确保系统代理端口为 7890
#    crawler_config.py 中 PROXY 默认已设为 http://127.0.0.1:7890

# 6) MySQL：本地安装或连接远程 MySQL 5.7+，执行建表脚本（见第4节）
```

**验证环境：**
```bash
python -c "import pymysql, requests, bs4; print('依赖OK')"
python -c "from playwright.sync_api import sync_playwright; print('Playwright OK')"
```

---

## 2. 配置文件说明

**`crawler_config.py`** 是唯一的配置文件，开发者**必须**修改以下三项：

```python
# 数据库连接 —— 改成你本地/远程 MySQL 地址
DB = {
    "host":     "localhost",
    "user":     "root",
    "password": "你的密码",
    "database": "ry-vue",
    "charset":  "utf8mb4",
}

# 代理地址（Windows 环境使用 Clash Verge）
PROXY = "http://127.0.0.1:7890"

# 图片存储路径（默认存到当前目录下 _images 文件夹）
IMAGE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_images")
```

**其他配置（通常无需修改）：**

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `MAX_ARTICLES` | 10 | 新闻爬虫每次最多爬取条数 |
| `MAX_PAGES` | 3 | 新闻爬虫列表页最多翻页数 |
| `MAX_PER_KEYWORD` | 2 | 社媒爬虫每个关键词最多爬取条数 |
| `REQUEST_TIMEOUT` | 30 | HTTP 请求超时（秒） |
| `REQUEST_DELAY` | (1, 3) | 请求间隔随机范围（秒） |
| `USER_AGENT` | Chrome UA | 模拟浏览器 |

> ⚠️ 所有配置集中在 `crawler_config.py` 里改，不要用环境变量传配置。

> 💡 **接入系统后**，关键词、爬取间隔等由系统配置控制，通过命令行参数传入，爬虫本身不需要额外修改。

---

## 3. 文件分类与职责

### 必须修改的文件（开发者改）

| 文件 | 说明 |
|------|------|
| `crawler_config.py` | DB、代理、图片路径（第2节） |
| `xxx_spider.py` | 你新建的爬虫文件 |

### 禁止修改的文件（框架代码）

| 文件 | 说明 |
|------|------|
| `common_db.py` | 数据库操作函数（`save_news_article`、`save_social_post` 等） |
| `proxy_config.py` | 代理配置模块 |
| `content_utils.py` | HTML 内容清洗工具 |

### 参考文件（复制为模板或学习）

| 文件 | 说明 |
|------|------|
| `template_news_spider.py` | **新闻类爬虫模板**，RSS/HTML 新闻站复制此文件 |
| `template_social_spider.py` | **社媒类爬虫模板**，社交媒体平台复制此文件 |
| `hrw_spider.py` | 新闻爬虫案例（HRW，使用 Playwright） |
| `bluesky_spider.py` | 社媒爬虫案例（Bluesky，使用 atproto） |

---

## 4. 数据库建表

`sentiment_tables.sql` 包含建库建表脚本，开发者需要在本地 MySQL 执行：

```bash
# 方式一：命令行导入
mysql -u root -p < sentiment_tables.sql

# 方式二：进入 MySQL 后用 source 命令（推荐）
mysql -u root -p
> source sentiment_tables.sql;
```

**6张表说明：**

| 表名 | 用途 | 爬虫角色 |
|------|------|----------|
| `crawl_config` | 爬取配置 | 读取（系统自动管理） |
| `crawl_log` | 爬取日志 | 通过 `--log-id` 回写状态 |
| `news_article` | 新闻文章 | **写入**（新闻类爬虫） |
| `social_post` | 社交帖子 | **写入**（社媒类爬虫） |
| `social_comment` | 社交评论 | **写入**（社媒类爬虫） |
| `social_post_image` | 帖子图片 | **写入**（社媒类爬虫） |

> 新闻爬虫只写 `news_article`；社媒爬虫写 `social_post` + `social_comment` + `social_post_image`。

---

## 5. 新建爬虫（模板用法）

### 第一步：选择模板

- **新闻类**（新闻网站、RSS）→ 复制 `template_news_spider.py`
- **社媒类**（论坛、Twitter、Reddit）→ 复制 `template_social_spider.py`

```bash
cp template_news_spider.py bbc_spider.py      # 示例：新闻类
cp template_social_spider.py reddit_spider.py  # 示例：社媒类
```

### 第二步：修改配置区

在新文件顶部的「配置区」修改站点信息：

```python
# 新闻模板示例
SITE_NAME = "BBC"                         # 站点名（写入 news_article.source）
BASE_URL = "https://www.bbc.com/news"     # 列表页 URL
KEYWORDS = ["china", "taiwan"]            # 关键词（用于过滤）
```

```python
# 社媒模板示例
SITE_NAME = "Reddit"
ALL_KEYWORDS = ["china", "taiwan"]        # 关键词（用于搜索）
```

> 💡 **接入系统后**，关键词通过 `--keyword` 参数传入，模板中的默认值仅用于本地测试。

### 第三步：实现核心函数

**新闻模板**需要实现两个函数：

| 函数 | 职责 | 返回值 |
|------|------|--------|
| `fetch_article_list(session, max_pages)` | 从列表页收集文章链接 | `[{"title": str, "url": str}, ...]` |
| `fetch_article_detail(session, url, title)` | 解析单篇文章详情 | `{"title", "url", "date", "keywords", "content", "cover_image", "source"}` 或 `None` |

**社媒模板**需要实现一个函数：

| 函数 | 职责 | 返回值 |
|------|------|--------|
| `search_posts(keyword, max_count)` | 搜索帖子并返回结构化数据 | 见下方数据结构 |

`search_posts` 返回结构：

```python
{
    "post_id": str,          # 帖子唯一 ID（必填）
    "title": str,            # 标题/摘要（前200字）
    "author": str,           # 作者
    "content": str,          # 完整内容
    "publish_time": str,     # "YYYY-MM-DD HH:MM:SS"
    "like_count": int,
    "comment_count": int,
    "original_url": str,
    "image_urls": list,      # 图片 URL 列表
    "comments": [            # 评论列表
        {"comment_id": str, "commenter": str, "comment_content": str,
         "like_count": int, "comment_time": str}
    ]
}
```

### 第四步：测试运行

```bash
python bbc_spider.py --max 3
```

---

## 6. 数据保存范式

### 返回值约定

所有爬虫的 `crawl()` 函数返回三元组：

```python
return items_found, items_new, items_updated
```

| 含义 | 说明 |
|------|------|
| `items_found` | 爬虫发现的总条数（经过过滤前） |
| `items_new` | 新插入数据库的条数 |
| `items_updated` | 更新已有记录的条数 |

### 保存函数（common_db.py 提供）

**新闻类：**
```python
save_news_article(cursor, article_data)  # → (is_new, is_updated)
```
- `article_data` 必须包含：`title`, `url`, `date`（或 `publish_date`）, `content`
- 可选：`keywords`, `cover_image`, `source`
- 基于 `url` 唯一索引去重（`ON DUPLICATE KEY UPDATE`）

**社媒类：**
```python
save_social_post(cursor, post_data)      # → (is_new, is_updated)
save_social_comment(cursor, comment_data)  # → 无返回值
save_social_post_image(cursor, image_data) # → is_new: bool
```

- `post_data` 必须包含：`uuid`（用 `uuid.uuid4()`）, `site_name`, `post_id`, `content`
- 可选：`title`, `author`, `publish_time`, `like_count`, `comment_count`, `original_url`, `image_url`, `trigger_keyword`
- 基于 `post_id` 唯一索引去重

### 典型保存流程

```python
# 新闻类
conn = pymysql.connect(**DB)
cur = conn.cursor()
try:
    is_new, is_updated = save_news_article(cur, detail)
    conn.commit()
finally:
    cur.close()
    conn.close()
```

---

## 7. 命令行参数

所有爬虫统一支持以下参数：

| 参数 | 说明 | 开发者常用 |
|------|------|-----------|
| `--max N` | 最多爬取/保存 N 条 | ✅ 手动测试用 |
| `--keyword XXX` | 只搜索指定关键词 | ✅ 手动测试用 |
| `--config-id N` | 配置表的 ID | ❌ 系统调度时自动传入 |
| `--log-id N` | 日志表的 ID | ❌ 系统调度时自动传入 |

**开发者手动测试只需关注 `--max` 和 `--keyword`：**

```bash
python my_spider.py --max 3                    # 最多爬 3 条
python my_spider.py --keyword taiwan --max 5   # 只搜 taiwan，最多 5 条
```

> 💡 **接入系统后**，系统会自动传入 `--config-id` 和 `--log-id`，爬虫自动回写状态。

---

## 8. 手动测试

开发者在本地测试爬虫：

```bash
cd crawlers/

# 确保 MySQL 建好表
mysql -u root -p ry-vue < sentiment_tables.sql

# 确保代理在跑（Clash Verge 端口 7890）

# 直接运行，--max 控制条数
python hrw_spider.py --max 3
python bluesky_spider.py --max 2
python my_new_spider.py --max 5
```

观察控制台输出，看到 `[NEW]`、`[UPDATE]` 标记说明入库成功。检查数据库确认数据正确。

> `--config-id` 和 `--log-id` 是系统调度时才用的参数，开发者本地测试完全不需要。

---

## 9. 关键规则

### 1）正文内容不能截断

数据库中 `news_article.content` 和 `social_post.content` 都是 `longtext` 类型，**必须保存完整内容**，不要做截断：

```python
# ❌ 错误：截断了内容
article["content"] = content[:5000]

# ✅ 正确：完整保存
article["content"] = content
```

### 2）所有请求必须走代理

```python
# requests
resp = session.get(url, proxies=PROXIES, timeout=30)

# Playwright
browser = p.chromium.launch(proxy=PLAYWRIGHT_PROXY, args=[
    "--disable-dev-shm-usage", "--disable-gpu",
    "--disable-extensions", "--no-sandbox"
])
```

### 3）Playwright 启动参数

```python
browser = p.chromium.launch(
    headless=True,
    proxy=PLAYWRIGHT_PROXY,
    args=["--disable-dev-shm-usage", "--disable-gpu",
          "--disable-extensions", "--no-sandbox"]
)
```

这四个 `args` 是必须的。

### 4）请求间必须加延迟

```python
time.sleep(random.uniform(1, 3))  # REQUEST_DELAY = (1, 3)
```

防止请求过快被封 IP。每个详情页请求后都应 sleep。

### 5）图片下载走代理

```python
resp = requests.get(url, proxies=PROXIES, timeout=30)
```

---

## 10. 案例参考

### 新闻爬虫参考：`hrw_spider.py`

HRW（Human Rights Watch）爬虫，展示了完整的新闻爬虫实现：

- 使用 **Playwright** 渲染 JS 页面
- 列表页 → 逐篇详情页
- `save_news_article()` 入库
- 关键词过滤、图片下载、日期提取

**适合参考的点：**
- Playwright 启动和页面导航
- 列表页文章链接提取逻辑
- 详情页正文提取（使用 `content_utils.clean_content_html`）
- 新闻类爬虫的整体流程

### 社媒爬虫参考：`bluesky_spider.py`

Bluesky 社交平台爬虫，展示了完整的社媒爬虫实现：

- 使用 **atproto** 库对接 Bluesky API
- 关键词搜索 → 帖子 + 评论 + 图片
- `save_social_post()` + `save_social_comment()` 入库
- 递归解析评论树

**适合参考的点：**
- 社媒 API 对接方式
- 帖子 + 评论的一并保存
- 图片下载和 `social_post_image` 表写入
- 社媒类爬虫的整体流程

---

## 快速开始清单

开发者新增一个爬虫，按此顺序操作：

- [ ] 确认 Python 环境和依赖已安装
- [ ] 修改 `crawler_config.py`（DB/PROXY/IMAGE_DIR）
- [ ] 执行 `sentiment_tables.sql` 建表
- [ ] 复制模板文件（`template_news_spider.py` 或 `template_social_spider.py`）
- [ ] 修改配置区（SITE_NAME、BASE_URL 等）
- [ ] 实现核心函数（`fetch_article_list` + `fetch_article_detail` 或 `search_posts`）
- [ ] 运行 `python xxx_spider.py --max 3` 测试
- [ ] 检查数据库确认数据正确
