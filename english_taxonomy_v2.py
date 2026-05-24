import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("local_curriculum_english")
VERSION = "english_taxonomy_v2"

KNOWLEDGE_TOPICS = [
    (1, "E2G", "Grammar Foundation"),
    (2, "E2V", "Verb System"),
    (3, "E2S", "Sentence and Clause Structures"),
    (4, "E2X", "Vocabulary and Lexical Resources"),
    (5, "E2R", "Reading Skills"),
    (6, "E2C", "Cloze and Text Completion"),
    (7, "E2O", "Ordering and Discourse"),
    (8, "E2W", "Writing"),
    (9, "E2F", "Functional Communication"),
    (10, "E2M", "Mixed Exam Skills"),
]

KNOWLEDGE_SUBTOPICS = [
    (101, 1, "E2G.01", "Parts of Speech"),
    (102, 1, "E2G.02", "Articles and Determiners"),
    (103, 1, "E2G.03", "Pronouns and Quantifiers"),
    (104, 1, "E2G.04", "Prepositions"),
    (105, 1, "E2G.05", "Phrasal Verbs"),
    (106, 1, "E2G.06", "Comparisons"),
    (201, 2, "E2V.01", "Present Tenses"),
    (202, 2, "E2V.02", "Past Tenses"),
    (203, 2, "E2V.03", "Future Forms"),
    (204, 2, "E2V.04", "Sequence of Tenses"),
    (205, 2, "E2V.05", "Subject-Verb Agreement"),
    (206, 2, "E2V.06", "Modal Verbs"),
    (207, 2, "E2V.07", "Gerunds and Infinitives"),
    (208, 2, "E2V.08", "Participles"),
    (301, 3, "E2S.01", "Passive Voice"),
    (302, 3, "E2S.02", "Reported Speech"),
    (303, 3, "E2S.03", "Conditionals and Wishes"),
    (304, 3, "E2S.04", "Relative Clauses"),
    (305, 3, "E2S.05", "Reduced Clauses"),
    (306, 3, "E2S.06", "Conjunctions and Adverbial Clauses"),
    (307, 3, "E2S.07", "Question Tags"),
    (308, 3, "E2S.08", "Cleft Sentences and Emphasis"),
    (309, 3, "E2S.09", "Inversion"),
    (310, 3, "E2S.10", "Sentence Types"),
    (311, 3, "E2S.11", "Parallelism and Apposition"),
    (401, 4, "E2X.01", "Word Formation"),
    (402, 4, "E2X.02", "Word Order"),
    (403, 4, "E2X.03", "Collocations"),
    (404, 4, "E2X.04", "Fixed Expressions and Phrasal Patterns"),
    (405, 4, "E2X.05", "Synonyms and Antonyms"),
    (406, 4, "E2X.06", "Vocabulary in Context"),
    (407, 4, "E2X.07", "Topic Vocabulary"),
    (408, 4, "E2X.08", "Semantic Fields"),
    (501, 5, "E2R.01", "Main Idea and Title"),
    (502, 5, "E2R.02", "Detail and Not-Mentioned Questions"),
    (503, 5, "E2R.03", "Reference Questions"),
    (504, 5, "E2R.04", "Vocabulary in Reading Context"),
    (505, 5, "E2R.05", "Inference Questions"),
    (506, 5, "E2R.06", "Paragraph Location and Sentence Insertion"),
    (507, 5, "E2R.07", "Paraphrase Questions"),
    (508, 5, "E2R.08", "Summary Questions"),
    (509, 5, "E2R.09", "Author Purpose, Tone, and Attitude"),
    (601, 6, "E2C.01", "Grammar Cloze"),
    (602, 6, "E2C.02", "Vocabulary Cloze"),
    (603, 6, "E2C.03", "Advertisement and Notice Cloze"),
    (604, 6, "E2C.04", "Press Release Cloze"),
    (605, 6, "E2C.05", "Long Passage Cloze"),
    (606, 6, "E2C.06", "Discourse Text Completion"),
    (701, 7, "E2O.01", "Dialogue Ordering"),
    (702, 7, "E2O.02", "Paragraph Ordering"),
    (703, 7, "E2O.03", "Email and Letter Ordering"),
    (704, 7, "E2O.04", "Sentence Coherence"),
    (801, 8, "E2W.01", "Sentence Transformation"),
    (802, 8, "E2W.02", "Sentence Combination"),
    (803, 8, "E2W.03", "Paragraph Writing"),
    (804, 8, "E2W.04", "Opinion and Argument Writing"),
    (901, 9, "E2F.01", "Dialogue Completion"),
    (902, 9, "E2F.02", "Agreement, Disagreement, and Suggestions"),
    (903, 9, "E2F.03", "Requests, Offers, and Invitations"),
    (1001, 10, "E2M.01", "HSA Logical Problem Solving"),
    (1002, 10, "E2M.02", "DGNL/SPT Mixed Use of English"),
    (1003, 10, "E2M.03", "VIP90 Weekly Mixed Review"),
    (1099, 10, "E2M.99", "Needs Review or Mixed"),
]

