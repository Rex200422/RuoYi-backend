"""
Bluesky 社交媒体爬虫
关键词: china, taiwan 及中文同义词
每个关键词最多爬取2条帖子及其评论 + 图片
"""
import os
import sys
import uuid
import hashlib
from datetime import datetime
os.environ["HTTP_PROXY"] = "http://192.168.0.14:7890/"
os.environ["HTTPS_PROXY"] = "http://192.168.0.14:7890/"

from atproto import Client
import pymysql
import time
import requests

DB_CONFIG = {"host": "localhost", "user": "root", "password": "200422", "database": "ry-vue", "charset": "utf8mb4"}
BSKY_USERNAME = os.environ.get("BSKY_USERNAME", "zao-17.bsky.social")
BSKY_PASSWORD = os.environ.get("BSKY_PASSWORD", "3ORI6-VJAFI")
KEYWORDS = ["china", "taiwan"]
KEYWORD_DISPLAY = {"china": "China", "taiwan": "Taiwan"}
MAX_PER_KW = int(sys.argv[1]) if len(sys.argv) > 1 else 2
DEPTH = 3

IMAGE_DIR = "/home/ruoyi/uploadPath/sentiment/images"
PROXY = "http://192.168.0.14:7890/"

def get_db(): return pymysql.connect(**DB_CONFIG)

def save_post(cur, p):
    sql = """INSERT INTO social_post (uuid,site_name,trigger_keyword,source_board,post_id,title,author,publish_time,like_count,comment_count,content,original_url,image_url)
    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE like_count=VALUES(like_count),comment_count=VALUES(comment_count),image_url=VALUES(image_url)"""
    cur.execute(sql, (p["uuid"],p["site_name"],p["trigger_keyword"],p["source_board"],p["post_id"],p["title"],p["author"],p["publish_time"],p["like_count"],p["comment_count"],p["content"],p["original_url"],p["image_url"]))

def save_comment(cur, c):
    sql = """INSERT INTO social_comment (post_id,title,comment_id,commenter,comment_content,like_count,comment_time)
    VALUES(%s,%s,%s,%s,%s,%s,%s)"""
    cur.execute(sql, (c["post_id"],c["title"],c["comment_id"],c["commenter"],c["comment_content"],c["like_count"],c["comment_time"]))

def get_image_urls(record, did=None):
    """从帖子记录中提取图片URL列表（含图片、外链缩略图）"""
    images = []
    if not (hasattr(record, 'embed') and record.embed):
        return images
    embed = record.embed
    base = "https://bsky.social/xrpc/com.atproto.sync.getBlob"
    # 1. 帖子内嵌图片 - Image.image 是 BlobRef
    if hasattr(embed, 'images') and embed.images:
        for img in embed.images:
            blob = getattr(img, 'image', None)
            if blob and hasattr(blob, 'ref') and hasattr(blob.ref, 'link'):
                cid = str(blob.ref.link)
                if did:
                    images.append(f"{base}?did={did}&cid={cid}")
    # 2. 外链缩略图 - embed.external.thumb 是 BlobRef
    if hasattr(embed, 'external') and embed.external:
        ext = embed.external
        thumb = getattr(ext, 'thumb', None)
        if thumb and hasattr(thumb, 'ref') and hasattr(thumb.ref, 'link'):
            cid = str(thumb.ref.link)
            if did:
                images.append(f"{base}?did={did}&cid={cid}")
    return images

def download_image(url, post_id, idx):
    """下载图片到本地，返回本地文件名"""
    post_id_hash = hashlib.md5(post_id.encode()).hexdigest()[:16]
    filename = f"{post_id_hash}_{idx}.jpg"
    local_path = os.path.join(IMAGE_DIR, filename)
    if os.path.exists(local_path):
        return filename
    try:
        resp = requests.get(url, proxies={'http': PROXY, 'https': PROXY}, timeout=30)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            f.write(resp.content)
        print(f"    [IMG] 下载: {filename} ({len(resp.content)} bytes)")
        return filename
    except Exception as e:
        print(f"    [IMG] 下载失败: {e}")
        return None

