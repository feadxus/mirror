import os
import re
import json
from urllib.parse import urljoin, urlparse
from pathlib import Path
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

# GitHub Actions 选框输入域名镜像网站,极为快捷!!!不需要修改脚本
# 优先读取环境变量 TARGET_URL,读取不到时使用默认值
TARGET_URL = os.environ.get("TARGET_URL", "https://www.geoglify.com/")

# ---------------------------------------------------------
# 1. 配置参数
# ---------------------------------------------------------
TARGET_URL = "https://www.geoglify.com/"
SAVE_DIR = "./geoglify_mirror"
MAX_WORKERS = 5  # 并发下载数量
MAX_DEPTH = 5  # 路由发现最大深度

# HTTP 代理配置
PROXIES = {
    #"http": "http://127.0.0.1:16662",   # 配置使用隧道连接
    #"https": "http://127.0.0.1:16662",  # 配置使用隧道连接
    None                                 # 在 GitHub Actions 环境中直连即可
}

# 排除的域名 (地图瓦片、分析等)
EXCLUDE_DOMAINS = [
    "api.protomaps.com",
    "tile.openstreetmap.org",
    "basemaps.cartocdn.com",
    "events.mapbox.com",
    "www.google-analytics.com",
    "analytics.google.com",
    "cdn.segment.com",
    "api.mapbox.com",
    "cdn.jsdelivr.net",  # 可选：排除 CDN
]

# 排除的文件扩展名
EXCLUDE_EXTENSIONS = [
    ".mp4", ".webm", ".mkv",
    ".mp3", ".wav", ".flac",
]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

# ---------------------------------------------------------
# 2. 全局状态
# ---------------------------------------------------------
visited_urls = set()
failed_urls = set()
discovered_routes = set()

# ---------------------------------------------------------
# 3. 核心判断逻辑
# ---------------------------------------------------------
def is_target_domain(url):
    """判断 URL 是否属于目标域名"""
    target_netloc = urlparse(TARGET_URL).netloc
    url_netloc = urlparse(url).netloc
    return url_netloc == target_netloc


def is_excluded_domain(url):
    """判断 URL 是否在排除列表中"""
    parsed = urlparse(url)
    return any(domain in parsed.netloc for domain in EXCLUDE_DOMAINS)


def is_excluded_extension(url):
    """判断文件扩展名是否应该被排除"""
    path = urlparse(url).path.lower()
    return any(path.endswith(ext) for ext in EXCLUDE_EXTENSIONS)


def is_valid_asset(url):
    """判断 URL 是否应该被下载"""
    if is_excluded_domain(url):
        return False
    if is_excluded_extension(url):
        return False

    parsed = urlparse(url)

    # 过滤地图瓦片相关
    if parsed.path.endswith(".mvt") or "/tiles/" in parsed.path:
        return False

    # 过滤某些大文件格式
    if parsed.path.endswith((".zip", ".tar", ".gz", ".rar")):
        return False

    return True


def get_local_path(url):
    """根据 URL 生成本地保存路径"""
    parsed = urlparse(url)
    path = parsed.path

    # 处理根路径
    if not path or path.endswith("/"):
        path += "index.html"

    local_path = os.path.join(SAVE_DIR, path.lstrip("/"))
    return local_path


def extract_route_from_html(html_content, base_url):
    """从 HTML 中提取可能的 SPA 路由链接"""
    routes = set()

    # 匹配 <a href="..."> 标签
    href_pattern = re.compile(r'href=["\']([^"\']+)["\']', re.IGNORECASE)
    for match in href_pattern.finditer(html_content):
        link = match.group(1)

        # 过滤数据 URI 和锚点
        if link.startswith("data:") or link.startswith("javascript:") or link.startswith("#"):
            continue

        # 解析为完整 URL
        full_url = urljoin(base_url, link)

        if is_target_domain(full_url) and full_url not in visited_urls:
            # 提取路由部分 (去掉查询参数和片段)
            parsed = urlparse(full_url)
            route_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            routes.add(route_url)

    return routes


