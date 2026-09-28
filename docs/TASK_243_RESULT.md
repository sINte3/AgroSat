# TASK_243 — MANAGED FRONTEND LAN BINDING RESULT

## Verdict
**PASS_TASK_243**

The production frontend can now listen on one operator-chosen address, managed end to end by the release control plane.
- **Default.** Without the setting it listens on `127.0.0.1:5173`, exactly as today.
- **With the setting.** It listens on exactly the configured address, for example `10.103.25.14:5173`, and on nothing else.
- **Backend.** It stays on `127.0.0.1:8000`, and the frontend keeps proxying relative `/api/` and `/health` to it.
- **Scope.** Development only. Nothing was deployed, and no production file, task, listener, firewall rule or database was changed.

## Base
| item | value |
|---|---|
| origin/main (verified after `git fetch origin`) | `077e20acdb5da9de3d62d7877bb48ad770fa0f21` |
| production at start | `C:\AgroSat_runtime\PROGRAM_R3\control\current-release.json` names the same SHA (release `R20260927-task241`). All five `\AgroSat_PROGRAM_R3_*` task actions are bound to the `077e20a` release. Listeners: `127.0.0.1:8000` (pid 2188) and `127.0.0.1:5173` (pid 9420) |
| branch | `task/243-managed-frontend-lan-bind`, created from that exact SHA |
| worktree | `C:\AgroSat_worktrees\task_243` (the dirty `C:\AgroSat` checkout was not touched) |
| code commit | `449a029` feat(ops): operator-controlled frontend bind address through the release control plane |

## Root cause
The frontend was loopback-only because the address was a constant in three layers, and none of them had a configuration path.

| layer | file | what fixed it to loopback |
|---|---|---|
| server | `ops/qualification/Serve-ProgramR1QualificationFrontend.mjs` | line 8, `const host = '127.0.0.1'`. It was both the `server.listen` address and the API upstream, and the script read only `QUALIFICATION_FRONTEND_PORT`, `QUALIFICATION_BACKEND_PORT` and `QUALIFICATION_DIST_ROOT` |
| supervisor | `ops/release/Run-AgroSatApplication.py` | the production profile accepts exactly 7 configuration keys, so any other key gives `APPLICATION_CONFIGURATION_KEYSET_REJECTED`. It passed node only the three `QUALIFICATION_*` values, and its readiness probe and port-free check connect to `127.0.0.1` |
| control plane | `ops/release/controlplane/{profiles,health}.py` | "Production is not configurable". `health.http_get` refused every non-loopback target, and `probe_frontend` always probed `http://127.0.0.1:<port>` |

The serving model is otherwise unchanged. The Scheduled Task `\AgroSat_PROGRAM_R3_Stabilization_Frontend` (SYSTEM, BootTrigger) runs the release venv's `python.exe -B Run-AgroSatApplication.py --configuration <runtime>\application\application-release.json --component frontend`. That supervisor puts itself in a kill-on-close Job Object and starts `node.exe` with the release server script, which serves `frontend/dist` and proxies `/api/` and `/health*`.

## Implementation
**Setting**
- **Name:** `AGROSAT_FRONTEND_BIND_ADDRESS`, one line in the runtime environment file (`AGROSAT_RUNTIME_ENV_FILE`; in production `C:\AgroSat\backend\.env`, where operator settings already live).
- **Default:** absent means `127.0.0.1`. There is no `0.0.0.0` default and no all-interfaces mode.
- **Explicit LAN value:** for example `AGROSAT_FRONTEND_BIND_ADDRESS=10.103.25.14`. The frontend then listens on exactly `10.103.25.14:5173`, and `127.0.0.1:5173` stops answering.
- **Backend:** the backend ignores the key. Pydantic settings use `extra="ignore"`, which was checked by loading the backend settings with the key present.

