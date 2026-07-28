# AgroSat Product Context

## Register

product

## Users

AgroSat serves four operational roles within an agricultural enterprise:

- administrators who manage the platform and review organization-wide state;
- managers who need accountable enterprise and field-level operational control;
- agronomists who inspect fields, record evidence, coordinate corrective actions, and verify outcomes, including on mobile devices;
- viewers who need safe read-only access to current operational and satellite context.

Users work with field, enterprise, satellite, inspection, alert, and reporting data. Their primary job is to decide what requires attention, assign or perform the next action, and verify that the issue was resolved.

## Product Purpose

AgroSat is an industrial AgTech/GIS platform for Bukhoro Agrocluster in Uzbekistan. It closes the operational agronomy loop from satellite observation through field attention, contextual interpretation, inspection, evidence, corrective action, accountable ownership and deadline, closure, later-observation verification, and management control.

Success means that satellite and field evidence lead to traceable, tenant-safe operational decisions. Maps, indices, charts, queues, and reports must support action and accountability rather than display data for its own sake.

## Brand Personality

Operational, trustworthy, field-centered.

The interface should feel precise and calm under management review while remaining efficient for agronomists working through mobile field workflows.

## Anti-references

- Generic SaaS or bootstrap-admin styling without agricultural or spatial context.
- Decorative dashboards, ornamental charts, and metrics that do not answer an operational question.
- Farming cartoons or illustrations that dominate the working interface.
- A secondary or miniature map on map-centered workflows.
- Presentation-style whitespace that reduces useful information density.
- Risk, quality, or status communicated by color alone.
- Interfaces that imply unsupported backend data, agronomic causality, or external integration success.

## Design Principles

1. Make the next accountable action clear: every screen should help the user identify attention, ownership, deadline, or verification state.
2. Keep the field and its spatial context central: operational journeys start from or return to a concrete field or anomaly zone.
3. Show management truth before visual decoration: totals reconcile with underlying records, stale or low-confidence data remain visible, and empty states are explicit.
4. Preserve role focus: managers see enterprise accountability, agronomists see personal field work, and viewers remain read-only without misleading controls.
5. Design for interruption and recovery: mobile, offline, loading, error, conflict, and insufficient-data states must be understandable and safely recoverable.

## Accessibility & Inclusion

Target WCAG AA contrast and interaction quality across desktop, tablet, and mobile workflows. All interactive controls require accessible names, keyboard access, visible focus, and appropriate native semantics. Dialogs, comboboxes, listboxes, live regions, expanded states, and current navigation must expose correct accessibility semantics. The UI must remain usable at 200% zoom, avoid unintended horizontal overflow, provide adequate touch targets, avoid color-only meaning, and respect reduced-motion preferences.
