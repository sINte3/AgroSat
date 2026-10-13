# TASK_258 — Windows credential ACL CI compatibility (fail-closed): result

```text
TASK_258_RESULT=CONDITIONAL
ROOT_CAUSE_PROVEN=YES
REAL_WINDOWS_ACL_TESTS=PASS
CREDENTIAL_ACL_SECURITY_WEAKENED=NO
BACKEND_TESTS=PASS
REMOTE_CI_QUALIFIED=NO
PRODUCTION_MUTATIONS=0
CDSE_AUTHENTICATED_REQUESTS=0
PUSHED=NO
MERGED=NO
DEPLOYED=NO
```

**CONDITIONAL** means the root cause is reproduced and the fix is proven on real Windows ACLs
locally, including in a faithful simulation of the runner's PowerShell environment. One
condition remains: an authorized GitHub Windows run that shows the Backend job green on a real
runner. No workflow has run on this branch.

| Item | Value |
|---|---|
| Repository | `sINte3/AgroSat` |
| Branch / worktree | `task/258-windows-acl-ci-compatibility`, `C:\AgroSat_worktrees\task_258` |
| Base | `bda97e85340be5bec3444645cab20006bf954dfe` (`task/ci-256-257-qualification` = `origin/main` `3bcbcab` + TASK_256 `bc2d30d` + TASK_257 `bda97e8`) |
| Fix commit | `5e3d2c3b3416260f29e1e9b43f7f8757062f5a78`: `backend/scripts/manage_user_accounts.py`, new `backend/tests/test_task258_credential_acl_probe.py` |
| Report commit | the commit that adds this file |
| Evidence | `C:\AgroSat_backups\TASK_258_WINDOWS_ACL_CI_20261013_171042` (+ `.zip`) |
| Not part of this task | frontend `npm audit` (still red on GitHub), unpublished TASK_253/TASK_255 code (not on this branch), workflows (unchanged), Sentinel, deployment |

## 1. Root cause

The original failure is GitHub Actions run `38389373056`, job `115269571868` (*Backend
deterministic and contract suite*, `windows-latest`): `2 failed, 1664 passed, 174 skipped`.
Both TASK_238 real-ACL tests raised "The access control list of the credential location
could not be read."

1. A Windows `run:` step without `shell:` runs in **pwsh** (PowerShell 7; the runner image has 7.6.6).
2. PowerShell 7 prefixes `PSModulePath` with its own module folders. python, started by
   pytest in that step, inherits the value.
3. `_read_windows_acl` copied the whole environment into `powershell.exe` (Windows PowerShell
   5.1). Windows PowerShell therefore searched PowerShell 7's folders first and auto-loaded
   PowerShell 7's `Microsoft.PowerShell.Security`. That module's `Security.types.ps1xml`
   re-defines `ObjectSecurity` members that 5.1 already has, so the load fails
   (`FormatXmlUpdateException`). `Get-Acl` then fails with `CouldNotAutoloadMatchingModule` /
   `CommandNotFoundException`, `$ErrorActionPreference='Stop'` gives exit code 1, and the tool
   correctly fails closed with exit 20.
4. Microsoft documents exactly this for "an intermediate process, such as a cmd.exe or
   Python process that PowerShell 7 launched". The documented remedy is to remove
   `PSModulePath` from the child's environment (`about_PSModulePath`, "Starting Windows
   PowerShell from PowerShell 7").

The Ops job and the rest of Backend stayed green. Their `powershell.exe` children use only
Management/Utility cmdlets, and PowerShell 7's manifests for those modules have no
conflicting type file, so they still load in 5.1.

**Proof.** The runner condition was rebuilt locally from the documented PowerShell 7 startup
rules and the official v7.6.6 module files. The full CI-equivalent Backend suite at
`bda97e8` in an `actions/checkout`-like clone then gave **`2 failed, 1664 passed,
174 skipped`**: the same counts and the same two tests as the runner, with the same call
path and message. Without the PowerShell 7 prefix it gave 0 failures.

**Refuted by experiment.** Console code page or UTF-8 BOM (CP 866/437/1251/65001), inherited
`SECRET_KEY`, current-user SID mismatch, a weaker fixture ACL, spawn failure or timeout, and
JSON shape ambiguity. Details: evidence `audit/ROOT_CAUSE.md`.

The defect is not specific to CI. An operator running the CLI from a PowerShell 7 console is
blocked the same way. The TASK_238 PostgreSQL end-to-end suite (create/reset with real
credential files) gives 10 failed / 4 passed on the base in that environment and 14/14 with
the fix.

## 2. Change (`5e3d2c3`)

`backend/scripts/manage_user_accounts.py`, credential ACL reading only:

* `_read_windows_acl` starts Windows PowerShell **without `PSModulePath`** (case-insensitive
  removal; every other variable is kept). Windows PowerShell then builds its own default
  module path. The command, `-NoProfile -NonInteractive`, the timeout, `stdin=DEVNULL` and the
  `_ACL_QUERY` text (Get-Acl, rules by `SecurityIdentifier`, `AreAccessRulesProtected`, current
  user SID, `ErrorActionPreference='Stop'`) are unchanged.
* **Safe diagnostics.** An unreadable ACL still raises `AccountError(EXIT_CREDENTIAL_OUTPUT)`
  with the same sentence, plus a fixed stage: `(stage: start | timeout | exit code N | output
  | payload)`. No path, SID, ACE, descriptor or PowerShell output is ever included.
* **Exact payload.** `_acl_from_payload` accepts only `{"protected": bool, "user": SID,
  "rules": [{"sid": SID, "allow": bool, "inherit_only": bool}, …]}`. A lone rule object is
  read as one rule, as before. Anything else is unreadable. The base parser accepted 15 of 18
  malformed or incomplete payloads (for example `{}`, a missing `rules`, `"protected":
  "False"`, non-object rules). 14 of them made a credential *file* "private". This was latent,
  because the real query never emits those shapes, but it was not fail-closed.

