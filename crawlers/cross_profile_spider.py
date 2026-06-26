#!/usr/bin/env python3
"""
cross_profile_spider.py
调用 socid_extractor 查询指定用户名在各平台的信息

用法:
  python3 cross_profile_spider.py --username "someuser" --config-id 1 --log-id 99999
"""

import os
import sys
import json
import argparse
import requests
from datetime import datetime

# 添加 maigret 路径（使用已安装的 socid_extractor）
sys.path.insert(0, '/root/workspace/maigret')

from socid_extractor import schemes as schemes_module
from socid_extractor.main import extract, parse

# 需要查询的平台列表
PLATFORMS = {
    'Reddit': {
        'url_template': 'https://www.reddit.com/user/{username}/',
        'fields': ['reddit_id', 'reddit_username', 'fullname', 'image', 'is_employee',
                    'is_nsfw', 'is_mod', 'post_karma', 'comment_karma'],
    },
    'Instagram': {
        'url_template': 'https://www.instagram.com/{username}/',
        'fields': ['username', 'fullname', 'id', 'image', 'bio',
                    'business_email', 'external_url', 'facebook_uid'],
    },
    'TikTok': {
        'url_template': 'https://www.tiktok.com/@{username}',
        'fields': ['tiktok_id', 'tiktok_username', 'fullname', 'bio', 'image',
                    'is_verified', 'sec_uid'],
    },
    'Twitter': {
        'url_template': 'https://x.com/{username}',
        'fields': ['uid', 'fullname', 'bio', 'created_at', 'image', 'image_bg',
                    'follower_count', 'following_count', 'location'],
    },
    'Twitch': {
        'url_template': 'https://www.twitch.tv/{username}',
        'fields': ['id', 'username', 'bio', 'fullname', 'image', 'likes_count', 'image_bg'],
    },
    'Tumblr': {
        'url_template': 'https://{username}.tumblr.com/',
        'fields': ['fullname', 'title', 'image', 'image_bg', 'links'],
    },
    'Telegram': {
        'url_template': 'https://t.me/{username}',
        'fields': ['fullname', 'image', 'bio'],
    },
}


def query_user(username):
    """
    查询一个用户名在各平台的信息
    返回 dict: {platform: {status: 'claimed'|'unknown', data: {...}}}
    """
    result = {}
    for platform_name, config in PLATFORMS.items():
        url = config['url_template'].format(username=username)
        try:
            # 使用 socid_extractor 查询
            page, status_code = parse(url, timeout=8)
            if status_code in [404, 410]:
                result[platform_name] = {'status': 'available', 'data': {}}
                continue
            
            info = extract(page)
            if info:
                # 过滤出我们关心的字段
                filtered = {k: v for k, v in info.items() if k in config['fields']}
                result[platform_name] = {
                    'status': 'claimed',
                    'data': filtered
                }
            else:
                result[platform_name] = {'status': 'unknown', 'data': {}}
        except Exception as e:
            result[platform_name] = {
                'status': 'unknown',
                'data': {},
                'error': str(e)
            }
    
    return result


def main():
    parser = argparse.ArgumentParser(description='跨平台用户信息提取')
    parser.add_argument('--username', required=True, help='要查询的用户名')
    parser.add_argument('--config-id', type=int, help='配置ID(兼容CrawlScheduler)')
    parser.add_argument('--log-id', type=int, help='日志ID(兼容CrawlScheduler)')
    parser.add_argument('--max', type=int, default=1, help='最大查询数')
    args = parser.parse_args()

    username = args.username
    print(f"=== Cross-Profile Spider ===")
    print(f"  Query: {username}")
    print(f"  Platforms: {', '.join(PLATFORMS.keys())}")
    print()

    result = query_user(username)

    # 统计
    claimed_count = sum(1 for p in result.values() if p['status'] == 'claimed')
    print(f"  Result: {claimed_count}/{len(PLATFORMS)} platforms claimed")
    for platform, info in result.items():
        status = info['status']
        icon = '✅' if status == 'claimed' else ('❌' if status == 'available' else '⚠️')
        data_str = json.dumps(info.get('data', {}), ensure_ascii=False)[:100] if info['status'] == 'claimed' else ''
        print(f"  {icon} {platform:<12s} {status:<10s} {data_str}")

    # 输出 JSON 供 Java 端读取
    output = {
        'username': username,
        'query_time': datetime.now().isoformat(),
        'claimed_count': claimed_count,
        'platforms': result,
    }
    # 写到文件供 Java 读取
    output_path = os.path.join(os.path.dirname(__file__), 'logs',
                                f'cross_{username}_{args.log_id}.json' if args.log_id else f'cross_{username}.json')
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, default=str, indent=2)

    print(f"\n  JSON saved: {output_path}")
    return output


if __name__ == '__main__':
    main()
