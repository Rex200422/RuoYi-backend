package com.ruoyi.system.domain.sentiment;

import com.fasterxml.jackson.annotation.JsonFormat;
import java.util.Date;

public class UserCrossProfile {
    private Long id;
    private String username;
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date queryTime;
    
    private String redditStatus;
    private String redditData;
    private String instagramStatus;
    private String instagramData;
    private String tiktokStatus;
    private String tiktokData;
    private String twitterStatus;
    private String twitterData;
    private String twitchStatus;
    private String twitchData;
    private String tumblrStatus;
    private String tumblrData;
    private String telegramStatus;
    private String telegramData;
    
    private Integer claimedCount;
    private String rawResult;
    private String errorMsg;
    @JsonFormat(pattern = "yyyy-MM-dd HH:mm:ss")
    private Date createdAt;

    // getters/setters
    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public Date getQueryTime() { return queryTime; }
    public void setQueryTime(Date queryTime) { this.queryTime = queryTime; }
    public String getRedditStatus() { return redditStatus; }
    public void setRedditStatus(String redditStatus) { this.redditStatus = redditStatus; }
    public String getRedditData() { return redditData; }
    public void setRedditData(String redditData) { this.redditData = redditData; }
    public String getInstagramStatus() { return instagramStatus; }
    public void setInstagramStatus(String instagramStatus) { this.instagramStatus = instagramStatus; }
    public String getInstagramData() { return instagramData; }
    public void setInstagramData(String instagramData) { this.instagramData = instagramData; }
    public String getTiktokStatus() { return tiktokStatus; }
    public void setTiktokStatus(String tiktokStatus) { this.tiktokStatus = tiktokStatus; }
    public String getTiktokData() { return tiktokData; }
    public void setTiktokData(String tiktokData) { this.tiktokData = tiktokData; }
    public String getTwitterStatus() { return twitterStatus; }
    public void setTwitterStatus(String twitterStatus) { this.twitterStatus = twitterStatus; }
    public String getTwitterData() { return twitterData; }
    public void setTwitterData(String twitterData) { this.twitterData = twitterData; }
    public String getTwitchStatus() { return twitchStatus; }
    public void setTwitchStatus(String twitchStatus) { this.twitchStatus = twitchStatus; }
    public String getTwitchData() { return twitchData; }
    public void setTwitchData(String twitchData) { this.twitchData = twitchData; }
    public String getTumblrStatus() { return tumblrStatus; }
    public void setTumblrStatus(String tumblrStatus) { this.tumblrStatus = tumblrStatus; }
    public String getTumblrData() { return tumblrData; }
    public void setTumblrData(String tumblrData) { this.tumblrData = tumblrData; }
    public String getTelegramStatus() { return telegramStatus; }
    public void setTelegramStatus(String telegramStatus) { this.telegramStatus = telegramStatus; }
    public String getTelegramData() { return telegramData; }
    public void setTelegramData(String telegramData) { this.telegramData = telegramData; }
    public Integer getClaimedCount() { return claimedCount; }
    public void setClaimedCount(Integer claimedCount) { this.claimedCount = claimedCount; }
    public String getRawResult() { return rawResult; }
    public void setRawResult(String rawResult) { this.rawResult = rawResult; }
    public String getErrorMsg() { return errorMsg; }
    public void setErrorMsg(String errorMsg) { this.errorMsg = errorMsg; }
    public Date getCreatedAt() { return createdAt; }
    public void setCreatedAt(Date createdAt) { this.createdAt = createdAt; }
}
