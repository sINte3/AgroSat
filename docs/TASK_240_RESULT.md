# TASK_240 — AGRONOMIST OPERATIONAL CENTER SQL FIX RESULT

## Verdict
**PASS_TASK_240**

B6 from TASK_237 is fixed on the task branch. One missing parenthesis in `_agronomist_field_condition` made every agronomist request to the Operational Center filter options and field timeline fail with HTTP 500. The fix adds that one parenthesis and changes nothing else in the product.
- Development only. Nothing was deployed. Production, the pilot accounts and account 20 were neither accessed nor changed.
- There is no schema change, migration, endpoint, frontend or release-tooling change.

## Base
| item | value |
|---|---|
| origin/main (verified after `git fetch origin`) | `a69dd664e6bec79af6e098bf9a940924001d04d6` |
| production at start | `C:\AgroSat_runtime\PROGRAM_R3\control\current-release.json` names the same SHA (R20260927-task239). `GET /health/ready` reports `release_revision` = the same SHA, database `0016_operational_command_center`, `revision_match: true` |
| branch | `task/240-fix-agronomist-operational-center-sql`, created from that exact SHA |
| worktree | `C:\AgroSat_worktrees\task_240` (the dirty `C:\AgroSat` checkout was not touched) |
| code commit | `dad1fd5` fix(task240): close the agronomist Operational Center scope condition |
| test database | `agrosat_h0a_task240` on the local PostgreSQL 16 server (PostGIS 3.6, Alembic `0016_operational_command_center`), created for this task under the `agrosat_h0a*` isolation guard |
| evidence | `C:\AgroSat_backups\TASK_240_AGRONOMIST_OC_SQL_FIX_EVIDENCE_20260927` (scripts, reproduction, mutation, regression, production probes). `EVIDENCE_SECRET_SCAN.json` is clean: the server password is in none of the files. The only URL-pattern hit is a backend test's fixture URL with a dummy password |

## Reproduction
**Setup.** `scripts/reproduce_b6.py` ran against the unfixed a69dd66 code on the test database. It seeded synthetic tenants shaped like the pilot:
- enterprise 9: a manager, an agronomist and a viewer;
- enterprise 7: a manager and an agronomist;
- a global admin.

Each manager opened one canonical inspection through the real inspection API and assigned it to that enterprise's agronomist. The script then called the endpoints on `main.app`. Only authentication and the session factory were overridden, and server exceptions were not re-raised, so the client saw what a browser sees (`reproduction/pre_fix.json`).

| request (unfixed code) | agronomist 9 | agronomist 7 | admin | manager 9 | manager 7 | viewer 9 |
|---|---|---|---|---|---|---|
| `GET /api/operational-center/filter-options` | **500** | **500** | 200 | 200 | 200 | 200 |
| `GET …/fields/{id}/timeline`, enterprise-9 field assigned to agronomist 9 | **500** | **500** | 200 | 200 | 404 | 200 |
| `GET …/fields/{id}/timeline`, enterprise-9 field with no assignment | **500** | **500** | 200 | 200 | 404 | 200 |
| `GET …/fields/{id}/timeline`, enterprise-7 field assigned to agronomist 7 | **500** | **500** | 200 | 404 | 200 | 404 |
| `GET /api/management-analytics` | 403 | 403 | 200 | 200 | 200 | 403 |

**Database error.** Calling the service functions directly gave the exact error. For this evidence session only, the connection set `lc_messages=C`, because the server's Russian messages reach a Windows client garbled.

| failing statement | exception | SQLSTATE | message | error position |
|---|---|---|---|---|
| `filter_options`, fields statement | `sqlalchemy.exc.ProgrammingError` wrapping `psycopg2.errors.SyntaxError` | 42601 | `syntax error at or near "ORDER"` | 1127 of 1204: the `ORDER BY` right after the fragment |
| `field_timeline` → `_authorized_field` | same | 42601 | `syntax error at end of input` | 1121 of 1179: the end of the statement |

- **Client response.** The client received only the generic text `Internal Server Error`. The app is not built in debug mode, so no SQL reached the client.
- **New suite, before the fix.** On the unfixed code the new PostgreSQL suite gave **8 failed, 3 passed** (`reproduction/pre_fix_tests.*`).
  - Every agronomist test failed with the same SQLSTATE 42601.
  - The database-free guard failed with `unbalanced parentheses`.
  - Only the admin/manager/viewer tests and the Management Analytics test passed.

