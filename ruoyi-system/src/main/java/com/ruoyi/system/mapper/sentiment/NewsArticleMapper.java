package com.ruoyi.system.mapper.sentiment;
import java.util.List;
import com.ruoyi.system.domain.sentiment.NewsArticle;
public interface NewsArticleMapper {
    List<NewsArticle> selectList(NewsArticle q);
    NewsArticle selectById(Long id);
    long selectCount(NewsArticle q);
    int deleteByIds(Long[] ids);
}
