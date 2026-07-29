# AgroSat Commercial Tenant Boundary

## Status and scope

This contract defines the commercial multi-tenant boundary for TASK_209 Phase
9G. It does not enable payment processing, production tenant deletion, secret
storage, or production deployment.

The existing `enterprises` row remains the operational tenant boundary for this
rollout. A separate billing customer hierarchy is deliberately not introduced
until a human-approved commercial contract requires it. Users may have one
active membership per enterprise; the legacy `users.enterprise_id` remains the
compatibility scope until membership cutover is separately approved.

## Invariants

- Every tenant-owned row has an explicit `enterprise_id` foreign key and an
  index whose leading column is `enterprise_id`.
- Membership roles are `owner`, `manager`, `agronomist`, or `viewer`. An active
  user may not receive more privilege from membership than the server-side
  application role permits.
- Feature flags fail closed. An absent flag is disabled.
- Quotas are explicit limits, not counters inferred in a browser.
- Retention is expressed as a bounded policy; enforcement is a standalone,
  review-only job boundary and never runs in FastAPI lifespan.
- Provider credential records contain an opaque secret reference and metadata,
  never a token, password, connection URL, or Authorization value.
- Storage, cache, background-job, and export namespaces are derived from the
  numeric enterprise ID by one shared implementation.
- Cross-tenant IDs return a non-disclosing not-found response where practical.
- Tenant export and deletion are request workflows. They require an
  idempotency key, management approval, an audit trail, and a later,
  independently authorized executor. API requests do not delete tenant data.
- Billing exposes plan and usage boundary data only. No card, payment,
  invoice-charge, refund, or payment-provider operation exists.

## Namespace contract

For enterprise `42`, the canonical namespaces are:

```text
storage: tenants/42/
cache: agrosat:tenant:42:
jobs: tenant.42.
exports: tenant-42/
```

Raw enterprise names, user identifiers, emails, or provider secrets are never
used in namespace keys.

## Request lifecycle

Export and deletion requests use:

```text
requested -> approved | rejected
approved -> executing -> completed | failed
```

Only an admin may approve or reject deletion. Managers may request an export
for their own enterprise. The API added by this phase only creates and reads
requests; execution remains a separate reviewed operations boundary.

## Credential boundary

Allowed provider metadata:

```text
provider_code
secret_reference
status
last_validated_at
last_failure_category
```

`secret_reference` is an opaque locator such as an approved vault item name.
The API returns only `configured: true|false` and validation status. It never
returns the reference itself.

## Retention and audit

Commercial configuration mutations append immutable audit events with tenant,
actor, action, target type, target ID, request correlation ID, and a sanitized
details object. Retention defaults are conservative and no purge is performed
by this phase.

## Billing boundary

The billing interface may return a stable internal plan code, quota summary,
and externally managed subscription state. It cannot collect payment details or
perform a financial transaction. An unavailable billing provider produces an
explicit `unsupported` state.

