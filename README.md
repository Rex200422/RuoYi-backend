# RuoYi 舆情分析平台 - 后端

基于 Spring Boot 3 + MyBatis Plus 的舆情监测平台后端，集成 Python 爬虫和 AI 简报生成。

## 项目概述

| 项目 | 说明 |
|------|------|
| 框架 | RuoYi-Vue 后端框架（Spring Boot 3） |
| JDK | 17+ |
| 端口 | 8080 |
| 数据库 | MySQL 5.7+（库名 `ry-vue`，密码 `200422`） |
| AI 模型 | Ollama（`qwen3.5:4b` 预摘要 + `qwen3.6:27b` 简报生成） |
| 爬虫 | Python 3.10+（Bluesky、HRW、CNN、ASPI、Amnesty 等 10 个源） |

## 目录结构

```
RuoYi-backend/
├── ruoyi-admin/          # 主模块（启动入口、控制器）
│   └── src/main/java/com/ruoyi/web/controller/
│       └── sentiment/    # 舆情业务控制器
│           ├── AiSummaryGenerator.java   # AI 简报生成器（Java 实现）
│           ├── AiSummaryScheduler.java   # 简报定时调度器
│           ├── CrawlScheduler.java       # 爬虫定时调度器
│           ├── CrawlConfigController.java  # 爬取配置 API
│           ├── CrawlLogController.java     # 爬取日志 API
│           ├── SocialPostController.java   # 社交帖子 API
│           ├── NewsArticleController.java  # 新闻资讯 API
│           ├── SentimentImageController.java # 图片服务
│           └── SpaController.java          # SPA 路由转发
├── ruoyi-common/         # 公共模块（常量、工具类、异常处理）
├── ruoyi-framework/      # 框架模块（安全配置、拦截器、CORS）
├── ruoyi-system/         # 系统模块（菜单、用户、权限）
├── ruoyi-quartz/         # 定时任务模块
├── ruoyi-generator/      # 代码生成模块
├── crawlers/             # Python 爬虫目录
│   ├── crawler_config.py       # 统一配置（DB、代理、图片路径）
│   ├── proxy_config.py         # 代理配置模块
│   ├── common_db.py            # 数据库操作函数
│   ├── content_utils.py        # HTML 内容清洗工具
│   ├── template_news_spider.py # 新闻类爬虫模板
│   ├── template_social_spider.py # 社媒类爬虫模板
│   ├── bluesky_spider.py       # Bluesky 爬虫
│   ├── hrw_spider.py           # HRW 爬虫（Playwright）
│   ├── aspi_spider.py          # ASPI 爬虫（Playwright）
│   ├── amnesty_spider.py       # Amnesty 爬虫（Playwright）
│   ├── cnn_spider.py           # CNN 爬虫（RSS）
│   ├── treasury_spider.py      # 美国财政部爬虫
│   ├── white_house_spider.py   # 白宫爬虫（Playwright）
│   ├── japan_mofa_spider.py    # 日本外务省爬虫
│   ├── taiwan_mofa_spider.py   # 台湾外交部爬虫
│   └── CRAWLER_DEVELOPMENT_GUIDE.md  # 爬虫开发指南
└── pom.xml               # Maven 配置
```

## 核心功能模块

### 1. 舆情简报生成（AI）

`AiSummaryGenerator.java` 实现双模型策略：

| 步骤 | 模型 | 功能 |
|------|------|------|
| 预摘要 | `qwen3.5:4b`（全 GPU） | 超长内容压缩为结构化摘要 |
| 最终生成 | `qwen3.6:27b`（45/64 层 GPU） | 基于预摘要 + 原始数据生成简报 |

- Token 预算：总 32K × 90% = 28,800 tokens
- 帖子预算 50%（14,400）、新闻预算 40%（11,520）、前次简报 10%（2,880）
- 跳过逻辑：无新鲜数据时插入 `skipped` 记录保持时间线连续
- `keep_alive=-1`：两个模型常驻内存，无需交替加载

### 2. 爬虫调度

`CrawlScheduler.java` 每 60 秒检查到期的爬取任务：

- 并发控制：最多 2 个爬虫同时运行（Semaphore）
- 同一配置不会重复触发
- Python 脚本通过 `ProcessBuilder` 调用，捕获 stdout/stderr 作为错误日志
- 代理链路：本地 gost(7890) → Clash(192.168.0.14:7890) → 住宅 IP(203.166.136.112:443)

### 3. 数据库表

| 表名 | 用途 |
|------|------|
| `crawl_config` | 爬取配置（站点、关键词、间隔、启用状态） |
| `crawl_log` | 爬取日志（状态、结果数、错误信息） |
| `news_article` | 新闻文章（标题、正文、来源、关键词、封面图） |
| `social_post` | 社交帖子（作者、内容、点赞数、评论数） |
| `social_comment` | 社交评论（评论者、内容、点赞数） |
| `social_post_image` | 帖子图片（本地路径） |
| `ai_summary` | AI 生成的简报 |

## 快速开始

```bash
# 编译
mvn clean package -DskipTests

# 运行
java -jar ruoyi-admin/target/ruoyi-admin.jar

# 启动 gost 代理（爬虫需要）
nohup gost -L :7890 -F http://192.168.0.14:7890 \
  -F "socks5://user:pass@203.166.136.112:443" &
```

## 开发说明

### 新增爬虫
参见 `crawlers/CRAWLER_DEVELOPMENT_GUIDE.md`，按模板文件开发：
- 新闻类：复制 `template_news_spider.py`
- 社媒类：复制 `template_social_spider.py`

### 禁止修改的框架文件
| 文件 | 原因 |
|------|------|
| `common_db.py` | 数据库操作函数，被所有爬虫共用 |
| `proxy_config.py` | 代理配置模块 |
| `content_utils.py` | HTML 内容清洗工具 |
| `crawler_config.py` | 统一配置文件 |
