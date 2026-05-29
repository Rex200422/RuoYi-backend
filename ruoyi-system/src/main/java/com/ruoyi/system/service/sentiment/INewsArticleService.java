package com.ruoyi.system.service.sentiment;
import java.util.List;
import com.ruoyi.system.domain.sentiment.NewsArticle;
public interface INewsArticleService {
    List<NewsArticle> selectList(NewsArticle q);
    NewsArticle selectById(Long id);
    int deleteByIds(Long[] ids);
}
