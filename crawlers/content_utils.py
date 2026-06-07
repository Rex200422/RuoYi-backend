"""
Shared content extraction utilities for news spiders.
内容提取工具模块 - 新闻爬虫共用的内容清洗和提取功能。
=====================================

功能概述：
    提供文章正文的清洗、提取、去噪等功能。
    支持两种使用场景：
      1. BeautifulSoup 直接解析（静态页面）
      2. Playwright 浏览器解析（需要 JS 渲染的页面）

核心函数：
    - clean_content_html(): 清洗HTML内容，去除广告、脚注等噪音
    - remove_boilerplate_text(): 对纯文本内容去除模板文字
    - extract_content_playwright(): 从 Playwright 页面提取正文
    - scroll_to_bottom(): 模拟滚动到底部，触发懒加载

使用方式:
    from content_utils import clean_content_html, extract_content_playwright
"""
import re  # 正则表达式库
from bs4 import BeautifulSoup  # HTML解析库


# ============================================================
# 模板文字正则（需要清理的噪音内容）
# ============================================================
# 这些正则匹配常见的网页噪音文本，如版权声明、分享按钮、QR码提示等。
BOILERPLATE = [  # 模板文字正则列表，用于清理网页噪音
    r"©\s*\d{4}.*?All Rights Reserved.*",       # 版权声明: "© 2026 xxx All Rights Reserved"
    r"All Rights Reserved.*",                     # 版权声明变体
    r"Scan the QR code.*",                        # QR码扫描提示
    r"CNN values your feedback.*",                # CNN反馈提示
    r"Download the CNN app.*",                    # CNN应用下载提示
    r"Sign up for.*newsletter.*",                 # 订阅新闻邮件提示
    r"Click to expand\s*Image",                   # 图片展开提示
    r"©\s*\d{4}\s+\w+.*?/\w+.*",                 # 图片版权: "© 2026 Dom Gibbons/LA"
    r"Share\s*(This|Print|on Facebook|on Twitter|on LinkedIn|via Email)",  # 分享按钮文本
    r"Print This Post",                           # 打印文章
    r"^(Share|SHARE)\s*$",                        # 独立的分享标签
    r"^\|.*$",                                    # 表格行碎片
]

# 预编译正则，提高性能。IGNORECASE 匹配大小写，MULTILINE 支持多行匹配。
BOILERPLATE_RE = re.compile("|".join(BOILERPLATE), re.IGNORECASE | re.MULTILINE)  # 预编译正则，提高匹配性能

# ============================================================
# 需要完全删除的 HTML 标签
# ============================================================
# 这些标签不包含正文内容，直接删除整个标签及其子元素。
REMOVE_TAGS = ['script', 'style', 'nav', 'footer', 'aside', 'form', 'iframe',
               'figure', 'figcaption', 'div.share', 'div.social', '.tweet',
               '.share-buttons', '.article-share', '.social-share']  # 需要完全删除的HTML标签

# ============================================================
# 页脚类元素的 CSS 选择器（需要删除）
# ============================================================
# 匹配各种网站常见的页脚、分享栏、相关文章等区域。
FOOTER_SELECTORS = [  # 页脚类元素的CSS选择器列表
    '.article-footer', '.post-footer', '.entry-footer',   # 文章页脚
    '.share-links', '.social-links', '.related-posts',     # 分享和相关链接
    '.article-tags', '.post-tags', '.article-categories', # 标签和分类
    '.newsletter-signup', '.subscribe-box',                # 订阅区域
    '.sr-only',                                            # 仅屏幕阅读器文本（HRW的"Click to expand Image"）
    '.article-share', '.share-bar',                        # 分享栏
]


