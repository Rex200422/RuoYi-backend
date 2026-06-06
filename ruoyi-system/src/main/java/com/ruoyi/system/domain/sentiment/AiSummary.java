package com.ruoyi.system.domain.sentiment;

import com.ruoyi.common.core.domain.BaseEntity;
import java.util.Date;

public class AiSummary extends BaseEntity {
    private static final long serialVersionUID = 1L;
    private Long id;
    private String summaryType;
    private String title;
    private String content;
    private String riskLevel;
    private Date dataStart;
    private Date dataEnd;
    private Integer newsCount;
    private Integer socialCount;
    private String modelName;
    private Integer generateTime;
    private Date createTime;

    public Long getId() { return id; } public void setId(Long id) { this.id = id; }
    public String getSummaryType() { return summaryType; } public void setSummaryType(String summaryType) { this.summaryType = summaryType; }
    public String getTitle() { return title; } public void setTitle(String title) { this.title = title; }
    public String getContent() { return content; } public void setContent(String content) { this.content = content; }
    public String getRiskLevel() { return riskLevel; } public void setRiskLevel(String riskLevel) { this.riskLevel = riskLevel; }
    public Date getDataStart() { return dataStart; } public void setDataStart(Date dataStart) { this.dataStart = dataStart; }
    public Date getDataEnd() { return dataEnd; } public void setDataEnd(Date dataEnd) { this.dataEnd = dataEnd; }
    public Integer getNewsCount() { return newsCount; } public void setNewsCount(Integer newsCount) { this.newsCount = newsCount; }
    public Integer getSocialCount() { return socialCount; } public void setSocialCount(Integer socialCount) { this.socialCount = socialCount; }
    public String getModelName() { return modelName; } public void setModelName(String modelName) { this.modelName = modelName; }
    public Integer getGenerateTime() { return generateTime; } public void setGenerateTime(Integer generateTime) { this.generateTime = generateTime; }
    public Date getCreateTime() { return createTime; } public void setCreateTime(Date createTime) { this.createTime = createTime; }
}
