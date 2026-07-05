#!/usr/bin/env python3
"""
從 Mistral OCR 頁面目錄組裝 EPUB。

用法:
  python3 build_epub.py --pages ./work/pages/ --output ./out/book.epub
  python3 build_epub.py --pages ./work/pages/ --title "書名" --author "作者" -o ./out/book.epub
"""

import os, re, html, sys, argparse, json
from pathlib import Path
from collections import OrderedDict

import ebooklib
from ebooklib import epub

from utils import (
    natural_sort_key,
    clean_ocr_noise,
    auto_detect_metadata_from_markdown,
    is_toc_heading,
    is_metadata_heading,
    text_to_html,
    load_project_meta,
)


# ── 頁面清潔 ──────────────────────────────────────────────────────────

PAGE_NUMBER_RE = re.compile(r'^\d{1,4}$')
PDF_METADATA_KEYWORDS = [
    'Anna\'s Archive', 'DuXiu collection', 'annas-blog.org',
    'filename_decoded', 'pdg_main_pages', 'pdf_generation_missing_pages',
    'header_md5', 'uncompressed_size', 'zip_password', 'total_pixels',
]


def clean_page_markdown(md_text: str) -> str:
    """清理單頁 markdown：去頁碼、過濾 PDF 元數據、去多餘空行。"""
    lines = md_text.split('\n')

    # 過濾 PDF 生成元數據行
    filtered = []
    in_metadata_block = False
    for line in lines:
        if any(kw in line for kw in PDF_METADATA_KEYWORDS):
            in_metadata_block = True
            continue
        if in_metadata_block:
            if line.strip().startswith('}'):
                in_metadata_block = False
            continue
        filtered.append(line)
    lines = filtered

    # 去尾頁碼：末尾獨立數字行
    while lines and PAGE_NUMBER_RE.match(lines[-1].strip()):
        lines.pop()

    text = '\n'.join(lines)
    text = clean_ocr_noise(text)
    return text


# ── 掃描頁面 ──────────────────────────────────────────────────────────

def scan_pages(pages_dir: Path) -> list[dict]:
    """掃描 pages_dir 下的 page-NNNN/ 目錄，回傳排序後的頁面列表。"""
    page_dirs = sorted(
        [d for d in pages_dir.iterdir() if d.is_dir() and d.name.startswith("page-")],
        key=natural_sort_key,
    )
    pages = []
    for pd in page_dirs:
        md_file = pd / "markdown.md"
        if not md_file.exists():
            continue
        md_text = clean_page_markdown(md_file.read_text(encoding="utf-8"))

        images = {}
        images_dir = pd / "images"
        if images_dir.exists():
            for img_file in images_dir.iterdir():
                if img_file.suffix.lower() in (".jpeg", ".jpg", ".png", ".webp", ".gif"):
                    images[img_file.name] = img_file

        tables = {}
        tables_dir = pd / "tables"
        if tables_dir.exists():
            for tbl_file in tables_dir.iterdir():
                if tbl_file.suffix.lower() == ".html":
                    tables[tbl_file.name] = tbl_file.read_text(encoding="utf-8")

        meta = {}
        meta_file = pd / "meta.json"
        if meta_file.exists():
            meta = json.loads(meta_file.read_text(encoding="utf-8"))

        needs_correction = (pd / "needs_correction.txt").exists()

        pages.append({
            "dir": pd,
            "index": len(pages),
            "markdown": md_text,
            "images": images,
            "tables": tables,
            "meta": meta,
            "needs_correction": needs_correction,
        })
    return pages


# ── 章節偵測 ──────────────────────────────────────────────────────────

def merge_all_markdown(pages: list[dict]) -> str:
    """將所有頁面的 markdown 合併為一個完整文本（頁間加換頁標記）。"""
    parts = []
    for i, page in enumerate(pages):
        md = page["markdown"].strip()
        if md:
            parts.append(md)
    return "\n\n<!-- page-break -->\n\n".join(parts)