def clean_content_html(html):
    """
    清洗HTML内容，去除广告、页脚、分享按钮等噪音。

    处理流程：
      1. 用 BeautifulSoup 解析HTML
      2. 删除 script/style/nav/aside/form/iframe 等标签
      3. 根据 CSS 类名删除 footer/share/social/newsletter 等元素
      4. 删除 <figure> 标签（图片+说明文字）
      5. 提取所有 <p> 标签的文本，过滤掉短文本和模板文字
      6. 如果没有 <p> 标签，回退到按行分割纯文本

    参数:
        html (str): 待清洗的HTML字符串

    返回值:
        str: 清洗后的HTML，每个段落包裹在 <p> 标签中，
             段落之间用换行符分隔。
             如果输入为空则返回空字符串。
    """
    if not html:
        return ""
    
    soup = BeautifulSoup(html, "html.parser")
    
    # 步骤1: 删除不包含正文的标签
    for tag_name in ['script', 'style', 'nav', 'aside', 'form', 'iframe']:
        for tag in soup.find_all(tag_name):
            tag.decompose()  # 删除标签及其所有子元素
    
    # 步骤2: 根据 CSS 类名模式删除页脚类元素
    for element in soup.find_all(True):  # 遍历所有HTML元素
        if not element.attrs:  # 跳过没有属性的元素
            continue
        classes = element.get('class', []) or []  # 获取CSS类列表
        if isinstance(classes, str):  # 类名可能是字符串而非列表
            classes = [classes]
        class_str = " ".join(classes).lower()  # 合并为小写字符串
        
        # 检查类名中是否包含噪音关键词
        for pattern in ['footer', 'share', 'social', 'newsletter', 'subscribe',
                        'related', 'sr-only', 'tweet', 'comment-form']:
            if pattern in class_str:
                element.decompose()
                break
    
    # 步骤3: 删除 <figure> 标签（通常包含图片和图片说明，非正文）
    for fig in soup.find_all('figure'):
        fig.decompose()  # 删除标签及其所有子元素
    
    # 步骤4: 提取所有 <p> 标签作为段落
    paragraphs = []  # 存储清洗后的段落
    for p in soup.find_all('p'):  # 遍历所有<p>标签
        text = p.get_text(" ", strip=True)  # 获取文本，用空格连接
        if not text:  # 跳过空文本
            continue
        if BOILERPLATE_RE.search(text):  # 跳过匹配模板文字正则的行
            continue
        if len(text) < 15:  # 跳过过短的文本片段（通常是元数据）
            continue
        if text.startswith("Image:") or text.startswith("Photo:"):  # 跳过图片说明或元数据行
            continue
        paragraphs.append(f"<p>{text}</p>")  # 包裹在<p>标签中
    
    # 步骤5: 如果没有找到 <p> 标签，回退到按行分割纯文本
    if not paragraphs:  # 没有找到<p>标签
        text = soup.get_text(separator="\n")  # 获取所有文本，用换行符分隔
        for line in text.split("\n"):  # 按行分割
            line = line.strip()  # 去除首尾空白
            if not line or len(line) < 15:  # 跳过空行和过短行
                continue
            if BOILERPLATE_RE.search(line):  # 跳过模板文字
                continue
            paragraphs.append(f"<p>{line}</p>")  # 包裹在<p>标签中
    
    return "\n".join(paragraphs)


def scroll_to_bottom(page):
    """
    模拟浏览器滚动到底部，触发懒加载内容。

    许多网站采用懒加载（lazy loading）技术，只有用户滚动到可视区域时
    才加载内容。此函数逐步滚动页面，每次滚动 800px，等待 400ms，
    直到滚动到底部，最后额外等待 2 秒确保内容完全加载。

    参数:
        page: Playwright Page 对象

    返回值:
        None
    """
    try:
        total_height = page.evaluate("document.body.scrollHeight")  # 获取页面总高度
        current = 0  # 当前滚动位置
        while current < total_height:  # 逐步滚动到底部
            current += 800  # 每次滚动800像素
            page.evaluate(f"window.scrollTo(0, {current})")  # 执行滚动
            page.wait_for_timeout(400)  # 等待400ms，让内容加载
        page.wait_for_timeout(2000)  # 额外等待2秒，确保内容完全加载
    except Exception:
        pass  # 忽略滚动错误


