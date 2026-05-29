package com.ruoyi.system.mapper.sentiment;

import java.util.List;
import com.ruoyi.system.domain.sentiment.CrawlConfig;

public interface CrawlConfigMapper {
    List<CrawlConfig> selectList(CrawlConfig q);
    CrawlConfig selectById(Long id);
    long selectCount(CrawlConfig q);
    int insert(CrawlConfig config);
    int update(CrawlConfig config);
    int deleteByIds(Long[] ids);
    List<CrawlConfig> selectDueConfigs();
}
