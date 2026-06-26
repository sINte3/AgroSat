# TASK_052_FRONTEND_VITE_SECURITY_UPDATE_NO_FORCE

## Objective
Fix the frontend `npm audit` findings caused by `vite` / `esbuild` without using `npm audit fix --force` and without changing application behavior.

This task is atomic. It is a frontend dependency security update only.

## Current verified state
- Repository path: `C:\AgroSat`
- Branch: `main`
- Expected starting HEAD: `b935c95`
- `origin/main` is expected to equal local `HEAD`.
- Working tree must be clean before starting.
- TASK_051 was diagnostic only and did not change `package.json` or `package-lock.json`.

## Audit findings from TASK_051
`npm audit --json` reported:

- Total vulnerabilities: 2
- Moderate: 1
- High: 1

Affected packages:

1. `esbuild`
   - Severity: moderate
   - Installed via `vite@5.4.21`
   - Vulnerable range: `<=0.24.2`
   - Advisory: `GHSA-67mh-4wv8-2f99`

2. `vite`
   - Severity: high
   - Direct dependency
   - Installed version: `5.4.21`
   - Vulnerable range: `<=6.4.2`
   - Advisories:
     - `GHSA-4w7w-66w2-5vf9`
     - `GHSA-v6wh-96g9-6wx3`
     - `GHSA-fx2h-pf6j-xcff`
   - `npm audit` reported `fixAvailable` as `vite@8.1.0`, `isSemVerMajor=true`.

## Hard constraints
1. Do **not** run `npm audit fix --force`.
2. Do **not** use broad, uncontrolled dependency updates.
3. Do **not** change backend files.
4. Do **not** change React application code unless absolutely required for Vite compatibility. If code changes are required, stop and report instead of modifying app code.
5. Prefer changing only:
   - `frontend/package.json`
   - `frontend/package-lock.json`
   - this task markdown file if needed
6. Do not commit.
7. Do not push.
8. Keep the repository clean except for the expected package file changes.

## Required investigation before changing files
From `C:\AgroSat\frontend`, inspect package compatibility:

```powershell
npm view vite@8.1.0 version engines peerDependencies
npm view @vitejs/plugin-react@latest version engines peerDependencies
npm view @vitejs/plugin-react versions --json
```

Determine a compatible `@vitejs/plugin-react` version for `vite@8.1.0` and the current local Node runtime.

Current runtime from TASK_051:

```text
node v24.16.0
npm 11.16.0
```

## Implementation approach
Use a targeted install. Example, only if compatibility checks support it:

```powershell
Set-Location "C:\AgroSat\frontend"
npm install -D vite@8.1.0 @vitejs/plugin-react@<compatible-version>
```

If `@vitejs/plugin-react` current version already supports `vite@8.1.0`, still verify using `npm ls vite @vitejs/plugin-react esbuild --all` after install.

## Required validation
Run all of the following from `C:\AgroSat\frontend`:

```powershell
npm ci
npm audit --json
npm ls vite @vitejs/plugin-react esbuild --all
npm run build
```

Then from `C:\AgroSat`:

```powershell
git status --short
git diff --name-status
git diff --check
git diff -- frontend/package.json frontend/package-lock.json
```

## Acceptance criteria
The task is accepted only if all are true:

1. `npm ci` passes.
2. `npm run build` passes.
3. `npm audit --json` reports:
   - `high: 0`
   - `critical: 0`
   - preferably `total: 0`; if any moderate remains, explain exactly why.
4. `npm ls vite @vitejs/plugin-react esbuild --all` shows a non-vulnerable `vite` and `esbuild` tree.
5. No application source files changed.
6. No backend files changed.
7. Expected changed files are only:
   - `frontend/package.json`
   - `frontend/package-lock.json`
8. Do not commit.

## Stop conditions
Stop and report if any of these occur:

- Vite 8 requires application code changes.
- `@vitejs/plugin-react` compatibility is unclear.
- `npm install` wants to rewrite unrelated dependencies massively.
- `npm ci` fails after changes.
- `npm run build` fails after changes.
- `npm audit` still reports high or critical vulnerabilities.
- Any file outside `frontend/package.json` and `frontend/package-lock.json` changes.

## Final response required
Report:

- exact package versions before and after
- audit metadata before and after
- `npm ls vite @vitejs/plugin-react esbuild --all` result summary
- build result
- `git diff --name-status`
- whether the task is ready for independent validation
