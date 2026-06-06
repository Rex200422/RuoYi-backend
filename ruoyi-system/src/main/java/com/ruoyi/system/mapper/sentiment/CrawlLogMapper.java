package com.ruoyi.system.mapper.sentiment;

import java.util.List;
import com.ruoyi.system.domain.sentiment.CrawlLog;

public interface CrawlLogMapper {
    List<CrawlLog> selectList(CrawlLog q);
    CrawlLog selectById(Long id);
    long selectCount(CrawlLog q);
    int insert(CrawlLog log);
    int update(CrawlLog log);
    int deleteByIds(Long[] ids);
    long selectTotalCount();
    long selectSuccessCount();
    long selectFailedCount();
    int selectRunningCountByConfigId(Long configId);
    int selectTotalRunningCount();
}