def extract_assets_from_html(html_content, base_url):
    """从 HTML 中提取所有资源 URL (完整的正则匹配)"""
    assets = set()

    # 模式 1: src, href, data-src, poster 属性
    pattern1 = re.compile(
        r'(?:src|href|data-src|poster|icon)=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )

    # 模式 2: CSS url() 语法
    pattern2 = re.compile(
        r'url\(["\']?([^"\'\)]+)["\']?\)',
        re.IGNORECASE,
    )

    # 模式 3: srcset 属性 (图片响应式)
    pattern3 = re.compile(
        r'srcset=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )

    # 模式 4: picture > source 标签
    pattern4 = re.compile(
        r'<source[^>]+srcset=["\']([^"\']+)["\']',
        re.IGNORECASE,
    )

    # 提取单一 URL
    for pattern in [pattern1, pattern2]:
        for match in pattern.finditer(html_content):
            url = match.group(1)
            if url and not url.startswith("data:") and not url.startswith("#"):
                full_url = urljoin(base_url, url)
                if is_valid_asset(full_url):
                    assets.add(full_url)

    # 处理 srcset (可能包含多个 URL)
    for match in pattern3.finditer(html_content):
        srcset_str = match.group(1)
        # srcset 格式: "url1 1x, url2 2x"
        urls = re.findall(r'([^\s,]+)\s+[\d\.]+(w|x)', srcset_str)
        for url, _ in urls:
            if url and not url.startswith("data:"):
                full_url = urljoin(base_url, url)
                if is_valid_asset(full_url):
                    assets.add(full_url)

    # 处理 picture > source
    for match in pattern4.finditer(html_content):
        srcset_str = match.group(1)
        urls = re.findall(r'([^\s,]+)', srcset_str)
        for url in urls:
            if url and not url.startswith("data:"):
                full_url = urljoin(base_url, url)
                if is_valid_asset(full_url):
                    assets.add(full_url)

    return assets


def extract_json_urls(json_str, base_url):
    """从 JSON 字符串中提取 URL (用于配置文件、数据文件)"""
    urls = set()

    # 简单的 URL 模式匹配 (http/https URL)
    url_pattern = re.compile(
        r'https?://(?:[a-zA-Z0-9\-._~:/?#\[\]@!$&\'()*+,;=])+',
        re.IGNORECASE,
    )

    for match in url_pattern.finditer(json_str):
        url = match.group(0)
        if is_valid_asset(url):
            urls.add(url)

    return urls


# ---------------------------------------------------------
# 4. 下载与解析
# ---------------------------------------------------------
def download_file(url):
    """下载单个文件"""
    if url in visited_urls:
        return None

    if not is_valid_asset(url):
        return None

    visited_urls.add(url)
    local_path = get_local_path(url)

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            proxies=PROXIES,
            timeout=15,
            allow_redirects=True,
        )

        if response.status_code == 200:
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(response.content)

            print(f"[✓] {url}")

            # 根据内容类型进行递归解析
            content_type = response.headers.get("Content-Type", "").lower()

            new_urls = set()

            if "text/html" in content_type:
                # HTML 文件：提取资源和路由
                text_content = response.text
                new_urls.update(extract_assets_from_html(text_content, url))
                new_urls.update(extract_route_from_html(text_content, url))

            elif "text/css" in content_type:
                # CSS 文件：提取资源 URL
                text_content = response.text
                new_urls.update(extract_assets_from_html(text_content, url))

            elif "application/json" in content_type or "text/json" in content_type:
                # JSON 文件：提取 URL
                try:
                    text_content = response.text
                    new_urls.update(extract_json_urls(text_content, url))
                except:
                    pass

            elif "application/javascript" in content_type or url.endswith(".js"):
                # JavaScript 文件：提取 URL (动态导入、资源等)
                text_content = response.text
                new_urls.update(extract_assets_from_html(text_content, url))
                new_urls.update(extract_json_urls(text_content, url))

            elif "image/svg" in content_type:
                # SVG 文件：提取资源
                text_content = response.text
                new_urls.update(extract_assets_from_html(text_content, url))

            return new_urls

        else:
            failed_urls.add(url)
            print(f"[✗ {response.status_code}] {url}")
            return None

    except Exception as e:
        failed_urls.add(url)
        print(f"[✗ ERROR] {url}: {type(e).__name__}")
        return None


# ---------------------------------------------------------
# 5. 路由发现 (BFS)
# ---------------------------------------------------------
def discover_routes(initial_url):
    """通过 BFS 发现 SPA 应用的所有路由"""
    queue = deque([(initial_url, 0)])
    visited = {initial_url}

    print("\n[阶段1] 发现路由...")

    while queue:
        url, depth = queue.popleft()

        if depth >= MAX_DEPTH:
            continue

        print(f"  [深度 {depth}] 探索: {url}")

        try:
            response = requests.get(
                url,
                headers=HEADERS,
                proxies=PROXIES,
                timeout=10,
            )

            if response.status_code == 200:
                routes = extract_route_from_html(response.text, url)

                for route in routes:
                    if route not in visited:
                        visited.add(route)
                        queue.append((route, depth + 1))
                        print(f"    → 发现: {route}")

        except Exception as e:
            print(f"  [ERROR] {url}: {type(e).__name__}")


# ---------------------------------------------------------
# 6. 获取目录大小
# ---------------------------------------------------------
def get_dir_size(path):
    """计算文件夹大小 (MB)"""
    total = 0
    try:
        for entry in os.scandir(path):
            if entry.is_file():
                total += entry.stat().st_size
            elif entry.is_dir():
                total += get_dir_size(entry.path)
    except:
        pass
    return round(total / (1024 * 1024), 2)


# ---------------------------------------------------------
# 7. 主程序
# ---------------------------------------------------------
def main():
    print("=" * 80)
    print(f"🌐 网站镜像工具 - 目标: {TARGET_URL}")
    print("=" * 80)

    # 第一步：发现路由
    discover_routes(TARGET_URL)

    # 第二步：下载所有资源
    print("\n[阶段2] 下载资源...")

    to_download = {TARGET_URL}
    batch_count = 0

    while to_download:
        batch_count += 1
        current_batch = to_download.copy()
        to_download.clear()

        print(f"\n  批次 {batch_count}: 处理 {len(current_batch)} 个 URL")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(download_file, url): url for url in current_batch}

            for future in as_completed(futures):
                try:
                    new_urls = future.result()
                    if new_urls:
                        to_download.update(new_urls - visited_urls)
                except Exception as e:
                    print(f"  [EXECUTOR ERROR] {e}")

    # 第三步：生成报告
    print("\n" + "=" * 80)
    print("✅ 镜像完成！")
    print("=" * 80)
    print(f"  ✓ 成功: {len(visited_urls) - len(failed_urls)} 个文件")
    print(f"  ✗ 失败: {len(failed_urls)} 个文件")
    print(f"  📁 位置: {os.path.abspath(SAVE_DIR)}")
    print(f"  📊 大小: {get_dir_size(SAVE_DIR)} MB")

    if failed_urls and len(failed_urls) <= 20:
        print("\n失败列表:")
        for url in sorted(failed_urls):
            print(f"    {url}")
    elif failed_urls:
        print(f"\n有 {len(failed_urls)} 个文件失败（太多,已省略）")

    print("\n📝 启动本地服务器:")
    print(f"    cd {SAVE_DIR}")
    print(f"    python -m http.server 8000")
    print(f"    # 然后访问: http://localhost:8000")
    print("=" * 80)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⏹️  用户中断")
        print(f"已下载 {len(visited_urls)} 个文件")
    except Exception as e:
        print(f"\n❌ 致命错误: {e}")
        import traceback
        traceback.print_exc()