QUESTION_FORMATS = [
    ("thpt_press_release_cloze", "THPT press-release cloze", ["E2C.04", "E2C.01", "E2C.02", "E2X.03", "E2X.04"]),
    ("thpt_advertisement_cloze", "THPT advertisement cloze", ["E2C.03", "E2C.01", "E2C.02", "E2X.04"]),
    ("thpt_arrangement_exchange", "THPT exchange/dialogue arrangement", ["E2O.01", "E2F.01"]),
    ("thpt_arrangement_text", "THPT paragraph/email/text arrangement", ["E2O.02", "E2O.03", "E2O.04"]),
    ("thpt_text_completion", "THPT discourse text completion", ["E2C.06", "E2S.06", "E2O.04"]),
    ("thpt_reading_passage", "THPT reading passage questions", ["E2R.02", "E2R.03", "E2R.04", "E2R.05", "E2R.06", "E2R.07", "E2R.08"]),
    ("hsa_sentence_completion", "HSA sentence completion", ["E2G.01", "E2V.04", "E2S.03", "E2X.06"]),
    ("hsa_synonym", "HSA synonym question", ["E2X.05", "E2X.06"]),
    ("hsa_antonym", "HSA antonym question", ["E2X.05", "E2X.06"]),
    ("hsa_dialogue_completion", "HSA dialogue completion", ["E2F.01", "E2F.02", "E2F.03"]),
    ("hsa_dialogue_arrangement", "HSA dialogue arrangement", ["E2O.01", "E2O.04"]),
    ("hsa_sentence_rewriting", "HSA sentence rewriting", ["E2W.01", "E2S.06", "E2S.03"]),
    ("hsa_sentence_combination", "HSA sentence combination", ["E2W.02", "E2S.06", "E2S.05"]),
    ("hsa_cloze_text", "HSA cloze text", ["E2C.05", "E2C.01", "E2C.02"]),
    ("hsa_reading_comprehension", "HSA reading comprehension", ["E2R.02", "E2R.03", "E2R.04", "E2R.05", "E2R.08"]),
    ("hsa_logical_problem_solving", "HSA logical problem solving", ["E2M.01"]),
    ("spt_use_of_english", "SPT Use of English", ["E2G.01", "E2V.04", "E2X.06", "E2S.06"]),
    ("spt_cloze", "SPT cloze", ["E2C.05", "E2C.01", "E2C.02"]),
    ("spt_arrangement", "SPT arrangement", ["E2O.01", "E2O.02", "E2O.04"]),
    ("spt_reading", "SPT reading", ["E2R.02", "E2R.03", "E2R.04", "E2R.05", "E2R.09"]),
    ("spt_word_formation", "SPT word formation", ["E2X.01"]),
    ("spt_paragraph_writing", "SPT paragraph writing", ["E2W.03", "E2W.04"]),
]

EXAM_BLUEPRINTS = [
    {
        "exam_profile": "THPT_2025_CORE",
        "priority": "core",
        "duration_minutes": 50,
        "question_count": 40,
        "sections": [
            {"range": "1-6", "section": "press_release_cloze", "question_format": "thpt_press_release_cloze", "group_type": "cloze_passage"},
            {"range": "7-12", "section": "advertisement_cloze", "question_format": "thpt_advertisement_cloze", "group_type": "advertisement_cloze"},
            {"range": "13-17", "section": "arrangement", "question_format": "thpt_arrangement_text", "group_type": "ordering"},
            {"range": "18-22", "section": "text_completion", "question_format": "thpt_text_completion", "group_type": "text_completion"},
            {"range": "23-30", "section": "reading_passage_1", "question_format": "thpt_reading_passage", "group_type": "reading_passage"},
            {"range": "31-40", "section": "reading_passage_2", "question_format": "thpt_reading_passage", "group_type": "reading_passage"},
        ],
    },
    {
        "exam_profile": "HSA_ENGLISH",
        "priority": "supplement",
        "duration_minutes": 60,
        "question_count": 50,
        "sections": [
            {"range": "1-10", "section": "sentence_completion", "question_format": "hsa_sentence_completion", "group_type": "independent_questions"},
            {"range": "11-12", "section": "synonyms", "question_format": "hsa_synonym", "group_type": "independent_questions"},
            {"range": "13-14", "section": "antonyms", "question_format": "hsa_antonym", "group_type": "independent_questions"},
            {"range": "15-18", "section": "dialogue_completion", "question_format": "hsa_dialogue_completion", "group_type": "dialogue"},
            {"range": "19-22", "section": "dialogue_arrangement", "question_format": "hsa_dialogue_arrangement", "group_type": "ordering"},
            {"range": "23-26", "section": "sentence_rewriting", "question_format": "hsa_sentence_rewriting", "group_type": "independent_questions"},
            {"range": "27-30", "section": "sentence_combination", "question_format": "hsa_sentence_combination", "group_type": "independent_questions"},
            {"range": "31-35", "section": "cloze_text", "question_format": "hsa_cloze_text", "group_type": "cloze_passage"},
            {"range": "36-45", "section": "reading_comprehension", "question_format": "hsa_reading_comprehension", "group_type": "reading_passage"},
            {"range": "46-50", "section": "logical_problem_solving", "question_format": "hsa_logical_problem_solving", "group_type": "logic_problem"},
        ],
    },
    {
        "exam_profile": "SPT_ENGLISH",
        "priority": "supplement",
        "duration_minutes": 60,
        "sections": [
            {"range": "1-5", "section": "use_of_english", "question_format": "spt_use_of_english", "group_type": "independent_questions"},
            {"range": "6-17", "section": "cloze_and_arrangement", "question_format": "spt_cloze", "group_type": "cloze_passage"},
            {"range": "19-28", "section": "reading", "question_format": "spt_reading", "group_type": "reading_passage"},
            {"range": "wf1-wf4", "section": "word_formation", "question_format": "spt_word_formation", "group_type": "word_formation_text"},
            {"range": "writing", "section": "paragraph_writing", "question_format": "spt_paragraph_writing", "group_type": "writing_prompt"},
        ],
    },
]

