# English Curriculum Taxonomy Pipeline

Muc tieu: quet bo tai lieu tieng Anh on thi tot nghiep THPT, tao lo trinh hoc theo kien thuc, trich xuat cau hoi, chuan hoa moi cau vao taxonomy, roi day len database co kha nang truy van theo topic/subtopic/question_type.

## Trang thai project hien tai

- Input hien tai: `local_curriculum_english/input_sources`.
- Manifest moi nhat: `local_curriculum_english/output_json/curriculum_manifest.json`.
- Preview manifest: `local_curriculum_english/previews/curriculum_manifest.html`.
- So PDF hien tai: 707 file, 1 source `MP`, 82 folder.
- Cache text hien tai moi co 6 PDF, nen `curriculum_scan.json` va `curriculum_roadmap.json` cu chua dung du lieu moi.
- `taxonomy_v2.py`, `standardize_questions.py`, `standard_exam_ingest.py` hien van hard-code nhieu logic Toan. Khong nen dung thang cho English neu chua tach subject/config.

## Luong xu ly de xuat

1. Manifest
   - Quet tat ca PDF trong `local_curriculum_english/input_sources`.
   - Giu folder path va natural order lam source of truth.
   - Lenh:

```bash
python3 curriculum_manifest.py --root local_curriculum_english
```

2. Text extraction va lesson scan
   - Doc text PDF bang PyMuPDF truoc.
   - Neu `char_count` thap hoac OCR loi, danh dau `needs_ocr=true` de chay MinerU/OCR sau.
   - Moi PDF sinh 1 lesson record gom: source, folder, file, page_count, char_count, lesson_type, program_area, concepts, objectives, application_questions.
   - Lenh:

```bash
python3 curriculum_scan.py --root local_curriculum_english --subject english
```

3. Synthesize roadmap
   - Gom cac lesson trung kien thuc thanh unit hoc tap.
   - Giữ thu tu hoc theo folder: Grammar foundation -> Vocabulary -> Reading skills -> Test practice.
   - Lenh:

```bash
python3 curriculum_synthesize.py --root local_curriculum_english --subject english
```

4. Canonical taxonomy English
   - Tao file rieng, vi taxonomy Toan hien tai khong phu hop.
   - Output nen la `local_curriculum_english/output_json/english_taxonomy_v1.json`.
   - Dong bo vao SQLite local truoc, khong day Supabase ngay.

5. Question extraction
   - Tach pipeline cau hoi English rieng voi schema question_type phu hop.
   - Moi cau hoi phai co `canonical_topic_code`, `canonical_subtopic_code`, `question_type`, `skill`, `exam_format`, `difficulty`, `source_file`, `page_number`.
   - Chay dry-run sinh JSON/HTML preview truoc khi commit database.

6. Review gate
   - Chi sync DB khi:
     - Manifest dung so file.
     - Scan success rate dat nguong chap nhan.
     - Topic/subtopic preview khong bi gom sai.
     - Cau hoi co dap an/explanation hoac duoc danh dau `needs_review`.

7. Sync database
   - Insert/upsert taxonomy truoc.
   - Insert/upsert questions sau.
   - Moi question tham chieu taxonomy bang id/code, khong chi luu text topic/subtopic.

## Taxonomy English v1

De xuat 10 topic cap 1:

| Code | Topic |
| --- | --- |
| EN01 | Grammar Foundation |
| EN02 | Verb Tenses and Verb Forms |
| EN03 | Sentence Structures and Clauses |
| EN04 | Vocabulary and Word Formation |
| EN05 | Reading Comprehension |
| EN06 | Cloze and Gap Filling |
| EN07 | Writing and Sentence Transformation |
| EN08 | Pronunciation and Phonetics |
| EN09 | Communication and Functional Language |
| EN10 | Test Practice and Mixed Skills |

Subtopic goi y:

| Code | Subtopic |
| --- | --- |
| EN01.01 | Parts of Speech |
| EN01.02 | Articles, Determiners, Quantifiers |
| EN01.03 | Prepositions and Phrasal Verbs |
| EN01.04 | Comparisons |
| EN02.01 | Present Tenses |
| EN02.02 | Past Tenses |
| EN02.03 | Future Forms |
| EN02.04 | Sequence of Tenses |
| EN02.05 | Gerunds and Infinitives |
| EN03.01 | Passive Voice |
| EN03.02 | Reported Speech |
| EN03.03 | Conditional Sentences |
| EN03.04 | Relative Clauses |
| EN03.05 | Inversion |
| EN03.06 | Conjunctions and Adverbial Clauses |
| EN03.07 | Cleft Sentences and Emphasis |
| EN04.01 | Word Formation |
| EN04.02 | Collocations |
| EN04.03 | Synonyms and Antonyms in Context |
| EN04.04 | Semantic Fields |
| EN04.05 | Topic Vocabulary |
| EN05.01 | Main Idea and Title |
| EN05.02 | Detail Questions |
| EN05.03 | Reference Questions |
| EN05.04 | Vocabulary in Context |
| EN05.05 | True False Not Given and Except |
| EN05.06 | Inference Questions |
| EN05.07 | Paragraph Matching and Sentence Insertion |
| EN05.08 | Summary and Paraphrase |
| EN06.01 | Grammar Gap Filling |
| EN06.02 | Vocabulary Gap Filling |
| EN06.03 | Advertisement and Notice Cloze |
| EN06.04 | Long Passage Cloze |
| EN07.01 | Sentence Transformation |
| EN07.02 | Sentence Combination |
| EN07.03 | Paragraph and Letter Ordering |
| EN07.04 | Writing Prompts |
| EN08.01 | Stress |
| EN08.02 | Sound Identification |
| EN08.03 | Ending Sounds |
| EN09.01 | Everyday Conversation |
| EN09.02 | Agreement, Disagreement, Suggestions |
| EN09.03 | Requests, Offers, Invitations |
| EN10.01 | Basic Mock Exams |
| EN10.02 | Applied Mock Exams |
| EN10.03 | Advanced Mock Exams |
| EN10.04 | HSA/DGNL Practice |
| EN10.99 | Needs Review or Mixed |