`_acl_problem`, `_privacy_problem`, `check_credential_path`, `write_credential_file`,
`_TRUSTED_SIDS` (SYSTEM, Administrators, OWNER RIGHTS + the operator) and the CREATOR OWNER
inherit-only rule are unchanged. No schema change, no migration, no new dependency, no
workflow change, no frontend change.

## 3. Credential privacy semantics: preserved

* The private directory must be explicitly protected and grant only the trusted principals.
* A directory with inherited entries is rejected even when every inherited entry is trusted.
* Everyone, BUILTIN\Users, Authenticated Users, INTERACTIVE and unrelated principals are
  rejected; a deny entry grants nothing.
* The credential file is created exclusively and proven private while empty, then written;
  it is never overwritten, is removed on any failure, and the secret is never printed.
* Every failure to read the ACL (cannot start, timeout, nonzero exit, non-JSON, wrong shape)
  is a refusal with exit 20. It is never read as an empty or permissive ACL.

Invariant table with proofs: evidence `audit/SECURITY_INVARIANTS.md`.

## 4. Tests

New `backend/tests/test_task258_credential_acl_probe.py` (16 tests):

* **Real ACL.** Real `powershell.exe`, `Get-Acl` and `icacls` on disposable directories, run
  with `PSModulePath` polluted by a module folder laid out like PowerShell 7's
  `Microsoft.PowerShell.Security`. A control test proves this pollution breaks `Get-Acl` when
  it is passed on. The tests cover: the private directory is recognized; the credential file
  is written once, private, never printed, and removable; an inherited directory is rejected;
  five broad or unrelated principals are rejected; a deny stays a deny; a file that inherits a
  broad grant is removed before any secret is written.
* **Fault injection.** Only `subprocess.run` is replaced, in addition to the real tests:
  cannot start, timeout, nonzero exit with valid stdout, non-JSON, BOM, scalar or array
  payload, missing or extra keys, wrong types, non-SID values; plus refusal of the location
  and removal of the new file.
* **Contract.** `_TRUSTED_SIDS` and CREATOR OWNER are unchanged; the query still uses
  Get-Acl by SID with Stop.

The two original tests in `test_task238_manage_user_accounts.py` are unchanged: not skipped,
not softened, and still on the real filesystem.

| Run (CI-like env: no host secrets, fresh in-memory `SECRET_KEY`, `PYTHONUTF8=1`) | Base `bda97e8` | Fix `5e3d2c3` |
|---|---|---|
| Full Backend suite, checkout-like clone, runner `PSModulePath` simulation | **2 failed**, 1664 passed, 174 skipped | **0 failed**, 1682 passed, 174 skipped |
| Full Backend suite, checkout-like clone, Windows PowerShell module path | 1666 passed, 174 skipped | 1682 passed, 174 skipped |
| Full Backend suite, task worktree | – | 1681 passed, 175 skipped (release-manifest test skips until published) |
| TASK_238 PostgreSQL end to end, disposable PG16 cluster, runner simulation | 10 failed, 4 passed | 14 passed (and 14 passed without the simulation) |
| Guards job steps / Ops job steps (runner simulation) | – | 6 + 137 passed / syntax OK, 118 passed |
| Mutation harness (16 deliberate weakenings) | – | controls pass, **16/16 detected** |

All 174 skips are pre-existing and identical in every run. Their single reason is
`AGROSAT_TEST_DATABASE_URL is not set` (the PostgreSQL suites run in `postgres.yml`). No skip
policy changed. Full matrix: evidence `tests/TEST_MATRIX.md`.

## 5. Remote CI

`REMOTE_CI_QUALIFIED=NO`. A later, explicitly authorized push must show, on its run id:

* Guards, Backend and Ops = success;
* the two original tests and the 16 new tests passing on the runner;
* PostgreSQL contracts = success.

Frontend `npm audit` will still fail and belongs to a separate task. See evidence
`audit/REMOTE_CI_STATUS.md`.

## 6. Rollback, staging check, side effects

* **Rollback.** Nothing is deployed or published. Locally: `git revert 5e3d2c3` (the report
  commit is documentation only).
* **What to verify after a release.** Run the CLI dry run from a PowerShell 7 console and
  from cmd, with `--credential-file` in a private directory: both must accept the location.
  Then point it at a directory with inherited permissions, or with a BUILTIN\Users grant:
  both must be refused with exit 20.
* **Side effects.**
  * The ACL error text gains a `(stage: …)` suffix. The sentence prefix is unchanged; no code
    or test matches the old full text.
  * `powershell.exe` no longer receives `PSModulePath`. It only reads the ACL and needs no
    custom module.
  * No database, scheduler, API, frontend or release-tooling impact.
  * The tool reaches production only through a later release of a commit that contains it
    (the TASK_230 control plane).

## 7. Evidence

`C:\AgroSat_backups\TASK_258_WINDOWS_ACL_CI_20261013_171042`:

* audits: `audit/ROOT_CAUSE.md`, `audit/SECURITY_INVARIANTS.md`,
  `audit/PRODUCTION_NON_MUTATION.md`, `audit/REMOTE_CI_STATUS.md`,
  `audit/REDACTION_MANIFEST.json`;
* tests: `tests/TEST_MATRIX.md`, mutation results;
* git: `git/DIFF.patch`, `git/STATUS.txt`, `git/COMMITS.txt`, `git/CHANGED_FILES.txt`;
* sanitized logs, probe JSON and scripts;
* `SECRET_SCAN.json` and `SHA256SUMS.txt` (self-checked), plus a ZIP next to the directory.
