"""AgroSat production operations control plane (TASK_230).

Release, rollback, migration planning, health qualification, backup,
retention, secondary copy and restore rehearsal for the AgroSat Windows host.

The package uses only the Python standard library. Everything that touches the
host (Scheduled Tasks, processes, HTTP, PostgreSQL client tools, signing) goes
through ``platform.Platform`` so that every gate is testable offline and the
same code runs the isolated rehearsal and, when explicitly authorized,
production.
"""

CONTROL_PLANE_VERSION = 1
