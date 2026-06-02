"""
URL fetcher using curl (works reliably through proxy).
"""
import subprocess
import os

PROXY = "http://192.168.0.14:7890/"

def fetch_url(url, timeout=30):
    """
    Fetch URL using curl, returns HTML content.
    Tries proxy first, then direct.
    """
    # Try with proxy first
    try:
        result = subprocess.run(
            ['curl', '-s', '-L', '--proxy', PROXY, '-m', str(timeout),
             '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
             url],
            capture_output=True, text=True, timeout=timeout+5
        )
        if result.returncode == 0 and len(result.stdout) > 1000:
            return result.stdout
    except Exception:
        pass
    
    # Try direct
    try:
        result = subprocess.run(
            ['curl', '-s', '-L', '-m', str(timeout),
             '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
             url],
            capture_output=True, text=True, timeout=timeout+5
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    
    return ""