def detect_chapters_from_pages(pages: list[dict], book_title: str = "",
                                chapter_level: int = 0,
                                strategy: str = "heading",
                                pattern: str = "",
                                skip_headings: list[str] = None) -> list[dict]:
    """從頁面列表偵測章節。

    strategy: "heading" (from #/## 標題), "page" (每頁一章), "regex" (正則分章)
    pattern: 僅 regex 策略使用，正則表達式匹配章節標題
    chapter_level: 0=自動, 1=只 H1, 2=H1+H2 (僅 heading 策略)
    skip_headings: 要從導航中排除的標題前綴列表（如 ["BLUF", "CONOP"]）
    回傳: [{title, start_page, end_page, body, page_indices, images, tables}]
    """
    if not pages:
        return []

    all_lines = []
    page_breaks = []  # (line_index, page_index)
    for pi, page in enumerate(pages):
        md = page["markdown"].strip()
        if md:
            lines = md.split('\n')
            all_lines.extend(lines)
        page_breaks.append((len(all_lines), pi))

    full_text = '\n'.join(all_lines)

    # 自動檢測章節層級
    if chapter_level == 0:
        h1_count = 0
        h2_count = 0
        for line in all_lines:
            ls = line.strip()
            if re.match(r'^#\s+(.+)$', ls):
                h = re.match(r'^#\s+(.+)$', ls).group(1).strip()
                if not is_toc_heading(h) and not is_metadata_heading(h, book_title):
                    h1_count += 1
            elif re.match(r'^##\s+(.+)$', ls):
                h = re.match(r'^##\s+(.+)$', ls).group(1).strip()
                if not is_toc_heading(h) and not is_metadata_heading(h, book_title):
                    h2_count += 1
        if h1_count >= 3:
            chapter_level = 1
        elif h2_count > 0 and h1_count <= 2:
            chapter_level = 2
        elif h1_count == 0 and h2_count > 0:
            chapter_level = 2
        else:
            chapter_level = 1

    heading_pattern = r'^#{1,%d}\s+(.+)$' % chapter_level

    # ── 策略分支 ────────────────────────────────────────────────────
    if strategy == "page":
        # 每頁一章
        chapters = []
        for pi, page in enumerate(pages):
            lines = page["markdown"].strip().split('\n')
            first_line = lines[0].strip() if lines else ""
            title = first_line[:50] if first_line else f"第 {pi+1} 頁"
            content = page["markdown"].strip()
            chapters.append({
                "title": title,
                "start_page": pi,
                "end_page": pi,
                "body": content,
                "page_indices": [pi],
                "images": [{"page": pi, "name": k, "path": v} for k, v in page["images"].items()],
                "tables": [{"page": pi, "name": k, "content": v} for k, v in page["tables"].items()],
            })
        return chapters

    if strategy == "regex" and pattern:
        # 正則匹配分章
        chapters = []
        lines = all_lines
        full = '\n'.join(lines)
        matches = list(re.finditer(pattern, full))

        match_positions = []
        for m in matches:
            line_num = full[:m.start()].count('\n')
            match_positions.append((line_num, m.group(0)))

        if len(match_positions) >= 2:
            # 去重：相同標題出現多次時取最後一次（跳過目錄表的匹配）
            seen = {}
            for idx, (ln, title) in enumerate(match_positions):
                seen[title] = idx
            unique_indices = sorted(seen.values())
            match_positions = [match_positions[i] for i in unique_indices]

            for idx, (ln, title) in enumerate(match_positions):
                end_ln = match_positions[idx+1][0] if idx+1 < len(match_positions) else len(lines)
                body = '\n'.join(lines[ln+1:end_ln])
                sp, ep = 0, 0
                for pb_line, pb_page in page_breaks:
                    if pb_line <= ln:
                        sp = pb_page
                    if pb_line <= end_ln:
                        ep = pb_page
                pi = list(range(sp, ep+1))
                chapter_images = []
                chapter_tables = []
                for pgi in pi:
                    page = pages[pgi]
                    for name, fpath in page["images"].items():
                        chapter_images.append({"page": pgi, "name": name, "path": fpath})
                    for name, content in page["tables"].items():
                        chapter_tables.append({"page": pgi, "name": name, "content": content})
                chapters.append({
                    "title": title,
                    "start_page": sp, "end_page": ep,
                    "body": body,
                    "page_indices": pi,
                    "images": chapter_images,
                    "tables": chapter_tables,
                })
            return chapters

    # ── 預設 heading 策略 ────────────────────────────────────────────────
    headings = []
    toc_range = set()
    in_toc = False
    for i, line in enumerate(all_lines):
        ls = line.strip()
        if re.match(r'^#\s*目\s*[次录錄]', ls):
            in_toc = True
            toc_range.add(i)
            continue
        if in_toc:
            hm = re.match(r'^(#+)\s(.+)', ls)
            if hm:
                text = hm.group(2).strip()
                if not is_toc_heading(text):
                    lookahead = '\n'.join(
                        all_lines[j].rstrip() for j in range(i+1, min(i+20, len(all_lines)))
                    )
                    non_heading = '\n'.join(
                        l for l in lookahead.split('\n')
                        if l.strip() and not l.strip().startswith('#')
                    )
                    has_toc = bool(re.search(r'\.{3,}\s*\(?\d+', non_heading))
                    if not has_toc and len(lookahead.strip()) > 100:
                        in_toc = False
                        continue
            toc_range.add(i)

    for i, line in enumerate(all_lines):
        if i in toc_range:
            continue
        ls = line.strip()
        m = re.match(heading_pattern, ls)
        if not m:
            continue
        heading = m.group(1).strip()
        heading_clean = heading.strip('* ')
        if is_metadata_heading(heading, book_title):
            continue
        if len(heading_clean) <= 1:
            continue
        headings.append((i, heading_clean))

    # 合併連續 H1（同一頁的書名分段、無正文間隔）
    merged = []
    for item in headings:
        if merged:
            last_line, last_title = merged[-1]
            gap_lines = all_lines[last_line + 1:item[0]]
            gap_text = ''.join(gap_lines).strip()
            is_same_page = True
            has_meaningful_text = len(gap_text) > 60 or any(
                l.strip() and not l.strip().startswith('#') for l in gap_lines
            )
            if not has_meaningful_text:
                merged[-1] = (last_line, last_title + item[1])
                continue
        merged.append(item)
    headings = merged

    # 使用者指定跳過的標題（支持精確匹配和前綴匹配）
    skip_patterns = []
    for s in (skip_headings or []):
        s = s.strip()
        if s.endswith('*'):
            skip_patterns.append(('prefix', s.rstrip('*').rstrip(':')))
        else:
            skip_patterns.append(('exact', s.rstrip(':')))
    def _should_skip(title: str) -> bool:
        ct = title.strip().rstrip(':')
        for mode, pat in skip_patterns:
            if mode == 'exact' and ct == pat:
                return True
            if mode == 'prefix' and ct.startswith(pat):
                return True
        return False
    headings = [(ln, t) for ln, t in headings if not _should_skip(t)]

    if not headings:
        return [{
            "title": book_title or "全文",
            "start_page": 0,
            "end_page": len(pages) - 1,
            "body": full_text,
            "page_indices": list(range(len(pages))),
            "images": [],
            "tables": [],
        }]

    chapters = []
    for idx, (start_line, title) in enumerate(headings):
        end_line = headings[idx + 1][0] if idx + 1 < len(headings) else len(all_lines)
        body_lines = all_lines[start_line + 1:end_line]
        body = '\n'.join(body_lines)

        # 映射到頁面範圍
        start_page = 0
        end_page = len(pages) - 1
        for pb_line, pb_page in page_breaks:
            if pb_line <= start_line:
                start_page = pb_page
            if pb_line <= end_line:
                end_page = pb_page
        page_indices = list(range(start_page, end_page + 1))

        # 收集此章節範圍內的圖片和表格
        chapter_images = []
        chapter_tables = []
        for pi in page_indices:
            page = pages[pi]
            for name, fpath in page["images"].items():
                chapter_images.append({"page": pi, "name": name, "path": fpath})
            for name, content in page["tables"].items():
                chapter_tables.append({"page": pi, "name": name, "content": content})

        chapters.append({
            "title": title,
            "start_page": start_page,
            "end_page": end_page,
            "body": body,
            "page_indices": page_indices,
            "images": chapter_images,
            "tables": chapter_tables,
        })

    return chapters


