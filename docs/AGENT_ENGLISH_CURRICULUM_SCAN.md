# Agent Guide: Scan English Curriculum Locally

Mục tiêu: scan bộ tài liệu tiếng Anh giống quy trình đã làm với Toán, nhưng tách riêng dữ liệu local để không trộn roadmap/câu hỏi giữa hai môn.

## Folder chuẩn

- Input tiếng Anh: `D:\Projects\pipeline\local_curriculum_english\input_sources`
- Output JSON: `D:\Projects\pipeline\local_curriculum_english\output_json`
- Output SQLite: `D:\Projects\pipeline\local_curriculum_english\output_sqlite\curriculum.sqlite`
- Preview HTML: `D:\Projects\pipeline\local_curriculum_english\previews`
- Cache text PDF: `D:\Projects\pipeline\local_curriculum_english\cache\pdf_text`

Mỗi bên cung cấp tài liệu là một folder con trong `input_sources`, ví dụ:

```text
local_curriculum_english/
  input_sources/
    MP/
      Grammar/
      Vocabulary - Reading/
      Practice Tests/
    OTHER_PROVIDER/
```

## Quy trình chạy

Chạy từ `D:\Projects\pipeline`.

1. Tạo manifest:

```powershell
py .\curriculum_manifest.py --root .\local_curriculum_english
```

2. Scan từng PDF:

```powershell
py .\curriculum_scan.py --root .\local_curriculum_english --subject english
```

3. Tổng hợp roadmap:

```powershell
py .\curriculum_synthesize.py --root .\local_curriculum_english --subject english
```

4. Mở preview để kiểm tra:

```text
D:\Projects\pipeline\local_curriculum_english\previews\curriculum_manifest.html
D:\Projects\pipeline\local_curriculum_english\previews\curriculum_scan.html
D:\Projects\pipeline\local_curriculum_english\previews\curriculum_roadmap.html
```

## Prompt cần đổi cho tiếng Anh

Các script hiện tại đã có cờ `--subject english`. Nếu agent fork script mới hoặc dùng model khác, giữ schema JSON nhưng dùng vai trò chuyên gia sau:

```text
Bạn là chuyên gia thiết kế lộ trình học Tiếng Anh cho học sinh Việt Nam.
Hãy đọc tài liệu bài học và trích xuất cấu trúc dạy học.
Tập trung vào: Grammar, Vocabulary, Reading, Listening, Speaking, Writing,
Pronunciation, Test Practice, Error Correction, Communication Functions.
Chỉ trả về JSON object hợp lệ, không markdown.
```

Khi phân tích bài học tiếng Anh, agent cần trích:

- `lesson_title`: tên bài/chủ điểm tiếng Anh sạch.
- `program_area`: Grammar, Vocabulary, Reading, Writing, Listening, Speaking, Pronunciation, Test Practice, Mixed Skills.
- `chapter`: folder hoặc unit lớn.
- `lesson_type`: lesson, homework, practice, bonus, test, unknown.
- `phase`: Foundation, Practice, Application, Test Prep hoặc null.
- `objectives`: học xong làm được gì.
- `concepts`: điểm ngữ pháp, nhóm từ vựng, kỹ năng đọc/nghe/viết.
- `prerequisites`: kiến thức cần có trước.
- `teaching_methods`: cách tài liệu dạy: rule explanation, examples, guided practice, drills, passages, cloze test, error correction.
- `example_types`: dạng ví dụ/bài tập.
- `application_questions`: dạng câu hỏi/bài tập: multiple choice grammar, word form, synonym/antonym, reading comprehension, sentence transformation, gap filling, writing prompt.
- `difficulty_progression`: trình tự cơ bản đến nâng cao.
- `source_strengths`: điểm mạnh của nguồn.
- `gaps_or_warnings`: thiếu audio, thiếu đáp án, OCR kém, scan mờ, tài liệu chỉ là bài tập.

## Taxonomy tiếng Anh đề xuất

Sau khi có roadmap thô, chuẩn hoá thành topic/subtopic 2 tầng:

1. Grammar Foundation
2. Verb Tenses and Verb Forms
3. Sentence Structures and Clauses
4. Vocabulary and Word Formation
5. Reading Comprehension
6. Listening Skills
7. Speaking and Communication
8. Writing Skills
9. Pronunciation and Phonetics
10. Test Practice and Mixed Skills

Các tài liệu ngoài phạm vi hoặc không rõ thì đưa vào:

- `Out of scope / Needs review`
- `Unclear English metadata`

## Quy tắc quan trọng

- Không sync Supabase khi chưa kiểm preview local.
- Không dùng chung `local_curriculum/output_sqlite/curriculum.sqlite` của Toán.
- Không đổi dữ liệu Toán khi scan tiếng Anh.
- Nếu PDF là scan ảnh/OCR kém, ghi rõ trong `gaps_or_warnings`.
- Nếu tài liệu có tiếng Việt giải thích tiếng Anh, vẫn phân loại theo kỹ năng/chủ điểm tiếng Anh.
- Sau mỗi bước phải kiểm số file, số lesson scan thành công, số lỗi/fallback.

## Kết quả cần báo lại

Agent phải báo:

- Tổng số PDF theo từng nguồn.
- Số PDF scan thành công/thất bại.
- Số unit roadmap thô.
- Các topic/subtopic chính.
- Các tài liệu cần review thủ công.
- Đường dẫn preview HTML và SQLite local.
