package com.ruoyi.system.domain.sentiment;

import com.ruoyi.common.core.domain.BaseEntity;
import java.util.Date;

public class CrawlConfig extends BaseEntity {
    private static final long serialVersionUID = 1L;
    private Long id;
    private String siteName;
    private String keyword;
    private Integer intervalMinutes;
    private Integer maxResults;
    private Date lastCrawlTime;
    private Integer enabled;

    public Long getId() { return id; } public void setId(Long id) { this.id = id; }
    public String getSiteName() { return siteName; } public void setSiteName(String siteName) { this.siteName = siteName; }
    public String getKeyword() { return keyword; } public void setKeyword(String keyword) { this.keyword = keyword; }
    public Integer getIntervalMinutes() { return intervalMinutes; } public void setIntervalMinutes(Integer intervalMinutes) { this.intervalMinutes = intervalMinutes; }
    public Integer getMaxResults() { return maxResults; } public void setMaxResults(Integer maxResults) { this.maxResults = maxResults; }
    public Date getLastCrawlTime() { return lastCrawlTime; } public void setLastCrawlTime(Date lastCrawlTime) { this.lastCrawlTime = lastCrawlTime; }
    public Integer getEnabled() { return enabled; } public void setEnabled(Integer enabled) { this.enabled = enabled; }
}
