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
    /** 查询某 config_id 是否有 running 状态的日志（防止重复触发） */
    int selectRunningCountByConfigId(Long configId);
    int selectPendingOrRunningCountByConfigId(Long configId);
    /** 查询当前 running 状态的总数（限制并发） */
    int selectTotalRunningCount();
}