**Validation.** The control plane, the supervisor and the node server apply the same rule, and one corpus proves it (see Tests).
- **Accepted:** one canonical dotted-quad IPv4 literal, either loopback (`127.0.0.0/8`) or, in production only, private RFC 1918 (`10/8`, `172.16/12`, `192.168/16`). A rehearsal never leaves loopback.
- **Refused:** `0.0.0.0` and every other wildcard or public address, link-local, multicast, broadcast, CGNAT and TEST-NET ranges. Also refused: host names, IPv6, a port, a CIDR suffix, leading zeros, hexadecimal or integer forms, full-width digits, whitespace or a newline, and shell fragments (`;`, `&&`, `|`, `$()`, backticks).
- **Local check:** the supervisor proves the address belongs to this host by binding port 0, without listening.
- **No injection path:** the address never reaches a command line. The supervisor passes it to node only through the child environment (`QUALIFICATION_FRONTEND_BIND_ADDRESS`), and node gives it to `server.listen`. The Scheduled Task action is byte-for-byte the same shape as before.
- **Errors:** error texts never echo the rejected value.

**Changed files** (code commit)
| file | change |
|---|---|
| `ops/qualification/Serve-ProgramR1QualificationFrontend.mjs` | listen address from `QUALIFICATION_FRONTEND_BIND_ADDRESS`, validated (absent means loopback); API upstream fixed at `127.0.0.1`; client forwarding headers dropped |
| `ops/release/Run-AgroSatApplication.py` | optional configuration key `frontend_bind_address` (schema 2 unchanged); the policy check; the address passed to node; readiness and port-free checks on that address; `--validate-only` reports the address and proves it is local; a real start waits up to 180 s for the address (see Security) |
| `ops/release/controlplane/network.py` (new) | reading and validating the setting; reading the address of an application configuration |
| `ops/release/controlplane/controller.py` | PRECHECK, MATERIALIZE, VERIFY_FRONTEND, FINAL_HEALTH, COMMIT and automatic-rollback integration (next section) |
| `ops/release/controlplane/health.py` | `probe_frontend(..., address=)`; the probe guard changes from "loopback" to "this host" |
| `ops/release/controlplane/winproc.py`, `platform.py` | `listener_endpoints()` lists every (local address, pid) on a port; `listeners()` keeps its behaviour |
| `ops/release/Invoke-AgroSatControlPlane.py` | `preflight --operation release` shows the address a release would write (`frontend_bind`), and `BLOCKED` if the setting is invalid |
| `ops/release/README.md` | operator section "Frontend bind address (TASK_243)" |
| `ops/tests/test_task243_frontend_bind.py` (new), `ops/tests/fakehost.py`, `ops/tests/test_controlplane_contracts.py` | tests; the in-memory host models listener addresses; one assertion follows the renamed guard code |

## Control-plane integration
The control plane stays the only path. There is no second Scheduled Task, no new startup mechanism, no `netsh portproxy` and no firewall change.