## Question taxonomy fields

Moi cau hoi nen co cac field bat buoc:

```json
{
  "source_code": "stable unique code",
  "subject": "english",
  "question_type": "multiple_choice|cloze|reading_comprehension|sentence_transformation|sentence_combination|ordering|pronunciation|stress|communication|writing_prompt",
  "skill": "grammar|vocabulary|reading|writing|pronunciation|communication|mixed",
  "exam_format": "thpt_2025|thpt_2026_practice|hsa|hnue_dgnl|lesson_practice|online_test",
  "question_text": "string",
  "passage_text": "string|null",
  "option_a": "string|null",
  "option_b": "string|null",
  "option_c": "string|null",
  "option_d": "string|null",
  "correct_answer": "A|B|C|D|string|null",
  "explanation": "string|null",
  "difficulty": "foundation|basic|intermediate|advanced|exam",
  "canonical_topic_id": 1,
  "canonical_topic_code": "EN05",
  "canonical_topic_title": "Reading Comprehension",
  "canonical_subtopic_id": 501,
  "canonical_subtopic_code": "EN05.01",
  "canonical_subtopic_title": "Main Idea and Title",
  "source_file": "relative pdf path",
  "page_number": 1,
  "needs_review": false,
  "raw_text": {}
}
```

## Database shape de xuat

Neu giu schema rieng:

```sql
create schema if not exists english_exam;

create table if not exists english_exam.taxonomy_topics (
  id integer primary key,
  topic_code text not null unique,
  topic_title text not null,
  topic_order integer not null
);

create table if not exists english_exam.taxonomy_subtopics (
  id integer primary key,
  topic_id integer not null references english_exam.taxonomy_topics(id),
  subtopic_code text not null unique,
  subtopic_title text not null,
  subtopic_order integer not null,
  description text,
  examples jsonb not null default '[]'::jsonb
);

create table if not exists english_exam.questions (
  id uuid primary key,
  source_code text not null unique,
  subject text not null default 'english',
  question_type text not null,
  skill text,
  exam_format text,
  question_text text not null,
  passage_text text,
  option_a text,
  option_b text,
  option_c text,
  option_d text,
  correct_answer text,
  explanation text,
  difficulty text,
  canonical_topic_id integer references english_exam.taxonomy_topics(id),
  canonical_topic_code text,
  canonical_topic_title text,
  canonical_subtopic_id integer references english_exam.taxonomy_subtopics(id),
  canonical_subtopic_code text,
  canonical_subtopic_title text,
  source_file text not null,
  source_hash text,
  page_number integer,
  raw_text jsonb not null default '{}'::jsonb,
  needs_review boolean not null default false,
  is_published boolean not null default false,
  created_at timestamptz not null default now()
);
```

Neu muon dung chung schema `standard_exam`, can them `subject='english'` va mo rong `question_type`, `skill`, `exam_format`, `passage_text`. Cach nay it tao schema moi nhung de lam lech logic Toan hien co.

## Chien luoc trich xuat cau hoi

- Tai lieu bai hoc: uu tien trich concept/exercise type, khong can day het vi du thanh question neu chua co dap an.
- Tai lieu `Thi Online`, `Đề thi thử`, `Bộ đề`: trich thanh question bank.
- Tai lieu reading/cloze: group passage + nhieu cau hoi con; moi cau van la 1 record, `passage_text` lap lai hoac tham chieu bang `passage_id`.
- Tai lieu sap xep/transform: dung `question_type=ordering` hoac `sentence_transformation`, option co the null.
- Neu khong co dap an: van insert dry-run local, nhung `needs_review=true`, `is_published=false`.

## Thu tu hoc tap v1

1. Grammar Foundation: tu loai, mao tu, luong tu, gioi tu.
2. Verb Tenses and Verb Forms: thi, phoi thi, V-ing/to V.
3. Sentence Structures: passive, reported speech, conditional, relative clause, inversion, conjunctions.
4. Vocabulary Core: word formation, collocation, semantic fields, synonyms/antonyms.
5. Reading Basics: reference, detail, main idea, vocabulary in context.
6. Reading Advanced: inference, except/not mentioned, paragraph matching, sentence insertion, summary.
7. Cloze: grammar gap, vocabulary gap, advertisement/notice, long passage.
8. Writing/Sentence Handling: transformation, combination, ordering dialogue/letter/paragraph.
9. Pronunciation and Communication.
10. Full exam practice: basic -> applied -> advanced -> DGNL if needed.

## Cac viec can lam tiep

1. Tao `english_taxonomy_v1.py` de sinh JSON/SQLite taxonomy rieng tu bang tren.
2. Them `english_question_ingest.py` hoac refactor `standard_exam_ingest.py` theo `subject_config`.
3. Chay scan lai 707 PDF, bat dau bang batch nho 20-50 file de kiem preview.
4. Tao preview mapping: folder -> roadmap unit -> taxonomy subtopic -> question count.
5. Chi sync Supabase sau khi preview dat.
