package com.ruoyi.system.domain.sentiment;

import com.ruoyi.common.core.domain.BaseEntity;
import java.util.Date;

public class CrawlLog extends BaseEntity {
    private static final long serialVersionUID = 1L;
    private Long id;
    private String siteName;
    private String keyword;
    private String status;
    private Date startTime;
    private Date endTime;
    private Integer itemsFound;
    private Integer itemsSaved;
    private Integer itemsNew;
    private Integer itemsUpdated;
    private String errorMsg;
    private Long configId;

    public Long getId() { return id; } public void setId(Long id) { this.id = id; }
    public String getSiteName() { return siteName; } public void setSiteName(String siteName) { this.siteName = siteName; }
    public String getKeyword() { return keyword; } public void setKeyword(String keyword) { this.keyword = keyword; }
    public String getStatus() { return status; } public void setStatus(String status) { this.status = status; }
    public Date getStartTime() { return startTime; } public void setStartTime(Date startTime) { this.startTime = startTime; }
    public Date getEndTime() { return endTime; } public void setEndTime(Date endTime) { this.endTime = endTime; }
    public Integer getItemsFound() { return itemsFound; } public void setItemsFound(Integer itemsFound) { this.itemsFound = itemsFound; }
    public Integer getItemsSaved() { return itemsSaved; } public void setItemsSaved(Integer itemsSaved) { this.itemsSaved = itemsSaved; }
    public Integer getItemsNew() { return itemsNew; } public void setItemsNew(Integer itemsNew) { this.itemsNew = itemsNew; }
    public Integer getItemsUpdated() { return itemsUpdated; } public void setItemsUpdated(Integer itemsUpdated) { this.itemsUpdated = itemsUpdated; }
    public String getErrorMsg() { return errorMsg; } public void setErrorMsg(String errorMsg) { this.errorMsg = errorMsg; }
    public Long getConfigId() { return configId; } public void setConfigId(Long configId) { this.configId = configId; }
}
