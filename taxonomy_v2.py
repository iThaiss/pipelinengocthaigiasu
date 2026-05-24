import argparse
import html
import json
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("local_curriculum")

TOPICS = [
    (1, "01", "Kiến thức tiền đề"),
    (2, "02", "Ứng dụng đạo hàm và khảo sát hàm số"),
    (3, "03", "Ứng dụng đạo hàm thực tế"),
    (4, "04", "Nguyên hàm và tích phân"),
    (5, "05", "Vectơ và tọa độ Oxyz nền tảng"),
    (6, "06", "Phương trình và hình học Oxyz"),
    (7, "07", "Thống kê & mẫu số liệu ghép nhóm"),
    (8, "08", "Xác suất có điều kiện"),
    (9, "09", "Kiến thức lớp 10"),
    (10, "10", "Kiến thức lớp 11"),
]

DISPLAY_RENAMES = {
    "Bài 2.4 Toán Thực Tế Oxyz - Phương Trình Đường Thẳng": "Toán thực tế Oxyz với phương trình đường thẳng",
    "Bài 0. Tích Có Hướng Và Ứng Dụng": "Tích có hướng và ứng dụng",
    "Bài 3.2 Đường Tiệm Cận Của Đồ Thị Hàm Số - Tiệm Cận Xiên": "Tiệm cận xiên của đồ thị hàm số",
    "Ứng Dụng Đạo Hàm - Phần 2: Bài Toán Kinh Tế": "Bài toán kinh tế ứng dụng đạo hàm",
    "Ứng Dụng Đạo Hàm - Phần 4. Bài Toán Hình Học Không Gian": "Bài toán hình học không gian ứng dụng đạo hàm",
    "Bài Toán Tâm Tỉ Cự": "Tâm tỉ cự",
    "Theme 20. Xác suất có điều kiện": "Xác suất có điều kiện và công thức nhân xác suất",
    "Theme 15. Ứng dụng thực tế của vectơ trong không gian": "Ứng dụng thực tế của vectơ trong không gian",
    "Theme 7. Nguyên hàm hàm lượng giác nâng cao": "Nguyên hàm hàm lượng giác và các hàm đặc biệt",
    "Công Thức Bayes": "Công thức Bayes",
    "Khảo Sát Đồ Thị Hàm Số Hàm Phân Thức Bậc Hai": "Khảo sát đồ thị hàm phân thức bậc hai",
    "Đơn Điệu, Cực Trị, Maxmin của Hàm Lượng Giác và Mũ Logarit": "Đơn điệu, cực trị, GTLN-GTNN của hàm lượng giác, mũ và logarit",
    "Phương Trình Đường Thẳng - Góc và Khoảng Cách, Vị Trí Tương Đối": "Góc, khoảng cách và vị trí tương đối của đường thẳng",
}