| moment | behaviour |
|---|---|
| PRECHECK (release) | Reads the setting once and validates it before anything changes. An invalid value fails the release with `FRONTEND_BIND_ADDRESS_REJECTED`: no backup, no staging, no task touched (tested). It records the previous release's address from its own `application-release.json` (pre-TASK_243 releases have no key, so `127.0.0.1`) and probes the previous frontend there |
| MATERIALIZE | Writes exactly the PRECHECK value into the candidate runtime's `application-release.json` as `frontend_bind_address`, then reads it back. A resumed release keeps its materialized address, even if the file was edited in between (tested) |
| VALIDATE | Launcher `--validate-only` for both components. The frontend report carries `address`, and fails if the address is not on this host |
| task install / rebind | The frontend action is unchanged in shape (`python -B …Run-AgroSatApplication.py --configuration "<runtime>\application\application-release.json" --component frontend`). The address lives in the configuration that the action already names, so rebinding carries it and nothing else changes |
| SWITCH_FRONTEND | Unchanged. The stop proves there is no listener on 5173 on any address before the new start, and the start proves the listener belongs to the task's process lineage |
| VERIFY_FRONTEND | Probes `http://<address>:5173/` (exact `index.html`) and `/health/live` through the proxy. Then it proves every listener on 5173 is on exactly that address, with no wildcard and no extra socket (`LISTENER_ADDRESS_MISMATCH` otherwise, which triggers the automatic rollback; tested with a launcher that bound `0.0.0.0`) |
| FINAL_HEALTH | The same probe and address proof, plus: every listener on 8000 is on `127.0.0.1`, and every listener owner is in the task lineage |
| COMMIT | `current-release.json` records `frontend_bind_address` |
| automatic rollback | Restores the recorded previous actions, probes the previous frontend on its own recorded address, and proves the previous backend is on `127.0.0.1` and the previous frontend on its address (`previous_listeners`). Otherwise `MANUAL_RECOVERY_REQUIRED` |
| explicit rollback | The target's address is read from the target release's own configuration (`facts.target.frontend_bind_address`), and the setting in the environment file is not consulted. Rolling back to `077e20a` or any pre-TASK_243 release returns the frontend to `127.0.0.1:5173` (tested) |
| `preflight` | Shows `frontend_bind: {address, source}` for a release, read-only |

**Enable, change, disable** (for TASK_244 and later)
- **Enable:** append the line to the runtime environment file, then run authorize → release. The value takes effect only through a release, never by editing the file and restarting a task.
- **Change:** edit the value, then run a release.
- **Disable:** run a control-plane rollback to the previous release, or release a later commit without the line.
- **Re-release:** re-releasing the same SHA after a rollback needs its runtime directory moved aside first (existing control-plane rule).

## Backend isolation
- **Supervisor.** `child_command("backend")` is unchanged: `uvicorn main:app --host 127.0.0.1 --port 8000 --no-access-log`. `Settings.address("backend")` is always `127.0.0.1`, and the backend child environment never carries the frontend address (tested with `10.103.25.14` configured).
- **Release gate.** FINAL_HEALTH now proves on every release and rollback that port 8000 has listeners only on `127.0.0.1`. Evidence: `listener_addresses.backend`.
- **Browser and CORS.** No browser JavaScript changed. `frontend/` and `backend/` are identical to `077e20a`. The bundle keeps calling relative `/api/` (`frontend/src/api/client.js`), and backend CORS is unchanged (a fixed localhost list, no wildcard).

## Proxy/header handling
- **Finding.** The backend application code never reads the client address or forwarding headers: no `request.client`, `X-Forwarded-*`, `X-Real-IP` or `Forwarded`, and no IP-based limiter. **uvicorn 0.49 does trust them.**
  - `proxy_headers=True` and `forwarded_allow_ips="127.0.0.1"` are the defaults, and the supervisor passes no `FORWARDED_ALLOW_IPS`.
  - Every request arrives from the proxy on `127.0.0.1`, so a LAN client's `X-Forwarded-For` / `X-Forwarded-Proto` would have become uvicorn's `scope["client"]` and `scope["scheme"]`.
- **Change (minimal, in the proxy only).** The proxy drops `Forwarded`, every `X-Forwarded-*`, `X-Real-IP`, `X-Client-IP`, `True-Client-IP`, `CF-Connecting-IP`, `X-Cluster-Client-IP` and `Fastly-Client-IP`.
  - It adds none of its own. The backend therefore always sees the proxy, `127.0.0.1`, which is the truth from its side.
  - No client-IP authorization was invented, and the backend code was not changed.
- **Unchanged.** Hop-by-hop headers are still dropped, `Host` is still rewritten to `127.0.0.1:<backend port>`, and `Authorization` and every other header pass as before (tested).

