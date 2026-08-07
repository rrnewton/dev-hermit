// Project-scoped ORC configuration. Substantive logic lives in the plugins.
import "./plugins/hermit-dev/index.ts";
// The recurring owner-facing `Periodic showcase`. Kept as its own plugin rather
// than folded into hermit-dev so the two can be edited independently.
import "./plugins/periodic-showcase/index.ts";