EXTRA_SUBTOPICS = [
    (9001, 9, "09.01", "Bất phương trình và quy hoạch tuyến tính", "Lớp 10"),
    (9002, 9, "09.02", "Hình học không gian nền tảng", "Lớp 10"),
    (9003, 9, "09.03", "Thống kê và xác suất lớp 10", "Lớp 10"),
    (9004, 9, "09.99", "Kiến thức lớp 10 khác", "Lớp 10"),
    (10001, 10, "10.01", "Dãy số, cấp số cộng và cấp số nhân", "Lớp 11"),
    (10002, 10, "10.02", "Lượng giác và phương trình lượng giác", "Lớp 11"),
    (10003, 10, "10.03", "Hàm số mũ, logarit và phương trình mũ-logarit", "Lớp 11"),
    (10004, 10, "10.04", "Giới hạn và đạo hàm lớp 11", "Lớp 11"),
    (10005, 10, "10.99", "Kiến thức lớp 11 khác", "Lớp 11"),
]


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def fold_text(value: Any) -> str:
    text = "" if value is None else str(value)
    normalized = unicodedata.normalize("NFD", text.casefold())
    no_marks = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return no_marks.replace("đ", "d")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_json_maybe(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def clean_display_title(title: str) -> str:
    title = DISPLAY_RENAMES.get(title, title)
    title = re.sub(r"^\s*Bài\s+toán\s+", "Toán ", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*Theme\s+\d+[\.\-:]\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"^\s*Bài\s+\d+(?:\.\d+)?[\.\-:]?\s*", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*Buổi\s+\d+[\.\-:]?\s*", " - ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*-\s*Phần\s+\d+[\.\-:]?\s*", " - ", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+", " ", title).strip(" -:")
    title = DISPLAY_RENAMES.get(title, title)
    return title[:1].upper() + title[1:] if title else title


def topic_id_for_unit(unit_id: int, title: str) -> int:
    folded = fold_text(title)
    if unit_id in {18, 19, 20, 21, 22}:
        return 1
    if unit_id in {1, 2, 3, 4, 5, 50, 51, 52}:
        return 2
    if unit_id in {6, 45, 46, 47, 48, 49, 53, 54}:
        return 3
    if unit_id in {7, 8, 9, 10, 27, 28, 29, 30, 31, 32, 33, 34}:
        return 4
    if unit_id in {11, 12, 13, 42, 55, 58}:
        return 5
    if unit_id in {14, 15, 16, 17, 35, 36, 37, 38, 39, 40, 41, 56, 57, 60}:
        return 6
    if unit_id in {23, 24}:
        return 7
    if unit_id in {25, 26, 43, 44, 59, 64}:
        return 8
    if any(key in folded for key in ["nguyen ham", "tich phan", "dien tich", "the tich", "chuyen dong"]):
        return 4
    if any(key in folded for key in ["xac suat", "bayes", "so do cay"]):
        return 8
    if any(key in folded for key in ["mau so lieu", "phuong sai", "do lech chuan", "tu phan vi"]):
        return 7
    if any(key in folded for key in ["vecto", "he truc", "tich co huong"]):
        return 5
    if any(key in folded for key in ["mat phang", "duong thang", "mat cau", "oxyz", "hinh chieu", "goc", "khoang cach"]):
        return 6
    if any(key in folded for key in ["dao ham", "cuc tri", "don dieu", "tiem can", "khao sat", "gtln", "gtnn"]):
        return 2
    return 2


def topic_title(topic_id: int) -> str:
    return next(title for current_id, _code, title in TOPICS if current_id == topic_id)


def topic_code(topic_id: int) -> str:
    return next(code for current_id, code, _title in TOPICS if current_id == topic_id)


def classify_out_of_roadmap(row: sqlite3.Row) -> tuple[int, int, str]:
    if not row["canonical_needs_review"]:
        return int(row["canonical_topic_id"]), int(row["canonical_subtopic_id"]), "canonical_mapping"

    text = fold_text(" ".join(str(row[key] or "") for key in ["topic", "subtopic", "chapter", "source_file", "question_text"]))
    if any(key in text for key in ["mu", "logarit", "log", "luy thua"]):
        return 10, 10003, "out_of_roadmap_lop11_rule"
    if any(key in text for key in ["luong giac", "phuong trinh luong giac", "ham so luong giac"]):
        return 10, 10002, "out_of_roadmap_lop11_rule"
    if any(key in text for key in ["day so", "cap so cong", "cap so nhan", "lai kep"]):
        return 10, 10001, "out_of_roadmap_lop11_rule"
    if any(key in text for key in ["quy hoach tuyen tinh", "bat phuong trinh"]):
        return 9, 9001, "out_of_roadmap_lop10_rule"
    if any(key in text for key in ["khoi da dien", "hinh chop", "hinh hop", "lang tru"]):
        return 9, 9002, "out_of_roadmap_lop10_rule"
    if any(key in text for key in ["xac suat lop 10", "xac suat cua bien co", "xac suat co dien"]):
        return 9, 9003, "out_of_roadmap_lop10_rule"
    if any(key in text for key in ["gioi han lop 11", "dao ham lop 11"]):
        return 10, 10004, "out_of_roadmap_lop11_rule"

    if any(key in text for key in ["mau so lieu ghep nhom", "tu phan vi", "khoang bien thien"]):
        return 7, 23, "review_to_roadmap_statistics_rule"
    if any(key in text for key in ["phuong sai", "do lech chuan", "so dac trung", "mot cua du lieu"]):
        return 7, 24, "review_to_roadmap_statistics_rule"
    if any(key in text for key in ["xac suat co dieu kien", "cong thuc nhan"]):
        return 8, 25, "review_to_roadmap_probability_rule"
    if "bayes" in text:
        return 8, 26, "review_to_roadmap_probability_rule"
    if "so do cay" in text:
        return 8, 44, "review_to_roadmap_probability_rule"
    if any(key in text for key in ["nguyen ham"]):
        return 4, 7, "review_to_roadmap_integral_rule"
    if any(key in text for key in ["tich phan", "dien tich hinh phang"]):
        return 4, 9 if "dien tich" in text else 8, "review_to_roadmap_integral_rule"
    if any(key in text for key in ["khoi tron xoay", "the tich vat the", "the tich khoi tron xoay"]):
        return 4, 10, "review_to_roadmap_integral_application_rule"
    if any(key in text for key in ["vecto", "vec to"]):
        return 5, 11, "review_to_roadmap_oxyz_rule"
    if "he truc" in text:
        return 5, 12, "review_to_roadmap_oxyz_rule"
    if "tich co huong" in text:
        return 5, 55, "review_to_roadmap_oxyz_rule"
    if "mat phang" in text:
        return 6, 14, "review_to_roadmap_oxyz_rule"
    if "mat cau" in text:
        return 6, 17, "review_to_roadmap_oxyz_rule"
    if "duong thang" in text:
        return 6, 15, "review_to_roadmap_oxyz_rule"
    if any(key in text for key in ["hinh chieu", "doi xung"]):
        return 6, 36, "review_to_roadmap_oxyz_rule"
    if any(key in text for key in ["goc", "khoang cach"]):
        return 6, 37, "review_to_roadmap_oxyz_rule"
    if "ham doanh thu" in text or "loi nhuan" in text or "kinh te" in text:
        return 3, 53, "review_to_roadmap_derivative_application_rule"
    if any(key in text for key in ["toc do thay doi", "van toc", "quang duong", "thoi gian"]):
        return 3, 46, "review_to_roadmap_derivative_application_rule"
    if any(key in text for key in ["bai toan toi uu", "toi uu hoa", "dien tich", "the tich"]):
        return 3, 48, "review_to_roadmap_derivative_application_rule"
    if any(key in text for key in ["bai toan thuc te", "ung dung dao ham"]):
        return 3, 6, "review_to_roadmap_derivative_application_rule"
    if any(key in text for key in ["gia tri lon nhat", "gia tri nho nhat", "gtln", "gtnn"]):
        return 2, 3, "review_to_roadmap_function_graph_rule"
    if "cuc tri" in text:
        return 2, 2, "review_to_roadmap_function_graph_rule"
    if "tiem can xien" in text:
        return 2, 51, "review_to_roadmap_function_graph_rule"
    if "tiem can" in text:
        return 2, 4, "review_to_roadmap_function_graph_rule"
    if any(key in text for key in ["dong bien", "nghich bien", "don dieu"]):
        return 2, 1, "review_to_roadmap_function_graph_rule"
    if any(key in text for key in ["nhan dang do thi", "nhan dang ham so", "do thi ham so", "ham so phan thuc", "ham so huu ti", "ham bac ba", "khao sat"]):
        return 2, 52 if "phan thuc" in text else 5, "review_to_roadmap_function_graph_rule"
    if "gioi han" in text:
        return 1, 18, "review_to_roadmap_prerequisite_rule"
    if "dao ham" in text:
        return 1, 21, "review_to_roadmap_prerequisite_rule"
    if "thong ke" in text:
        return 9, 9003, "out_of_roadmap_lop10_rule"
    if "hinh hoc khong gian" in text:
        return 9, 9002, "out_of_roadmap_lop10_rule"
    if row["canonical_topic_id"] and row["canonical_subtopic_id"]:
        return int(row["canonical_topic_id"]), int(row["canonical_subtopic_id"]), "canonical_review_kept"
    return 9, 9004, "out_of_roadmap_unknown_rule"


def create_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        create table if not exists canonical_topics (
            id integer primary key,
            topic_code text not null unique,
            topic_title text not null,
            topic_order integer not null
        )
        """
    )
    db.execute(
        """
        create table if not exists canonical_subtopics (
            id integer primary key,
            topic_id integer not null,
            subtopic_code text not null unique,
            subtopic_title text not null,
            display_title text not null,
            subtopic_order integer not null,
            source_unit_id integer,
            phase text,
            is_out_of_roadmap integer not null default 0,
            foreign key(topic_id) references canonical_topics(id)
        )
        """
    )
    db.execute("delete from canonical_topics")
    db.execute("delete from canonical_subtopics")


def load_source_unit(db: sqlite3.Connection, unit_id: int) -> dict[str, Any]:
    row = db.execute("select * from canonical_roadmap_units where id = ?", (unit_id,)).fetchone()
    if not row:
        raise ValueError(f"Missing canonical_roadmap_units id={unit_id}")
    return dict(row)


def build_taxonomy(root: Path) -> dict[str, Any]:
    db_path = root / "output_sqlite" / "curriculum.sqlite"
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    create_schema(db)

    for topic_id, code, title in TOPICS:
        db.execute(
            "insert into canonical_topics (id, topic_code, topic_title, topic_order) values (?, ?, ?, ?)",
            (topic_id, code, title, topic_id),
        )

    units = [dict(row) for row in db.execute("select * from canonical_roadmap_units order by id")]
    unit_topic: dict[int, int] = {}
    unit_display: dict[int, str] = {}
    per_topic_counts: dict[int, int] = {}
    for unit in units:
        unit_id = int(unit["id"])
        original_title = unit["canonical_title"]
        display_title = clean_display_title(original_title)
        topic_id = topic_id_for_unit(unit_id, display_title)
        unit_topic[unit_id] = topic_id
        unit_display[unit_id] = display_title
        per_topic_counts[topic_id] = per_topic_counts.get(topic_id, 0) + 1
        subtopic_code = f"{topic_code(topic_id)}.{per_topic_counts[topic_id]:02d}"
        db.execute(
            """
            insert into canonical_subtopics (
                id, topic_id, subtopic_code, subtopic_title, display_title,
                subtopic_order, source_unit_id, phase, is_out_of_roadmap
            ) values (?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (unit_id, topic_id, subtopic_code, display_title, display_title, per_topic_counts[topic_id], unit_id, unit.get("phase")),
        )

    for subtopic_id, topic_id, code, title, phase in EXTRA_SUBTOPICS:
        db.execute(
            """
            insert into canonical_subtopics (
                id, topic_id, subtopic_code, subtopic_title, display_title,
                subtopic_order, source_unit_id, phase, is_out_of_roadmap
            ) values (?, ?, ?, ?, ?, ?, null, ?, 1)
            """,
            (subtopic_id, topic_id, code, title, title, subtopic_id % 1000, phase),
        )

    subtopic_topic = {
        int(row["id"]): int(row["topic_id"])
        for row in db.execute("select id, topic_id from canonical_subtopics")
    }

    db.execute("drop table if exists question_topic_overrides")
    db.execute(
        """
        create table question_topic_overrides (
            question_id text primary key,
            final_topic_id integer not null,
            final_subtopic_id integer not null,
            assignment_reason text not null
        )
        """
    )
    base_rows = db.execute(
        """
        select
            q.id as question_id,
            q.topic, q.subtopic, q.chapter, q.source_file, q.question_text,
            m.canonical_unit_id,
            m.needs_review as canonical_needs_review,
            s.topic_id as canonical_topic_id,
            s.id as canonical_subtopic_id
        from questions_local q
        join question_canonical_map m on m.question_id = q.id
        join canonical_subtopics s on s.source_unit_id = m.canonical_unit_id
        """
    ).fetchall()
    for row in base_rows:
        topic_id, subtopic_id, reason = classify_out_of_roadmap(row)
        actual_topic_id = subtopic_topic.get(subtopic_id)
        if actual_topic_id is None:
            topic_id, subtopic_id, reason = 9, 9004, "out_of_roadmap_unknown_rule"
        elif topic_id != actual_topic_id:
            topic_id = actual_topic_id
            reason = f"{reason}_topic_normalized"
        db.execute(
            """
            insert into question_topic_overrides (
                question_id, final_topic_id, final_subtopic_id, assignment_reason
            ) values (?, ?, ?, ?)
            """,
            (row["question_id"], topic_id, subtopic_id, reason),
        )

    db.execute("drop view if exists questions_standardized_v2")
    db.execute(
        """
        create view questions_standardized_v2 as
        select
            q.*,
            m.canonical_unit_id,
            m.canonical_title as old_canonical_title,
            m.application_type as canonical_application_type,
            m.confidence as canonical_confidence,
            m.match_method as canonical_match_method,
            m.match_reason as canonical_match_reason,
            case
                when o.assignment_reason like 'out_of_roadmap_%_rule' then 0
                when o.assignment_reason like 'review_to_roadmap_%_rule' then 0
                else m.needs_review
            end as canonical_needs_review,
            m.needs_review as old_canonical_needs_review,
            t.id as canonical_topic_id,
            t.topic_code as canonical_topic_code,
            t.topic_title as canonical_topic_title,
            s.id as canonical_subtopic_id,
            s.subtopic_code as canonical_subtopic_code,
            s.subtopic_title as canonical_subtopic_title,
            s.display_title as canonical_display_title,
            s.is_out_of_roadmap,
            o.assignment_reason
        from questions_local q
        join question_canonical_map m on m.question_id = q.id
        join question_topic_overrides o on o.question_id = q.id
        join canonical_subtopics s on s.id = o.final_subtopic_id
        join canonical_topics t on t.id = s.topic_id
        """
    )

    db.commit()
    raw_prefix_count = 0
    for row in db.execute("select subtopic_title from canonical_subtopics where source_unit_id is not null"):
        if fold_text(row["subtopic_title"]).startswith(("bai ", "buoi ", "phan ", "theme ")):
            raw_prefix_count += 1

    summary = {
        "topics": db.execute("select count(*) from canonical_topics").fetchone()[0],
        "subtopics": db.execute("select count(*) from canonical_subtopics").fetchone()[0],
        "questions_v2": db.execute("select count(*) from questions_standardized_v2").fetchone()[0],
        "out_of_roadmap_questions": db.execute("select count(*) from questions_standardized_v2 where is_out_of_roadmap = 1").fetchone()[0],
        "needs_review": db.execute("select count(*) from questions_standardized_v2 where canonical_needs_review = 1").fetchone()[0],
        "old_needs_review": db.execute("select count(*) from questions_standardized_v2 where old_canonical_needs_review = 1").fetchone()[0],
        "topic_subtopic_mismatches": db.execute(
            """
            select count(*)
            from question_topic_overrides o
            join canonical_subtopics s on s.id = o.final_subtopic_id
            where o.final_topic_id != s.topic_id
            """
        ).fetchone()[0],
        "raw_prefix_subtopics": raw_prefix_count,
    }
    db.close()
    return summary


def rows_as_dicts(db: sqlite3.Connection, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in db.execute(query, params).fetchall()]


def render_canonical_html(root: Path) -> None:
    db = sqlite3.connect(root / "output_sqlite" / "curriculum.sqlite")
    db.row_factory = sqlite3.Row
    topics = rows_as_dicts(db, "select * from canonical_topics order by topic_order")
    sections = []
    for topic in topics:
        subtopics = rows_as_dicts(
            db,
            """
            select s.*, coalesce(count(q.id), 0) as question_count
            from canonical_subtopics s
            left join questions_standardized_v2 q on q.canonical_subtopic_id = s.id
            where s.topic_id = ?
            group by s.id
            order by s.subtopic_order, s.id
            """,
            (topic["id"],),
        )
        rows = []
        for subtopic in subtopics:
            source_unit = load_source_unit(db, subtopic["source_unit_id"]) if subtopic["source_unit_id"] else None
            apps = parse_json_maybe(source_unit.get("application_types_json")) if source_unit else []
            goals = parse_json_maybe(source_unit.get("learning_goals_json")) if source_unit else []
            rows.append(
                "<tr>"
                f"<td>{html.escape(subtopic['subtopic_code'])}</td>"
                f"<td>{html.escape(subtopic['subtopic_title'])}</td>"
                f"<td>{html.escape(subtopic['phase'] or '')}</td>"
                f"<td>{subtopic['question_count']}</td>"
                f"<td>{html.escape('; '.join((goals or [])[:4]))}</td>"
                f"<td>{html.escape('; '.join((apps or [])[:5]))}</td>"
                "</tr>"
            )
        sections.append(
            f"""
            <section>
              <h2>{html.escape(topic['topic_code'])}. {html.escape(topic['topic_title'])}</h2>
              <table>
                <thead>
                  <tr><th>Mã</th><th>Subtopic chuẩn</th><th>Phase</th><th>Số câu</th><th>Mục tiêu</th><th>Dạng câu áp dụng</th></tr>
                </thead>
                <tbody>{''.join(rows)}</tbody>
              </table>
            </section>
            """
        )
    total_questions = db.execute("select count(*) from questions_standardized_v2").fetchone()[0]
    total_subtopics = db.execute("select count(*) from canonical_subtopics").fetchone()[0]
    db.close()
    html_text = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Canonical Roadmap V2</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f6f7f9; color: #1f2328; }}
    header {{ padding: 24px 32px; background: #fff; border-bottom: 1px solid #d8dee4; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    main {{ padding: 24px 32px 40px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .metric {{ background: #fff; border: 1px solid #d8dee4; border-radius: 6px; padding: 16px; }}
    .metric strong {{ display: block; font-size: 24px; margin-bottom: 4px; }}
    section {{ margin-bottom: 28px; }}
    h2 {{ font-size: 20px; margin: 0 0 12px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dee4; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #eaeef2; vertical-align: top; text-align: left; font-size: 13px; }}
    th {{ position: sticky; top: 0; background: #eef2f5; z-index: 1; }}
  </style>
</head>
<body>
  <header>
    <h1>Canonical Roadmap V2</h1>
    <div>Topic lớn → Subtopic chuẩn · Generated at {html.escape(datetime.now().isoformat(timespec='seconds'))}</div>
  </header>
  <main>
    <section class="summary">
      <div class="metric"><strong>{len(TOPICS)}</strong>Topics</div>
      <div class="metric"><strong>{total_subtopics}</strong>Subtopics</div>
      <div class="metric"><strong>{total_questions}</strong>Questions</div>
    </section>
    {''.join(sections)}
  </main>
</body>
</html>
"""
    (root / "previews" / "canonical_roadmap.html").write_text(html_text, encoding="utf-8")
    (root / "previews" / "canonical_roadmap_v2.html").write_text(html_text, encoding="utf-8")


def render_question_mapping_html(root: Path, limit: int) -> None:
    db = sqlite3.connect(root / "output_sqlite" / "curriculum.sqlite")
    db.row_factory = sqlite3.Row
    rows = rows_as_dicts(
        db,
        """
        select canonical_topic_code, canonical_topic_title, canonical_subtopic_code,
               canonical_subtopic_title, canonical_application_type, canonical_confidence,
               canonical_match_method, canonical_needs_review, is_out_of_roadmap,
               topic, subtopic, source_file, question_type, question_text
        from questions_standardized_v2
        order by canonical_needs_review desc, is_out_of_roadmap desc,
                 canonical_topic_code, canonical_subtopic_code, canonical_confidence asc
        limit ?
        """,
        (limit,),
    )
    summary = {
        "total": db.execute("select count(*) from questions_standardized_v2").fetchone()[0],
        "needs_review": db.execute("select count(*) from questions_standardized_v2 where canonical_needs_review = 1").fetchone()[0],
        "out_of_roadmap": db.execute("select count(*) from questions_standardized_v2 where is_out_of_roadmap = 1").fetchone()[0],
        "topics_used": db.execute("select count(distinct canonical_topic_id) from questions_standardized_v2").fetchone()[0],
    }
    db.close()
    body = []
    for row in rows:
        body.append(
            "<tr>"
            f"<td>{'review' if row['canonical_needs_review'] else 'ok'}</td>"
            f"<td>{'extra' if row['is_out_of_roadmap'] else 'roadmap'}</td>"
            f"<td>{html.escape(row['canonical_topic_code'] + '. ' + row['canonical_topic_title'])}</td>"
            f"<td>{html.escape(row['canonical_subtopic_code'] + '. ' + row['canonical_subtopic_title'])}</td>"
            f"<td>{html.escape(row['canonical_application_type'] or '')}</td>"
            f"<td>{html.escape(str(row['canonical_confidence']))}</td>"
            f"<td>{html.escape(row['topic'] or '')}<br>{html.escape(row['subtopic'] or '')}</td>"
            f"<td>{html.escape(row['source_file'] or '')}</td>"
            f"<td>{html.escape((row['question_text'] or '')[:420])}</td>"
            f"<td>{html.escape(row['canonical_match_method'] or '')}</td>"
            "</tr>"
        )
    html_text = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Question Canonical Mapping V2</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f6f7f9; color: #1f2328; }}
    header {{ padding: 24px 32px; background: #fff; border-bottom: 1px solid #d8dee4; }}
    h1 {{ margin: 0 0 8px; font-size: 28px; }}
    main {{ padding: 24px 32px 40px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .metric {{ background: #fff; border: 1px solid #d8dee4; border-radius: 6px; padding: 16px; }}
    .metric strong {{ display: block; font-size: 24px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dee4; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #eaeef2; vertical-align: top; text-align: left; font-size: 13px; }}
    th {{ position: sticky; top: 0; background: #eef2f5; z-index: 1; }}
  </style>
</head>
<body>
  <header>
    <h1>Question Canonical Mapping V2</h1>
    <div>Generated at {html.escape(datetime.now().isoformat(timespec='seconds'))}</div>
  </header>
  <main>
    <section class="summary">
      <div class="metric"><strong>{summary['total']}</strong>Total</div>
      <div class="metric"><strong>{summary['needs_review']}</strong>Needs review</div>
      <div class="metric"><strong>{summary['out_of_roadmap']}</strong>Lớp 10/11 extra</div>
      <div class="metric"><strong>{summary['topics_used']}</strong>Topics used</div>
    </section>
    <table>
      <thead>
        <tr>
          <th>Status</th><th>Scope</th><th>Topic lớn</th><th>Subtopic chuẩn</th><th>Dạng câu</th>
          <th>Confidence</th><th>Old topic</th><th>Source file</th><th>Question</th><th>Method</th>
        </tr>
      </thead>
      <tbody>{''.join(body)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    (root / "previews" / "question_canonical_mapping.html").write_text(html_text, encoding="utf-8")
    (root / "previews" / "question_canonical_mapping_v2.html").write_text(html_text, encoding="utf-8")


def export_json(root: Path) -> None:
    db = sqlite3.connect(root / "output_sqlite" / "curriculum.sqlite")
    db.row_factory = sqlite3.Row
    topics = rows_as_dicts(db, "select * from canonical_topics order by topic_order")
    subtopics = rows_as_dicts(db, "select * from canonical_subtopics order by topic_id, subtopic_order, id")
    question_summary = rows_as_dicts(
        db,
        """
        select canonical_topic_code, canonical_topic_title, canonical_subtopic_code,
               canonical_subtopic_title, count(*) as question_count,
               sum(canonical_needs_review) as needs_review_count
        from questions_standardized_v2
        group by canonical_topic_code, canonical_topic_title, canonical_subtopic_code, canonical_subtopic_title
        order by canonical_topic_code, canonical_subtopic_code
        """,
    )
    db.close()
    path = root / "output_json" / "taxonomy_v2.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "topics": topics,
                "subtopics": subtopics,
                "question_summary": question_summary,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build topic/subtopic taxonomy v2 over local canonical roadmap and questions.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--preview-limit", type=int, default=1000)
    return parser.parse_args()


def main() -> None:
    configure_stdio()
    args = parse_args()
    root = Path(args.root)
    summary = build_taxonomy(root)
    render_canonical_html(root)
    render_question_mapping_html(root, args.preview_limit)
    export_json(root)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Wrote {(root / 'previews' / 'canonical_roadmap.html').resolve()}")
    print(f"Wrote {(root / 'previews' / 'question_canonical_mapping.html').resolve()}")
    print(f"Wrote {(root / 'output_json' / 'taxonomy_v2.json').resolve()}")


if __name__ == "__main__":
    main()