## Security
- **Static serving.** Unchanged code. The containment corpus ran against the explicitly bound address: TASK_242's 21 probes plus encoded-slash, backslash, NUL, malformed-escape and scheme-relative variants (28 in total).
  - Every probe returned the release `index.html` or a 4xx. None returned a file outside `frontend/dist` (fixture `.env`, `backend/.env`, `.git/config`, `package.json`, the manifest, the server script), and none returned a listing.
  - The SPA fallback, `/assets/*` immutable caching, `index.html` `no-store` and the 405 for non-GET static requests are unchanged.
- **Control-plane probes.** They never leave the host. `health.http_get` accepts loopback or an IPv4 literal that this host can bind (`target_not_local` otherwise, previously `target_not_loopback`). `0.0.0.0`, multicast, foreign and TEST-NET targets are refused.
- **Boot robustness.** The frontend task has a BootTrigger with no delay, and on this host Task Scheduler does not restart a task that exited non-zero (measured in TASK_230). A LAN address that comes up after the task starts would therefore have left the frontend down after a reboot.
  - A real start now waits up to 180 s for the address, then fails closed (`APPLICATION_FRONTEND_BIND_ADDRESS_NOT_LOCAL`).
  - `--validate-only` does not wait.
  - Loopback is always present, so the default path never waits.
- **Evidence.** Values are addresses and pids only. The control plane's credential scanner accepts every new evidence field.

## Tests
**Focused suite** `ops/tests/test_task243_frontend_bind.py`: **146 passed, 0 skipped, 0 failed**.

Real sockets bind only `127.0.0.1` and `127.0.0.2`. Windows routes the whole `127/8` to loopback, so `127.0.0.2` proves "exactly the configured, non-default address" without any LAN socket. `10.103.25.14` is exercised wherever nothing binds it.

| group | tests | what it proves |
|---|---|---|
| one policy, three implementations | 51 + 47 | The control plane and the supervisor accept or refuse the same 51 values, per profile. The real node server refuses the same 39 malformed/wildcard/injection-like strings before listening, and accepts the 8 loopback/LAN ones (it then stops at the missing bundle, so nothing binds) |
| supervisor composition | 15 | **Default:** frontend `127.0.0.1:5173`. **Explicit `10.103.25.14`:** frontend env `10.103.25.14`, port 5173, and the address in no argv. **Backend:** still `--host 127.0.0.1 --port 8000`, and its env never carries the address. **Refused:** a rehearsal LAN address, unknown keys, malformed/wildcard/public values, and non-local addresses. **Boot wait:** it waits for an appearing address and gives up at exactly 180 s |
| operator setting | 12 | read from the runtime env file (quoted, commented, last value wins); invalid values refused and never echoed |
| control plane, in-memory host | 10 | **Release:** materialize, verify and commit the exact address; with no setting the frontend stays on loopback. **Invalid setting:** stops at PRECHECK with zero side effects (4 cases). **Wildcard listener:** refused, then automatic rollback. **Explicit rollback:** returns the previous address. **Previous release:** probed and restored on its own non-default address. **Resume:** keeps the validated address |
| preflight CLI | 3 | reports the address and source; `BLOCKED` for `0.0.0.0` |
| probe guard, listener table | 3 | probes stay on this host; the real Windows TCP table reports `(address, pid)` for every socket |
| real node server | 4 | **Default:** exactly one listener on `127.0.0.1`. **Explicit `127.0.0.2`:** exactly one listener on it, with `127.0.0.1:<port>` refused. **Serving:** bundle, SPA fallback, assets, 405, and the control plane's own `probe_frontend` over real sockets. **Proxy:** reaches only the loopback backend (peer `127.0.0.1`, Host rewritten), path, query and body intact, 11 spoofed forwarding headers dropped, `Authorization` kept. **Containment:** 28 probes |
| real supervisor + node + Job Object (Windows) | 1 | `--validate-only` reports the address. **Twice**, start, prove and stop: exactly one socket on `127.0.0.2:<port>`, owned by `node.exe` below the supervisor; node in a Job Object; node's command line equals `list2cmdline([node, server])` with no address; nothing on `127.0.0.1:<port>`; lifecycle events carry the address. After `TerminateProcess` of the supervisor (what Task Scheduler does), the whole tree is gone and the port has no listener, so there is no orphan node and no extra listener |