# ── EPUB 組裝 ─────────────────────────────────────────────────────────

def build_epub(pages_dir: Path, output: str, meta: dict,
               chapter_level: int = 0,
               chapter_strategy: str = "heading",
               chapter_pattern: str = "",
               skip_headings: list[str] = None):
    """從 OCR 頁面目錄組裝 EPUB。"""
    pages = scan_pages(pages_dir)
    if not pages:
        print("Error: 沒有找到頁面")
        return False

    print(f"掃描到 {len(pages)} 頁")

    # 低信度檢查
    low_conf_path = pages_dir / "low_conf_pages.json"
    if low_conf_path.exists():
        low_conf = json.loads(low_conf_path.read_text(encoding="utf-8"))
        if low_conf:
            print(f"Warning: {len(low_conf)} 頁低信度，建議先校正再組裝")
            print(f"  低信度頁面: {', '.join(str(p['page']) for p in low_conf)}")

    # 檢查是否有頁面仍需校正
    pages_needing_correction = [p for p in pages if p["needs_correction"]]
    if pages_needing_correction:
        print(f"Warning: {len(pages_needing_correction)} 頁標記了 needs_correction")
        print(f"  頁面: {', '.join(str(p['index']) for p in pages_needing_correction)}")

    # 合併 markdown 用於元數據檢測
    full_text = merge_all_markdown(pages)

    # 自動檢測元數據
    if not meta.get("title"):
        detected = auto_detect_metadata_from_markdown(full_text)
        for k in meta:
            if not meta[k]:
                meta[k] = detected.get(k, '')

    # 章節偵測（從 markdown 標題 / page / regex 實時檢測）
    chapters = detect_chapters_from_pages(
        pages, meta.get("title", ""), chapter_level,
        strategy=chapter_strategy, pattern=chapter_pattern,
        skip_headings=skip_headings,
    )
    if not chapters:
        print("Error: 無法偵測章節")
        return False

    only_one = len(chapters) <= 1
    if only_one and chapter_strategy == "heading":
        print("Tip: 只用標題偵測到 1 章，可試 --chapter-strategy page 或 --chapter-strategy regex '正則'")

    # 單章時用書名（若有）覆蓋合併後的章名
    if len(chapters) == 1 and meta.get("title"):
        chapters[0]["title"] = meta["title"]

    print(f"偵測到 {len(chapters)} 章")
    for i, ch in enumerate(chapters):
        body_len = len(ch["body"])
        print(f"  {i+1}. {ch['title']}: {body_len} chars, pages {ch['start_page']}-{ch['end_page']}")

    # ── 建立 EPUB ────────────────────────────────────────────────────
    book = epub.EpubBook()
    book.set_identifier(str(hash(str(pages_dir)) % (10**13)))
    book.set_title(meta.get("title", "Untitled"))
    book.set_language("zh-CN")
    if meta.get("author"):
        book.add_author(meta["author"])
    if meta.get("translator"):
        book.add_metadata("DC", "contributor", meta["translator"])
    if meta.get("publisher"):
        book.add_metadata("DC", "publisher", meta["publisher"])

    # CSS
    css = """
    body { font-family: 'Noto Serif CJK SC', 'SimSun', 'Source Han Serif SC', serif;
           line-height: 1.8; font-size: 0.9em; margin: 1em; text-align: justify; }
    h1 { text-align: center; font-size: 1.4em; margin: 2em 0 1em; }
    h2 { font-size: 1.15em; margin: 1.5em 0 0.8em; }
    h3 { font-size: 1.05em; margin: 1.2em 0 0.6em; }
    p { text-indent: 2em; margin: 0.3em 0; }
    sup { font-size: 0.75em; }
    .cover-page { text-align: center; margin-top: 20%; }
    .cover-page h1 { font-size: 2em; }
    .cover-page .sub { font-size: 1.2em; margin-top: 1em; }
    .cover-page .author { font-size: 1.1em; margin-top: 2em; }
    table { border-collapse: collapse; width: 100%; margin: 1em 0; font-size: 0.85em; }
    th, td { border: 1px solid #999; padding: 4px 6px; text-align: left; }
    th { background: #f0f0f0; }
    img { max-width: 100%; height: auto; margin: 1em 0; }
    pre { background: #f5f5f5; padding: 0.5em; font-size: 0.85em; overflow-x: auto; }
    code { font-family: 'Courier New', monospace; font-size: 0.85em; }
    """
    nav_css = epub.EpubItem(uid="style_nav", file_name="style/nav.css",
                            media_type="text/css", content=css)
    book.add_item(nav_css)

    spine_order = ["nav"]
    toc_map = []

    # 封面
    title_str = meta.get("title", "") or pages_dir.parent.name
    cover_chapter = epub.EpubHtml(title="封面", file_name="cover.xhtml", lang="zh-CN")
    cover_html = f'''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"><head>
<meta charset="utf-8"/><link rel="stylesheet" type="text/css" href="style/nav.css"/></head>
<body><div class="cover-page">
<h1>{html.escape(title_str)}</h1>'''
    if meta.get("author"):
        cover_html += f'\n<p class="author">{html.escape(meta["author"])} 著</p>'
    if meta.get("translator"):
        cover_html += f'\n<p class="sub">{html.escape(meta["translator"])} 译</p>'
    cover_html += '\n</div></body></html>'
    cover_chapter.content = cover_html
    book.add_item(cover_chapter)
    spine_order.append(cover_chapter)

    # 收集所有用到的圖片（去重）
    all_images = {}
    for ch in chapters:
        for img in ch.get("images", []):
            name = img["name"]
            if name not in all_images:
                image_item = epub.EpubImage()
                image_item.file_name = f"images/{name}"
                image_item.media_type = _guess_image_mime(name)
                image_item.content = img["path"].read_bytes()
                book.add_item(image_item)
                all_images[name] = f"images/{name}"

    # 處理各章
    for i, ch in enumerate(chapters):
        title = ch["title"]
        body_text = ch["body"]

        # 移除 body 中的標題行
        body_clean = re.sub(r'^#\s+.+$\n?', '', body_text, flags=re.MULTILINE).strip()
        body_clean = re.sub(r'^##\s+.+$\n?', '', body_clean, flags=re.MULTILINE).strip()

        # 替換表格引用 [tbl-N.html](tbl-N.html) → 實際 HTML（內聯）
        for tbl in ch.get("tables", []):
            tbl_ref = f'[{tbl["name"]}]({tbl["name"]})'
            body_clean = body_clean.replace(tbl_ref, tbl["content"])

        # 處理 markdown → HTML
        body_html = _page_markdown_to_html(body_clean, all_images)

        safe_title = re.sub(r'[^\w\u4e00-\u9fff_-]', '_', title)
        safe_title = re.sub(r'_+', '_', safe_title).strip('_') or f'chapter_{i+1:03d}'
        file_name = f'chap_{i+1:03d}_{safe_title}.xhtml'

        full_html = f'''<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml"><head><meta charset="utf-8"/>
<link rel="stylesheet" type="text/css" href="style/nav.css"/>
<title>{html.escape(title)}</title>
</head>
<body>
<h1>{html.escape(title)}</h1>
{body_html}
</body></html>'''

        chapter = epub.EpubHtml(title=title, file_name=file_name, lang="zh-CN")
        chapter.content = full_html
        book.add_item(chapter)
        spine_order.append(chapter)
        toc_map.append(chapter)

    book.toc = toc_map
    book.spine = spine_order
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(out_path), book)
    print(f"\nEPUB 建立成功: {out_path}")
    print(f"檔案大小: {out_path.stat().st_size / 1024:.1f} KB")
    return out_path


