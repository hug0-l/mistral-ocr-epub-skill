#!/usr/bin/env python3
"""
Mistral OCR 全書掃描管道（JSON workflow）

一次 API 調用取得：逐頁 markdown + 結構化章節目錄（document_annotation）。

用法:
  python3 ocr_book.py --input book.pdf --save-pages ./work/pages
  python3 ocr_book.py --input book.pdf --save-pages ./work/pages --correct
  python3 ocr_book.py --input book.pdf --save-pages ./work/pages --pages 0-10
"""

import os, sys, json, base64, argparse, time, re, mimetypes
from pathlib import Path

from utils import extract_pdf_metadata


SUPPORTED_DOC_TYPES = {
    ".pdf":  ("document_url", "application/pdf"),
    ".docx": ("document_url", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
    ".pptx": ("document_url", "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
    ".odt":  ("document_url", "application/vnd.oasis.opendocument.text"),
    ".png":  ("image_url", "image/png"),
    ".jpg":  ("image_url", "image/jpeg"),
    ".jpeg": ("image_url", "image/jpeg"),
    ".avif": ("image_url", "image/avif"),
    ".tiff": ("image_url", "image/tiff"),
    ".tif":  ("image_url", "image/tiff"),
    ".bmp":  ("image_url", "image/bmp"),
}


BOOK_SCHEMA = {
    "name": "book_metadata",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "author": {"type": "string"},
            "translator": {"type": "string"},
            "publisher": {"type": "string"},
            "genre": {
                "type": "string",
                "description": "书籍体裁：novel / textbook / literature / poetry / reference / academic / biography / essay / tech_manual / other",
            },
            "front_matter_pages": {
                "type": "integer",
                "description": "正文开始前的 OCR page index（0-based），0 = 正文从第一页开始",
            },
            "chapters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "章节标题"},
                        "page_start": {"type": "integer", "description": "起始 OCR page index (0-based)"},
                        "page_end": {"type": "integer", "description": "结束 OCR page index (0-based)"},
                    },
                    "required": ["title", "page_start", "page_end"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["title", "author", "chapters", "front_matter_pages"],
        "additionalProperties": False,
    },
}

ANNOTATION_PROMPT = """Analyze this book and extract its structure. Output title, author, translator, publisher, genre, and chapters.

This could be any type of book: a novel, textbook, academic paper, poetry collection, reference work, biography, essay collection, or technical manual. Adapt to the actual structure you see.

Chapter detection rules by genre:
- Novels / Literature: look for "第X章", "Chapter X", "Part X", date markers, or section breaks
- Textbooks / Manuals: look for units, modules, chapters, lessons, sections (§)
- Academic papers: Abstract, Introduction, Methods/ Methodology, Results, Discussion, Conclusion, References
- Poetry: each poem or canto is a chapter; if unmarked, use page breaks
- Reference / Encyclopedia: each letter or major topic entry is a chapter
- Biography: chronological periods, parts, or chapter numbers
- Essay collections: each essay is a chapter

front_matter_pages: the 0-based OCR page index where real content starts. Skip cover, TOC, copyright, preface, foreword — anything before the main body.

chapters: list every chapter/section/essay/poem with its exact OCR page_start and page_end. Be comprehensive — include ALL content-bearing divisions. If unsure, prefer splitting more rather than merging.

Output ONLY structure metadata JSON. Do NOT output any content text."""


def detect_doc_type(filepath: str) -> tuple[str, str]:
    """返回 (api_type, mime_type) — 根据扩展名判断文档类型。"""
    ext = Path(filepath).suffix.lower()
    if ext in SUPPORTED_DOC_TYPES:
        return SUPPORTED_DOC_TYPES[ext]
    # fallback: try mimetypes
    mt, _ = mimetypes.guess_type(filepath)
    if mt and mt.startswith("image/"):
        return ("image_url", mt)
    return ("document_url", "application/pdf")


def ocr_with_structure(filepath: str, pages: str = "",
                        include_images: bool = False) -> tuple[list, str]:
    """一次 API 調用取得逐頁 markdown + 結構化 JSON。"""
    from mistralai.client import Mistral
    from mistralai.client.models import JSONSchema, ResponseFormat

    api_key = os.environ.get("MISTRAL_API_KEY")
    if not api_key:
        print("Error: 請設定 MISTRAL_API_KEY 環境變數")
        sys.exit(1)

    client = Mistral(api_key=api_key)

    doc_type, mime = detect_doc_type(filepath)
    with open(filepath, "rb") as f:
        content_bytes = f.read()
    content_b64 = base64.b64encode(content_bytes).decode("ascii")

    if doc_type == "image_url":
        data_url = f"data:{mime};base64,{content_b64}"
        document = {"type": "image_url", "image_url": data_url}
    else:
        data_url = f"data:{mime};base64,{content_b64}"
        document = {"type": "document_url", "document_url": data_url}

    schema = JSONSchema(
        name="book_metadata",
        strict=True,
        schema_definition=BOOK_SCHEMA["schema"],
    )
    fmt = ResponseFormat(type="json_schema", json_schema=schema)

    payload_pages = None
    if pages:
        payload_pages = pages

    print(f"OCR + 結構分析...（{pages or '全部頁面'}，類型: {mime}）")
    t0 = time.time()
    resp = client.ocr.process(
        model="mistral-ocr-latest",
        document=document,
        include_image_base64=include_images,
        include_blocks=False,
        table_format="html",
        pages=payload_pages,
        document_annotation_format=fmt,
        document_annotation_prompt=ANNOTATION_PROMPT,
    )
    elapsed = time.time() - t0
    pages_count = len(resp.pages)
    print(f"完成: {pages_count} 頁, 耗時 {elapsed:.0f}s")

    pages_data = []
    for p in resp.pages:
        page = {"index": p.index, "markdown": p.markdown or "", "images": [], "tables": []}
        for img in (p.images or []):
            img_dict = {"id": img.id}
            if img.image_base64:
                img_dict["image_base64"] = img.image_base64
            page["images"].append(img_dict)
        for tbl in (p.tables or []):
            page["tables"].append({"id": tbl.id, "content": tbl.content or ""})
        pages_data.append(page)

    annot_str = resp.document_annotation or "{}"
    return pages_data, annot_str, elapsed


def save_pages(pages_data: list, pages_dir: Path, min_confidence: float = 0.0):
    """逐頁保存 markdown。"""
    pages_dir.mkdir(parents=True, exist_ok=True)
    low_conf = []
    for pg in pages_data:
        idx = pg["index"]
        pd = pages_dir / f"page-{idx:04d}"
        pd.mkdir(exist_ok=True)
        (pd / "markdown.md").write_text(pg.get("markdown", ""), encoding="utf-8")
        imgs_dir = pd / "images"
        if imgs_dir.exists():
            for f in imgs_dir.iterdir():
                if f.suffix.lower() in (".jpeg", ".jpg", ".png"):
                    f.unlink()
        imgs_dir.mkdir(exist_ok=True)
        for img in pg.get("images", []):
            b64 = img.get("image_base64", "")
            if b64:
                try:
                    if b64.startswith("data:"):
                        b64 = b64.split(",", 1)[1]
                    (imgs_dir / img["id"]).write_bytes(base64.b64decode(b64))
                except Exception:
                    pass
        tables_dir = pd / "tables"
        tables_dir.mkdir(exist_ok=True)
        for tbl in pg.get("tables", []):
            if tbl.get("content"):
                (tables_dir / tbl["id"]).write_text(tbl["content"], encoding="utf-8")
    print(f"保存 {len(pages_data)} 頁 -> {pages_dir}")
    return low_conf


def flag_correction(pages_dir: Path, low_conf: list):
    for item in low_conf:
        (Path(item["path"]) / "needs_correction.txt").write_text(
            "此頁 OCR 信度偏低，需要 agent 校正。\n", encoding="utf-8"
        )


def ocr_pipeline(input_path: str, pages_dir: Path, output_path: str = "",
                 title: str = "", author: str = "",
                 pages: str = "", correct: bool = False,
                 min_confidence: float = 0.0,
                 no_auto_meta: bool = False,
                 genre: str = "",
                 extract_images: bool = False):
    """完整管道：OCR + 結構化 → 保存 → project_meta.json。"""
    print(f"{'='*60}")
    print(f"  Mistral OCR 全書掃描 (JSON workflow)")
    print(f"{'='*60}")
    print(f"  輸入: {input_path}")

    meta_title, meta_author = title, author
    if not no_auto_meta:
        ext = Path(input_path).suffix.lower()
        if ext == ".pdf":
            pdf_meta = extract_pdf_metadata(input_path)
            meta_title = meta_title or pdf_meta.get("title", "")
            meta_author = meta_author or pdf_meta.get("author", "")

    # 体裁提示注入 prompt
    if genre:
        global ANNOTATION_PROMPT
        ANNOTATION_PROMPT += f"\n\nThe genre is \"{genre}\". Adapt chapter detection to this genre's typical structure."

    pages_data, annot_str, elapsed = ocr_with_structure(input_path, pages=pages, include_images=extract_images)

    # Parse annotation
    structure = {}
    try:
        structure = json.loads(annot_str) if annot_str else {}
    except json.JSONDecodeError:
        print("  [warn] 結構化 JSON 解析失敗，將使用 heading 策略")

    # Merge metadata: annotation > CLI > PDF metadata
    annot_title = structure.get("title", "")
    annot_author = structure.get("author", "")
    final_title = title or annot_title or meta_title or Path(input_path).stem
    final_author = author or annot_author or meta_author or ""

    save_pages(pages_data, pages_dir, min_confidence)

    # Save structure for build_epub
    project_meta = {
        "title": final_title,
        "author": final_author,
        "translator": structure.get("translator", ""),
        "publisher": structure.get("publisher", ""),
        "source": input_path,
        "pages_processed": len(pages_data),
        "genre": structure.get("genre", genre),
        "front_matter_pages": structure.get("front_matter_pages", 0),
        "chapters": structure.get("chapters", []),
        "elapsed_seconds": elapsed,
    }
    meta_path = pages_dir.parent / "project_meta.json"
    meta_path.write_text(json.dumps(project_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'='*60}")
    print(f"  OCR 完成！")
    print(f"  頁數: {len(pages_data)} | 章節: {len(structure.get('chapters', []))}")
    print(f"  書名: {final_title} | 作者: {final_author}")
    print(f"  元數據: {meta_path}")
    print(f"{'='*60}")

    if output_path:
        print(f"\n下一步: build_epub.py --pages {pages_dir} --output {output_path}")
    return project_meta


def main():
    ap = argparse.ArgumentParser(description="Mistral OCR 全書掃描 (JSON workflow)")
    ap.add_argument("--input", "-i", required=True)
    ap.add_argument("--output", "-o", default="")
    ap.add_argument("--save-pages", default="")
    ap.add_argument("--title", "-t", default="")
    ap.add_argument("--author", "-a", default="")
    ap.add_argument("--genre", default="",
                    help="书籍体裁提示：novel / textbook / literature / poetry / reference / academic / biography / essay / tech_manual")
    ap.add_argument("--pages", default="")
    ap.add_argument("--extract-images", action="store_true",
                    help="提取内嵌图片（默认关闭以节省成本/速度；开启后 EPUB 体积增大但含原书插图）")
    ap.add_argument("--correct", action="store_true")
    ap.add_argument("--min-confidence", type=float, default=0.0)
    ap.add_argument("--no-auto-meta", action="store_true")
    args = ap.parse_args()

    if not os.environ.get("MISTRAL_API_KEY"):
        print("Error: 請設定 MISTRAL_API_KEY")
        sys.exit(1)

    pages_dir = Path(args.save_pages) if args.save_pages else Path("pages")
    pages_dir = pages_dir.resolve()

    ocr_pipeline(
        input_path=args.input,
        pages_dir=pages_dir,
        output_path=args.output,
        title=args.title,
        author=args.author,
        pages=args.pages,
        correct=args.correct,
        min_confidence=args.min_confidence,
        no_auto_meta=args.no_auto_meta,
        genre=args.genre,
    )


if __name__ == "__main__":
    main()