def save_images(cur, conn, post_id, image_urls):
    """保存图片记录到 social_post_image 表，并下载到本地"""
    # 先检查是否已有图片记录
    cur.execute("SELECT COUNT(*) FROM social_post_image WHERE post_id=%s", (post_id,))
    if cur.fetchone()[0] > 0:
        return ""
    first_local = ""
    for idx, url in enumerate(image_urls):
        local_file = download_image(url, post_id, idx)
        local_path = f"sentiment/images/{local_file}" if local_file else ""
        if idx == 0:
            first_local = local_path
        cur.execute(
            "INSERT INTO social_post_image (post_id, image_url, local_path, idx) VALUES(%s, %s, %s, %s)",
            (post_id, url, local_path, idx)
        )
    conn.commit()
    return first_local

def parse_thread(node, root_uri, root_title, root_image, kw, display_kw, conn, cur, depth=0):
    if not node or depth > DEPTH: return
    if hasattr(node, 'post'):
        p = node.post
        cid = p.uri
        ct = ""
        if hasattr(p.record, 'created_at') and p.record.created_at:
            try: ct = datetime.fromisoformat(str(p.record.created_at).replace("Z","+00:00")).strftime("%Y-%m-%d %H:%M:%S")
            except: pass
        if root_uri == cid:
            # 主帖 - 提取图片URL并下载
            img_urls = get_image_urls(p.record, did=p.author.did)
            local_first = ""
            if img_urls:
                local_first = save_images(cur, conn, cid, img_urls)
                # image_url 存第一个图片的本地路径用于快速访问
                root_image = local_first if local_first else ",".join(img_urls)
            save_post(cur, {"uuid":str(uuid.uuid4()),"site_name":"Bluesky","trigger_keyword":display_kw,"source_board":"search",
                "post_id":cid,"title":(p.record.text or "")[:100],"author":p.author.handle,"publish_time":ct,
                "like_count":p.like_count or 0,"comment_count":p.reply_count or 0,"content":p.record.text or "",
                "original_url":f"https://bsky.app/profile/{p.author.handle}/post/{cid.split('/')[-1]}",
                "image_url":img_urls[0] if img_urls else ""})
            conn.commit()
            print(f"  [POST] {p.author.handle}: {(p.record.text or '')[:60]}")
        else:
            save_comment(cur, {"post_id":root_uri,"title":root_title,"comment_id":cid,"commenter":p.author.handle,
                "comment_content":p.record.text or "","like_count":p.like_count or 0,"comment_time":ct})
            conn.commit()
    if hasattr(node, 'replies') and node.replies:
        for r in node.replies: parse_thread(r, root_uri, root_title, root_image, kw, display_kw, conn, cur, depth+1)

def crawl():
    print("=== Bluesky 爬虫 ===")
    client = Client()
    client.login(BSKY_USERNAME, BSKY_PASSWORD)
    conn = get_db(); cur = conn.cursor()
    try:
        for kw in KEYWORDS:
            print(f"\n--- 搜索: {kw} ---")
            result = client.app.bsky.feed.search_posts({"q": kw, "limit": MAX_PER_KW*6, "sort": "latest"})
            
            def has_media(post):
                embed = getattr(post.record, 'embed', None)
                if not embed: return 0
                if hasattr(embed, 'images') and embed.images: return 2
                if hasattr(embed, 'external') and getattr(embed.external, 'thumb', None): return 1
                return 0
            
            sorted_posts = sorted(result.posts, key=has_media, reverse=True)
            cnt = 0
            for post in sorted_posts:
                if cnt >= MAX_PER_KW: break
                uri = post.uri
                title = (post.record.text or "")[:100]
                if hasattr(post.record,'reply') and post.record.reply: uri = post.record.reply.root.uri
                cur.execute("SELECT 1 FROM social_post WHERE post_id=%s",(uri,))
                if cur.fetchone(): continue
                try:
                    th = client.app.bsky.feed.get_post_thread({"uri":uri,"depth":DEPTH})
                    parse_thread(th.thread, uri, title, "", kw, KEYWORD_DISPLAY.get(kw, kw), conn, cur)
                    cnt += 1
                except Exception as e: print(f"  [ERR] {e}")
                time.sleep(1)
            print(f"  {kw}: {cnt}条")
    finally: cur.close(); conn.close()
    print("=== 完成 ===")

if __name__=="__main__": crawl()
