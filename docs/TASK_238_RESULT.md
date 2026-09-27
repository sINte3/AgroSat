# TASK_238 — NON-ADMIN USER ACCOUNT MANAGEMENT CLI RESULT

## Verdict
**PASS_TASK_238**

AgroSat now has a supported, audited, operator-invoked CLI for creating non-admin accounts and resetting their passwords: `backend/scripts/manage_user_accounts.py`.
- Development only: nothing was deployed, production was not changed, and no pilot user or pilot credential was created.
- There is no schema change, migration, endpoint or frontend change.

## Base
| item | value |
|---|---|
| origin/main (verified after `git fetch origin`) | `d883879e5d23b9a79ebd62ea01b85364ec5fb590` |
| branch | `task/238-user-account-management-cli`, created from that exact SHA |
| worktree | `C:\AgroSat_worktrees\task_238` (clean; the dirty `C:\AgroSat` checkout was not touched) |
| code commit | `3ca312b` feat(ops): audited CLI to create and reset non-admin user accounts |

## Existing gap
TASK_237 stopped before any production write (§6.4, §8). The deployed code at d883879 had no supported way to create enterprise-scoped users or reset a non-admin password:
- **API:** of 146 operations, the only user-related ones are login, me, a list-only memberships call, and `/api/auth/register`. Register works only when `environment=development` and a flag is set, and it always creates a viewer.
- **`manage_admin_credentials.py`:** its `inspect` and `reset-password` work only on an existing **admin**.
- **Seed scripts:** `seed_data.py` bootstraps only the first admin, and the qualification seed is locked to an isolated database.
- **History:** past non-admin accounts came from ad hoc inserts, for example account 20 with `created_at` NULL.

## Implemented CLI
- **Path:** `backend/scripts/manage_user_accounts.py`, next to `manage_admin_credentials.py`, whose credential policy and HTTP/login helpers it reuses.
- **Invocation:** run it with the backend of the release whose database is the target:
  - `python -m scripts.manage_user_accounts …`, or `python scripts\manage_user_accounts.py …`, which puts `backend` on `sys.path` like the other scripts.
  - Configuration is selected exactly as the application selects it: `AGROSAT_RUNTIME_ENV_FILE`, else `backend/.env`.

| command | purpose | writes |
|---|---|---|
| `inspect --login L [--full-name N] --expected-database D` | Read-only identity report: normalized login, case-insensitive login matches, strong full-name matches, database target | never |
| `create --login --full-name --role --enterprise-id [--distinct-from ID…] --expected-database --credential-file --reason [--audit-dir] [--verify-api-base] [--apply]` | Create one non-admin account | only with `--apply` |
| `reset-password --login --expected-user-id --expected-database --credential-file --reason [--audit-dir] [--verify-api-base] [--apply]` | Replace the credential of one non-admin account | only with `--apply` |

**Supported roles:** `manager`, `agronomist` and `viewer`, which are exactly `TENANT_ROLES` in `api/dependencies.py`. A test pins this against `UserRole` and `TENANT_ROLES`.
- Each role is scoped by `users.enterprise_id`, so `--enterprise-id` is required and must name an existing, active enterprise.
- **`admin` is refused in both directions.** `create --role admin` exits 17, and so does resetting an admin target. Admins stay with `manage_admin_credentials.py`.

**Dry run and apply**
- **Default is a dry run.** Without `--apply`, `create` and `reset-password` validate everything inside a `SET TRANSACTION READ ONLY` transaction, including whether the credential path is usable.
  - They print one `preflight` JSON line (target, request, matches, plan `CREATE`/`ALREADY_EXISTS`/`RESET`/`BLOCKED`, reasons) and exit with the code `--apply` would fail with.
  - They write nothing: no database row, credential file or audit file.
- **`--apply` is explicit.** It requires `--audit-dir` and prints the same `preflight` line before the change, then a `result` line.
  - There is no interactive prompt. `allow_abbrev=False`, so `--app` is refused.

**Exit codes**

| code | meaning |
|---|---|
| 0 | done, or already exists (no change) |
| 2 | invalid input |
| 10 | database identity |
| 11 | target identity |
| 12 | API preflight |
| 13 | database update failed (nothing written) |
| 14 | verification failed, change rolled back |
| 15 | critical, manual intervention needed |
| 16 | audit write |
| 17 | role rejected (admin) |
| 18 | enterprise missing or inactive |
| 19 | conflict: operator decision needed |
| 20 | unsafe credential output |
| 21 | schema mismatch |
| 70 | unexpected (text withheld) |

