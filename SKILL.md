---
name: mistral-ocr-epub
description: Use when the user wants to OCR a scanned book (PDF/DOCX/images) and convert to EPUB using Mistral OCR API. Universal — no book-specific rules. Triggers on "ocr这本书", "扫描转epub", "mistral ocr", "pdf转epub", "把这本书ocr了" and similar.
---

# mistral-ocr-epub

扫描版图书 → OCR → 结构化 EPUB（导航、图片、表格、章节）。

## 总览

```
                         ┌─ 逐页 markdown（全文）
PDF → Mistral OCR API ──┤                       → EPUB 组裝
   (单次调用)            └─ document_annotation（书名/作者/译者）
```

**支持输入：** PDF、DOCX、PPTX、ODT、PNG、JPEG、AVIF、TIFF、BMP

**收费：** Mistral OCR $4/1000 页（300 页 ≈ $1.20）

## 开始前需确认

1. **输入文件路径**
2. **输出目录**（默认 `~/<书名>/out/`）
3. **书名/作者**（可选，自动检测）
4. **体裁 `--genre`**（可选，改善自动检测）
5. **`--extract-images`**（可选，默认关闭节省成本）
6. **校正**（可选，低信度页面 agent 审查）
7. **翻译**（可选，接入 ainiee-translate）

## 前置依赖

```bash
export MISTRAL_API_KEY="sk-..."
pip install mistralai httpx ebooklib Pillow PyPDF2
export SKILL_DIR=~/.config/opencode/skills/mistral-ocr-epub
```

## 完整流程

### 步骤 1：OCR

```bash
mkdir -p ./work/pages ./out
PYTHONPATH="$SKILL_DIR/scripts" python3 -m ocr_book \
  --input book.pdf --save-pages ./work/pages
```

| 参数 | 说明 |
|------|------|
| `--extract-images` | 提取内嵌图片（EPUB 体积增大但含原书插图） |
| `--correct` | 标记低信度页面供 agent 校正 |
| `--pages 0-20` | 只处理前 21 页（测试用） |
| `--genre novel` | 体裁提示 |
| `--title` / `--author` | 覆盖自动检测 |

### 步骤 2：Agent 校正（可选）

```bash
ls ./work/pages/page-*/needs_correction.txt 2>/dev/null
```

对每个标记页：读取 → 修正 OCR 错误 → 写回 → 删除标记。

### 步骤 3：组裝 EPUB

```bash
PYTHONPATH="$SKILL_DIR/scripts" python3 -m build_epub \
  --pages ./work/pages --output ./out/book.epub
```

**章节策略（通用，无书特定规则）：**

| 策略 | 适用场景 | 命令 |
|------|---------|------|
| `heading`（默认） | 有 `#`/`##` 标题的书 | `--chapter-strategy heading` |
| `page` | 无标题结构 | `--chapter-strategy page` |
| `regex` | 自定义分割（日期等） | `--chapter-strategy regex --chapter-pattern "昭和 \d+"` |

**标题过滤（按需配置）：**

```bash
# 跳过精确匹配
--skip-heading BLUF

# 跳过前缀匹配（带 *）
--skip-heading BLUF*  --skip-heading CONOP
```

**完整范例：**

```bash
PYTHONPATH="$SKILL_DIR/scripts" python3 -m build_epub \
  --pages ./work/pages \
  --title "100 Deadly Skills" --author "Clint Emerson" \
  --skip-heading BLUF* --skip-heading CONOP \
  --output ./out/book.epub
```

## 与 ainiee-translate 配合

```bash
# OCR → EPUB
PYTHONPATH="$SKILL_DIR/scripts" python3 -m build_epub \
  --pages ./work/pages --output ./out/book.epub

# 翻译
# <ainiee-pfx> -m ainiee_translate.parse --input ./out/book.epub --out ./work/cache.json
```

翻译后导航需手动 sync（`nav.xhtml` → `H1`）：

```bash
python3 -c "
import zipfile, re, os
src = 'translated.epub'
epub = zipfile.ZipFile(src)
data = {n: epub.read(n) for n in epub.namelist()}
h1_map = {}
for f in data:
    if f.endswith('.xhtml') and 'nav' not in f and 'cover' not in f:
        m = re.search(r'<h1>(.*?)</h1>', data[f].decode('utf-8'))
        if m: h1_map[f] = m.group(1).strip()
nav = data['EPUB/nav.xhtml'].decode('utf-8')
for f, h1 in h1_map.items():
    short = f.replace('EPUB/', '')
    nav = re.sub(rf'(<a href=\"{re.escape(short)}\">)[^<]+(</a>)', rf'\1{h1}\2', nav)
data['EPUB/nav.xhtml'] = nav.encode('utf-8')
os.remove(src)
with zipfile.ZipFile(src, 'w', zipfile.ZIP_DEFLATED) as out:
    for n in data: out.writestr(n, data[n])
"
```

## 设计原则

此技能的设计只包含 **OCR → EPUB 管道的通用层**：

- ❌ 无硬编码的书名/作者过滤
- ❌ 无章节格式假设（PART、Chapter、日期…）
- ❌ 无语言/体裁特化规则
- ❌ 不依赖 `document_annotation` 的章节边界
- ✅ 章节从实际 markdown 标题、page 边界或用户提供的 regex 实时检测
- ✅ 标题过滤通过 `--skip-heading` 由用户按需配置
- ✅ 页面清洁（页码、PDF 元数据）基于模式匹配，无书特定判断

## 故障排除

**Q: 章节检测不准确？**  
A: 试 `--chapter-strategy page`（每页一章）或 `--chapter-strategy regex --chapter-pattern "正则"`。

**Q: 导航有噪声条目（BLUF、CONOP…）？**  
A: 用 `--skip-heading BLUF* --skip-heading CONOP` 排除。

**Q: 图片损坏？**  
A: 确保 `ocr_book.py` ≥ v2（含 data URI 前缀剥离）。旧版图片需重新提取。

**Q: 上传失败？**  
A: 文件 ≥ 512MB 或 `MISTRAL_API_KEY` 未设。

**Q: 中文显示不正常？**  
A: 阅读器需支持 CJK 字体。CSS 已指定 `Noto Serif CJK SC` / `SimSun`。