def _guess_image_mime(name: str) -> str:
    ext = Path(name).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
    }.get(ext, "image/jpeg")


def _page_markdown_to_html(markdown: str, image_map: dict) -> str:
    """將 Mistral OCR 的 page markdown 轉為 HTML。

    處理:
      - 圖片引用: ![img-0.jpeg](img-0.jpeg) → <img src="images/...">
      - 表格引用: [tbl-0.html](tbl-0.html) → 直接嵌入內容
      - 標準 markdown: **粗體** *斜體* 等
    """
    lines = markdown.split('\n')
    html_parts = []
    in_para = False

    for line in lines:
        line_orig = line
        line = line.strip()
        if not line:
            if in_para:
                html_parts.append('</p>')
                in_para = False
            continue

        # 圖片行：![...](...)
        img_match = re.match(r'^!\[.*?\]\((.+?)\)$', line)
        if img_match:
            if in_para:
                html_parts.append('</p>')
                in_para = False
            img_name = img_match.group(1)
            if img_name in image_map:
                html_parts.append(f'<img src="{image_map[img_name]}" alt=""/>')
            html_parts.append('</p>' if in_para else '')
            in_para = False
            continue

        # 內聯圖片
        def _replace_img(m):
            name = m.group(1)
            if name in image_map:
                return f'<img src="{image_map[name]}" alt=""/>'
            return m.group(0)
        line = re.sub(r'!\[.*?\]\((.+?)\)', _replace_img, line)

        # HTML 標籤直接輸出（表格、塊級、行內 HTML 標籤）
        if re.match(r'\s*</?(table|tr|td|th|thead|tbody|tfoot|div|pre|blockquote|ul|ol|li|hr|br|p|h[1-6])', line):
            if in_para:
                html_parts.append('</p>')
                in_para = False
            html_parts.append(line)
            continue

        # 水平線
        if re.match(r'^[-*]{3,}$', line):
            if in_para:
                html_parts.append('</p>')
                in_para = False
            html_parts.append('<hr/>')
            continue

        # 標題（h2-h6，h1 已在 body_clean 移除但有殘留的話也保留）
        hm = re.match(r'^(#{2,6})\s+(.+)$', line)
        if hm:
            if in_para:
                html_parts.append('</p>')
                in_para = False
            level = len(hm.group(1))
            text = hm.group(2).strip()
            html_parts.append(f'<h{level}>{html.escape(text)}</h{level}>')
            continue

        # 列表（簡單支援）
        lm = re.match(r'^[\-\*]\s+(.+)$', line)
        if lm and not in_para:
            html_parts.append(f'<li>{html.escape(lm.group(1))}</li>')
            continue

        # 引用
        bqm = re.match(r'^>\s+(.+)$', line)
        if bqm:
            if not in_para:
                html_parts.append('<blockquote>')
            html_parts.append(f'<p>{html.escape(bqm.group(1))}</p>')
            in_para = True
            continue

        # 段落
        escaped = html.escape(line, quote=False)
        escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
        escaped = re.sub(r'\*(.+?)\*', r'<em>\1</em>', escaped)
        escaped = re.sub(r'`(.+?)`', r'<code>\1</code>', escaped)

        if not in_para:
            html_parts.append('<p>')
            in_para = True
            html_parts.append(escaped)
        else:
            html_parts.append(escaped)

    if in_para:
        html_parts.append('</p>')

    return '\n'.join(html_parts)