## Root cause
- **Function.** `backend/services/operational_center.py::_agronomist_field_condition(alias)`, lines 739–760.
  - It came in with b53d96e (TASK_221, 2026-09-19) and was never changed afterwards.
  - Every production release since at least 4cd8ea7 contains it, including the current a69dd66.
- **Grouping defect.** Balanced literal lists (`IN (…)`) and `now()` are left aside below.
  - After `interval '30 days'`, eight groups are open. All but the outermost must close there, which is seven.
  - The code closed six, so the assigned-inspection `EXISTS` (group 2) stayed open.

| # | opened by | role |
|---|---|---|
| 1 | `(EXISTS` | the whole condition, closed by the last character |
| 2 | `EXISTS (SELECT 1 FROM field_inspections scope_inspection` | inspection assigned to the actor |
| 3 | `AND (scope_inspection.status IN (…)` | active, or confirmed |
| 4 | `OR (scope_inspection.status='confirmed'` | confirmed branch |
| 5 | `AND (NOT EXISTS (…historical_plan…)` | no plan yet, or a current plan |
| 6 | `OR EXISTS (SELECT 1 FROM agronomy_plans current_plan` | current plan |
| 7 | `AND (current_plan.status NOT IN (…)` | open, or closed recently |
| 8 | `OR (current_plan.status='closed'` | closed within 30 days |

- **Consequence.** Because group 2 stayed open, `OR EXISTS (… agronomy_work_items scope_work …)` was parsed inside the inspection subquery's `WHERE`.
  - The final `))` then closed the work `EXISTS` and group 2, so group 1 was never closed.
  - Raw counts: 14 `(` against 13 `)`.
- **How each statement fails.** Every statement that embeds the fragment ends with one `(` still open.
  - In `filter_options`, the next token is `ORDER`.
  - In `_authorized_field`, the statement ends, so the error is "at end of input".
- **Callers affected.**
  - `filter_options`: the fields statement and the crops statement. The crops statement carries the same defect, but the fields statement fails first.
  - `_authorized_field`, which only `field_timeline` uses.
  - A repository-wide search finds no other caller.
  - Queue, summary and case detail use `_scope_conditions`, which was never broken.
- **Only a parenthesis.**
  - Every column the fragment reads exists at 0016: `field_inspections` (`enterprise_id`, `field_id`, `assigned_to_id`, `status`), `agronomy_plans` (`inspection_id`, `enterprise_id`, `status`, `closed_at`) and `agronomy_work_items` (`enterprise_id`, `field_id`, `assigned_to_id`, `status`).
  - Its single bind, `:actor_user_id`, is supplied by both callers.
  - `alias` is the literal `"f"` in code.
  - There is no parameter bug and no second grouping bug.

## Fix
**Change.** One character in a constant string, `backend/services/operational_center.py` line 754:

```diff
-        "OR (current_plan.status='closed' AND current_plan.closed_at>=now()-interval '30 days')))))) "
+        "OR (current_plan.status='closed' AND current_plan.closed_at>=now()-interval '30 days'))))))) "
```

The condition is now `(EXISTS (inspection assigned to the actor, active, or confirmed without a plan or with a current or recently closed plan) OR EXISTS (planned or in-progress work item assigned to the actor on this field))`. Both subqueries stay correlated to the field and its enterprise.

**Why it is correct**
- **Contract.** It implements the TASK_221 RBAC rule exactly: "enterprise scope further restricted to assigned inspections or active assigned work".
- **Consistency.** The inspection branch has the same grouping the read model already uses for active inspections (`freshness_cases` in `CASES_CTE`).
- **Placement.** The added `)` closes the inspection `EXISTS` directly before the work `EXISTS`, so the two become sibling disjuncts of the outer group.

**Placements that also parse but are wrong** (see the mutation check under Tests)
- **`)` appended at the very end.** This nests the work `EXISTS` inside the inspection subquery. It is then evaluated once per `field_inspections` row and is false when that table has no row. The committed structural guard rejects it.
- **One `)` moved into the active-status list.** The confirmed branch then loses its field, enterprise and assignee correlation, which widens agronomist scope. The PostgreSQL scope tests reject it.

**No error hiding.** No exception handling was added: the invalid SQL is fixed, not hidden.

## Authorization preservation
All figures below come from `backend/tests/test_task240_agronomist_operational_center_postgres.py` on the fixed code, using synthetic identities. The fixture covers every branch of the condition. A is the enterprise-9 agronomist, A2 a second agronomist in enterprise 9, and B the enterprise-7 agronomist.

