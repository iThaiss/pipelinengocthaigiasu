import argparse
import copy
import html
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_ROOT = Path("local_curriculum")


MANUAL_MOVES = {
    ("NGPHANTIEN", 5): "Cực trị của hàm số",
    ("LB", 42): "Phương trình mặt cầu",
    ("NGPHANTIEN", 30): "Phương trình mặt phẳng",
    ("NGPHANTIEN", 31): "Phương trình mặt phẳng",
    ("NGPHANTIEN", 38): "Phương trình mặt cầu",
    ("NGPHANTIEN", 41): "Ứng dụng thực tế của phương trình mặt phẳng trong không gian",
    ("NGPHANTIEN", 42): "Vectơ và các phép toán vectơ trong không gian",
    ("NGPHANTIEN", 43): "Theme 15. Ứng dụng thực tế của vectơ trong không gian",
    ("NGPHANTIEN", 45): "Theme 15. Ứng dụng thực tế của vectơ trong không gian",
    ("NGPHANTIEN", 46): "Theme 15. Ứng dụng thực tế của vectơ trong không gian",
    ("NGPHANTIEN", 47): "Theme 15. Ứng dụng thực tế của vectơ trong không gian",
}


RENAME_TITLES = {
    "Theme 20. Xác suất có điều kiện": "Xác suất có điều kiện và công thức nhân xác suất",
    "Theme 15. Ứng dụng thực tế của vectơ trong không gian": "Ứng dụng thực tế của vectơ trong không gian",
    "Theme 7. Nguyên hàm hàm lượng giác nâng cao": "Nguyên hàm hàm lượng giác và các hàm đặc biệt",
}


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def merge_unique(existing: list[str], incoming: list[str], limit: int = 8) -> list[str]:
    result = list(existing or [])
    seen = {item.casefold().strip() for item in result}
    for item in incoming or []:
        text = str(item).strip()
        key = text.casefold()
        if text and key not in seen:
            result.append(text)
            seen.add(key)
        if len(result) >= limit:
            break
    return result


