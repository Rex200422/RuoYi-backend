"""
Shared content extraction utilities for news spiders.
Uses innerHTML + BeautifulSoup to preserve paragraph structure.
"""
import re
from bs4 import BeautifulSoup

# Boilerplate patterns to remove from content
BOILERPLATE = [
    r"©\s*\d{4}.*?All Rights Reserved.*",
    r"All Rights Reserved.*",
    r"Scan the QR code.*",
    r"CNN values your feedback.*",
    r"Download the CNN app.*",
    r"Sign up for.*newsletter.*",
    r"Click to expand\s*Image",
    r"©\s*\d{4}\s+\w+.*?/\w+.*",  # photo credits like "© 2026 Dom Gibbons/LA"
    r"Share\s*(This|Print|on Facebook|on Twitter|on LinkedIn|via Email)",
    r"Print This Post",
    r"^(Share|SHARE)\s*$",
    r"^\|.*$",  # table row fragments
]

BOILERPLATE_RE = re.compile("|".join(BOILERPLATE), re.IGNORECASE | re.MULTILINE)

# Tags to remove entirely
REMOVE_TAGS = ['script', 'style', 'nav', 'footer', 'aside', 'form', 'iframe',
               'figure', 'figcaption', 'div.share', 'div.social', '.tweet',
               '.share-buttons', '.article-share', '.social-share']

# CSS selectors for footer-like elements to remove
FOOTER_SELECTORS = [
    '.article-footer', '.post-footer', '.entry-footer',
    '.share-links', '.social-links', '.related-posts',
    '.article-tags', '.post-tags', '.article-categories',
    '.newsletter-signup', '.subscribe-box',
    '.sr-only',  # screen-reader only text (HRW has "Click to expand Image" here)
    '.article-share', '.share-bar',
]


def clean_content_html(html):
    """
    Clean HTML content from a news article.
    Removes boilerplate, footer, share buttons, etc.
    Returns cleaned HTML string.
    """
    if not html:
        return ""
    
    soup = BeautifulSoup(html, "html.parser")
    
    # Remove unwanted tags
    for tag_name in ['script', 'style', 'nav', 'aside', 'form', 'iframe']:
        for tag in soup.find_all(tag_name):
            tag.decompose()
    
    # Remove elements by CSS class patterns
    for element in soup.find_all(True):
        if not element.attrs:
            continue
        classes = element.get('class', []) or []
        if isinstance(classes, str):
            classes = [classes]
        class_str = " ".join(classes).lower()
        
        # Remove footer-like elements
        for pattern in ['footer', 'share', 'social', 'newsletter', 'subscribe',
                        'related', 'sr-only', 'tweet', 'comment-form']:
            if pattern in class_str:
                element.decompose()
                break
    
    # Remove <figure> tags (contain images with captions)
    for fig in soup.find_all('figure'):
        fig.decompose()
    
    # Get all <p> tags as paragraphs
    paragraphs = []
    for p in soup.find_all('p'):
        text = p.get_text(" ", strip=True)
        if not text:
            continue
        # Skip boilerplate
        if BOILERPLATE_RE.search(text):
            continue
        # Skip very short fragments (likely metadata)
        if len(text) < 15:
            continue
        # Skip lines that look like image captions or metadata
        if text.startswith("Image:") or text.startswith("Photo:"):
            continue
        paragraphs.append(f"<p>{text}</p>")
    
    # If no <p> tags found, fall back to text splitting
    if not paragraphs:
        text = soup.get_text(separator="\n")
        for line in text.split("\n"):
            line = line.strip()
            if not line or len(line) < 15:
                continue
            if BOILERPLATE_RE.search(line):
                continue
            paragraphs.append(f"<p>{line}</p>")
    
    return "\n".join(paragraphs)


def scroll_to_bottom(page):
    """Scroll page incrementally to trigger lazy-loaded content."""
    try:
        total_height = page.evaluate("document.body.scrollHeight")
        current = 0
        while current < total_height:
            current += 800
            page.evaluate(f"window.scrollTo(0, {current})")
            page.wait_for_timeout(400)
        page.wait_for_timeout(2000)
    except Exception:
        pass

def extract_content_playwright(page, selector="article", base_url=""):
    """
    Extract and clean article content from a Playwright page.
    Scrolls to bottom first to trigger lazy load, then uses innerHTML.
    """
    # Scroll to trigger lazy-loaded content
    scroll_to_bottom(page)
    
    # Try specific content selector first (more precise)
    for sel in ["div.entry-content", selector]:
        container = page.locator(sel)
        if container.count() > 0:
            html = container.first.inner_html()
            result = clean_content_html(html)
            if result and len(result) > 100:
                return result
    
    return ""


def remove_boilerplate_text(text):
    """
    For plain text content, remove boilerplate and split into paragraphs.
    Returns HTML with <p> tags.
    """
    # Remove boilerplate lines
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if len(line) < 10:
            continue
        if BOILERPLATE_RE.search(line):
            continue
        cleaned.append(line)
    
    text = " ".join(cleaned)
    if not text:
        return ""
    
    # If text is very long without newlines, split on sentence boundaries
    if len(text) > 200:
        # Split on double space or sentence endings followed by space+capital
        import re as _re
        sentences = _re.split(r'\s{2,}|(?<=[.!?])\s+(?=[A-Z])', text)
        paragraphs = []
        current = ""
        for s in sentences:
            s = s.strip()
            if not s:
                continue
            if len(current) + len(s) > 300:
                if current:
                    paragraphs.append(f"<p>{current.strip()}</p>")
                current = s
            else:
                current += " " + s if current else s
        if current:
            paragraphs.append(f"<p>{current.strip()}</p>")
        return "\n".join(paragraphs)
    else:
        return f"<p>{text}</p>"
