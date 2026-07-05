import re, html, json
from pathlib import Path


def natural_sort_key(p):
    nums = re.findall(r'\d+', p.name if hasattr(p, 'name') else str(p))
    return tuple(int(n) for n in nums) if nums else (0,)


def clean_ocr_noise(text: str) -> str:
    text = re.sub(r'\u3000', ' ', text)
    text = re.sub(r' +', ' ', text)
    return text.strip()


def auto_detect_metadata_from_markdown(text: str) -> dict:
    meta = {'title': '', 'author': '', 'translator': '', 'publisher': ''}
    lines = text.split('\n')[:80]
    for line in lines:
        ls = line.strip()
        m = re.match(r'^#\s+(.+)$', ls)
        if m:
            cand = m.group(1).strip()
            if re.search(r'CIP|版权|出版|编目|数据|版权所有|图书在版', cand):
                continue
            if len(cand) <= 60:
                meta['title'] = cand
                break
    author_pats = [
        r'([\u4e00-\u9fff·]{2,10})\s*(?:著|作|编|撰|述)',
        r'(?:著|作|编|撰|述)\s*[：:]\s*([\u4e00-\u9fff·]{2,10})',
    ]
    for line in lines:
        for pat in author_pats:
            m = re.search(pat, line)
            if m:
                meta['author'] = m.group(1).strip()
                break
        if meta['author']:
            break
    trans_pats = [
        r'([\u4e00-\u9fff·]{2,10})\s*(?:译|翻译)',
        r'(?:译|翻译)\s*[：:]\s*([\u4e00-\u9fff·]{2,10})',
    ]
    for line in lines:
        for pat in trans_pats:
            m = re.search(pat, line)
            if m and m.group(1).strip() != meta.get('author', ''):
                meta['translator'] = m.group(1).strip()
                break
        if meta['translator']:
            break
    pub_pats = [
        r'([\u4e00-\u9fff]{2,10}出版社)',
        r'([\u4e00-\u9fff]{2,10}出版[社]?)',
    ]
    for line in lines:
        for pat in pub_pats:
            m = re.search(pat, line)
            if m:
                meta['publisher'] = m.group(1).strip()
                break
        if meta['publisher']:
            break
    return meta


def extract_pdf_metadata(filepath: str) -> dict:
    meta = {'title': '', 'author': ''}
    try:
        from PyPDF2 import PdfReader
        reader = PdfReader(filepath)
        info = reader.metadata
        if info:
            if info.title:
                meta['title'] = info.title.strip()
            if info.author:
                meta['author'] = info.author.strip()
    except Exception:
        pass
    return meta


def is_toc_heading(heading: str) -> bool:
    if re.search(r'[………]\.?\(?\d+', heading):
        return True
    if re.search(r'\.{3,}\(?\d+', heading):
        return True
    if re.match(r'^\d+\s', heading):
        return True
    return False


def is_metadata_heading(heading: str, book_title: str = '') -> bool:
    noise = ['CIP', '版权', '图书在版', '出版编目', '版权所有', '图书出版编目', '数据']
    for n in noise:
        if n in heading:
            return True
    if len(heading) > 50:
        return True
    if book_title:
        ct = book_title.replace(' ', '')
        ch = heading.replace(' ', '')
        if ch == ct:
            return True
    stripped = heading.replace(' ', '')
    if len(stripped) >= 4 and len(heading) >= len(stripped) * 2 - 1:
        if all(c in heading[::2] for c in stripped):
            return True
    return False


def is_front_matter_title(title: str) -> bool:
    front_matter = {
        '前言', '序', '引言', '出版说明', '凡例', '内容提要',
        '作者简介', '译者序', '序言', '目次', '目录', '目 次',
        '目  录', '目錄', '内容概要', '自序', '代序',
    }
    return title.replace(' ', '') in front_matter


def expand_page_range(page_spec: str) -> list[int]:
    pages = []
    for part in page_spec.split(','):
        part = part.strip()
        if '-' in part:
            a, b = part.split('-', 1)
            pages.extend(range(int(a.strip()), int(b.strip()) + 1))
        else:
            pages.append(int(part.strip()))
    return sorted(set(pages))


def text_to_html(text: str) -> str:
    lines = text.split('\n')
    html_lines = []
    in_para = False
    for line in lines:
        line = line.strip()
        if not line:
            if in_para:
                html_lines.append('</p>')
                in_para = False
            continue
        if '<' in line and '>' in line and ('</' in line or '/>' in line):
            if in_para:
                html_lines.append('</p>')
                in_para = False
            html_lines.append(line)
            continue
        escaped = html.escape(line, quote=False)
        escaped = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)
        escaped = re.sub(r'\*(.+?)\*', r'<em>\1</em>', escaped)
        if not in_para:
            html_lines.append('<p>')
            in_para = True
            html_lines.append(escaped)
        else:
            html_lines.append(escaped)
    if in_para:
        html_lines.append('</p>')
    return '\n'.join(html_lines)


def load_project_meta(work_dir: Path) -> dict:
    meta_path = work_dir / "project_meta.json"
    if meta_path.exists():
        return json.loads(meta_path.read_text(encoding="utf-8"))
    return {}