| field | how it is set up (canonical APIs) | A | A2 | B | manager / viewer 9 |
|---|---|---|---|---|---|
| active (ent 9) | inspection assigned to A, not started | ✔ | – | – | ✔ |
| confirmed (ent 9) | A's inspection confirmed, no plan | ✔ | – | – | ✔ |
| work (ent 9) | A2's inspection confirmed; approved plan whose planned work is A's | ✔ via work | ✔ via inspection with a current plan | – | ✔ |
| closed_recent (ent 9) | A's inspection, plan override-closed now | ✔ | – | – | ✔ |
| closed_old (ent 9) | same, closure moved 31 days back | – | – | – | ✔ |
| rejected (ent 9) | A's inspection rejected | – | – | – | ✔ |
| others (ent 9) | inspection assigned to A2 | – | ✔ | – | ✔ |
| idle (ent 9) | no workflow | – | – | – | ✔ |
| foreign (ent 7) | inspection assigned to B | – | – | ✔ | – |

**Enterprise-9 agronomist**
- **Filter options:**
  - `enterprises` is `[9]`;
  - `fields` holds exactly its four fields;
  - `crops` holds cotton only (the idle field's wheat and enterprise 7's barley are absent);
  - `assignees` holds the agronomist alone.
- **Timeline:** 200 for each field in scope, with the `field_id, items, limit, offset` contract.
- **Denials:** every other field returns 404 `{"detail":"Field not found"}`. That includes enterprise 7's field, and the status and JSON body are identical to those for unknown id 987654.

**Enterprise-7 agronomist**
- **Filter options:** `[7]`, its own field, barley, and itself.
- **Timeline:** 200 for its own field, 404 for every enterprise-9 field.

**Foreign-enterprise denial**
- `enterprise_id=7` requested by the enterprise-9 agronomist returns 404 `Enterprise not found`. So do the reverse request and an unknown id.
- Request filters can only narrow the scope. Requesting its own `enterprise_id` returns the same body as sending none.

**Empty scope.** An agronomist with no assignment gets 200 with its own enterprise, no fields, no crops and itself as the only assignee. A timeline request from it returns 404, never 500.

**Management Analytics.** Its semantics are unchanged: 403 for all three agronomists and 200 for the manager.

**Admin, manager and viewer**
- **Results:** their filter options and timelines are identical before and after the fix.
  - Their two tests (options and timelines) passed on the unfixed code and on the fixed code, and so did the Management Analytics test.
  - In the reproduction script their HTTP results are identical before and after.
- **Scopes:**
  - the manager and the viewer see every enterprise-9 field;
  - the admin sees both enterprises, and `enterprise_id=7` narrows the admin exactly to the enterprise-7 manager's view.

**Design note (unchanged by this task).** The field scope (options and timeline) and the queue scope (`_scope_conditions`) are defined differently by TASK_221, and this task did not touch either.
- **How they differ.** A queue case follows its assignee, `COALESCE(current work item assignee, inspection assignee)`. The field scope follows the inspection assignee or active work.
- **Evidence** (`reproduction/scope_comparison.json`, same fixture):
  - A: options and queue both cover {active, closed_recent, confirmed, work};
  - B: both cover {foreign};
  - A2: options {others, work}, queue {others}. A2 inspected the `work` field, whose current work belongs to A.
- **Assessment.** The divergence stays inside the enterprise and is read-only. See Residual gaps.

## Tests
Every PostgreSQL run used only `agrosat_h0a_task240`. The helper scripts read the server URL from `C:\AgroSat\backend\.env` and never print it.

