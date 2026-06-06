package com.ruoyi.system.service.sentiment;

import java.util.List;
import com.ruoyi.system.domain.sentiment.AiSummary;

public interface IAiSummaryService {
    List<AiSummary> selectList(AiSummary q);
    AiSummary selectById(Long id);
    long selectCount(AiSummary q);
    int insert(AiSummary s);
    int deleteByIds(Long[] ids);
    AiSummary selectLatest();
}
