# Mistral OCR API 參考

## Endpoint

```
POST https://api.mistral.ai/v1/ocr
```

## 請求格式

```json
{
  "model": "mistral-ocr-latest",
  "document": {
    "type": "document_url",
    "document_url": "https://..."
  },
  "include_blocks": true,
  "include_image_base64": true,
  "table_format": "html"
}
```

## 文件上傳

```
POST https://api.mistral.ai/v1/files
```

用 `multipart/form-data` 上傳，回傳 `{ "id": "file-id" }`。

## 回應格式

```json
{
  "pages": [
    {
      "index": 0,
      "markdown": "# Title\n\nContent...",
      "images": [
        {
          "id": "img-0.jpeg",
          "image_base64": "...",
          "top_left_x": 0, "top_left_y": 0,
          "bottom_right_x": 100, "bottom_right_y": 100
        }
      ],
      "tables": [
        {
          "id": "tbl-0.html",
          "content": "<table>...</table>"
        }
      ],
      "dimensions": { "dpi": 200, "height": 2200, "width": 1700 },
      "confidence_scores": {
        "average_page_confidence_score": 0.98
      }
    }
  ],
  "model": "mistral-ocr-4-0-completion",
  "usage_info": { "pages_processed": 29 }
}
```

## 支援格式

- PDF、DOCX、PPTX、ODT
- PNG、JPEG、AVIF、TIFF、BMP

## 收費

$4/1000 頁（$0.004/頁），Batch API 半價 $2/1000 頁。