def extract_content_playwright(page, selector="article", base_url=""):
    """
    从 Playwright 浏览器页面中提取并清洗文章正文。

    处理流程：
      1. 先滚动页面到底部，触发懒加载内容
      2. 依次尝试不同的 CSS 选择器定位正文容器：
         - "div.entry-content"（许多WordPress站点的正文容器）
         - selector 参数指定的选择器（默认为 "article"）
      3. 获取容器的 innerHTML，用 clean_content_html() 清洗
      4. 如果清洗后内容不足 100 字符，尝试下一个选择器

    参数:
        page: Playwright Page 对象（已加载目标页面）
        selector (str): 正文容器的 CSS 选择器，默认 "article"
        base_url (str): 基础URL（预留参数，当前未使用）

    返回值:
        str: 清洗后的HTML正文，如果提取失败返回空字符串
    """
    # 滚动页面触发懒加载
    scroll_to_bottom(page)  # 先滚动页面，加载懒加载内容
    
    # 依次尝试不同的选择器定位正文容器
    for sel in ["div.entry-content", selector]:  # 先尝试entry-content，再尝试指定选择器
        container = page.locator(sel)  # 定位元素
        if container.count() > 0:  # 如果找到元素
            html = container.first.inner_html()  # 获取innerHTML
            result = clean_content_html(html)  # 清洗HTML
            if result and len(result) > 100:  # 内容足够长则返回
                return result
    
    return ""  # 所有选择器都失败，返回空字符串


def remove_boilerplate_text(text):
    """
    对纯文本内容去除模板文字，分割为段落。

    处理流程：
      1. 按行分割文本
      2. 删除空行、过短行、匹配模板文字正则的行
      3. 合并为一段文本
      4. 如果文本较长（>200字符），按句子边界智能分割为多个段落，
         每段不超过 300 字符

    参数:
        text (str): 待处理的纯文本

    返回值:
        str: 带 <p> 标签的HTML，短文本返回单个段落，
             长文本返回多个段落。
    """
    # 删除模板文字行
    lines = text.split("\n")  # 按行分割
    cleaned = []  # 存储清洗后的行
    for line in lines:
        line = line.strip()  # 去除首尾空白
        if not line:  # 跳过空行
            continue
        if len(line) < 10:  # 跳过过短行
            continue
        if BOILERPLATE_RE.search(line):  # 跳过模板文字
            continue
        cleaned.append(line)  # 添加到清洗后的列表
    
    text = " ".join(cleaned)  # 合并为一段文本
    if not text:  # 如果为空则返回
        return ""
    
    # 如果文本较长且没有换行符，按句子边界智能分割
    if len(text) > 200:  # 文本较长，需要分段
        # 在双空格或句号/问号/感叹号后接大写字母处分割
        import re as _re  # 延迟导入，避免顶部重复
        sentences = _re.split(r'\s{2,}|(?<=[.!?])\s+(?=[A-Z])', text)  # 按句子边界分割
        paragraphs = []  # 存储分段后的段落
        current = ""  # 当前段落内容
        for s in sentences:  # 遍历每个句子
            s = s.strip()  # 去除首尾空白
            if not s:  # 跳过空句子
                continue
            if len(current) + len(s) > 300:  # 当前段落超过300字符则分割
                if current:  # 保存当前段落
                    paragraphs.append(f"<p>{current.strip()}</p>")
                current = s  # 开始新段落
            else:  # 否则合并到当前段落
                current += " " + s if current else s
        if current:  # 保存最后一个段落
            paragraphs.append(f"<p>{current.strip()}</p>")
        return "\n".join(paragraphs)  # 返回多个段落
    else:
        return f"<p>{text}</p>"  # 短文本返回单个段落
