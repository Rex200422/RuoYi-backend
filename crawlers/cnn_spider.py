"""
CNN 报刊杂志新闻爬虫 v2 - 保留HTML格式
"""
import os, sys, re, time, random, requests
from bs4 import BeautifulSoup
import pymysql

PROXY = "http://192.168.0.14:7890/"
PROXIES = {"http": PROXY, "https": PROXY}
DB_CONFIG = {"host": "localhost", "user": "root", "password": "200422", "database": "ry-vue", "charset": "utf8mb4"}
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36", "Referer": "https://edition.cnn.com/"}
MAX_ARTICLES = int(sys.argv[1]) if len(sys.argv) > 1 else 3
CNN_RSS_FEEDS = ["http://rss.cnn.com/rss/edition.rss", "http://rss.cnn.com/rss/edition_world.rss"]

def get_db(): return pymysql.connect(**DB_CONFIG)
def clean(text): return re.sub(r"\s+", " ", text).strip()

def save_article(cursor, article):
    sql = """INSERT INTO news_article (title,url,publish_date,keywords,content,source)
    VALUES (%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE content=VALUES(content), keywords=VALUES(keywords)"""
    cursor.execute(sql, (article["title"], article["url"], article["date"], article["keywords"], article["content"], article["source"]))

def extract_keywords(text):
    t = text.lower()
    return ",".join(sorted([k for k in ["china","taiwan","trade","technology","military","economy","politics","health","climate"] if re.search(rf"\b{k}\b", t)]))

def get_article_content(url):
    """获取文章正文 - 保留HTML格式"""
    invalid = ["/collections/", "/video/", "/interactive/", "live-news"]
    if any(kw in url for kw in invalid):
        return ""
    for attempt in range(3):
        try:
            r = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=25, verify=False, allow_redirects=True)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            
            # 优先找 CNN 正文区域
            content_parts = []
            p_tags = soup.find_all("p", class_="paragraph")
            if not p_tags:
                p_tags = soup.find_all("div", class_="zn-body__paragraph")
            if not p_tags:
                # 通用兜底：所有 p 标签
                for p in soup.find_all("p"):
                    text = clean(p.get_text())
                    if len(text) > 20:
                        content_parts.append(f"<p>{text}</p>")
            else:
                for p in p_tags:
                    text = clean(p.get_text())
                    if text:
                        content_parts.append(f"<p>{text}</p>")
            
            # 也找 h2/h3 标题
            for h in soup.find_all(["h2", "h3"]):
                text = clean(h.get_text())
                if text and len(text) > 5:
                    content_parts.append(f"<{h.name}>{text}</{h.name}>")
            
            content = "\n".join(content_parts)
            return content[:8000] if len(content) >= 50 else ""
        except Exception as e:
            if attempt == 2:
                print(f"  [WARN] 正文抓取失败: {url[:50]}... | {e}")
                return ""
            time.sleep(2)

def crawl_cnn():
    print(f"=== CNN 新闻爬虫 v2 (HTML格式) ===")
    print(f"最大文章数: {MAX_ARTICLES}")
    conn = get_db(); cursor = conn.cursor(); count = 0
    try:
        for feed_url in CNN_RSS_FEEDS:
            if count >= MAX_ARTICLES: break
            print(f"\n访问 RSS: {feed_url}")
            try:
                resp = requests.get(feed_url, headers=HEADERS, proxies=PROXIES, timeout=15, verify=False)
                resp.raise_for_status()
                soup = BeautifulSoup(resp.text, "xml")
                items = soup.find_all("item")
                print(f"  获取 {len(items)} 条新闻")
                for item in items:
                    if count >= MAX_ARTICLES: break
                    title_tag = item.find("title")
                    link_tag = item.find("link")
                    if not title_tag or not link_tag: continue
                    title = title_tag.text.strip()
                    href = link_tag.text.strip()
                    if len(title) < 5: continue
                    content = get_article_content(href)
                    if not content:
                        print(f"  [SKIP] {title[:40]}... (无正文)")
                        continue
                    keywords = extract_keywords(title + " " + clean(content))
                    pub_date = ""
                    date_tag = item.find("pubDate")
                    if date_tag: pub_date = date_tag.text.strip()
                    article = {"title": title, "url": href, "date": pub_date, "keywords": keywords, "content": content, "source": "CNN"}
                    save_article(cursor, article)
                    conn.commit()
                    count += 1
                    print(f"  [OK] {count}/{MAX_ARTICLES}: {title[:50]}...")
                    time.sleep(random.uniform(1, 2.5))
            except Exception as e:
                print(f"  [ERROR] RSS抓取失败: {e}")
                continue
        print(f"\n=== 爬取完成，共 {count} 条新闻 ===")
    finally:
        cursor.close(); conn.close()

if __name__ == "__main__":
    crawl_cnn()
