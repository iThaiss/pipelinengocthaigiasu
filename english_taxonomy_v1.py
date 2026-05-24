import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("local_curriculum_english")

TOPICS = [
    (1, "EN01", "Grammar Foundation"),
    (2, "EN02", "Verb Tenses and Verb Forms"),
    (3, "EN03", "Sentence Structures and Clauses"),
    (4, "EN04", "Vocabulary and Word Formation"),
    (5, "EN05", "Reading Comprehension"),
    (6, "EN06", "Cloze and Gap Filling"),
    (7, "EN07", "Writing and Sentence Transformation"),
    (8, "EN08", "Pronunciation and Phonetics"),
    (9, "EN09", "Communication and Functional Language"),
    (10, "EN10", "Test Practice and Mixed Skills"),
]

SUBTOPICS = [
    (101, 1, "EN01.01", "Parts of Speech"),
    (102, 1, "EN01.02", "Articles, Determiners, Quantifiers"),
    (103, 1, "EN01.03", "Prepositions and Phrasal Verbs"),
    (104, 1, "EN01.04", "Comparisons"),
    (201, 2, "EN02.01", "Present Tenses"),
    (202, 2, "EN02.02", "Past Tenses"),
    (203, 2, "EN02.03", "Future Forms"),
    (204, 2, "EN02.04", "Sequence of Tenses"),
    (205, 2, "EN02.05", "Gerunds and Infinitives"),
    (301, 3, "EN03.01", "Passive Voice"),
    (302, 3, "EN03.02", "Reported Speech"),
    (303, 3, "EN03.03", "Conditional Sentences"),
    (304, 3, "EN03.04", "Relative Clauses"),
    (305, 3, "EN03.05", "Inversion"),
    (306, 3, "EN03.06", "Conjunctions and Adverbial Clauses"),
    (307, 3, "EN03.07", "Cleft Sentences and Emphasis"),
    (401, 4, "EN04.01", "Word Formation"),
    (402, 4, "EN04.02", "Collocations"),
    (403, 4, "EN04.03", "Synonyms and Antonyms in Context"),
    (404, 4, "EN04.04", "Semantic Fields"),
    (405, 4, "EN04.05", "Topic Vocabulary"),
    (501, 5, "EN05.01", "Main Idea and Title"),
    (502, 5, "EN05.02", "Detail Questions"),
    (503, 5, "EN05.03", "Reference Questions"),
    (504, 5, "EN05.04", "Vocabulary in Context"),
    (505, 5, "EN05.05", "True False Not Given and Except"),
    (506, 5, "EN05.06", "Inference Questions"),
    (507, 5, "EN05.07", "Paragraph Matching and Sentence Insertion"),
    (508, 5, "EN05.08", "Summary and Paraphrase"),
    (601, 6, "EN06.01", "Grammar Gap Filling"),
    (602, 6, "EN06.02", "Vocabulary Gap Filling"),
    (603, 6, "EN06.03", "Advertisement and Notice Cloze"),
    (604, 6, "EN06.04", "Long Passage Cloze"),
    (701, 7, "EN07.01", "Sentence Transformation"),
    (702, 7, "EN07.02", "Sentence Combination"),
    (703, 7, "EN07.03", "Paragraph and Letter Ordering"),
    (704, 7, "EN07.04", "Writing Prompts"),
    (801, 8, "EN08.01", "Stress"),
    (802, 8, "EN08.02", "Sound Identification"),
    (803, 8, "EN08.03", "Ending Sounds"),
    (901, 9, "EN09.01", "Everyday Conversation"),
    (902, 9, "EN09.02", "Agreement, Disagreement, Suggestions"),
    (903, 9, "EN09.03", "Requests, Offers, Invitations"),
    (1001, 10, "EN10.01", "Basic Mock Exams"),
    (1002, 10, "EN10.02", "Applied Mock Exams"),
    (1003, 10, "EN10.03", "Advanced Mock Exams"),
    (1004, 10, "EN10.04", "HSA/DGNL Practice"),
    (1099, 10, "EN10.99", "Needs Review or Mixed"),
]


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def build_taxonomy() -> dict[str, Any]:
    topic_rows = [
        {
            "id": topic_id,
            "topic_code": code,
            "topic_title": title,
            "topic_order": index,
        }
        for index, (topic_id, code, title) in enumerate(TOPICS, start=1)
    ]
    topic_by_id = {row["id"]: row for row in topic_rows}
    subtopic_rows = []
    order_by_topic: dict[int, int] = {}
    for subtopic_id, topic_id, code, title in SUBTOPICS:
        order_by_topic[topic_id] = order_by_topic.get(topic_id, 0) + 1
        topic = topic_by_id[topic_id]
        subtopic_rows.append(
            {
                "id": subtopic_id,
                "topic_id": topic_id,
                "topic_code": topic["topic_code"],
                "topic_title": topic["topic_title"],
                "subtopic_code": code,
                "subtopic_title": title,
                "subtopic_order": order_by_topic[topic_id],
            }
        )
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "subject": "english",
        "version": "english_taxonomy_v1",
        "topics": topic_rows,
        "subtopics": subtopic_rows,
        "question_types": [
            "multiple_choice",
            "cloze",
            "reading_comprehension",
            "sentence_transformation",
            "sentence_combination",
            "ordering",
            "pronunciation",
            "stress",
            "communication",
            "writing_prompt",
        ],
        "skills": ["grammar", "vocabulary", "reading", "writing", "pronunciation", "communication", "mixed"],
        "difficulties": ["foundation", "basic", "intermediate", "advanced", "exam"],
    }


def write_sqlite(root: Path, taxonomy: dict[str, Any]) -> None:
    db_path = root / "output_sqlite" / "curriculum.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(db_path)
    db.execute(
        """
        create table if not exists english_taxonomy_topics (
            id integer primary key,
            topic_code text not null unique,
            topic_title text not null,
            topic_order integer not null
        )
        """
    )
    db.execute(
        """
        create table if not exists english_taxonomy_subtopics (
            id integer primary key,
            topic_id integer not null,
            topic_code text not null,
            topic_title text not null,
            subtopic_code text not null unique,
            subtopic_title text not null,
            subtopic_order integer not null,
            foreign key(topic_id) references english_taxonomy_topics(id)
        )
        """
    )
    db.execute("delete from english_taxonomy_subtopics")
    db.execute("delete from english_taxonomy_topics")
    db.executemany(
        """
        insert into english_taxonomy_topics (id, topic_code, topic_title, topic_order)
        values (:id, :topic_code, :topic_title, :topic_order)
        """,
        taxonomy["topics"],
    )
    db.executemany(
        """
        insert into english_taxonomy_subtopics (
            id, topic_id, topic_code, topic_title, subtopic_code, subtopic_title, subtopic_order
        ) values (
            :id, :topic_id, :topic_code, :topic_title, :subtopic_code, :subtopic_title, :subtopic_order
        )
        """,
        taxonomy["subtopics"],
    )
    db.commit()
    db.close()


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Build English THPT taxonomy v1 JSON and local SQLite tables.")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    root = args.root
    taxonomy = build_taxonomy()
    output_path = root / "output_json" / "english_taxonomy_v1.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(taxonomy, ensure_ascii=False, indent=2), encoding="utf-8")
    write_sqlite(root, taxonomy)

    print(f"Wrote {output_path.resolve()}")
    print(f"Wrote SQLite tables in {(root / 'output_sqlite' / 'curriculum.sqlite').resolve()}")
    print(f"Topics: {len(taxonomy['topics'])}; subtopics: {len(taxonomy['subtopics'])}")


if __name__ == "__main__":
    main()
