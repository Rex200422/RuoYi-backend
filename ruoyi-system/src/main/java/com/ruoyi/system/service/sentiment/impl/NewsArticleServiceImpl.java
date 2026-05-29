package com.ruoyi.system.service.sentiment.impl;
import java.util.List;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import com.ruoyi.system.domain.sentiment.NewsArticle;
import com.ruoyi.system.mapper.sentiment.NewsArticleMapper;
import com.ruoyi.system.service.sentiment.INewsArticleService;
@Service
public class NewsArticleServiceImpl implements INewsArticleService {
    @Autowired private NewsArticleMapper m;
    public List<NewsArticle> selectList(NewsArticle q) { return m.selectList(q); }
    public NewsArticle selectById(Long id) { return m.selectById(id); }
    public int deleteByIds(Long[] ids) { return m.deleteByIds(ids); }
}
