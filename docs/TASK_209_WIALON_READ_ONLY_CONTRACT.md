# TASK 209 Wialon Read-Only Adapter Contract

## Scope

AgroSat may enrich an authorized field view with read-only telematics context. The integration does not create commands, notifications, geofences, units, sensor configuration, or other Wialon-side writes.

## Security boundary

- The browser never receives a Wialon token or calls Wialon directly.
- Credentials are resolved server-side per tenant by a credential provider.
- Tokens, session identifiers, request headers, and upstream response bodies are never logged.
- Missing credentials or mappings produce an explicit `unsupported` result, never fixture or mock data in a production path.
- A tenant-scoped user may only request a field in the same enterprise. Cross-tenant object identifiers remain non-enumerable.
- Cache keys include the enterprise and field identifiers.

## Mapping and read model

An enterprise-owned mapping links one AgroSat field to bounded Wialon unit identities and optional geofence identities. The response exposes:

- provider and mapping provenance;
- unit identity and display label;
- latest position with observation time;
- movement and ignition state;
- an allowlisted set of sensor values and units;
- requested time range;
- field/geofence intersection status;
- freshness age and an explicit stale flag.

Raw unit payloads, arbitrary sensor names, credentials, and unknown provider properties are not returned.

## Provider behavior

The provider boundary accepts an explicit tenant mapping, time range, timeout, page limit, and result limit. Calls use bounded timeouts, bounded pagination, and a per-tenant request limiter. Provider errors are classified as:

- `unsupported`;
- `authentication`;
- `authorization`;
- `quota`;
- `rate_limit`;
- `timeout`;
- `network`;
- `invalid_response`;
- `upstream`.

Only `timeout`, `network`, `rate_limit`, and selected upstream failures are retryable. A cache may serve a bounded previously validated response, but cache failure never fabricates data.

## API and UI

`GET /api/telematics/fields/{field_id}` is authenticated and field-authorized. It returns a stable read model with `status` equal to `available`, `unavailable`, `unsupported`, or `stale`.

The field UI:

- renders telematics only when the endpoint has trustworthy mapped data;
- labels provider, provenance, observation time, and staleness;
- shows an explicit unsupported/unavailable state;
- contains no write controls;
- cancels requests and rejects stale responses when field ownership changes.

## Validation status

Deterministic contract fixtures may validate mapping, pagination, error classification, tenant-safe cache keys, response filtering, and UI states. They must be labeled fixtures. Live Wialon validation remains blocked until review-approved read-only credentials and tenant mappings are supplied.
