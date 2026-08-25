"""Align the already-created TASK_217 database after a pre-commit migration repair."""

from __future__ import annotations

from pathlib import Path
import os
import sys

from dotenv import dotenv_values
import psycopg2
from sqlalchemy.engine import make_url


PREFIX = "agrosat_r3_task217_"
SOURCE_PREFIX = "agrosat_r3_d_pixel_ndvi_"


def main() -> int:
    backend = Path(__file__).resolve().parents[2] / "backend"
    sys.path.insert(0, str(backend))
    from config import settings

    target_url = make_url(settings.database_url)
    if target_url.database == "agrosat" or not str(target_url.database or "").startswith(PREFIX):
        raise RuntimeError("TASK217_DATABASE_IDENTITY_REJECTED")
    source_env = Path(os.environ["AGROSAT_RUNTIME_ENV_FILE"]).resolve(strict=True)
    values = dotenv_values(source_env)
    source_url = make_url(str(values.get("DATABASE_URL") or ""))
    if source_url.database == "agrosat" or not str(source_url.database or "").startswith(SOURCE_PREFIX):
        raise RuntimeError("TASK217_SOURCE_DATABASE_IDENTITY_REJECTED")

    with psycopg2.connect(source_url.render_as_string(hide_password=False)) as source:
        with source.cursor() as cursor:
            cursor.execute(
                "SELECT id,status,started_at,completed_at,cancelled_at,completion_summary,cancellation_reason "
                "FROM field_inspections ORDER BY id"
            )
            inspections = cursor.fetchall()
            cursor.execute("SELECT id,cause_code,cause_details FROM inspection_results ORDER BY id")
            results = cursor.fetchall()
            cursor.execute("SELECT id,status FROM corrective_actions ORDER BY id")
            actions = cursor.fetchall()

    with psycopg2.connect(target_url.render_as_string(hide_password=False)) as target:
        with target.cursor() as cursor:
            for constraint in (
                "ck_field_inspections_cancel_state", "ck_field_inspections_submit_state",
                "ck_field_inspections_review_state", "ck_field_inspections_assignment_state",
                "ck_field_inspections_source", "ck_field_inspections_status",
            ):
                cursor.execute(f"ALTER TABLE field_inspections DROP CONSTRAINT IF EXISTS {constraint}")
            cursor.execute("DROP INDEX IF EXISTS uq_field_inspections_one_active_legacy_per_field")
            for row in inspections:
                cursor.execute(
                    """UPDATE field_inspections SET source_kind='legacy',status=%s,started_at=%s,
                    completed_at=%s,cancelled_at=%s,completion_summary=%s,cancellation_reason=%s,
                    reviewed_by_id=NULL,reviewed_at=NULL,review_reason=NULL,submitted_at=NULL,
                    confirmed_at=NULL,rejected_at=NULL WHERE id=%s""",
                    (row[1], row[2], row[3], row[4], row[5], row[6], row[0]),
                )
            cursor.execute(
                "ALTER TABLE field_inspections ADD CONSTRAINT ck_field_inspections_status CHECK "
                "(status IN ('pending','new','assigned','in_progress','submitted','completed',"
                "'confirmed','rejected','cancelled'))"
            )
            cursor.execute(
                "ALTER TABLE field_inspections ADD CONSTRAINT ck_field_inspections_source CHECK "
                "(source_kind IN ('legacy','pixel_ndvi','alert','manual'))"
            )
            cursor.execute(
                "ALTER TABLE field_inspections ADD CONSTRAINT ck_field_inspections_legacy_source CHECK "
                "(source_kind <> 'legacy' OR source IN ('attention_queue','manual','irrigation_context'))"
            )
            cursor.execute(
                "ALTER TABLE field_inspections ADD CONSTRAINT ck_field_inspections_assignment_state CHECK "
                "(source_kind='legacy' OR status='new' OR assigned_to_id IS NOT NULL)"
            )
            cursor.execute(
                """ALTER TABLE field_inspections ADD CONSTRAINT ck_field_inspections_review_state CHECK
                (source_kind='legacy' OR
                 (status='confirmed' AND reviewed_by_id IS NOT NULL AND reviewed_at IS NOT NULL
                  AND confirmed_at IS NOT NULL AND rejected_at IS NULL) OR
                 (status='rejected' AND reviewed_by_id IS NOT NULL AND reviewed_at IS NOT NULL
                  AND review_reason IS NOT NULL AND rejected_at IS NOT NULL AND confirmed_at IS NULL) OR
                 (status NOT IN ('confirmed','rejected') AND confirmed_at IS NULL AND rejected_at IS NULL))"""
            )
            cursor.execute(
                "ALTER TABLE field_inspections ADD CONSTRAINT ck_field_inspections_submit_state CHECK "
                "(source_kind='legacy' OR status NOT IN ('submitted','confirmed','rejected') OR submitted_at IS NOT NULL)"
            )
            cursor.execute(
                """ALTER TABLE field_inspections ADD CONSTRAINT ck_field_inspections_cancel_state CHECK
                ((source_kind='legacy' AND
                  ((status='completed' AND completed_at IS NOT NULL AND cancelled_at IS NULL) OR
                   (status='cancelled' AND cancelled_at IS NOT NULL AND completed_at IS NULL) OR
                   (status IN ('pending','in_progress') AND completed_at IS NULL AND cancelled_at IS NULL))) OR
                 (source_kind<>'legacy' AND
                  (status<>'cancelled' OR (cancelled_at IS NOT NULL AND cancellation_reason IS NOT NULL))))"""
            )
            cursor.execute(
                "CREATE UNIQUE INDEX uq_field_inspections_one_active_legacy_per_field ON field_inspections(field_id) "
                "WHERE source_kind='legacy' AND status IN ('pending','in_progress')"
            )

            cursor.execute("ALTER TABLE inspection_results DROP CONSTRAINT IF EXISTS ck_inspection_results_cause_code")
            for row in results:
                cursor.execute(
                    "UPDATE inspection_results SET cause_code=%s,cause_details=%s WHERE id=%s",
                    (row[1], row[2], row[0]),
                )
            cursor.execute(
                """ALTER TABLE inspection_results ADD CONSTRAINT ck_inspection_results_cause_code CHECK
                (cause_code IN ('water_stress','irrigation_failure','pest','disease','nutrient_deficiency',
                'weed_pressure','mechanical_damage','soil_salinity','weather_damage','false_positive','other',
                'unconfirmed','irrigation','nutrient','weather','soil','mechanical','crop_stage','no_issue'))"""
            )

            for constraint in (
                "ck_corrective_actions_verification_state", "ck_corrective_actions_cancel_state",
                "ck_corrective_actions_completion_state", "ck_corrective_actions_status",
            ):
                cursor.execute(f"ALTER TABLE corrective_actions DROP CONSTRAINT IF EXISTS {constraint}")
            cursor.execute("ALTER TABLE corrective_actions ALTER COLUMN action_type DROP NOT NULL")
            cursor.execute("ALTER TABLE corrective_actions ALTER COLUMN action_type DROP DEFAULT")
            for row in actions:
                cursor.execute(
                    """UPDATE corrective_actions SET status=%s,action_type=NULL,planned_start_at=NULL,
                    due_at=NULL,started_at=NULL,completion_note=NULL,completed_at=NULL,
                    cancelled_reason=NULL,cancelled_at=NULL,verified_by_id=NULL,verified_at=NULL,
                    verification_result=NULL,verification_notes=NULL,verification_index_name=NULL,
                    verification_sample_value=NULL,follow_up_inspection_id=NULL WHERE id=%s""",
                    (row[1], row[0]),
                )
            cursor.execute(
                """ALTER TABLE corrective_actions ADD CONSTRAINT ck_corrective_actions_status CHECK
                (status IN ('planned','in_progress','completed','verified_effective','verified_ineffective',
                'cancelled','open','blocked','closed'))"""
            )
            cursor.execute(
                """ALTER TABLE corrective_actions ADD CONSTRAINT ck_corrective_actions_legacy_closure_state CHECK
                (action_type IS NOT NULL OR
                 ((status='closed' AND closure_reason IS NOT NULL AND btrim(closure_reason)<>''
                   AND closed_by_id IS NOT NULL AND closed_at IS NOT NULL) OR
                  (status<>'closed' AND closure_reason IS NULL AND closed_by_id IS NULL AND closed_at IS NULL)))"""
            )
            cursor.execute(
                """ALTER TABLE corrective_actions ADD CONSTRAINT ck_corrective_actions_completion_state CHECK
                (action_type IS NULL OR status NOT IN ('completed','verified_effective','verified_ineffective') OR
                 (completion_note IS NOT NULL AND completed_at IS NOT NULL))"""
            )
            cursor.execute(
                """ALTER TABLE corrective_actions ADD CONSTRAINT ck_corrective_actions_cancel_state CHECK
                (action_type IS NULL OR status<>'cancelled' OR
                 (cancelled_reason IS NOT NULL AND cancelled_at IS NOT NULL))"""
            )
            cursor.execute(
                """ALTER TABLE corrective_actions ADD CONSTRAINT ck_corrective_actions_verification_state CHECK
                (action_type IS NULL OR
                 (status='verified_effective' AND verification_result='effective'
                  AND verified_by_id IS NOT NULL AND verified_at IS NOT NULL) OR
                 (status='verified_ineffective' AND verification_result IN ('ineffective','another_cycle')
                  AND verified_by_id IS NOT NULL AND verified_at IS NOT NULL) OR
                 (status NOT IN ('verified_effective','verified_ineffective') AND verification_result IS NULL
                  AND verified_by_id IS NULL AND verified_at IS NULL))"""
            )
        target.commit()
    values.clear()
    print("PASS_TASK217_ISOLATED_COMPATIBILITY_REPAIR")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
