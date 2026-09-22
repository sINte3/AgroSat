"""H0-A: PostgreSQL-backed lifecycle proof for freshness notifications.

The H0-A production freshness dry-run reported 1372 rows moving to FRESH and
3 moving to AGING. The three AGING rows exposed a lifecycle defect: an active
freshness notification was treated as still current merely because the
freshness row was still non-FRESH, without checking that the notification's
persisted ``source_cycle`` still matched the current one. A status change from
NEVER_COLLECTED to AGING therefore left the old *critical* notification active
while candidate generation opened a new *warning* for the same field and index.

The existing TASK_221 tests are structural string contracts and could not catch
this, so these tests drive the real reconciler against a real database.

Runs against the same isolated database as the freshness contract suite, and
skips unless ``AGROSAT_TEST_DATABASE_URL`` is set:

    AGROSAT_TEST_DATABASE_URL=postgresql://.../agrosat_h0a_contract \
        python -m pytest tests/test_h0a_notification_lifecycle_postgres.py
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from test_h0a_freshness_postgres_contract import ContractBase


class FreshnessNotificationLifecycleTests(ContractBase):
    """One active freshness notification per field/index/recipient, always."""

    # ─── harness ────────────────────────────────────────────────────────────

    def setUp(self):
        super().setUp()
        self.manager_id = self._user(role="manager", email="h0a-manager@example.invalid")
        self.as_of = datetime.now(timezone.utc)

    def _user(self, *, role, email, enterprise_id=None):
        from sqlalchemy import text

        user_id = self.session.execute(
            text(
                "INSERT INTO users (enterprise_id, email, hashed_password, full_name, "
                " role, is_active) VALUES (:enterprise_id, :email, 'x', :name, :role, true) "
                "RETURNING id"
            ),
            {
                "enterprise_id": enterprise_id or self.enterprise_id,
                "email": email,
                "name": f"H0A {role}",
                "role": role,
            },
        ).scalar_one()
        self.session.commit()
        return user_id

    def _set_freshness(self, *, status, last_accepted_at, index_code="ndvi"):
        """Put the freshness row into an exact state, as the collector would."""
        from sqlalchemy import text

        self.session.execute(
            text(
                "INSERT INTO satellite_field_freshness "
                "(enterprise_id, field_id, index_code, status, last_accepted_at) "
                "VALUES (:enterprise_id, :field_id, :index_code, :status, :accepted) "
                "ON CONFLICT (field_id, index_code) DO UPDATE SET "
                "  status = excluded.status, "
                "  last_accepted_at = excluded.last_accepted_at, "
                "  last_accepted_scene = NULL"
            ),
            {
                "enterprise_id": self.enterprise_id,
                "field_id": self.field_id,
                "index_code": index_code,
                "status": status,
                "accepted": last_accepted_at,
            },
        )
        self.session.commit()

    def _reconcile(self, *, apply=True, limit=None):
        from services.operational_notifications import (
            DEFAULT_LIMIT,
            reconcile_notifications,
        )

        return reconcile_notifications(
            self.session,
            apply=apply,
            as_of=self.as_of,
            limit=DEFAULT_LIMIT if limit is None else limit,
        )

    def _notifications(self, *, active_only=False):
        from sqlalchemy import text

        clause = " AND status IN ('unread','read')" if active_only else ""
        return self.session.execute(
            text(
                "SELECT id, severity, status, recipient_user_id, "
                "       provenance->>'source_cycle' AS source_cycle "
                "  FROM operational_notifications "
                " WHERE source_kind = 'freshness'" + clause + " ORDER BY id"
            )
        ).mappings().all()

    def _current_cycle(self, index_code="ndvi"):
        """The cycle the database itself derives, via the shared expression."""
        from sqlalchemy import text

        from services.operational_notifications import FRESHNESS_SOURCE_CYCLE_SQL

        return self.session.execute(
            text(
                f"SELECT {FRESHNESS_SOURCE_CYCLE_SQL} AS cycle "
                "  FROM satellite_field_freshness s "
                " WHERE s.field_id = :field_id AND s.index_code = :index_code"
            ),
            {"field_id": self.field_id, "index_code": index_code},
        ).scalar_one()

    def _events(self, notification_id):
        from sqlalchemy import text

        return {
            row[0]
            for row in self.session.execute(
                text(
                    "SELECT event_type FROM operational_notification_events "
                    " WHERE notification_id = :id"
                ),
                {"id": notification_id},
            ).all()
        }

    def _seed_never_collected_notification(self):
        """Reproduce production: one active critical NEVER_COLLECTED alert."""
        self._set_freshness(status="NEVER_COLLECTED", last_accepted_at=None)
        summary = self._reconcile()
        self.assertEqual(summary["created"], 1, summary)
        rows = self._notifications(active_only=True)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["severity"], "critical")
        self.assertEqual(rows[0]["source_cycle"], "status:NEVER_COLLECTED:accepted:never")
        return rows[0]

    # ─── 1. NEVER_COLLECTED -> AGING (the production case) ──────────────────

    def test_never_collected_to_aging_replaces_in_one_apply(self):
        old = self._seed_never_collected_notification()

        self._set_freshness(
            status="AGING", last_accepted_at=self.as_of - timedelta(days=15)
        )
        summary = self._reconcile()

        # One apply does both halves of the transition.
        self.assertEqual(summary["resolved"], 1, summary)
        self.assertEqual(summary["created"], 1, summary)

        active = self._notifications(active_only=True)
        self.assertEqual(len(active), 1, f"exactly one active alert expected: {active}")
        new = active[0]
        self.assertEqual(new["severity"], "warning")
        self.assertEqual(new["source_cycle"], self._current_cycle())
        self.assertNotEqual(new["id"], old["id"])

        # The old critical is resolved, not deleted.
        everything = self._notifications()
        self.assertEqual(len(everything), 2)
        retired = next(row for row in everything if row["id"] == old["id"])
        self.assertEqual(retired["status"], "resolved")
        self.assertEqual(retired["severity"], "critical")

        # Both lifecycle events are recorded.
        self.assertIn("resolved", self._events(old["id"]))
        self.assertIn("created", self._events(new["id"]))

    def test_never_collected_to_aging_is_idempotent(self):
        self._seed_never_collected_notification()
        self._set_freshness(
            status="AGING", last_accepted_at=self.as_of - timedelta(days=15)
        )
        self._reconcile()
        settled = [dict(row) for row in self._notifications()]

        second = self._reconcile()
        self.assertEqual(second["created"], 0, second)
        self.assertEqual(second["resolved"], 0, second)
        self.assertEqual([dict(row) for row in self._notifications()], settled)
        self.assertEqual(len(self._notifications(active_only=True)), 1)

    # ─── 2. NEVER_COLLECTED -> FRESH ────────────────────────────────────────

    def test_never_collected_to_fresh_resolves_without_replacement(self):
        old = self._seed_never_collected_notification()

        self._set_freshness(status="FRESH", last_accepted_at=self.as_of)
        summary = self._reconcile()

        self.assertEqual(summary["resolved"], 1, summary)
        self.assertEqual(summary["created"], 0, summary)
        self.assertEqual(self._notifications(active_only=True), [])
        self.assertEqual(
            next(row for row in self._notifications() if row["id"] == old["id"])["status"],
            "resolved",
        )

    # ─── 3. AGING cycle A -> AGING cycle B ──────────────────────────────────

    def test_same_status_new_accepted_observation_rotates_the_cycle(self):
        self._set_freshness(
            status="AGING", last_accepted_at=self.as_of - timedelta(days=18)
        )
        self._reconcile()
        first = self._notifications(active_only=True)
        self.assertEqual(len(first), 1)
        cycle_a = first[0]["source_cycle"]

        # Same status, a newer accepted observation: a different cycle.
        self._set_freshness(
            status="AGING", last_accepted_at=self.as_of - timedelta(days=12)
        )
        cycle_b = self._current_cycle()
        self.assertNotEqual(cycle_a, cycle_b)

        summary = self._reconcile()
        self.assertEqual(summary["resolved"], 1, summary)
        self.assertEqual(summary["created"], 1, summary)

        active = self._notifications(active_only=True)
        self.assertEqual(len(active), 1, active)
        self.assertEqual(active[0]["source_cycle"], cycle_b)
        self.assertEqual(active[0]["severity"], "warning")

    # ─── 4. AGING -> STALE ──────────────────────────────────────────────────

    def test_aging_to_stale_rotates_and_stays_warning(self):
        self._set_freshness(
            status="AGING", last_accepted_at=self.as_of - timedelta(days=15)
        )
        self._reconcile()
        old = self._notifications(active_only=True)[0]

        self._set_freshness(
            status="STALE", last_accepted_at=self.as_of - timedelta(days=40)
        )
        summary = self._reconcile()
        self.assertEqual(summary["resolved"], 1, summary)
        self.assertEqual(summary["created"], 1, summary)

        active = self._notifications(active_only=True)
        self.assertEqual(len(active), 1, active)
        self.assertEqual(active[0]["severity"], "warning")
        self.assertEqual(active[0]["source_cycle"], self._current_cycle())
        self.assertNotEqual(active[0]["id"], old["id"])

    def test_stale_to_never_collected_returns_to_critical(self):
        """Severity follows the current status, in both directions."""
        self._set_freshness(
            status="STALE", last_accepted_at=self.as_of - timedelta(days=40)
        )
        self._reconcile()
        self.assertEqual(self._notifications(active_only=True)[0]["severity"], "warning")

        self._set_freshness(status="NEVER_COLLECTED", last_accepted_at=None)
        summary = self._reconcile()
        self.assertEqual((summary["resolved"], summary["created"]), (1, 1), summary)

        active = self._notifications(active_only=True)
        self.assertEqual(len(active), 1, active)
        self.assertEqual(active[0]["severity"], "critical")

    # ─── 5. no duplicates across a longer walk ──────────────────────────────

    def test_a_full_status_walk_never_leaves_two_active(self):
        walk = [
            ("NEVER_COLLECTED", None),
            ("AGING", self.as_of - timedelta(days=15)),
            ("STALE", self.as_of - timedelta(days=40)),
            ("PROVIDER_DEGRADED", self.as_of - timedelta(days=40)),
            ("AGING", self.as_of - timedelta(days=11)),
            ("FRESH", self.as_of),
        ]
        for status, accepted in walk:
            with self.subTest(status=status):
                self._set_freshness(status=status, last_accepted_at=accepted)
                self._reconcile()
                active = self._notifications(active_only=True)
                expected = 0 if status == "FRESH" else 1
                self.assertEqual(len(active), expected, active)
                if expected:
                    self.assertEqual(active[0]["source_cycle"], self._current_cycle())
                # And the pass is stable.
                repeat = self._reconcile()
                self.assertEqual((repeat["created"], repeat["resolved"]), (0, 0), repeat)

    def test_a_notification_without_a_stored_cycle_resolves_rather_than_lingering(self):
        """A row whose provenance lacks source_cycle must not become a zombie."""
        from sqlalchemy import text

        self._seed_never_collected_notification()
        self.session.execute(
            text(
                "UPDATE operational_notifications "
                "SET provenance = provenance - 'source_cycle' WHERE source_kind='freshness'"
            )
        )
        self.session.commit()

        summary = self._reconcile()
        self.assertEqual(summary["resolved"], 1, summary)
        self.assertEqual(self._notifications(active_only=True), [])

    def test_known_limitation_a_reused_cycle_string_does_not_re_notify(self):
        """Pre-existing, unchanged by this fix, and deliberately not fixed here.

        Notification identity (``dedupe_key``) hashes the source cycle, and the
        candidate filter matches existing rows regardless of status. So if a
        field returns to a cycle string it already used -- only reachable by
        going through FRESH and back to a run-derived status, both of which
        carry ``accepted:never`` -- the resolved row still suppresses a new
        candidate and no fresh alert is raised.

        Verified byte-identical before and after this change, so it is not a
        regression. Fixing it means changing notification identity, which is
        out of scope for this release blocker and would need a data migration
        for the dedupe keys already stored. Recorded for follow-up.
        """
        self._set_freshness(status="PROVIDER_DEGRADED", last_accepted_at=None)
        first = self._reconcile()
        self.assertEqual(first["created"], 1, first)
        self.assertEqual(len(self._notifications(active_only=True)), 1)

        self._set_freshness(status="FRESH", last_accepted_at=self.as_of)
        self._reconcile()
        self.assertEqual(self._notifications(active_only=True), [])

        # Same status, same NULL accepted timestamp: the identical cycle string.
        self._set_freshness(status="PROVIDER_DEGRADED", last_accepted_at=None)
        again = self._reconcile()
        self.assertEqual(again["created"], 0, "documented limitation, not a goal")
        self.assertEqual(len(self._notifications(active_only=True)), 0)

    # ─── 6. TASK_221 scoping and bounds are preserved ───────────────────────

    def test_recipient_scope_still_oversight_only(self):
        from sqlalchemy import text

        agronomist = self._user(role="agronomist", email="h0a-agro@example.invalid")
        admin = self._user(role="admin", email="h0a-admin@example.invalid")
        self._set_freshness(status="NEVER_COLLECTED", last_accepted_at=None)
        self._reconcile()

        recipients = {row["recipient_user_id"] for row in self._notifications(active_only=True)}
        self.assertIn(self.manager_id, recipients)
        self.assertIn(admin, recipients)
        self.assertNotIn(agronomist, recipients, "agronomists are not an oversight role")

        # Every row carries the tenant that owns the field.
        self.assertEqual(
            self.session.execute(
                text(
                    "SELECT DISTINCT enterprise_id FROM operational_notifications "
                    "WHERE source_kind='freshness'"
                )
            ).scalars().all(),
            [self.enterprise_id],
        )

    def test_tenant_isolation_holds_across_the_rotation(self):
        from sqlalchemy import text

        other = self.session.execute(
            text(
                "INSERT INTO enterprises (name, code, is_active) "
                "VALUES ('H0A Other', 'H0A2', true) RETURNING id"
            )
        ).scalar_one()
        self.session.commit()
        foreign_manager = self._user(
            role="manager", email="h0a-other@example.invalid", enterprise_id=other
        )

        self._seed_never_collected_notification()
        self._set_freshness(
            status="AGING", last_accepted_at=self.as_of - timedelta(days=15)
        )
        self._reconcile()

        recipients = {row["recipient_user_id"] for row in self._notifications()}
        self.assertNotIn(
            foreign_manager,
            recipients,
            "a manager of another enterprise must never receive this alert",
        )

    def test_reconciliation_stays_bounded_by_limit(self):
        from sqlalchemy import text

        # One freshness row per index for several fields: more than the limit.
        for ordinal in range(6):
            field_id = self.session.execute(
                text(
                    "INSERT INTO fields (enterprise_id, name, code, geometry, is_active) "
                    "VALUES (:enterprise_id, :name, :code, "
                    "ST_SetSRID(ST_GeomFromText(:polygon), 4326), true) RETURNING id"
                ),
                {
                    "enterprise_id": self.enterprise_id,
                    "name": f"H0A Bulk {ordinal}",
                    "code": f"H0A-B{ordinal}",
                    "polygon": self._polygon(),
                },
            ).scalar_one()
            for index_code in ("ndvi", "savi", "evi", "ndmi", "ndre"):
                self.session.execute(
                    text(
                        "INSERT INTO satellite_field_freshness "
                        "(enterprise_id, field_id, index_code, status) "
                        "VALUES (:enterprise_id, :field_id, :index_code, 'NEVER_COLLECTED')"
                    ),
                    {
                        "enterprise_id": self.enterprise_id,
                        "field_id": field_id,
                        "index_code": index_code,
                    },
                )
        self.session.commit()

        summary = self._reconcile(limit=5)
        self.assertLessEqual(summary["candidates"], 5)
        self.assertLessEqual(summary["created"], 5)

    def test_limit_outside_the_contract_is_rejected(self):
        from services.operational_notifications import MAX_LIMIT

        for bad in (0, -1, MAX_LIMIT + 1, True, 1.0):
            with self.subTest(limit=bad):
                with self.assertRaises(ValueError):
                    self._reconcile(limit=bad)

    # ─── source-cycle drift guard ───────────────────────────────────────────

    def test_one_definition_of_the_freshness_source_cycle(self):
        from services.operational_notifications import (
            FRESHNESS_SOURCE_CYCLE_SQL,
            NOTIFICATION_CANDIDATES_SQL,
            STALE_ACTIVE_SQL,
        )

        for name, statement in (
            ("candidates", NOTIFICATION_CANDIDATES_SQL),
            ("stale", STALE_ACTIVE_SQL),
        ):
            with self.subTest(statement=name):
                self.assertNotIn("@freshness_source_cycle@", statement)
                self.assertEqual(statement.count(FRESHNESS_SOURCE_CYCLE_SQL), 1)
        # No hand-written second definition anywhere.
        combined = NOTIFICATION_CANDIDATES_SQL + STALE_ACTIVE_SQL
        self.assertEqual(combined.count("'status:'||"), 2)
        # Stale resolution must consult the persisted cycle.
        self.assertIn("n.provenance->>'source_cycle'", STALE_ACTIVE_SQL)

    def test_unrelated_source_kinds_keep_their_lifecycle(self):
        """Only the freshness arm changed; other arms are untouched."""
        from services.operational_notifications import STALE_ACTIVE_SQL

        flat = " ".join(STALE_ACTIVE_SQL.split())
        # The cycle check is scoped to the freshness arm only.
        self.assertEqual(flat.count("n.provenance->>'source_cycle'"), 1)
        for kind in (
            "n.source_kind='inspection'",
            "n.source_kind='candidate'",
            "n.source_kind='alert'",
            "n.source_kind='agronomy_work_item'",
            "n.source_kind='agronomy_plan'",
            "n.source_kind='collection_run'",
        ):
            with self.subTest(kind=kind):
                self.assertIn(kind, flat)

    def test_collection_run_notifications_are_unaffected_by_the_fix(self):
        from sqlalchemy import text

        self.session.execute(
            text(
                "INSERT INTO satellite_collection_runs "
                "(id,run_key,mode,status,release_commit,rule_version,audit_identity,"
                " started_at,heartbeat_at,provider_status,failure_category) "
                "VALUES (gen_random_uuid(),'h0a-failed','apply','failed',"
                " repeat('c',40),'r3-e-v1','h0a',now(),now(),'degraded','network')"
            )
        )
        self.session.commit()

        self._reconcile()
        runs = self.session.execute(
            text(
                "SELECT count(*) FROM operational_notifications "
                "WHERE source_kind='collection_run' AND status IN ('unread','read')"
            )
        ).scalar_one()
        self.assertGreater(runs, 0, "a failed run must still notify")

        # Still current on a second pass: unchanged lifecycle.
        second = self._reconcile()
        self.assertEqual(second["created"], 0, second)
        self.assertEqual(second["resolved"], 0, second)

    def _polygon(self):
        from test_h0a_freshness_postgres_contract import POLYGON

        return POLYGON


if __name__ == "__main__":
    unittest.main()