**Regression** on the final tree, local, with the CI commands:
| lane | command | result |
|---|---|---|
| ops (CI `ops`) | `python -m pytest ops/tests -q` | **263 passed** (117 pre-existing + 146 new), 0 failed |
| backend (CI `backend`) | `python -m pytest tests -q` in `backend` | **1497 passed, 170 skipped, 0 failed**, identical totals to TASK_240's final run (the 170 skips are the PostgreSQL suites, as in CI without a database) |
| supervisor and startup guards | `tests/test_task220_application_supervision.py`, `tests/test_web_startup_safety.py` | 21 passed |
| PowerShell parse (CI `ops`) | the `Parser]::ParseFile` loop over `ops/**/*.ps1` | 24 scripts, 0 errors (no `.ps1` changed) |
| Alembic (CI `guards`) | `Invoke-AgroSatControlPlane.py rollback-contract --backend backend`; `alembic heads` | PASS, head `0016_operational_command_center`, 17/17 classified; one head |
| node | `node --check` on the server script (node v24.16.0) | OK |
| frontend | — | not run: no file under `frontend/` changed (`git diff 077e20a -- frontend backend` is empty). At release time the control plane copies `077e20a`'s `dist` |

The baseline before any change was ops 117 passed. The existing controller tests (all 117) pass unchanged with the address-aware host; one contract assertion follows the renamed probe-guard code.

## Alembic
- Starting head: `0016_operational_command_center` (single head).
- Ending head: `0016_operational_command_center` (single head).
- Migration added: **NO**. There is no schema, model or runtime DDL change.

## Production
**NOT DEPLOYED**
**NOT CHANGED**

- **Contact.** The only contact with production was read-only:
  - the release pointer and task definitions;
  - the listener table;
  - the frontend task's trigger XML;
  - the current runtime's `application-release.json`, which read as `127.0.0.1` through the new PRECHECK logic, as intended.
- **Not touched.** No task, listener, pointer, runtime file, database or firewall setting was changed. The runtime environment file was not read or edited, and nothing was restarted.
- **After the work** (read-only, 2026-09-28, after the code commit):
  - **Pointer.** `current-release.json` still names `077e20acdb5da9de3d62d7877bb48ad770fa0f21`, release `R20260927-task241`, committed `2026-09-28T02:30:23Z`, with no `frontend_bind_address` key.
  - **Tasks.** All five `\AgroSat_PROGRAM_R3_*` tasks are still bound to `077e20a`.
  - **No restart.** Backend and frontend show last run 07:30:30, the TASK_241 release time.
  - **Listeners.** Still `127.0.0.1:8000` pid 2188 and `127.0.0.1:5173` pid 9420, the same processes as at the start.
  - **Nothing added.** The `077e20a` runtime configuration still has its 7 keys. There is no release or runtime directory for `449a029`, and `netsh interface portproxy` is empty.
  - **origin/main.** It is unchanged at `077e20acdb5da9de3d62d7877bb48ad770fa0f21` after `git fetch origin`.

## Follow-up release
**TASK_244 must deploy this change before TASK_242 LAN qualification can resume.** Until then production keeps listening on `127.0.0.1:5173` only.

