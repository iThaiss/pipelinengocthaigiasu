# Pipeline Workspace

Workspace này chứa các script ingest, scan curriculum, chuẩn hoá taxonomy và sync metadata.

## Thư mục chính

- `local_curriculum/`: dữ liệu local cho roadmap/câu hỏi Toán.
- `local_curriculum_english/`: dữ liệu local cho roadmap tiếng Anh.
- `docs/`: hướng dẫn vận hành cho agent/người dùng.
- `logs/`: log runtime.
- `artifacts/`: preview, ảnh crop, dry-run JSON và các output thử nghiệm.
- `mineru_models/`: model local cho MinerU/OCR.
- `.ingest_cache/`: cache ingest.

## Script chính

- `curriculum_manifest.py`: quét danh sách PDF trong `input_sources`.
- `curriculum_scan.py`: đọc PDF và phân tích từng bài học.
- `curriculum_synthesize.py`: tổng hợp roadmap từ các lesson đã scan.
- `curriculum_canonicalize.py`: chuẩn hoá roadmap Toán.
- `standardize_questions.py`: mirror câu hỏi và map vào roadmap Toán.
- `taxonomy_v2.py`: tạo taxonomy Toán 2 tầng.
- `sync_taxonomy_to_supabase.py`: sync metadata chuẩn lên Supabase.
- `ingest_pipeline.py`: ingest câu hỏi từ PDF vào Supabase.

## Quy trình tiếng Anh

Xem chi tiết tại `docs/AGENT_ENGLISH_CURRICULUM_SCAN.md`.

## Ghi chú vận hành agent

- `docs/AGENT_TOOLING_NOTES.md`: lưu ý dùng tool, đặc biệt cách gọi `apply_patch` đúng dạng freeform.
