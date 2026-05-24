import argparse
import html
import json
import os
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from supabase import create_client

from ingest_pipeline import SUPABASE_KEY, SUPABASE_URL


DEFAULT_ROOT = Path("local_curriculum")
QUESTION_COLUMNS = [
    "id",
    "source_code",
    "question_type",
    "topic",
    "subtopic",
    "chapter",
    "part",
    "question_text",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "correct_answer",
    "statements",
    "numeric_answer",
    "difficulty",
    "needs_visual",
    "visual_type",
    "source_hint",
    "source_file",
    "page_number",
    "raw_text",
    "is_published",
    "needs_review",
]


STOPWORDS = {
    "bai",
    "theme",
    "cau",
    "cho",
    "tim",
    "tinh",
    "xac",
    "dinh",
    "hay",
    "hoi",
    "dung",
    "sai",
    "ham",
    "so",
    "cua",
    "va",
    "trong",
    "tren",
    "mot",
    "cac",
    "gia",
    "tri",
    "phuong",
    "trinh",
    "mat",
    "toan",
    "thuc",
    "te",
    "ung",
    "dung",
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def fold_text(value: Any) -> str:
    text = "" if value is None else str(value)
    normalized = unicodedata.normalize("NFD", text.casefold())
    no_marks = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return no_marks.replace("đ", "d")


def tokens(value: Any) -> set[str]:
    folded = fold_text(value)
    words = re.findall(r"[a-z0-9]+", folded)
    result = {word for word in words if len(word) >= 3 and word not in STOPWORDS and not word.isdigit()}
    if {"don", "dieu"} <= result:
        result.update({"dong", "nghich", "bien"})
    if {"dong", "bien"} <= result or {"nghich", "bien"} <= result:
        result.update({"don", "dieu"})
    if "maxmin" in result or {"lon", "nhat"} <= result or {"nho", "nhat"} <= result:
        result.update({"gtln", "gtnn", "maxmin"})
    return result


def similarity(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def safe_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def parse_json_maybe(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not value or not isinstance(value, str):
        return None
    try:
        return json.loads(value)
    except Exception:
        return None


def question_blob(row: dict[str, Any]) -> str:
    pieces = [
        row.get("topic"),
        row.get("subtopic"),
        row.get("chapter"),
        row.get("question_text"),
        row.get("option_a"),
        row.get("option_b"),
        row.get("option_c"),
        row.get("option_d"),
        row.get("source_file"),
    ]
    statements = parse_json_maybe(row.get("statements"))
    if isinstance(statements, list):
        pieces.extend(item.get("text") for item in statements if isinstance(item, dict))
    raw_item = parse_json_maybe(row.get("raw_text"))
    if isinstance(raw_item, dict):
        pieces.extend([raw_item.get("topic"), raw_item.get("subtopic"), raw_item.get("chapter")])
    return "\n".join(str(piece) for piece in pieces if piece)


def open_db(root: Path) -> sqlite3.Connection:
    db_path = root / "output_sqlite" / "curriculum.sqlite"
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.execute(
        """
        create table if not exists questions_local (
            id text primary key,
            source_code text,
            question_type text,
            topic text,
            subtopic text,
            chapter text,
            part text,
            question_text text,
            option_a text,
            option_b text,
            option_c text,
            option_d text,
            correct_answer text,
            statements text,
            numeric_answer text,
            difficulty text,
            needs_visual integer,
            visual_type text,
            source_hint text,
            source_file text,
            page_number integer,
            raw_text text,
            is_published integer,
            needs_review integer,
            imported_at text not null
        )
        """
    )
    db.execute(
        """
        create table if not exists question_canonical_map (
            question_id text primary key,
            canonical_unit_id integer not null,
            canonical_title text not null,
            application_type text,
            confidence real not null,
            match_method text not null,
            match_reason text not null,
            needs_review integer not null,
            mapped_at text not null
        )
        """
    )
    db.execute("create index if not exists idx_questions_local_source_file on questions_local(source_file)")
    db.execute("create index if not exists idx_question_canonical_unit on question_canonical_map(canonical_unit_id)")
    return db


def normalize_question_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {column: row.get(column) for column in QUESTION_COLUMNS}
    if not normalized.get("id"):
        key_blob = "|".join(str(normalized.get(key) or "") for key in ("source_code", "source_file", "page_number", "question_text"))
        normalized["id"] = str(abs(hash(key_blob)))
    normalized["statements"] = safe_json(normalized.get("statements"))
    normalized["raw_text"] = safe_json(normalized.get("raw_text"))
    return normalized


def upsert_questions(db: sqlite3.Connection, rows: list[dict[str, Any]]) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    for row in rows:
        normalized = normalize_question_row(row)
        db.execute(
            """
            insert or replace into questions_local (
                id, source_code, question_type, topic, subtopic, chapter, part, question_text,
                option_a, option_b, option_c, option_d, correct_answer, statements, numeric_answer,
                difficulty, needs_visual, visual_type, source_hint, source_file, page_number,
                raw_text, is_published, needs_review, imported_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized.get("id"),
                normalized.get("source_code"),
                normalized.get("question_type"),
                normalized.get("topic"),
                normalized.get("subtopic"),
                normalized.get("chapter"),
                normalized.get("part"),
                normalized.get("question_text"),
                normalized.get("option_a"),
                normalized.get("option_b"),
                normalized.get("option_c"),
                normalized.get("option_d"),
                normalized.get("correct_answer"),
                normalized.get("statements"),
                None if normalized.get("numeric_answer") is None else str(normalized.get("numeric_answer")),
                normalized.get("difficulty"),
                int(bool(normalized.get("needs_visual"))),
                normalized.get("visual_type"),
                normalized.get("source_hint"),
                normalized.get("source_file"),
                normalized.get("page_number"),
                normalized.get("raw_text"),
                int(bool(normalized.get("is_published"))),
                int(bool(normalized.get("needs_review"))),
                now,
            ),
        )
    db.commit()
    return len(rows)


def fetch_supabase_questions(limit: int = 0, batch_size: int = 1000) -> list[dict[str, Any]]:
    client = create_client(os.getenv("SUPABASE_URL", SUPABASE_URL), os.getenv("SUPABASE_KEY", SUPABASE_KEY))
    rows: list[dict[str, Any]] = []
    start = 0
    select_cols = ",".join(QUESTION_COLUMNS)
    while True:
        end = start + batch_size - 1
        if limit > 0:
            end = min(end, limit - 1)
        result = client.table("questions").select(select_cols).range(start, end).execute()
        batch = result.data or []
        rows.extend(batch)
        print(f"Fetched {len(rows)} questions", flush=True)
        if not batch or len(batch) < batch_size or (limit > 0 and len(rows) >= limit):
            break
        start += batch_size
    return rows[:limit] if limit > 0 else rows


def load_questions_from_json(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        rows = data.get("questions") or data.get("rows") or data.get("data") or []
        return [row for row in rows if isinstance(row, dict)]
    return []


def load_canonical_units(db: sqlite3.Connection) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    for row in db.execute("select * from canonical_roadmap_units order by id"):
        unit = dict(row)
        unit["source_lessons"] = parse_json_maybe(unit.get("source_lessons_json")) or []
        unit["learning_goals"] = parse_json_maybe(unit.get("learning_goals_json")) or []
        unit["application_types"] = parse_json_maybe(unit.get("application_types_json")) or []
        signature = "\n".join(
            [
                str(unit.get("canonical_title") or ""),
                str(unit.get("program_area") or ""),
                str(unit.get("phase") or ""),
                "\n".join(unit["learning_goals"]),
                "\n".join(unit["application_types"]),
            ]
        )
        unit["_tokens"] = tokens(signature)
        unit["_app_tokens"] = [(app, tokens(app)) for app in unit["application_types"]]
        units.append(unit)
    return units


def build_source_file_index(db: sqlite3.Connection, units: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    lesson_to_unit: dict[tuple[str, int], dict[str, Any]] = {}
    for unit in units:
        for lesson in unit.get("source_lessons", []):
            lesson_to_unit[(str(lesson.get("source")), int(lesson.get("source_order") or 0))] = unit

    source_file_index: dict[str, dict[str, Any]] = {}
    for row in db.execute("select source, source_order, file_name from curriculum_lessons"):
        unit = lesson_to_unit.get((row["source"], int(row["source_order"])))
        if unit:
            source_file_index[fold_text(row["file_name"]).strip()] = unit
    return source_file_index


def build_unit_title_index(units: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {fold_text(unit["canonical_title"]).strip(): unit for unit in units}


def find_unit_by_title(unit_title_index: dict[str, dict[str, Any]], title: str) -> dict[str, Any] | None:
    wanted = fold_text(title).strip()
    if wanted in unit_title_index:
        return unit_title_index[wanted]
    for key, unit in unit_title_index.items():
        if wanted in key or key in wanted:
            return unit
    return None


def alias_unit(row: dict[str, Any], unit_title_index: dict[str, dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    text = fold_text(
        " ".join(
            str(value or "")
            for value in [
                row.get("topic"),
                row.get("subtopic"),
                row.get("chapter"),
                row.get("source_file"),
                row.get("source_hint"),
            ]
        )
    )
    rules = [
        (["bayes"], "Công Thức Bayes"),
        (["xac suat toan phan"], "Công thức xác suất toàn phần và công thức Bayes"),
        (["cong thuc xac suat toan phan"], "Công thức xác suất toàn phần và công thức Bayes"),
        (["xac suat co dieu kien"], "Xác suất có điều kiện và công thức nhân xác suất"),
        (["so do cay"], "Sử dụng sơ đồ cây cho bài toán xác suất nâng cao"),
        (["mau so lieu ghep nhom", "khoang bien thien"], "Khoảng biến thiên và khoảng tứ phân vị của mẫu số liệu ghép nhóm"),
        (["phuong sai"], "Phương sai và độ lệch chuẩn của mẫu số liệu ghép nhóm"),
        (["do lech chuan"], "Phương sai và độ lệch chuẩn của mẫu số liệu ghép nhóm"),
        (["so dac trung"], "Phương sai và độ lệch chuẩn của mẫu số liệu ghép nhóm"),
        (["nguyen ham"], "Nguyên hàm và tính chất của nguyên hàm"),
        (["tich phan", "dien tich"], "Ứng dụng của tích phân vào tính diện tích hình phẳng"),
        (["tich phan", "the tich"], "Ứng dụng của tích phân vào tính thể tích vật thể"),
        (["tich phan"], "Tích phân và tính chất của tích phân"),
        (["gioi han"], "Giới hạn hàm số khi x → a"),
        (["lien tuc"], "Hàm số liên tục"),
        (["dao ham", "toc do"], "Bài toán thực tế liên quan đến tốc độ thay đổi của một đại lượng"),
        (["kinh te"], "Bài toán thực tế về hàm doanh thu, lợi nhuận, tối ưu chi phí"),
        (["gtln"], "Giá trị lớn nhất – giá trị nhỏ nhất của hàm số"),
        (["gtnn"], "Giá trị lớn nhất – giá trị nhỏ nhất của hàm số"),
        (["maxmin"], "Giá trị lớn nhất – giá trị nhỏ nhất của hàm số"),
        (["cuc tri"], "Cực trị của hàm số"),
        (["tiem can"], "Đường tiệm cận của đồ thị hàm số"),
        (["don dieu"], "Sự đồng biến, nghịch biến của hàm số"),
        (["dong bien"], "Sự đồng biến, nghịch biến của hàm số"),
        (["nghich bien"], "Sự đồng biến, nghịch biến của hàm số"),
        (["khao sat"], "Khảo sát sự biến thiên và vẽ đồ thị hàm số"),
        (["mat cau"], "Phương trình mặt cầu"),
        (["mat phang"], "Phương trình mặt phẳng"),
        (["duong thang"], "Phương trình đường thẳng"),
        (["hinh chieu"], "Các bài toán liên quan đến tìm hình chiếu vuông góc, đối xứng"),
        (["doi xung"], "Các bài toán liên quan đến tìm hình chiếu vuông góc, đối xứng"),
        (["toa do", "vecto"], "Biểu thức tọa độ của các phép toán vectơ"),
        (["vecto"], "Vectơ và các phép toán vectơ trong không gian"),
        (["oxyz"], "Hệ trục tọa độ trong không gian"),
        (["xac suat"], "Xác suất có điều kiện và công thức nhân xác suất"),
    ]
    for required, title in rules:
        if all(term in text for term in required):
            unit = find_unit_by_title(unit_title_index, title)
            if unit:
                return unit, f"alias rule: {', '.join(required)} -> {title}"
    return None, ""


def choose_application_type(unit: dict[str, Any], q_tokens: set[str]) -> tuple[str | None, float]:
    best_app = None
    best_score = 0.0
    for app, app_tokens in unit.get("_app_tokens", []):
        score = similarity(q_tokens, app_tokens)
        if score > best_score:
            best_app = app
            best_score = score
    return best_app, best_score


def map_question(row: dict[str, Any], units: list[dict[str, Any]], source_file_index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    q_blob = question_blob(row)
    q_tokens = tokens(q_blob)
    source_file_key = fold_text(row.get("source_file")).strip()

    if source_file_key in source_file_index:
        unit = source_file_index[source_file_key]
        app, app_score = choose_application_type(unit, q_tokens)
        return {
            "question_id": row["id"],
            "canonical_unit_id": int(unit["id"]),
            "canonical_title": unit["canonical_title"],
            "application_type": app,
            "confidence": round(0.92 + min(app_score, 0.07), 3),
            "match_method": "source_file",
            "match_reason": f"source_file matched curriculum lesson: {row.get('source_file')}",
            "needs_review": 0,
        }

    unit_title_index = build_unit_title_index(units)
    alias_matched_unit, alias_reason = alias_unit(row, unit_title_index)
    if alias_matched_unit is not None:
        app, app_score = choose_application_type(alias_matched_unit, q_tokens)
        return {
            "question_id": row["id"],
            "canonical_unit_id": int(alias_matched_unit["id"]),
            "canonical_title": alias_matched_unit["canonical_title"],
            "application_type": app,
            "confidence": round(0.76 + min(app_score, 0.12), 3),
            "match_method": "alias_rule",
            "match_reason": alias_reason,
            "needs_review": 0,
        }

    best_unit = None
    best_score = 0.0
    for unit in units:
        score = similarity(q_tokens, unit["_tokens"])
        if score > best_score:
            best_unit = unit
            best_score = score

    if best_unit is None:
        raise ValueError("No canonical units available")

    app, app_score = choose_application_type(best_unit, q_tokens)
    confidence = min(0.89, best_score * 1.9 + app_score * 0.5)
    needs_review = confidence < 0.42
    return {
        "question_id": row["id"],
        "canonical_unit_id": int(best_unit["id"]),
        "canonical_title": best_unit["canonical_title"],
        "application_type": app,
        "confidence": round(confidence, 3),
        "match_method": "semantic_tokens",
        "match_reason": f"token_similarity={best_score:.3f}; application_similarity={app_score:.3f}",
        "needs_review": int(needs_review),
    }


def save_mappings(db: sqlite3.Connection, mappings: list[dict[str, Any]]) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    db.execute("delete from question_canonical_map")
    for item in mappings:
        db.execute(
            """
            insert into question_canonical_map (
                question_id, canonical_unit_id, canonical_title, application_type,
                confidence, match_method, match_reason, needs_review, mapped_at
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item["question_id"],
                item["canonical_unit_id"],
                item["canonical_title"],
                item.get("application_type"),
                item["confidence"],
                item["match_method"],
                item["match_reason"],
                item["needs_review"],
                now,
            ),
        )
    db.execute("drop view if exists questions_standardized")
    db.execute(
        """
        create view questions_standardized as
        select
            q.*,
            m.canonical_unit_id,
            m.canonical_title,
            m.application_type as canonical_application_type,
            m.confidence as canonical_confidence,
            m.match_method as canonical_match_method,
            m.match_reason as canonical_match_reason,
            m.needs_review as canonical_needs_review
        from questions_local q
        join question_canonical_map m on m.question_id = q.id
        """
    )
    db.commit()


def render_preview(root: Path, db: sqlite3.Connection, limit: int = 500) -> None:
    rows = db.execute(
        """
        select q.id, q.question_type, q.topic, q.subtopic, q.chapter, q.source_file, q.question_text,
               m.canonical_unit_id, m.canonical_title, m.application_type, m.confidence,
               m.match_method, m.match_reason, m.needs_review
        from questions_local q
        join question_canonical_map m on m.question_id = q.id
        order by m.needs_review desc, m.confidence asc, q.source_file, q.page_number
        limit ?
        """,
        (limit,),
    ).fetchall()
    summary = dict(
        total=db.execute("select count(*) from question_canonical_map").fetchone()[0],
        review=db.execute("select count(*) from question_canonical_map where needs_review = 1").fetchone()[0],
        source_file=db.execute("select count(*) from question_canonical_map where match_method = 'source_file'").fetchone()[0],
        semantic=db.execute("select count(*) from question_canonical_map where match_method = 'semantic_tokens'").fetchone()[0],
    )
    body = []
    for row in rows:
        badge = "review" if row["needs_review"] else "ok"
        body.append(
            "<tr>"
            f"<td>{html.escape(badge)}</td>"
            f"<td>{html.escape(str(row['confidence']))}</td>"
            f"<td>{html.escape(row['canonical_title'] or '')}</td>"
            f"<td>{html.escape(row['application_type'] or '')}</td>"
            f"<td>{html.escape(row['question_type'] or '')}</td>"
            f"<td>{html.escape(row['topic'] or '')}<br>{html.escape(row['subtopic'] or '')}</td>"
            f"<td>{html.escape(row['source_file'] or '')}</td>"
            f"<td>{html.escape((row['question_text'] or '')[:500])}</td>"
            f"<td>{html.escape(row['match_method'] or '')}<br>{html.escape(row['match_reason'] or '')}</td>"
            "</tr>"
        )

    html_text = f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Question Canonical Mapping</title>
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
    tr:hover td {{ background: #fafbfc; }}
  </style>
</head>
<body>
  <header>
    <h1>Question Canonical Mapping</h1>
    <div>Generated at {html.escape(datetime.now().isoformat(timespec='seconds'))}</div>
  </header>
  <main>
    <section class="summary">
      <div class="metric"><strong>{summary['total']}</strong>Total mapped</div>
      <div class="metric"><strong>{summary['review']}</strong>Needs review</div>
      <div class="metric"><strong>{summary['source_file']}</strong>Source-file matches</div>
      <div class="metric"><strong>{summary['semantic']}</strong>Semantic matches</div>
    </section>
    <table>
      <thead>
        <tr>
          <th>Status</th><th>Confidence</th><th>Canonical unit</th><th>Dạng câu áp dụng</th>
          <th>Type</th><th>Old topic</th><th>Source file</th><th>Question</th><th>Match</th>
        </tr>
      </thead>
      <tbody>{''.join(body)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    path = root / "previews" / "question_canonical_mapping.html"
    path.write_text(html_text, encoding="utf-8")
    print(f"Wrote {path.resolve()}")


def export_mapping_json(root: Path, db: sqlite3.Connection) -> None:
    rows = [
        dict(row)
        for row in db.execute(
            """
            select q.*, m.canonical_unit_id, m.canonical_title, m.application_type,
                   m.confidence, m.match_method, m.match_reason, m.needs_review as mapping_needs_review
            from questions_local q
            join question_canonical_map m on m.question_id = q.id
            order by m.canonical_unit_id, q.source_file, q.page_number
            """
        )
    ]
    path = root / "output_json" / "question_canonical_mapping.json"
    path.write_text(json.dumps({"generated_at": datetime.now().isoformat(timespec="seconds"), "rows": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {path.resolve()}")


def map_all_questions(db: sqlite3.Connection) -> list[dict[str, Any]]:
    units = load_canonical_units(db)
    source_file_index = build_source_file_index(db, units)
    mappings = []
    for row in db.execute("select * from questions_local order by source_file, page_number"):
        mappings.append(map_question(dict(row), units, source_file_index))
    save_mappings(db, mappings)
    return mappings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Mirror questions locally and map them to canonical roadmap units.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--from-supabase", action="store_true", help="Read questions from Supabase and mirror to local SQLite.")
    parser.add_argument("--from-json", action="append", help="Import questions from a JSON file; can be repeated.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--preview-limit", type=int, default=500)
    parser.add_argument("--replace-local", action="store_true", help="Clear local question tables before importing.")
    return parser.parse_args()


def main() -> None:
    configure_stdio()
    args = parse_args()
    root = Path(args.root)
    db = open_db(root)

    if args.replace_local:
        db.execute("delete from question_canonical_map")
        db.execute("delete from questions_local")
        db.commit()

    imported = 0
    if args.from_supabase:
        rows = fetch_supabase_questions(limit=args.limit)
        imported += upsert_questions(db, rows)
    for json_path in args.from_json or []:
        rows = load_questions_from_json(Path(json_path))
        if args.limit > 0:
            rows = rows[: args.limit]
        imported += upsert_questions(db, rows)

    existing = db.execute("select count(*) from questions_local").fetchone()[0]
    if existing == 0:
        raise SystemExit("No questions_local rows. Use --from-supabase or --from-json first.")

    mappings = map_all_questions(db)
    export_mapping_json(root, db)
    render_preview(root, db, limit=args.preview_limit)

    review_count = sum(1 for item in mappings if item["needs_review"])
    print(f"Imported this run: {imported}")
    print(f"Questions local: {existing}")
    print(f"Mapped: {len(mappings)}")
    print(f"Needs review: {review_count}")
    db.close()


if __name__ == "__main__":
    main()
