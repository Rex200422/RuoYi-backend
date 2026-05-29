package com.ruoyi.system.domain.sentiment;

import com.ruoyi.common.core.domain.BaseEntity;

public class SocialComment extends BaseEntity {
    private static final long serialVersionUID = 1L;
    private Long id;
    private String postId;
    private String title;
    private String commentId;
    private String commenter;
    private String commentContent;
    private Integer likeCount;
    private String commentTime;

    public Long getId() { return id; } public void setId(Long id) { this.id = id; }
    public String getPostId() { return postId; } public void setPostId(String v) { this.postId = v; }
    public String getTitle() { return title; } public void setTitle(String v) { this.title = v; }
    public String getCommentId() { return commentId; } public void setCommentId(String v) { this.commentId = v; }
    public String getCommenter() { return commenter; } public void setCommenter(String v) { this.commenter = v; }
    public String getCommentContent() { return commentContent; } public void setCommentContent(String v) { this.commentContent = v; }
    public Integer getLikeCount() { return likeCount; } public void setLikeCount(Integer v) { this.likeCount = v; }
    public String getCommentTime() { return commentTime; } public void setCommentTime(String v) { this.commentTime = v; }
}