LEARNING_SUPPORT_TYPES = [
    {"support_type": "vocabulary_table", "description": "Post-exam vocabulary list for study support, not an exam question format."},
    {"support_type": "structure_table", "description": "Post-exam structure/fixed-expression list for study support, not an exam question format."},
    {"support_type": "answer_key", "description": "Answer key section."},
    {"support_type": "explanation", "description": "Solution/explanation section."},
]

def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

def build_taxonomy() -> dict[str, Any]:
    topic_rows = [
        {"id": topic_id, "topic_code": code, "topic_title": title, "topic_order": i}
        for i, (topic_id, code, title) in enumerate(KNOWLEDGE_TOPICS, start=1)
    ]
    topic_by_id = {row["id"]: row for row in topic_rows}
    order_by_topic: dict[int, int] = {}
    subtopic_rows = []
    for subtopic_id, topic_id, code, title in KNOWLEDGE_SUBTOPICS:
        order_by_topic[topic_id] = order_by_topic.get(topic_id, 0) + 1
        topic = topic_by_id[topic_id]
        subtopic_rows.append({
            "id": subtopic_id,
            "topic_id": topic_id,
            "topic_code": topic["topic_code"],
            "topic_title": topic["topic_title"],
            "subtopic_code": code,
            "subtopic_title": title,
            "subtopic_order": order_by_topic[topic_id],
        })
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "subject": "english",
        "version": VERSION,
        "design_notes": {
            "core_exam": "THPT_2025_CORE",
            "supplement_exams": ["HSA_ENGLISH", "SPT_ENGLISH"],
            "principle": "Knowledge is shared across exams; exam-specific formats and blueprints are metadata.",
            "learning_support_note": "Vocabulary/structure tables after exams are learning support, not exam question formats.",
        },
        "knowledge_topics": topic_rows,
        "knowledge_subtopics": subtopic_rows,
        "question_formats": [
            {"format_code": code, "format_title": title, "default_knowledge_subtopics": defaults}
            for code, title, defaults in QUESTION_FORMATS
        ],
        "exam_blueprints": EXAM_BLUEPRINTS,
        "learning_support_types": LEARNING_SUPPORT_TYPES,
    }

def write_sqlite(root: Path, taxonomy: dict[str, Any]) -> None:
    db_path = root / "output_sqlite" / "curriculum.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.execute("""
        create table if not exists english_taxonomy_v2_topics (
            id integer primary key,
            topic_code text not null unique,
            topic_title text not null,
            topic_order integer not null
        )
    """)
    db.execute("""
        create table if not exists english_taxonomy_v2_subtopics (
            id integer primary key,
            topic_id integer not null,
            topic_code text not null,
            topic_title text not null,
            subtopic_code text not null unique,
            subtopic_title text not null,
            subtopic_order integer not null
        )
    """)
    db.execute("delete from english_taxonomy_v2_subtopics")
    db.execute("delete from english_taxonomy_v2_topics")
    db.executemany(
        """insert into english_taxonomy_v2_topics (id, topic_code, topic_title, topic_order)
           values (:id, :topic_code, :topic_title, :topic_order)""",
        taxonomy["knowledge_topics"],
    )
    db.executemany(
        """insert into english_taxonomy_v2_subtopics (
            id, topic_id, topic_code, topic_title, subtopic_code, subtopic_title, subtopic_order
        ) values (:id, :topic_id, :topic_code, :topic_title, :subtopic_code, :subtopic_title, :subtopic_order)""",
        taxonomy["knowledge_subtopics"],
    )
    db.commit()
    db.close()

def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Build English exam-oriented taxonomy v2.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()
    taxonomy = build_taxonomy()
    output_path = args.root / "output_json" / "english_taxonomy_v2.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")
    write_sqlite(args.root, taxonomy)
    print(f"Wrote {output_path.resolve()}")
    print(f"Topics: {len(taxonomy['knowledge_topics'])}; subtopics: {len(taxonomy['knowledge_subtopics'])}; formats: {len(taxonomy['question_formats'])}")

if __name__ == "__main__":
    main()
