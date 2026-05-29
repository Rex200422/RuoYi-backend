package com.ruoyi.system.service.sentiment;

import java.util.List;
import com.ruoyi.system.domain.sentiment.CrawlConfig;

public interface ICrawlConfigService {
    List<CrawlConfig> selectList(CrawlConfig q);
    CrawlConfig selectById(Long id);
    int insert(CrawlConfig config);
    int update(CrawlConfig config);
    int deleteByIds(Long[] ids);
    List<CrawlConfig> selectDueConfigs();
}
