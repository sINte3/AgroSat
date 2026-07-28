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

Do not apply these scripts until the program branch, isolated database,
credentials, execution identity, ACLs, timezone, and log retention have been
reviewed.
