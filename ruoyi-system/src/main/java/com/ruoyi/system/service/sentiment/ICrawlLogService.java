package com.ruoyi.system.service.sentiment;

import java.util.List;
import com.ruoyi.system.domain.sentiment.CrawlLog;

public interface ICrawlLogService {
    List<CrawlLog> selectList(CrawlLog q);
    CrawlLog selectById(Long id);
    int insert(CrawlLog log);
    int update(CrawlLog log);
    int deleteByIds(Long[] ids);
    long selectTotalCount();
    long selectSuccessCount();
    long selectFailedCount();
}
