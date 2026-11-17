"""
东莞人社局政策文件采集脚本
--------------------------------------------------
数据来源：东莞市人力资源和社会保障局   https://dghrss.dg.gov.cn
用途：为本地 RAG 项目构建知识库，只采集公开的政策法规 / 政策解读页面。
输出：Markdown 文件（带元数据头）+ manifest_dg.json 清单

依赖：requests, beautifulsoup4
运行：python collect_dg.py

【2026-10-26 修订】原版只抓 /zcfg/ 与 /zcfg/zcjd/ 两个顶层列表页、且不翻页，
结果只拿到 29 份，缺少「就业补贴 / 应届生」等大量内容（多条件组合型评测做不出来）。
实测站点真实结构是「领域子栏目 + 分页」：
    /zcfg/jycy/zcwj/   就业创业-政策文件
    /zcfg/ldgx/        劳动关系
    /zcfg/shbz/        社会保险
    /zcfg/zyjsrc/      职业建设与人才
    /zcfg/qt/          其他
    /zcfg/zcjd/        政策解读
分页为 index.html -> index_2.html -> index_3.html ...
本版：① 遍历上述 6 个栏目 ② 翻页至无新条目 ③ 清单增量合并（原版会覆盖清单，是 bug）
      ④ 只保留 dghrss.dg.gov.cn 域名（排除转载的省级 hrss.gd.gov.cn 链接）
"""
import json
import pathlib
import re
import time

import requests
from bs4 import BeautifulSoup

NL = chr(10)
BASE = "https://dghrss.dg.gov.cn"

# (column, sub_column, path)
COLUMNS = [
    ("政策法规", "就业创业", "/zcfg/jycy/zcwj/"),
    ("政策法规", "劳动关系", "/zcfg/ldgx/"),
    ("政策法规", "社会保险", "/zcfg/shbz/"),
    ("政策法规", "职业建设人才", "/zcfg/zyjsrc/"),
    ("政策法规", "其他", "/zcfg/qt/"),
    ("政策解读", "政策解读", "/zcfg/zcjd/"),
]
MAX_PAGES = 15
OUT_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "raw"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
SLEEP = 0.8          # 限速，别把政府网站打挂
TIMEOUT = 20

# 控制字符与文件系统非法字符（用 chr() 拼，避免转义歧义）
ILLEGAL = "[\\\\/:*?" + chr(34) + "<>|" + chr(13) + chr(10) + chr(9) + "]"


def clean_title(t: str) -> str:
    t = re.sub(ILLEGAL, "", t).strip()
    t = re.sub(r"\s+", "", t)
    return t[:80]


def get_soup(session, url):
    r = session.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return BeautifulSoup(r.text, "lxml")


def list_articles(session, path):
    """从列表页取出 (标题, 绝对URL)，只要本站域名。"""
    soup = get_soup(session, BASE + path)
    items, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not re.search(r"post_\d+\.html", href):
            continue
        url = href if href.startswith("http") else BASE + href
        if "dghrss.dg.gov.cn" not in url:
            continue
        if url in seen:
            continue
        title = a.get_text(strip=True)
        if len(title) < 4:
            continue
        seen.add(url)
        items.append((title, url))
    return items


def collect_column(session, path):
    """翻页采集一个栏目：index.html -> index_2.html -> ... 直到无新条目或 404。"""
    seen, out = set(), []
    for i in range(1, MAX_PAGES + 1):
        p = path if i == 1 else path + "index_" + str(i) + ".html"
        try:
            items = list_articles(session, p)
        except Exception as e:
            print("    第 " + str(i) + " 页停止: " + str(e)[:60])
            break
        fresh = [it for it in items if it[1] not in seen]
        print("    第 " + str(i) + " 页: " + str(len(items)) + " 条（新增 " + str(len(fresh)) + "）")
        if not fresh:
            break
        for _t, u in fresh:
            seen.add(u)
        out.extend(fresh)
        time.sleep(SLEEP)
    return out


def fetch_article(session, url):
    """抓正文：东莞站正文容器是 <div class="article-content" id="zoomcon">"""
    soup = get_soup(session, url)
    box = soup.find("div", id="zoomcon")
    if box is None:
        box = soup.find("div", class_="article-content")
    if box is None:
        return None, None, None

    text = box.get_text(NL, strip=True)
    text = re.sub(NL + "{3,}", NL + NL, text)
    if len(text) < 100:
        return None, None, None

    title = None
    h = soup.find("h1") or soup.find("h2")
    if h:
        title = h.get_text(strip=True)

    page_text = soup.get_text(" ", strip=True)
    m = re.search(r"时间[：:]\s*(\d{4})-(\d{2})-(\d{2})", page_text)
    if m:
        date, year = m.group(0).split("：")[-1].split(":")[-1].strip()[:10], m.group(1)
    else:
        date, year = "", ""

    return title, (text, date, year), None


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR.parent / "manifest_dg.json"

    existing = []
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    seen_urls = {x.get("url") for x in existing}
    print("已有清单: " + str(len(existing)) + " 条（按 URL 去重）")

    session = requests.Session()
    added, skipped = [], 0

    for column, sub, path in COLUMNS:
        print(NL + "=== 栏目: " + column + " / " + sub + "  " + path + " ===")
        try:
            items = collect_column(session, path)
        except Exception as e:
            print("  栏目失败: " + str(e))
            continue

        for title, url in items:
            if url in seen_urls:
                skipped += 1
                continue
            seen_urls.add(url)
            try:
                t, body, _ = fetch_article(session, url)
            except Exception as e:
                print("  [跳过] " + title[:24] + " -> " + str(e)[:50])
                time.sleep(SLEEP)
                continue
            if not body:
                print("  [无正文] " + title[:24])
                time.sleep(SLEEP)
                continue

            text, date, year = body
            title = title or t or "未命名"
            stem = "东莞_市人社局_" + (year or "未知") + "_" + clean_title(title)
            fp = OUT_DIR / (stem + ".md")
            k = 2
            while fp.exists():                     # 标题撞车时加后缀，不覆盖
                fp = OUT_DIR / (stem + "_" + str(k) + ".md")
                k += 1

            head = NL.join([
                "---",
                "city: 东莞",
                "issuer: 市人社局",
                "year: " + (year or ""),
                "doc_title: " + title,
                "column: " + column,
                "sub_column: " + sub,
                "publish_date: " + date,
                "source_url: " + url,
                "---",
                "",
                "# " + title,
                "",
                "",
            ])
            fp.write_text(head + text + NL, encoding="utf-8")
            added.append({
                "file": fp.name, "title": title, "column": column, "sub_column": sub,
                "year": year, "date": date, "url": url, "chars": len(text),
            })
            print("  [" + str(len(added)) + "] " + fp.name + "  (" + str(len(text)) + " 字)")
            time.sleep(SLEEP)

    manifest_path.write_text(
        json.dumps(existing + added, ensure_ascii=False, indent=2), encoding="utf-8")
    print(NL + "完成：新增 " + str(len(added)) + " 个文件（跳过重复 " + str(skipped) + " 条）")
    print("清单总计 " + str(len(existing) + len(added)) + " 条 -> " + str(manifest_path))


if __name__ == "__main__":
    main()