Recommended TASK_244 order:
1. **Publish the branch.** Push `task/243-managed-frontend-lan-bind`, because the control plane refuses a candidate that is not on `origin/*`.
2. **Settle B242-2 first.** Windows Firewall on this server is disabled by domain GPO. Once the address is set and released, `10.103.25.14:5173` is reachable from the whole routed network, not only from `10.103.53.128/32`, until IT enforces the boundary (GPO or network ACL).
3. **Check the loopback consumers.** With the setting active, `http://127.0.0.1:5173` stops answering. Check the `cloudflared` ingress, which TASK_242 did not read, and any operator habit or script that uses the loopback URL: TASK_234 and TASK_241 smokes, and TASK_242 probes.
4. **Set the value.** Append `AGROSAT_FRONTEND_BIND_ADDRESS=10.103.25.14` to `C:\AgroSat\backend\.env`, append-only.
5. **Preflight.** `preflight --mode production --operation release …` must show `frontend_bind: {"address": "10.103.25.14", "source": "runtime_env_file"}`.
6. **Release.** Authorize, then release the branch head from `077e20a`. No migration is needed (noop at 0016), and venv and `dist` are copied because requirements and `frontend/` are unchanged.
   - Expect VERIFY_FRONTEND and FINAL_HEALTH evidence `observed: ["10.103.25.14"]` for 5173 and `["127.0.0.1"]` for 8000.
7. **Resume TASK_242.** Add the scoped rule, then the human check from `bak-tex11` at `http://10.103.25.14:5173`.

Rollback for TASK_244 is the control-plane rollback to `077e20a`, which returns the frontend to `127.0.0.1:5173`. Rolling back this development change means reverting its commits; there is no data or schema dependency.

## Side effects
- **New configuration key.** Every new release's `application-release.json` carries `frontend_bind_address`, also when it is the default `127.0.0.1`. `current-release.json` gains the same key.
  - The supervisor accepts the key as optional; the control plane reads fields by name.
  - Older supervisors never read a new release's configuration, because each release runs its own supervisor copy.
- **Renamed refusal code.** `health.http_get` now refuses with `target_not_local` instead of `target_not_loopback`. Only one contract test asserted the old text, and external probe scripts only display it.
- **Forwarding headers dropped for all traffic.** This includes any loopback consumer. uvicorn's `request.client` is therefore always `127.0.0.1` and the scheme `http`, and no backend code reads either.
- **Lifecycle events.** They now record `address`; the `started` event also records `address_wait_seconds`.
- **Loopback URL.** With the setting active, `http://127.0.0.1:5173` no longer answers (see Follow-up, step 3).

## Residual gaps
1. **Not deployed.** Production is loopback-only until TASK_244.
2. **Network boundary (B242-2) is open.** The setting opens no firewall rule. With the firewall disabled by GPO, enabling the address exposes 5173 to every routed host until IT acts.
3. **No real LAN socket in tests (by design).** The first bind on `10.103.25.14` happens in TASK_244. VALIDATE proves the address is local before any switch. VERIFY_FRONTEND and FINAL_HEALTH prove the exact listener, and any mismatch rolls back automatically.
4. **No real Task Scheduler rehearsal.** The TASK_230 Task-Scheduler rehearsal harness was not run here. The controller was exercised on the in-memory host, and the supervisor, node and Job Object on real processes. A TASK_244 pre-release rehearsal on spare ports with `127.0.0.2` would cover the gap.
5. **The loopback URL disappears when the setting is active**, which is the specified "exact address" behaviour. Local tools must use `http://10.103.25.14:5173` (see Follow-up, step 3).
6. **Pre-existing behaviour, not changed.**
   - The node server does not check the `Host` header. DNS rebinding could read unauthenticated responses, but not the per-origin session token.
   - A backend slash-redirect would carry `http://127.0.0.1:8000` in `Location`, but the bundle does not rely on redirects.
7. **Changing the address needs a release.** That is deliberate, because it keeps LAN exposure behind authorization, evidence and rollback. There is no in-place "rebind frontend" command.
8. **Boot wait not exercised on a real reboot.** It is unit-tested with a simulated clock.
9. **GitHub CI not observed.** The branch is not yet on `origin`. The evidence is the local run of the same lanes.