| lane | scope | result |
|---|---|---|
| B6 focused, PostgreSQL, **unfixed** code | new suite (11 tests) | 8 failed, 3 passed |
| B6 focused, PostgreSQL, fixed code | new suite | **11 passed** |
| Mutation check | the same suite against three parenthesis mutants (`scripts/t240_mutant.py`; worktree untouched) | unfixed: 8 failed; `)` at the end: 1 failed (structural guard); `)` moved into the status list: 4 failed (scope tests). Every mutant was caught |
| Operational Center and auth/tenant contracts, no database | `test_task221_operational_center_contract.py`, `test_task209_authorization_matrix.py` and the new file | 30 passed, 10 skipped (the new PostgreSQL classes) |
| Guards (CI `guards` job) | `rollback-contract`, `alembic heads`, `test_web_startup_safety.py`, retired-endpoint suites | rollback-contract exit 0; one head `0016_operational_command_center`; startup safety 6 passed; retired endpoints 137 passed |
| Backend full suite (CI `backend` job) | `pytest tests` | **1497 passed, 170 skipped, 0 failed** (junit total 1667); TASK_238's 1496 plus the new guard |
| Ops (CI `ops` job) | PowerShell parse; `pytest ops/tests` | 24 scripts parsed, 0 failed; 117 passed |
| PostgreSQL integration (CI `postgres.yml`) | every `tests/*postgres*.py` in its own process; `alembic current` = `0016_operational_command_center (head)` | **170 passed, 0 failed, 0 skipped** in 11 suites (TASK_238's 159 in 10 suites plus the new 11) |
| Frontend | — | not run: no frontend file changed |

PostgreSQL lane per suite, on commit `dad1fd5`:

| suite | tests | passed | failed | skipped |
|---|---|---|---|---|
| `test_h0a_freshness_postgres_contract` | 34 | 34 | 0 | 0 |
| `test_h0a_notification_lifecycle_postgres` | 16 | 16 | 0 | 0 |
| `test_task223_collection_run_lock_postgres` | 8 | 8 | 0 | 0 |
| `test_task225_closed_loop_postgres` | 14 | 14 | 0 | 0 |
| `test_task225_lifecycle_boundaries_postgres` | 12 | 12 | 0 | 0 |
| `test_task225_signal_to_inspection_postgres` | 14 | 14 | 0 | 0 |
| `test_task228_schema_readiness_postgres` | 14 | 14 | 0 | 0 |
| `test_task229_collector_finalization_postgres` | 4 | 4 | 0 | 0 |
| `test_task232_management_analytics_postgres` | 29 | 29 | 0 | 0 |
| `test_task238_user_accounts_postgres` | 14 | 14 | 0 | 0 |
| `test_task240_agronomist_operational_center_postgres` | 11 | 11 | 0 | 0 |

**Final regression.** It ran on the committed head `dad1fd5` with a clean tree, before any push (`regression/final_dad1fd5/`).
- **Backend skip list.** Compared with TASK_238's final regression, it differs only by the 10 new PostgreSQL tests, which skip without a database. No other test changed status.
- **Operational Center coverage.** It is exercised by the TASK_225 closed-loop and lifecycle suites, the TASK_232 analytics-equality suite and the new suite.
- **Auth and tenant coverage.** It comes from the authorization matrix (backend lane) and from the new suite.

**Post-fix reproduction** (`reproduction/post_fix.json`, same script and fixture as the pre-fix run)
- **Enterprise-9 agronomist:**
  - filter options return 200 with its assigned field only;
  - the timeline returns 200 for that field and 404 for the unassigned and the foreign field;
  - Management Analytics returns 403.
- **Enterprise-7 agronomist:** the same, mirrored.
- **Other roles:** the admin, manager and viewer responses are identical to the pre-fix run.

## Query/performance
- **Statement count.** It is captured with a `before_cursor_execute` listener and is fixed:
  - filter options run **4** statements (enterprises, fields, crops, assignees);
  - the field timeline runs **2** (authorized field, bounded timeline).
  - The agronomist and the manager execute the same number, and it does not change between 1 and 6 assigned fields (test `test_statement_count_is_fixed_and_equals_the_manager_path`).
- **Bounds.** The helper adds correlated `EXISTS` semi-joins inside the one bounded statement (`LIMIT 500` / `LIMIT 200`). No per-field or per-case round trip, ORM relationship or lazy load exists or was added.
- **N+1:** none.
- **Indexes.** `ix_agronomy_work_assignment` and `ix_field_inspections_assigned_status_due` already cover the assignee lookups. No index or schema change is needed.

## Security
- **Tenant isolation.** No tenant clause changed.
  - `filter_options` still forces `enterprise_id` to the actor's enterprise for every tenant role and answers 404 for any other value.
  - `_authorized_field` still requires `f.enterprise_id=:enterprise_id` for tenant roles.
  - The agronomist condition is ANDed on top, and both of its subqueries are correlated to the field's enterprise.
  - The tests show no foreign field, crop, enterprise or person in any agronomist response, and identical 404 bodies for foreign and unknown ids.
- **SQL injection.**
  - The fix adds one character to a constant.
  - The only interpolated value is `alias`, the literal `"f"` from code.
  - Every request value is a typed, validated integer sent as a bound parameter: `enterprise_id`, `field_id`, `limit`, `offset`, and `actor_user_id` from the authenticated user.
  - No string concatenation was introduced.
- **Parameter binding.**
  - `text(condition).compile().params` is exactly `{actor_user_id}`.
  - The captured DBAPI parameters of the agronomist's fields and crops statements are exactly `{enterprise_id: 9, actor_user_id: <agronomist>}`.
  - Those of the timeline authorization are exactly `{field_id, enterprise_id: 9, actor_user_id}`.
- **Authorization bypass.** No role check changed. Management Analytics is 403 for agronomists, and requesting another enterprise answers 404.
- **Broader agronomist visibility.** None. Agronomist scope matches the designed branches field for field, and the mutation check shows that a widening mistake is caught.
- **Error leakage.** Before the fix, the failure surfaced as a generic `Internal Server Error` without SQL. After it, valid requests return their normal 200 or 404 responses. No exception is caught or turned into empty data.

## Alembic
- Starting head: `0016_operational_command_center` (single head).
- Ending head: `0016_operational_command_center` (single head; `rollback-contract` exit 0).
- Migration added: **NO**. There is no runtime DDL and no index change.

## Scope
- **Backend only.** The code commit `dad1fd5` changes exactly:
  - `M backend/services/operational_center.py` (1 line);
  - `A backend/tests/test_task240_agronomist_operational_center_postgres.py`.
- **Report commit.** It adds only `docs/TASK_240_RESULT.md`.
- **Nothing else.** No frontend, migration, deployment script, release tooling, Scheduled Task, Sentinel, notification, backup, detector, media or production configuration change.
- **No deployment.** No release was started and the Controlled Pilot was not started.

## Production
**NOT DEPLOYED**
**NOT MUTATED**

- **Release pointer.** `current-release.json` is unchanged: SHA-256 `94cf90c5…` before and after, still `a69dd664e6bec79af6e098bf9a940924001d04d6`.
- **Readiness.** `/health/ready` reported the same `release_revision` and `0016_operational_command_center` before and after.
- **Database.** This task never connected to the production database `agrosat`. Its only connections went to `agrosat_h0a_task240`, plus the `postgres` maintenance database once, to `CREATE DATABASE agrosat_h0a_task240`.
- **Production contact.** The only contact with the running production application was two unauthenticated, read-only `GET http://127.0.0.1:8000/health/ready` probes, one before and one after, plus reading the release pointer file.
- **Pilot accounts.** The rows of baybutayev@, ivanov@ and yashkin@ were not read, changed or used to log in. Account 20 was not accessed and stays LEAVE_UNCHANGED.
- **origin/main.** It is unchanged at `a69dd664e6bec79af6e098bf9a940924001d04d6`.

## Rollback and release checks
- **Rollback.** Revert `dad1fd5`, one character plus a test file. There is no data or schema dependency. After a release, production rolls back through the control plane to the previous release as usual.
- **Release checks.** To be done in the later release task, not here.
  - The two pilot agronomists (enterprise 9 and enterprise 7) get 200 from filter options with only their assigned fields.
  - The timeline answers 200 for an assigned field and 404 for a foreign one.
  - Management Analytics stays 403.
  - The TASK_237 continuation scripts `t237c_qualify_api.py` and `t237c_browser.mjs` cover these checks. No re-provisioning is needed.
- **Visible side effect.** For agronomists, the Operational Center filter dropdowns, previously silently empty because of the 500, now list their assigned fields and crops. The UI does not call the field timeline. No other module uses the helper.

## Residual gaps
1. **B6 is still live in production.** Production runs a69dd66, which contains the defect, until this branch is merged and released through the control plane. TASK_237's PASS criterion 16 stays open until the agronomist re-qualification runs on the released code.
2. **Field scope and queue scope differ (pre-existing TASK_221 design).** When an inspection's assignee is not the assignee of its current work, the inspection assignee is offered the field in filter options and can read its timeline, but has no queue case for it. The evidence is `scope_comparison.json`, agronomist A2.
   - The divergence stays inside the enterprise and is read-only.
   - This task forbids changing scope semantics, so it neither widens nor narrows either scope. Whether to align them is a product decision for a separate task.
3. **CI on GitHub not observed.** `gh` is not installed on this host, so no GitHub Actions run for the branch was watched. The evidence is the local run of the same lanes: guards, backend, ops and PostgreSQL. The frontend lane was not run because no frontend file changed.

The test database `agrosat_h0a_task240` is kept idle for reuse. After the runs it holds only `alembic_version`, the migration-seeded `monitoring_rule_versions` row and PostGIS `spatial_ref_sys`, because every suite and the reproduction script truncate what they seed.