## Create contract
**Validation**
- **Login:** normalized exactly as `/api/auth/login` does (`strip().lower()`, then exact comparison with `users.email`). A new login must be a lowercase ASCII e-mail-form identifier.
- **Full name:** NFC, whitespace collapsed, at most 255 characters, no control characters.
- **Role:** must be one of the three above.
- **Enterprise:** must exist and be active.
- **Reason:** required.

**Duplicate handling**
- **Login collisions:** exact and case-insensitive collisions (`lower(btrim(email))`) are refused (exit 19), including legacy case-variant rows.
- **Idempotent rerun:** when the same normalized login already exists with the same full name, role and enterprise and is **active**, the run reports `ALREADY_EXISTS`. It does not write, does not generate a credential and needs no credential path, so a repeated command is a no-op with exit 0.
  - The same identity but **inactive** is a conflict, because reactivation is a separate decision.
- **Same person:** a *strong* full-name match counts as the same person. Names are compared after NFKC, casefold and ё→е.
  - A match means equal names, or one name contained in the other with **at least two** shared parts. For example, "Иванов Иван" matches "Иванов Иван Петрович".
  - A shared first name never counts: "Умид Саидов" does not match "Байбутаев Умид Шавкатович", and that pair is tested.
  - Such a match blocks the run with an explicit operator decision. `--distinct-from <id>` records that it is a different person, and it is refused unless the id really is a name match.
- The tool never repurposes an existing account.

**Transactions**
- The read-only preflight is followed by **one** write transaction:
  - `pg_advisory_xact_lock` serializes all runs of the tool that change accounts;
  - every check is re-run under that lock (with `SELECT … FOR UPDATE` on login matches);
  - the insert goes through the application's own `User` model (ORM defaults: `created_at`, `is_active=True`; `phone` NULL), then commit.
- Any failure rolls back with no row.
- A post-commit verification then re-reads the row (login, full name, role, enterprise, active, exactly one login match) and verifies the password with the production `pwd_context`.
- If verification fails, the new row is deleted with a compare-and-delete on id, login and hash (exit 14). If that is not safe, the result is exit 15 and nothing further is changed.

## Reset contract
- **Target resolution:** the login must match exactly one account case-insensitively, the id must equal `--expected-user-id`, and the stored login must already be normalized.
  - Refused: zero matches, two or more, a wrong id, admin (17), an unknown role, and an un-normalized login (the account cannot sign in).
- **Preserved fields:** only `hashed_password` changes, through a compare-and-swap on id and old hash in one locked transaction.
  - `email`, `full_name`, `phone`, `role`, `enterprise_id`, `is_active` and `created_at` are verified unchanged after commit. `last_login` changes only if someone signs in.
  - An inactive account stays inactive. `--verify-api-base` is refused for it because its sign-in is refused.
- **Credential change:** the new credential is hashed with the exact production `api.auth.pwd_context` (bcrypt) and verified after commit.
  - If verification fails, the previous hash is restored by compare-and-swap (exit 14).
  - The previous password stops working, and the new one works (proven through the real login endpoint).

