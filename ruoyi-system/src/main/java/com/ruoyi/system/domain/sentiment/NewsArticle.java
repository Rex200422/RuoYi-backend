package com.ruoyi.system.domain.sentiment;

import com.ruoyi.common.core.domain.BaseEntity;

public class NewsArticle extends BaseEntity {
    private static final long serialVersionUID = 1L;
    private Long id;
    private String title;
    private String url;
    private String publishDate;
    private String keywords;
    private String content;
    private String source;

    public Long getId() { return id; } public void setId(Long id) { this.id = id; }
    public String getTitle() { return title; } public void setTitle(String v) { this.title = v; }
    public String getUrl() { return url; } public void setUrl(String v) { this.url = v; }
    public String getPublishDate() { return publishDate; } public void setPublishDate(String v) { this.publishDate = v; }
    public String getKeywords() { return keywords; } public void setKeywords(String v) { this.keywords = v; }
    public String getContent() { return content; } public void setContent(String v) { this.content = v; }
    public String getSource() { return source; } public void setSource(String v) { this.source = v; }
}
