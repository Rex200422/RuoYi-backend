package com.ruoyi.system.domain.sentiment;

import com.ruoyi.common.core.domain.BaseEntity;

public class SocialPost extends BaseEntity {
    private static final long serialVersionUID = 1L;
    private String uuid;
    private String siteName;
    private String triggerKeyword;
    private String sourceBoard;
    private String postId;
    private String title;
    private String author;
    private String publishTime;
    private Integer likeCount;
    private Integer commentCount;
    private String content;
    private String originalUrl;
    private String imageUrl;

    public String getUuid() { return uuid; } public void setUuid(String uuid) { this.uuid = uuid; }
    public String getSiteName() { return siteName; } public void setSiteName(String v) { this.siteName = v; }
    public String getTriggerKeyword() { return triggerKeyword; } public void setTriggerKeyword(String v) { this.triggerKeyword = v; }
    public String getSourceBoard() { return sourceBoard; } public void setSourceBoard(String v) { this.sourceBoard = v; }
    public String getPostId() { return postId; } public void setPostId(String v) { this.postId = v; }
    public String getTitle() { return title; } public void setTitle(String v) { this.title = v; }
    public String getAuthor() { return author; } public void setAuthor(String v) { this.author = v; }
    public String getPublishTime() { return publishTime; } public void setPublishTime(String v) { this.publishTime = v; }
    public Integer getLikeCount() { return likeCount; } public void setLikeCount(Integer v) { this.likeCount = v; }
    public Integer getCommentCount() { return commentCount; } public void setCommentCount(Integer v) { this.commentCount = v; }
    public String getContent() { return content; } public void setContent(String v) { this.content = v; }
    public String getOriginalUrl() { return originalUrl; } public void setOriginalUrl(String v) { this.originalUrl = v; }
    public String getImageUrl() { return imageUrl; } public void setImageUrl(String v) { this.imageUrl = v; }
}
