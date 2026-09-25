# AgroSat satellite collector Windows task

These files are review artifacts. No Scheduled Task is created by checkout,
build, import, application startup, or script preview.

The task runs the canonical standalone collector through
`Invoke-Collector.ps1`. The checked-in example uses a gMSA placeholder,
requires the Windows `West Asia Standard Time` timezone, uses the `IgnoreNew`
single-instance policy, and bounds batch size, lookback, retries, field
timeout, and total cycle timeout.

## Review and apply order

1. Copy `collector-task.example.json` outside the repository.
2. Replace the identity and all paths. Keep credentials only in the ACL-
   protected runtime environment file; never put them in this JSON.
3. Run `Install-CollectorTask.ps1 -ConfigurationPath <absolute-path>` without
   `-Apply` and review its JSON preview.
4. Run `Test-CollectorTask.ps1 -ConfigurationPath <absolute-path>`. This invokes
   only collector `dry-run`: no provider call and no database write.
5. After human approval, run the install script with `-Apply`.
6. Use `Inspect-CollectorTask.ps1` to inspect last-run status and exit code.

`Disable-CollectorTask.ps1` and `Uninstall-CollectorTask.ps1` are previews
unless `-Apply` is present. All mutating scripts also use PowerShell
`ShouldProcess`.

## Production schedule contracts (TASK_230)

The canonical production Sentinel task `\AgroSat_PROGRAM_R3_SentinelCycle`
runs exactly once a day at 06:00:00 local time: no 18:00 run. Its
configuration must carry exactly `["06:00:00"]`, SYSTEM (`S-1-5-18`),
IgnoreNew, StartWhenAvailable and the current limits (6 h, 3 restarts every
15 minutes); zero triggers, two triggers or any other time fails closed in
the validator and again in the installer. `Inspect-CollectorTask.ps1` reports
any drift of the registered task and exits 2.

The canonical notification task `\AgroSat_PROGRAM_R3_OperationalNotifications`
stays every 15 minutes, IgnoreNew, SYSTEM, 10-minute limit, 3 restarts every 5
minutes. A release or rebind changes only a task's action; changing any
schedule value is an explicit edit of the contract constants.

Explicit `\AgroSat_TASKnnn_*` rehearsal identities keep the bounded generic
validation (one or two triggers); every other name is refused.

## Process ownership (TASK_230 Part C)

Measured with real temporary tasks: stopping these runners ends the whole
worker tree (runner, collector or reconciler Python, provider child Python)
within a second, because every worker process shares the task's console and
Task Scheduler's stop tears that console down. A child started with its own
console would survive; the canonical workers never create one. The runners
therefore need no Job Object and are unchanged.

Do not apply these scripts until the program branch, isolated database,
credentials, execution identity, ACLs, timezone, and log retention have been
reviewed.
