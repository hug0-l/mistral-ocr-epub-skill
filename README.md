# mistral-ocr-epub-skill

**opencode / Claude Code skill** — OCR scanned books → structured EPUB via the Mistral OCR API. Universal: no hardcoded book-specific rules.

Install into `~/.config/opencode/skills/mistral-ocr-epub/` or `~/.claude/skills/mistral-ocr-epub/`. The skill activates on phrases like "ocr这本书", "扫书转epub", "mistral ocr", "pdf转epub".

## Features

- **One API call** — gets per-page markdown + metadata (title, author) simultaneously
- **Universal chapter detection** — three strategies: headings (`#`), page breaks, or custom regex
- **No book-specific logic** — chapter boundaries come from real-time markdown analysis
- **Image extraction** — optionally include embedded illustrations (431 images from a 250-page book)
- **Multi-format input** — PDF, DOCX, PPTX, ODT, PNG, JPEG, AVIF, TIFF, BMP
- **Auto-cleanup** — strips page numbers, PDF metadata watermarks
- **Navigation sync** — translation-ready with post-export H1 alignment

## Installation

```bash
# 1. Clone into opencode skills directory
git clone https://github.com/hug0-l/mistral-ocr-epub-skill.git \
  ~/.config/opencode/skills/mistral-ocr-epub

# 2. Set API key
export MISTRAL_API_KEY="sk-..."

# 3. Install dependencies
pip install mistralai httpx ebooklib Pillow PyPDF2
```

## Quick Start

```bash
export SKILL_DIR=~/.config/opencode/skills/mistral-ocr-epub

# OCR → EPUB (single command via skill agent, or step-by-step:)
PYTHONPATH="$SKILL_DIR/scripts" python3 -m ocr_book \
  --input book.pdf --save-pages ./pages
PYTHONPATH="$SKILL_DIR/scripts" python3 -m build_epub \
  --pages ./pages --output ./book.epub
```

For image-heavy books:
```bash
PYTHONPATH="$SKILL_DIR/scripts" python3 -m ocr_book \
  --input book.pdf --save-pages ./pages --extract-images
PYTHONPATH="$SKILL_DIR/scripts" python3 -m build_epub \
  --pages ./pages --output ./book.epub
```

## Chapter Detection Strategies

| Strategy | When to use | Example |
|----------|------------|---------|
| `heading` (default) | Books with `#`/`##` headings | `--chapter-strategy heading` |
| `page` | No heading structure (scanned) | `--chapter-strategy page` |
| `regex` | Custom chapter markers (dates, etc.) | `--chapter-strategy regex --chapter-pattern "Chapter \d+"` |

Filter noisy sub-headings from navigation:
```bash
--skip-heading BLUF*  --skip-heading CONOP  --skip-heading "No."
```

## Pricing

Mistral OCR: **$4/1000 pages** ($0.004/page). A 300-page book costs ~$1.20.

## Design Principles

This pipeline is intentionally universal:

- **No hardcoded** book titles, author names, or chapter format assumptions
- **No dependency** on `document_annotation` chapter boundaries (unreliable)
- **Chapter detection** from real markdown content, not precomputed metadata
- **Heading filtering** via `--skip-heading` — user controls what appears in navigation
- **Page cleanup** (page numbers, PDF artifacts) via pattern matching, not book-specific rules

## How the Skill Activates

The file `SKILL.md` declares a trigger description. When the user says something like "ocr这本书", "扫书转epub", "mistral ocr", or "pdf转epub", opencode auto-loads this skill and the agent follows the workflow in `SKILL.md` — asking for the file path, output dir, genre, whether to extract images, etc.

## Translation Pipeline

This skill pairs with [ainiee-translate](https://github.com/hug0-l/ainiee-translate) for end-to-end OCR → translate → export:

```bash
# 1. OCR to EPUB
python3 -m build_epub --pages ./pages --output ./book.epub

# 2. Translate (via ainiee-translate)
python3 -m ainiee_translate.parse --input ./book.epub --out ./cache.json
# ... translate all items ...
python3 -m ainiee_translate.export --cache ./cache.json --output ./out/ --input ./book.epub

# 3. Sync navigation to translated H1s
python3 -c "
import zipfile, re, os
src = './out/book_translated.epub'
z = zipfile.ZipFile(src)
d = {n: z.read(n) for n in z.namelist()}
h1 = {}
for f in d:
    if f.endswith('.xhtml') and 'nav' not in f and 'cover' not in f:
        m = re.search(r'<h1>(.*?)</h1>', d[f].decode())
        if m: h1[f] = m.group(1).strip()
nav = d['EPUB/nav.xhtml'].decode()
for f, t in h1.items():
    s = f.replace('EPUB/', '')
    nav = re.sub(rf'(<a href=\"{re.escape(s)}\">)[^<]+(</a>)', rf'\1{t}\2', nav)
d['EPUB/nav.xhtml'] = nav.encode()
os.remove(src)
with zipfile.ZipFile(src, 'w', zipfile.ZIP_DEFLATED) as o:
    for n in d: o.writestr(n, d[n])
"
```

## File Structure

```
scripts/
  ocr_book.py        # Mistral OCR: upload → per-page markdown + metadata JSON
  build_epub.py      # Chapter detection → EPUB assembly → navigation sync
  utils.py           # Shared helpers (metadata detection, text→HTML)
```

## License

MIT
