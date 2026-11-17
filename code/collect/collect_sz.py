"""
深圳人社局政策文件采集脚本
--------------------------------------------------
数据来源：深圳市人力资源和社会保障局   https://hrss.sz.gov.cn
接口：TRS 站点自带 JSON —— 栏目元数据 + 文章正文
    https://hrss.sz.gov.cn/postmeta/i/30687.json   政策法规
    https://hrss.sz.gov.cn/postmeta/i/30688.json   政策解读

【为什么用 curl 而不是 requests】
深圳站的 TLS 配置特殊：Python 的 urllib / requests 走 OpenSSL 握手会直接报
    ssl.SSLError: [SSL: BAD_ECPOINT] bad ecpoint
放宽 cipher（SECLEVEL=1）也无效。而 Windows 自带的 curl.exe 走 Schannel 可以正常握手。
所以这里用 subprocess 调 curl.exe 拿数据，再用 BeautifulSoup 解析。

【为什么要重试】
该站在连续请求时会瞬时限流，表现为下载被截断（只拿到几百 KB）。
所以下载后必须校验：文件大小 + JSON 能否解析成功。

【2026-10-26 修订】原版只采了 30687 一个栏目、MAX_DOCS=90 硬上限、且用标题关键词过滤，
结果 217 篇只留了 81 篇（37%）。实测各子栏目的丢失率：
    社会保障 65 -> 2（97% 丢失，因为正则里写「社保」而标题写「社会保险/养老保险」）
    劳动关系 31 -> 0（100% 丢失）
    人才服务 86 -> 48
    就业创业 33 -> 31
并且**整栏目漏采**：publish_count=89 的「政策解读」(30688) 一篇都没采。
本版：① 采 30687 + 30688 两个栏目 ② 去掉 MAX_DOCS 上限 ③ 去掉标题关键词过滤
      ④ 按 URL 段写 sub_column 元数据 ⑤ 清单增量合并（原版会覆盖清单，是 bug）

依赖：beautifulsoup4 + lxml + Windows 自带 curl.exe
运行：python collect_sz.py
"""
import json
import pathlib
import re
import subprocess
import time

from bs4 import BeautifulSoup

NL = chr(10)
BASE = "https://hrss.sz.gov.cn"

COLUMNS = [
    ("政策法规", BASE + "/postmeta/i/30687.json"),
    ("政策解读", BASE + "/postmeta/i/30688.json"),
]
# URL 第 4 段 -> 子栏目名（用于 sub_column 元数据）
SUB_BY_SEG = {"rcfw": "人才服务", "shbx": "社会保障", "jycy": "就业创业",
              "ldgx": "劳动关系", "zcjd": "政策解读"}

OUT_DIR = pathlib.Path(__file__).resolve().parents[2] / "data" / "raw"
TMP = pathlib.Path(__file__).resolve().parent / "_sz_meta.json"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
SLEEP = 0.9
MIN_BYTES = 1000

ILLEGAL = "[\\\\/:*?" + chr(34) + "<>|" + chr(13) + chr(10) + chr(9) + "]"


def curl(url, out=None, retries=3):
    """用 curl.exe 取数据；out 给定时写文件并校验大小，否则返回 stdout 文本。"""
    cmd = ["curl.exe", "-s", "-L", "--max-time", "90", "-A", UA]
    if out:
        cmd += ["-o", str(out)]
    cmd.append(url)
    for _ in range(retries):
        r = subprocess.run(cmd, capture_output=True)
        if out:
            if out.exists() and out.stat().st_size > MIN_BYTES:
                return True
        elif r.stdout:
            return r.stdout.decode("utf-8", "ignore")
        time.sleep(1.5)
    return False if out else ""


def clean(t):
    t = re.sub(ILLEGAL, "", t or "").strip()
    return re.sub(r"\s+", "", t)[:80]


