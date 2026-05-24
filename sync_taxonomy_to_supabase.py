import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from supabase import create_client

from ingest_pipeline import SUPABASE_KEY, SUPABASE_URL


DEFAULT_ROOT = Path("local_curriculum")
BACKUP_COLUMNS = [
    "id",
    "topic",
    "subtopic",
    "chapter",
    "needs_review",
    "source_file",
    "page_number",
]


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def supabase_client():
    return create_client(os.getenv("SUPABASE_URL", SUPABASE_URL), os.getenv("SUPABASE_KEY", SUPABASE_KEY))


def fetch_remote_questions(client, batch_size: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    select_cols = ",".join(BACKUP_COLUMNS)
    while True:
        end = start + batch_size - 1
        result = client.table("questions").select(select_cols).range(start, end).execute()
        batch = result.data or []
        rows.extend(batch)
        if len(batch) < batch_size:
            break
        start += batch_size
    return rows


def load_local_updates(root: Path) -> list[dict[str, Any]]:
    db_path = root / "output_sqlite" / "curriculum.sqlite"
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    try:
        rows = [
            {
                "id": str(row["id"]),
                "topic": row["canonical_topic_title"],
                "subtopic": row["canonical_subtopic_title"],
                "needs_review": bool(row["canonical_needs_review"]),
            }
            for row in db.execute(
                """
                select id, canonical_topic_title, canonical_subtopic_title, canonical_needs_review
                from questions_standardized_v2
                order by id
                """
            )
        ]
    finally:
        db.close()
    return rows


def write_backup(root: Path, remote_rows: list[dict[str, Any]]) -> Path:
    backup_dir = root / "output_json"
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = backup_dir / f"supabase_questions_metadata_backup_{timestamp}.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "table": "questions",
                "columns": BACKUP_COLUMNS,
                "rows": remote_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def summarize_changes(remote_rows: list[dict[str, Any]], updates: list[dict[str, Any]]) -> dict[str, int]:
    remote_by_id = {str(row["id"]): row for row in remote_rows}
    changed_topic = 0
    changed_subtopic = 0
    changed_review = 0
    unchanged = 0
    for row in updates:
        old = remote_by_id[row["id"]]
        topic_changed = old.get("topic") != row["topic"]
        subtopic_changed = old.get("subtopic") != row["subtopic"]
        review_changed = bool(old.get("needs_review")) != bool(row["needs_review"])
        changed_topic += int(topic_changed)
        changed_subtopic += int(subtopic_changed)
        changed_review += int(review_changed)
        unchanged += int(not topic_changed and not subtopic_changed and not review_changed)
    return {
        "remote_rows": len(remote_rows),
        "local_updates": len(updates),
        "changed_topic": changed_topic,
        "changed_subtopic": changed_subtopic,
        "changed_needs_review": changed_review,
        "unchanged_rows": unchanged,
    }


def validate_id_sets(remote_rows: list[dict[str, Any]], updates: list[dict[str, Any]]) -> None:
    remote_ids = {str(row["id"]) for row in remote_rows}
    local_ids = {row["id"] for row in updates}
    missing_remote = sorted(local_ids - remote_ids)
    extra_remote = sorted(remote_ids - local_ids)
    if missing_remote or extra_remote:
        raise SystemExit(
            "Local/Supabase question IDs do not match. "
            f"missing_remote={len(missing_remote)} extra_remote={len(extra_remote)} "
            f"sample_missing={missing_remote[:5]} sample_extra={extra_remote[:5]}"
        )


def patch_rows(client, updates: list[dict[str, Any]]) -> None:
    for index, row in enumerate(updates, 1):
        payload = {
            "topic": row["topic"],
            "subtopic": row["subtopic"],
            "needs_review": row["needs_review"],
        }
        client.table("questions").update(payload).eq("id", row["id"]).execute()
        if index == 1 or index % 250 == 0 or index == len(updates):
            print(f"Synced {index}/{len(updates)}", flush=True)


def verify_remote(client, updates: list[dict[str, Any]], batch_size: int) -> dict[str, int]:
    remote_rows = fetch_remote_questions(client, batch_size)
    remote_by_id = {str(row["id"]): row for row in remote_rows}
    mismatch = 0
    needs_review = 0
    for row in updates:
        remote = remote_by_id.get(row["id"])
        if not remote:
            mismatch += 1
            continue
        if (
            remote.get("topic") != row["topic"]
            or remote.get("subtopic") != row["subtopic"]
            or bool(remote.get("needs_review")) != bool(row["needs_review"])
        ):
            mismatch += 1
        needs_review += int(bool(remote.get("needs_review")))
    return {"verified_rows": len(updates), "mismatches": mismatch, "remote_needs_review": needs_review}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sync local taxonomy v2 metadata to Supabase questions.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    parser.add_argument("--batch-size", type=int, default=int(os.getenv("SUPABASE_BATCH_SIZE", "100")))
    parser.add_argument("--apply", action="store_true", help="Actually write to Supabase. Without this, only backup and summarize.")
    return parser.parse_args()


def main() -> None:
    configure_stdio()
    args = parse_args()
    root = Path(args.root)
    client = supabase_client()

    updates = load_local_updates(root)
    remote_rows = fetch_remote_questions(client, args.batch_size)
    validate_id_sets(remote_rows, updates)
    backup_path = write_backup(root, remote_rows)
    summary = summarize_changes(remote_rows, updates)

    print(json.dumps({"backup": str(backup_path.resolve()), **summary}, ensure_ascii=False, indent=2))
    if not args.apply:
        print("Dry run only. Re-run with --apply to sync Supabase.")
        return

    patch_rows(client, updates)
    verify = verify_remote(client, updates, args.batch_size)
    print(json.dumps(verify, ensure_ascii=False, indent=2))
    if verify["mismatches"]:
        raise SystemExit("Supabase verification failed.")


if __name__ == "__main__":
    main()