## Credential security
**Generation**
- `secrets` CSPRNG: `secrets.choice` plus `SystemRandom().shuffle`; `random` is not imported, which a static test enforces.
- 20 characters from a 64-symbol alphabet, about 120 bits. At least one each of upper, lower, digit and symbol.
- Ambiguous characters are excluded, and the symbols are dotenv-safe: none of `"'#$=\` or space.
- Validated with the existing admin credential policy: 16 or more characters, four character classes, placeholder rejection. It stays under bcrypt's 72-byte limit.
- There is **no `--password` option** and no prompt, so a credential is never typed, reused or predictable.

**Output**
- The credential is written once, **before** the database change, to `--credential-file`, which the operator supplies. It is never hardcoded (no `C:\AgroSat_secrets` in the code).
- **File format:** dotenv, `AGROSAT_LOGIN_USERNAME` plus `AGROSAT_LOGIN_PASSWORD` plus comment lines (login, role, enterprise, operation, time). This fits the existing `Run-Task221BrowserProtected.py --pilot-credential-env` contract: exactly one USERNAME/EMAIL key and one PASSWORD key.
- **Lifetime:** the file exists exactly as long as the database holds its credential. Any failure before or at commit, and any rolled-back verification, removes it. It is kept only on success or on a critical, unverifiable state.

**Path and ACL behavior (fail-closed)**
- **Path shape:**
  - The path must be absolute with no `..`.
  - The file name must be simple: `[A-Za-z0-9._-]`, no alternate data stream `:`, no reserved device name, no trailing dot.
  - The directory must already exist and be given by its real path, so a link, junction or alias is refused.
- **Location:**
  - The file must not already exist; it is never overwritten (`O_CREAT|O_EXCL`).
  - It must not be inside a Git repository or worktree (`.git`), an AgroSat release tree (`release-manifest.json`), the tool's own source tree, or the audit directory.
- **Directory must be private:**
  - **Windows:** read by SID through `Get-Acl`, which is locale independent; the path is passed through an environment variable, not interpolated.
    - The DACL must be **protected** (`icacls /inheritance:r`).
    - It may allow only LocalSystem `S-1-5-18`, Administrators `S-1-5-32-544`, OWNER RIGHTS `S-1-3-4` (Python's own private-directory ACL) and the operator's own SID.
    - An inherit-only CREATOR OWNER entry is tolerated; any other allowed principal is refused.
  - **POSIX:** the directory must be owned by the operator with no group or other bits.
- **New file:** its own ACL (Windows) or mode 0600 (POSIX) is verified **while it is still empty**. If it is not private it is removed before any secret is written.
- **Not logged anywhere:**
  - The password never reaches stdout, stderr, the audit record, the result JSON, exception messages or Git.
  - Tests assert this for the generated password and its hash, on the in-memory store, on PostgreSQL, and in a real subprocess run (the operator-style demo: `passwords_absent_from_kept_outputs: true`, `hash_absent_from_kept_outputs: true`).
  - Unexpected errors print only their exception class.
- **`--verify-api-base`:** it sends the credential to the API, so it is accepted only for loopback (`127.0.0.1`, `localhost`, `::1`) or HTTPS, in addition to the admin tool's no-credentials/no-query rule.

## Auditability
There is no canonical DB audit model for identity events:
- `operational_audit_events` requires an inspection, a field and a CHECK-listed event type.
- `tenant_commercial_audit_events` requires an AgroSat actor user, which a CLI operator does not have.

Using either would need a migration, which is out of scope. The tool therefore uses the **existing credential-operation convention** from TASK_175's `manage_admin_credentials.py`: sanitized JSON and TXT records, written atomically (temp file plus `os.replace`) to `--audit-dir`, one pair per `--apply` attempt, blocked attempts included.
- **Fields:** `AUDIT_FIELDS`, checked by a test:
  - who and when: timestamp, tool, tool commit (Git, or the release manifest's `git_sha` in a release tree), host, OS user, operation, reason;
  - database: database name, server, environment, Alembic revision and expected head;
  - target: user id, login, full name, role, enterprise, active flag, `distinct_from` ids;
  - result: outcome (`CREATED`, `NO_CHANGE_ALREADY_EXISTS`, `PASSWORD_RESET`, `NOT_CHANGED`, `ROLLED_BACK`, `CRITICAL_MANUAL_INTERVENTION`), result, error code and message;
  - flags: row created, hash changed, credential path, written/removed, verification, rollback.
- The audit directory is probed before any change.
- If the record cannot be written after a completed change, the result is still printed with `audit_write_failed: true` (exit 16).
- The record never holds a password, hash, token or connection string.

## DB target safety
- **`--expected-database` is required** for every command and checked (`hmac.compare_digest` against `current_database()`) before anything else is read. A mismatch exits 10 with nothing read or written; this is tested on the real database with `agrosat` against the isolated database.
- **Target metadata is reported before any change:** database name, `host(inet_server_addr()):port` (never credentials), `settings.environment`, the database Alembic revision, and the code's head from `services/migration_head`.
- **`--apply` is refused** (exit 21) if the revision is not the code's single head, or the head cannot be resolved. This prevents running a tool from one release against another release's database.
- Isolated test databases and production are told apart by name: tests use `agrosat_h0a_*`, and production is `agrosat`.

## Tests
New files:
- `backend/tests/test_task238_manage_user_accounts.py`: **48 tests**, no database. An in-memory store with real transaction semantics, real file-system paths and ACLs (icacls/`Get-Acl`), and static source checks.
- `backend/tests/test_task238_user_accounts_postgres.py`: **14 tests**, PostgreSQL. Real schema at 0016, the `User` model, bcrypt, transactions and advisory locks, and sign-in through the FastAPI login and me endpoints. The guard is the same as for every PostgreSQL suite (`agrosat_h0a*` only).

| required case (§16) | covered by |
|---|---|
| manager and agronomist creation, enterprise binding, active default, login works with the generated password | unit `test_manager_and_agronomist_are_created…`; PG `test_manager_and_agronomists_are_created_and_can_sign_in` (real login and me) |
| exact, case-insensitive and same-person duplicates; nonexistent or inactive enterprise; invalid role; admin rejected | unit `test_login_collisions_are_refused`, `test_same_person_conflict…`, `test_enterprise_role_and_schema_are_validated`; PG equivalents |
| reset: old password fails, new works, role, enterprise and other fields unchanged; admin, missing, ambiguous and wrong-id rejected | unit `ResetTests`; PG `test_reset_rotates_only_the_credential` (real login 401/200), `test_reset_refusals_change_nothing` |
| failed create leaves no row; failed reset keeps the original credential | unit and PG commit-failure and verification-failure tests (compare-and-delete, compare-and-swap restore) |
| dry run validates and writes nothing | unit `test_dry_run…` (stores all read-only, no commit or lock); PG `test_dry_runs_and_inspect_write_nothing` (users md5, count and `users_id_seq` unchanged) |
| password absent from stdout and audit; hand-off file contents; never overwritten; unsafe paths rejected | unit `CredentialPathTests`, `AccessControlTests`, `test_unexpected_error_text_is_withheld`; PG sign-in with the file's credential |
| idempotency | unit and PG `test_rerun…` (no new row, sequence untouched, no credential file) |
| concurrency | PG `test_concurrent_change_is_serialised_and_rechecked_under_the_lock`: a conflict inserted while the CLI waits on the advisory lock is caught under the lock; no row is inserted and the credential is removed |

**Commands and results.** All used `C:\AgroSat\backend\venv\Scripts\python.exe` (Python 3.14) with `PYTHONUTF8=1`. The PostgreSQL runs used the isolated database `agrosat_h0a_task238`, created for this task, at `0016_operational_command_center`.

| run | result |
|---|---|
| `pytest tests/test_task238_manage_user_accounts.py tests/test_manage_admin_credentials.py tests/test_auth_security.py` | **119 passed** (48 + 29 + 42) |
| `pytest tests/test_task238_user_accounts_postgres.py` (isolated database) | **14 passed** |
| operator-style CLI demo (`scripts/cli_demo.py`, real subprocesses) | inspect 0; create dry run 0; create apply 0; rerun 0 (no change); admin 17; reset dry run 0; reset apply 0; overwrite 20; wrong database 10. The create credential is invalid after the reset and the reset credential is valid. Password and hash appear in no kept output. The demo credential directory was deleted |

**Final regression on the committed head `3ca312b`.** This is the local equivalent of `.github/workflows/ci.yml` and `postgres.yml`, run before any push, using the evidence script `scripts/final_regression.sh`. The frontend lane is not needed because no frontend file changed.

| lane | result |
|---|---|
| guard: control-plane `rollback-contract` | **PASS**, head `0016_operational_command_center`, 17/17 revisions classified |
| guard: `alembic heads` | single head `0016_operational_command_center` |
| guard: `tests/test_web_startup_safety.py` | 6 passed |
| guard: retired production mutation endpoints (4 files) | 137 passed |
| backend: `pytest tests` (PostgreSQL suites skip without a database) | 1656 collected: **1496 passed, 160 skipped, 0 failed** |
| ops: PowerShell parse | 24 scripts, 0 errors |
| ops: `pytest ops/tests` | 117 passed |
| PostgreSQL: each `tests/*postgres*.py` in its own process on `agrosat_h0a_task238` | 10 suites, **159 passed, 0 failed** |

The ten PostgreSQL suites:

| suite | passed |
|---|---|
| H0-A freshness | 34 |
| H0-A notifications | 16 |
| TASK_223 run lock | 8 |
| TASK_225 closed loop | 14 |
| TASK_225 lifecycle | 12 |
| TASK_225 signal to inspection | 14 |
| TASK_228 readiness | 14 |
| TASK_229 finalization | 4 |
| TASK_232 analytics | 29 |
| TASK_238 accounts | 14 |

The regression tree was clean before the run, and afterwards held only this untracked report. The warnings are pre-existing deprecations: `datetime.utcnow`, `declarative_base`, pydantic class `Config` and the starlette test client.

## Security review
| topic | result |
|---|---|
| SQL injection from CLI inputs | **none**. All user access goes through the ORM or SQLAlchemy Core with bound parameters. The only text SQL is constant or bound (`:id`, `:key`). No input is formatted into SQL |
| arbitrary file write through the credential path | **mitigated**. The path must be absolute with no `..`; the name must be simple (no ADS or device names); the directory must exist at its real path (no links or junctions); `O_EXCL` means no overwrite; repository, release and audit trees are refused; the directory must be private and the file is proven private before writing. The audit directory is operator-chosen and receives only non-secret records, as in the admin tool |
| path traversal | refused: `..`, links, junctions and 8.3 aliases through the real-path comparison |
| secrets in stdout and stderr | none. Only explicit preflight and result documents and fixed messages; tested, including a real subprocess run |
| secrets in exception messages | none. Database and unexpected exceptions become fixed messages (`from None`); `main` prints only the class of an unexpected exception (tested with credential-looking text) |
| password reuse and predictability | none. A fresh CSPRNG credential on every run, about 120 bits; no operator-chosen password; 400-sample distinctness test |
| privilege escalation by supplying `admin` | refused. `create --role admin` exits 17, as does resetting an admin target; roles are pinned to `TENANT_ROLES` |
| enterprise change during reset | impossible. Only `hashed_password` is updated (compare-and-swap), and all other profile fields are verified unchanged after commit |
| duplicate and case-fold login ambiguity | refused. Case-insensitive lookup; several case variants mean an ambiguous target; stored logins must be normalized; the database index is case-sensitive, so the check lives under the advisory lock |
| credential in transit (`--verify-api-base`) | loopback or HTTPS only |
| subprocess (ACL read) | constant PowerShell script; the path is passed through an environment variable, so there is no injection; fail-closed on any error |

## Alembic
- Starting head: `0016_operational_command_center`.
- Ending head: `0016_operational_command_center`, a single head (`alembic heads`; control-plane `rollback-contract` exit 0).
- Migration added: **no**. There is no runtime DDL.

## Production
- **NOT ACCESSED FOR MUTATION.** The only database touched was the new isolated `agrosat_h0a_task238`, on the same local PostgreSQL server as the other isolated test databases.
- **NOT DEPLOYED.** No release, no Scheduled Task, runtime or configuration change, no service restart.
- **NO PILOT USERS CREATED.** No production credential was generated; account 20 was not touched.
- **origin/main unchanged** at `d883879e5d23b9a79ebd62ea01b85364ec5fb590`, and production is still on that SHA.

## Using it for the TASK_237 continuation (not executed here)
1. Release the tool through the control plane as usual, so that production runs it from its own release tree. Running unreleased worktree code against production is not proposed.
2. Once, from an elevated shell, create a private hand-off directory under the protected secrets area. The host is in the Russian locale, so use SIDs:
   ```powershell
   New-Item -ItemType Directory -Path 'C:\AgroSat_secrets\<pilot-subfolder>'
   icacls 'C:\AgroSat_secrets\<pilot-subfolder>' /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F'
   ```
3. For each approved person, run a dry run from the released backend, review the preflight JSON, then repeat the same command with `--apply --audit-dir <TASK_237 evidence>\audit`. Optionally add `--verify-api-base http://127.0.0.1:8000`.
   ```powershell
   $env:AGROSAT_RUNTIME_ENV_FILE = 'C:\AgroSat\backend\.env'
   cd 'C:\AgroSat_releases\PROGRAM_R3\<released-sha>\backend'
   .\venv\Scripts\python.exe -B -m scripts.manage_user_accounts create --login ivanov@agrosat.uz `
     --full-name 'Иванов Иван' --role agronomist --enterprise-id 9 --expected-database agrosat `
     --credential-file 'C:\AgroSat_secrets\<pilot-subfolder>\ivanov.env' --reason 'TASK_237 pilot provisioning'
   ```
4. The coordinator hands each credential over in person, and the file is deleted afterwards. A forgotten password is handled with `reset-password`.

## Residual gaps
1. **Not deployed.** The tool exists only on this branch; production still has no supported path until a separately approved release carries it (step 1 above).
2. **Harmless stderr noise.** On the first bcrypt hash of a run, passlib 1.7.4 logs `(trapped) error reading bcrypt version` with a traceback, because bcrypt 4.x has no `__about__`. This is observed in the demo runs (`cli_demo/03_create_apply.txt`, `07_reset_apply.txt`). It is pre-existing, the same in the backend, not an error, and carries no secret.
3. **File audit, not database audit.** The audit follows the TASK_175 file convention; a database audit table would need a migration.
4. **Hand-off file is plaintext at rest by design.** It is protected by the directory ACL, and deleting it after the hand-off is a manual step.
5. **No self-service password change.** Accounts still have no self-service change or first-login reset (there is no endpoint), so rotation is operator-driven through `reset-password`.
6. **No reactivation.** The tool has no reactivation or deactivation; an identical but inactive account is reported as a conflict.