def find_unit_by_title(units: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    title_key = title.casefold().strip()
    for unit in units:
        if (unit.get("canonical_title") or "").casefold().strip() == title_key:
            return unit
    return None


def get_lesson_key(lesson: dict[str, Any]) -> tuple[str, int]:
    return (str(lesson["source"]), int(lesson["source_order"]))


def merge_unit_fields(target: dict[str, Any], source: dict[str, Any]) -> None:
    target["learning_goals"] = merge_unique(target.get("learning_goals", []), source.get("learning_goals", []))
    target["prerequisites"] = merge_unique(target.get("prerequisites", []), source.get("prerequisites", []))
    target["teaching_strategy"] = merge_unique(target.get("teaching_strategy", []), source.get("teaching_strategy", []))
    target["application_types"] = merge_unique(target.get("application_types", []), source.get("application_types", []))
    target["best_source_notes"] = merge_unique(target.get("best_source_notes", []), source.get("best_source_notes", []))
    target["gaps_to_fill"] = merge_unique(target.get("gaps_to_fill", []), source.get("gaps_to_fill", []))


def canonicalize(roadmap: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    canonical = copy.deepcopy(roadmap)
    units = canonical["roadmap_units"]
    moves_applied: list[dict[str, Any]] = []

    lesson_locations: dict[tuple[str, int], dict[str, Any]] = {}
    for unit in units:
        for lesson in unit.get("source_lessons", []):
            lesson_locations[get_lesson_key(lesson)] = unit

    for lesson_key, target_title in MANUAL_MOVES.items():
        source_unit = lesson_locations.get(lesson_key)
        target_unit = find_unit_by_title(units, target_title)
        if not source_unit or not target_unit or source_unit is target_unit:
            continue

        lesson = None
        remaining = []
        for current in source_unit.get("source_lessons", []):
            if get_lesson_key(current) == lesson_key:
                lesson = current
            else:
                remaining.append(current)
        if lesson is None:
            continue

        source_unit["source_lessons"] = remaining
        if lesson_key not in {get_lesson_key(item) for item in target_unit.get("source_lessons", [])}:
            target_unit.setdefault("source_lessons", []).append(lesson)
        merge_unit_fields(target_unit, source_unit)
        moves_applied.append(
            {
                "lesson": {"source": lesson_key[0], "source_order": lesson_key[1], "title": lesson.get("lesson_title")},
                "from": source_unit.get("canonical_title"),
                "to": target_unit.get("canonical_title"),
            }
        )

    units = [unit for unit in units if unit.get("source_lessons")]
    for unit in units:
        if unit.get("canonical_title") in RENAME_TITLES:
            unit["canonical_title"] = RENAME_TITLES[unit["canonical_title"]]
        unit["source_lessons"] = sorted(
            unit.get("source_lessons", []),
            key=lambda item: (item.get("source", ""), int(item.get("source_order", 0)), item.get("role", "")),
        )

    for index, unit in enumerate(units, start=1):
        unit["order"] = index

    canonical["roadmap_units"] = units
    canonical["_meta"] = {
        **canonical.get("_meta", {}),
        "canonicalized_at": datetime.now().isoformat(timespec="seconds"),
        "canonical_status": "auto_reviewed",
        "manual_move_count": len(moves_applied),
        "unit_count": len(units),
    }

    audit = build_audit(canonical, moves_applied)
    return canonical, audit


def build_audit(canonical: dict[str, Any], moves_applied: list[dict[str, Any]]) -> dict[str, Any]:
    units = canonical["roadmap_units"]
    duplicate_refs: dict[str, int] = {}
    for unit in units:
        for lesson in unit.get("source_lessons", []):
            key = f"{lesson['source']}#{lesson['source_order']}"
            duplicate_refs[key] = duplicate_refs.get(key, 0) + 1

    suspicious_units = []
    for unit in units:
        lessons = unit.get("source_lessons", [])
        titles = " | ".join(item.get("lesson_title", "") for item in lessons).casefold()
        reasons = []
        if len(lessons) >= 7:
            reasons.append("many_source_lessons")
        if len(lessons) > 1 and "mặt cầu" in titles and "mặt phẳng" in titles and "đường thẳng" in titles:
            reasons.append("mixed_oxyz_objects")
        if "tích phân" in titles and "đạo hàm" in titles:
            reasons.append("mixed_derivative_integral")
        if reasons:
            suspicious_units.append(
                {
                    "order": unit.get("order"),
                    "canonical_title": unit.get("canonical_title"),
                    "reasons": reasons,
                    "source_lessons": lessons,
                }
            )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "unit_count": len(units),
        "source_lesson_ref_count": sum(len(unit.get("source_lessons", [])) for unit in units),
        "duplicate_source_refs": {key: count for key, count in duplicate_refs.items() if count > 1},
        "manual_moves_applied": moves_applied,
        "suspicious_units": suspicious_units,
        "review_status": "auto_reviewed_needs_human_spot_check" if suspicious_units else "auto_reviewed_clean",
    }


def save_to_db(db_path: Path, canonical: dict[str, Any]) -> None:
    db = sqlite3.connect(db_path)
    db.execute(
        """
        create table if not exists canonical_roadmap_units (
            id integer primary key,
            canonical_title text not null,
            program_area text,
            phase text,
            source_lessons_json text not null,
            learning_goals_json text not null,
            prerequisites_json text not null,
            teaching_strategy_json text not null,
            application_types_json text not null,
            recommended_sequence_note text,
            best_source_notes_json text not null,
            gaps_to_fill_json text not null
        )
        """
    )
    db.execute("delete from canonical_roadmap_units")
    for unit in canonical["roadmap_units"]:
        db.execute(
            """
            insert into canonical_roadmap_units (
                id, canonical_title, program_area, phase, source_lessons_json,
                learning_goals_json, prerequisites_json, teaching_strategy_json,
                application_types_json, recommended_sequence_note, best_source_notes_json, gaps_to_fill_json
            ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                unit["order"],
                unit.get("canonical_title") or "",
                unit.get("program_area"),
                unit.get("phase"),
                json.dumps(unit.get("source_lessons", []), ensure_ascii=False),
                json.dumps(unit.get("learning_goals", []), ensure_ascii=False),
                json.dumps(unit.get("prerequisites", []), ensure_ascii=False),
                json.dumps(unit.get("teaching_strategy", []), ensure_ascii=False),
                json.dumps(unit.get("application_types", []), ensure_ascii=False),
                unit.get("recommended_sequence_note"),
                json.dumps(unit.get("best_source_notes", []), ensure_ascii=False),
                json.dumps(unit.get("gaps_to_fill", []), ensure_ascii=False),
            ),
        )
    db.commit()
    db.close()


def render_html(canonical: dict[str, Any], audit: dict[str, Any]) -> str:
    rows = []
    for unit in canonical["roadmap_units"]:
        lessons = "; ".join(
            f"{item['source']} #{item['source_order']} ({item.get('role', '')}): {item.get('lesson_title', '')}"
            for item in unit.get("source_lessons", [])
        )
        rows.append(
            "<tr>"
            f"<td>{unit.get('order')}</td>"
            f"<td>{html.escape(unit.get('canonical_title') or '')}</td>"
            f"<td>{html.escape(unit.get('program_area') or '')}</td>"
            f"<td>{html.escape(unit.get('phase') or '')}</td>"
            f"<td>{html.escape(lessons)}</td>"
            f"<td>{html.escape('; '.join(unit.get('learning_goals') or []))}</td>"
            f"<td>{html.escape('; '.join(unit.get('teaching_strategy') or []))}</td>"
            f"<td>{html.escape('; '.join(unit.get('application_types') or []))}</td>"
            f"<td>{html.escape('; '.join(unit.get('gaps_to_fill') or []))}</td>"
            "</tr>"
        )

    suspicious = "".join(
        f"<li>#{item['order']} {html.escape(item['canonical_title'])}: {html.escape(', '.join(item['reasons']))}</li>"
        for item in audit.get("suspicious_units", [])
    )
    if not suspicious:
        suspicious = "<li>No suspicious units detected.</li>"

    return f"""<!doctype html>
<html lang="vi">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Canonical Curriculum Roadmap</title>
  <style>
    body {{ margin: 0; font-family: Arial, sans-serif; background: #f6f7f9; color: #1f2328; }}
    header {{ padding: 24px 32px; background: #fff; border-bottom: 1px solid #d8dee4; }}
    h1 {{ margin: 0 0 8px; font-size: 30px; }}
    main {{ padding: 24px 32px 40px; }}
    .summary {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 20px; }}
    .metric, .audit {{ background: #fff; border: 1px solid #d8dee4; border-radius: 6px; padding: 16px; }}
    .metric strong {{ display: block; font-size: 24px; margin-bottom: 4px; }}
    table {{ width: 100%; border-collapse: collapse; background: #fff; border: 1px solid #d8dee4; }}
    th, td {{ padding: 9px 10px; border-bottom: 1px solid #eaeef2; vertical-align: top; text-align: left; font-size: 13px; }}
    th {{ position: sticky; top: 0; background: #eef2f5; z-index: 1; }}
    tr:hover td {{ background: #fafbfc; }}
  </style>
</head>
<body>
  <header>
    <h1>Canonical Curriculum Roadmap</h1>
    <div>Generated at {html.escape(canonical["_meta"]["canonicalized_at"])}</div>
  </header>
  <main>
    <section class="summary">
      <div class="metric"><strong>{audit["unit_count"]}</strong>Units</div>
      <div class="metric"><strong>{audit["source_lesson_ref_count"]}</strong>Lesson refs</div>
      <div class="metric"><strong>{len(audit["manual_moves_applied"])}</strong>Auto fixes</div>
      <div class="metric"><strong>{len(audit["suspicious_units"])}</strong>Review flags</div>
    </section>
    <section class="audit">
      <h2>Review Flags</h2>
      <ul>{suspicious}</ul>
    </section>
    <table>
      <thead>
        <tr>
          <th>#</th><th>Canonical unit</th><th>Area</th><th>Phase</th><th>Source lessons</th>
          <th>Goals</th><th>Teaching</th><th>Dạng câu áp dụng</th><th>Gaps</th>
        </tr>
      </thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create reviewed canonical roadmap from local curriculum roadmap.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    return parser.parse_args()


def main() -> None:
    configure_stdio()
    args = parse_args()
    root = Path(args.root)
    roadmap_path = root / "output_json" / "curriculum_roadmap.json"
    roadmap = read_json(roadmap_path)
    canonical, audit = canonicalize(roadmap)

    canonical_path = root / "output_json" / "canonical_roadmap.json"
    audit_path = root / "output_json" / "roadmap_review.json"
    html_path = root / "previews" / "canonical_roadmap.html"
    write_json(canonical_path, canonical)
    write_json(audit_path, audit)
    html_path.write_text(render_html(canonical, audit), encoding="utf-8")
    save_to_db(root / "output_sqlite" / "curriculum.sqlite", canonical)

    print(f"Units: {audit['unit_count']}")
    print(f"Lesson refs: {audit['source_lesson_ref_count']}")
    print(f"Auto fixes: {len(audit['manual_moves_applied'])}")
    print(f"Review flags: {len(audit['suspicious_units'])}")
    print(f"Wrote {canonical_path.resolve()}")
    print(f"Wrote {audit_path.resolve()}")
    print(f"Wrote {html_path.resolve()}")


if __name__ == "__main__":
    main()
