package com.ruoyi.system.service.sentiment.impl;

import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.ruoyi.system.domain.sentiment.CrawlLog;
import com.ruoyi.system.mapper.sentiment.CrawlLogMapper;
import com.ruoyi.system.service.sentiment.ICrawlLogService;

@Service
public class CrawlLogServiceImpl implements ICrawlLogService {
    @Autowired private CrawlLogMapper m;

    public List<CrawlLog> selectList(CrawlLog q) { return m.selectList(q); }
    public CrawlLog selectById(Long id) { return m.selectById(id); }
    public int insert(CrawlLog log) { return m.insert(log); }
    public int update(CrawlLog log) { return m.update(log); }
    public int deleteByIds(Long[] ids) { return m.deleteByIds(ids); }
    public long selectTotalCount() { return m.selectTotalCount(); }
    public long selectSuccessCount() { return m.selectSuccessCount(); }
    public long selectFailedCount() { return m.selectFailedCount(); }
    public int selectRunningCountByConfigId(Long configId) { return m.selectRunningCountByConfigId(configId); }
    public int selectPendingOrRunningCountByConfigId(Long configId) { return m.selectPendingOrRunningCountByConfigId(configId); }
    public int selectTotalRunningCount() { return m.selectTotalRunningCount(); }
}