# ── CLI ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="從 Mistral OCR 頁面目錄組裝 EPUB")
    ap.add_argument("--pages", required=True, help="OCR 頁面目錄（含 page-NNNN/）")
    ap.add_argument("--output", "-o", required=True, help="輸出 EPUB 路徑")
    ap.add_argument("--title", "-t", default="", help="書名（自動檢測可覆蓋）")
    ap.add_argument("--author", "-a", default="", help="作者（自動檢測可覆蓋）")
    ap.add_argument("--translator", default="", help="譯者")
    ap.add_argument("--publisher", "-p", default="", help="出版社")
    ap.add_argument("--chapter-level", "-l", type=int, default=0,
                    choices=[0, 1, 2],
                    help="章節標題層級: 0=自動, 1=只 H1, 2=H1+H2")
    ap.add_argument("--chapter-strategy", default="heading",
                    choices=["heading", "page", "regex"],
                    help="章節偵測策略: heading(標題), page(每頁一章), regex(正則分章)")
    ap.add_argument("--chapter-pattern", default="",
                    help="正則分章模式（僅 --chapter-strategy regex 時使用）")
    ap.add_argument("--skip-heading", action="append", default=[],
                    help="從導航中排除的標題前綴（可多次使用，如 --skip-heading BLUF --skip-heading CONOP）")
    args = ap.parse_args()

    pages_dir = Path(args.pages)
    if not pages_dir.is_dir():
        print(f"Error: {args.pages} 不是目錄")
        sys.exit(1)

    # 嘗試從 project_meta.json 讀取元數據
    project_meta = load_project_meta(pages_dir.parent)
    meta = {
        "title": args.title or project_meta.get("title", ""),
        "author": args.author or project_meta.get("author", ""),
        "translator": args.translator,
        "publisher": args.publisher,
    }

    print(f"組裝 EPUB 從: {pages_dir}")
    if args.chapter_strategy != "heading":
        print(f"章節策略: {args.chapter_strategy}" +
              (f" / 模式: {args.chapter_pattern}" if args.chapter_pattern else ""))
    if args.skip_heading:
        print(f"跳過標題: {', '.join(args.skip_heading)}")
    result = build_epub(pages_dir, args.output, meta,
                        chapter_level=args.chapter_level,
                        chapter_strategy=args.chapter_strategy,
                        chapter_pattern=args.chapter_pattern,
                        skip_headings=args.skip_heading)
    if not result:
        sys.exit(1)


if __name__ == "__main__":
    main()
