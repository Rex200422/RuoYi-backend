package com.ruoyi.system.mapper.sentiment;

import java.util.List;
import com.ruoyi.system.domain.sentiment.AiSummary;

public interface AiSummaryMapper {
    List<AiSummary> selectList(AiSummary q);
    AiSummary selectById(Long id);
    long selectCount(AiSummary q);
    int insert(AiSummary s);
    int deleteByIds(Long[] ids);
    AiSummary selectLatest();
}