def seg_of(url):
    parts = [x for x in re.sub(r"https?://[^/]+", "", url or "").split("/") if x]
    return parts[3] if len(parts) > 3 else ""


def load_meta(url):
    for attempt in range(1, 6):
        if curl(url, TMP):
            try:
                meta = json.loads(TMP.read_text(encoding="utf-8"))
                print("  元数据 OK（第 " + str(attempt) + " 次，"
                      + str(TMP.stat().st_size) + " 字节）")
                return meta
            except Exception as e:
                print("  第 " + str(attempt) + " 次解析失败: " + str(e)[:60])
        else:
            print("  第 " + str(attempt) + " 次下载失败")
        time.sleep(2)
    return None


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path = OUT_DIR.parent / "manifest_sz.json"

    existing = []
    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
    seen_urls = {x.get("url") for x in existing}
    print("已有清单: " + str(len(existing)) + " 条（按 URL 去重）")

    added, skipped, miss = [], 0, 0

    for column, meta_url in COLUMNS:
        print(NL + "=== 栏目: " + column + "  " + meta_url + " ===")
        meta = load_meta(meta_url)
        if meta is None:
            print("  元数据下载失败，跳过该栏目")
            continue

        arts = meta.get("articles", [])
        print("  文章总数: " + str(len(arts)) + "（publish_count="
              + str(meta.get("category", {}).get("publish_count")) + "）")

        for a in arts:
            url = a.get("url") or ""
            if not url:
                continue
            url = url if url.startswith("http") else BASE + url
            if url in seen_urls:
                skipped += 1
                continue
            seen_urls.add(url)

            title = (a.get("title") or "").strip()
            created = a.get("created_at", "") or ""
            year = created[:4] if created[:4].isdigit() else ""
            seg = seg_of(url)
            sub = SUB_BY_SEG.get(seg, column)

            html = curl(url)
            if not html:
                miss += 1
                print("  [取不到] " + title[:30])
                time.sleep(SLEEP)
                continue
            soup = BeautifulSoup(html, "lxml")
            box = soup.select_one("div.news_cont_d_wrap") or soup.select_one("div.xx_con")
            text = box.get_text(NL, strip=True) if box else ""
            text = re.sub(NL + "{3,}", NL + NL, text)
            if len(text) < 80:
                print("  [无正文] " + title[:30])
                time.sleep(SLEEP)
                continue

            h1 = soup.find("h1")
            title = (h1.get_text(strip=True) if h1 else "") or title
            stem = "深圳_市人社局_" + (year or "未知") + "_" + clean(title)
            fp = OUT_DIR / (stem + ".md")
            k = 2
            while fp.exists():
                fp = OUT_DIR / (stem + "_" + str(k) + ".md")
                k += 1

            head = NL.join([
                "---",
                "city: 深圳",
                "issuer: 市人社局",
                "year: " + (year or ""),
                "doc_title: " + title,
                "column: " + column,
                "sub_column: " + sub,
                "publish_date: " + created[:10],
                "source_url: " + url,
                "---",
                "",
                "# " + title,
                "",
                "",
            ])
            fp.write_text(head + text + NL, encoding="utf-8")
            added.append({"file": fp.name, "title": title, "column": column,
                          "sub_column": sub, "year": year, "date": created[:10],
                          "url": url, "chars": len(text)})
            print("  [" + str(len(added)) + "] " + fp.name + "  (" + str(len(text)) + " 字)")
            time.sleep(SLEEP)

    manifest_path.write_text(
        json.dumps(existing + added, ensure_ascii=False, indent=2), encoding="utf-8")
    TMP.unlink(missing_ok=True)
    print(NL + "完成：新增 " + str(len(added)) + " 个文件（跳过重复 " + str(skipped)
          + "，取不到 " + str(miss) + "）")
    print("清单总计 " + str(len(existing) + len(added)) + " 条 -> " + str(manifest_path))


if __name__ == "__main__":
    main()