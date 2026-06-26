package com.ruoyi.system.domain.sentiment;

import com.fasterxml.jackson.annotation.JsonFormat;
import java.util.Date;

public class HighFrequencyUser {
    private Long id;
    private String username;
    private String platform;
    private Integer postCount;
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date windowStart;
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date windowEnd;
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date createdAt;

    // getters/setters
    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public String getPlatform() { return platform; }
    public void setPlatform(String platform) { this.platform = platform; }
    public Integer getPostCount() { return postCount; }
    public void setPostCount(Integer postCount) { this.postCount = postCount; }
    public Date getWindowStart() { return windowStart; }
    public void setWindowStart(Date windowStart) { this.windowStart = windowStart; }
    public Date getWindowEnd() { return windowEnd; }
    public void setWindowEnd(Date windowEnd) { this.windowEnd = windowEnd; }
    public Date getCreatedAt() { return createdAt; }
    public void setCreatedAt(Date createdAt) { this.createdAt = createdAt; }
}
