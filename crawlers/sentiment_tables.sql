-- ============================================================
-- RuoYi 舆情爬虫 - 建库建表脚本
-- 用途：开发者本地初始化MySQL环境
-- 使用方法：
--   mysql -u root -p
--   然后执行以下命令：
--   source sentiment_tables.sql
-- ============================================================

-- 1. 建库
CREATE DATABASE IF NOT EXISTS `ry-vue` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;

-- 2. 选择库
USE `ry-vue`;

-- 3. 建表
-- 爬取配置表（系统调度器自动管理，爬虫不直接操作）
CREATE TABLE IF NOT EXISTS `crawl_config` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `site_name` varchar(100) NOT NULL COMMENT '站点名',
  `keyword` varchar(200) NOT NULL COMMENT '搜索关键词',
  `interval_minutes` int DEFAULT '60' COMMENT '爬取间隔(分钟)',
  `max_results` int DEFAULT '2' COMMENT '每次最多爬取条数',
  `last_crawl_time` datetime DEFAULT NULL COMMENT '上次爬取时间',
  `enabled` tinyint(1) DEFAULT '1' COMMENT '是否启用',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP,
  `update_time` datetime DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_site_keyword` (`site_name`,`keyword`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='爬取配置';

-- 爬取日志表（爬虫通过 --log-id 参数回写状态）
CREATE TABLE IF NOT EXISTS `crawl_log` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `site_name` varchar(100) NOT NULL COMMENT '站点名',
  `keyword` varchar(200) DEFAULT NULL COMMENT '搜索关键词',
  `config_id` bigint DEFAULT NULL,
  `status` varchar(20) DEFAULT 'running' COMMENT '状态(running/success/failed)',
  `start_time` datetime DEFAULT NULL COMMENT '开始时间',
  `end_time` datetime DEFAULT NULL COMMENT '结束时间',
  `items_found` int DEFAULT '0' COMMENT '发现条数',
  `items_saved` int DEFAULT '0' COMMENT '入库条数',
  `items_new` int DEFAULT '0',
  `items_updated` int DEFAULT '0',
  `error_msg` text COMMENT '错误信息',
  `create_time` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_site_time` (`site_name`,`start_time` DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='爬取日志';

-- 新闻文章表（爬虫写入）
CREATE TABLE IF NOT EXISTS `news_article` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `title` varchar(500) NOT NULL COMMENT '标题',
  `url` varchar(500) NOT NULL COMMENT '链接',
  `publish_date` varchar(50) DEFAULT NULL COMMENT '日期',
  `keywords` varchar(200) DEFAULT NULL COMMENT '关键词',
  `cover_image` varchar(500) DEFAULT NULL,
  `content` longtext COMMENT '内容',
  `pre_summary` text,
  `source` varchar(100) DEFAULT NULL COMMENT '来源',
  `crawl_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '爬取时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_url` (`url`),
  KEY `idx_source` (`source`),
  KEY `idx_date` (`publish_date`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='报刊杂志文章';

-- 社交媒体帖子表（爬虫写入）
CREATE TABLE IF NOT EXISTS `social_post` (
  `uuid` varchar(100) NOT NULL COMMENT '帖子唯一标识',
  `site_name` varchar(100) NOT NULL COMMENT '网站名',
  `trigger_keyword` varchar(200) DEFAULT NULL COMMENT '触发关键词',
  `source_board` varchar(100) DEFAULT NULL COMMENT '来自板块',
  `post_id` varchar(200) NOT NULL COMMENT '帖子ID',
  `title` varchar(500) DEFAULT NULL COMMENT '标题',
  `author` varchar(100) DEFAULT NULL COMMENT '作者',
  `publish_time` varchar(50) DEFAULT NULL COMMENT '发布时间',
  `like_count` int DEFAULT '0' COMMENT '点赞数',
  `comment_count` int DEFAULT '0' COMMENT '评论总数',
  `content` longtext COMMENT '帖子正文',
  `pre_summary` text,
  `original_url` varchar(500) DEFAULT NULL COMMENT '原帖链接',
  `image_url` varchar(500) DEFAULT NULL COMMENT '图片路径',
  `crawl_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '爬取时间',
  PRIMARY KEY (`uuid`),
  UNIQUE KEY `uk_post_id` (`post_id`),
  KEY `idx_site` (`site_name`),
  KEY `idx_keyword` (`trigger_keyword`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='社交媒体帖子';

-- 社交媒体评论表（爬虫写入）
CREATE TABLE IF NOT EXISTS `social_comment` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `post_id` varchar(200) NOT NULL COMMENT '帖子ID',
  `title` varchar(500) DEFAULT NULL COMMENT '标题',
  `comment_id` varchar(200) NOT NULL COMMENT '评论ID',
  `commenter` varchar(100) DEFAULT NULL COMMENT '评论人',
  `comment_content` longtext COMMENT '评论内容',
  `like_count` int DEFAULT '0' COMMENT '点赞数',
  `comment_time` varchar(50) DEFAULT NULL COMMENT '评论时间',
  `crawl_time` datetime DEFAULT CURRENT_TIMESTAMP COMMENT '爬取时间',
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_post_comment` (`post_id`,`comment_id`),
  KEY `idx_post_id` (`post_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='社交媒体评论';

-- 帖子图片表（爬虫写入）
CREATE TABLE IF NOT EXISTS `social_post_image` (
  `id` bigint NOT NULL AUTO_INCREMENT,
  `post_id` varchar(200) NOT NULL COMMENT '帖子post_id',
  `image_url` varchar(500) DEFAULT NULL COMMENT '原始图片URL',
  `local_path` varchar(500) DEFAULT NULL COMMENT '本地保存路径',
  `idx` int DEFAULT '0' COMMENT '图片序号',
  `crawl_time` datetime DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_post_id` (`post_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='帖子图片';
