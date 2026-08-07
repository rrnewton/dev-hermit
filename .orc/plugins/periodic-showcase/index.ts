// periodic-showcase — the recurring owner-facing `Periodic showcase` workflow.
//
// ---------------------------------------------------------------------------
// WHY THIS IS A SEPARATE PLUGIN, AND WHY IT IS THIS THIN.
//
// It replaces a workflow that existed only as a RUNTIME registration -- nothing
// in version control declared it -- and that crashed because it sent its wake
// to a hardcoded agent, `hermit-codex-probe`, which was dead. Two lessons are
// designed in here:
//
//   1. NO HARDCODED AGENT, EVER. `orc.sendWakeup([], ...)` with an empty target
//      list is the coordinator-wake form; it cannot be aimed at a corpse. This
//      file contains no agent name at all, and a test asserts that.
//   2. THE POLICY IS NOT IN HERE. Selecting what to showcase, and remembering
//      what was already shown, live in scripts/periodic_showcase.py, which has
//      real unit tests. A workflow body can only be exercised by running the
//      fleet; a script can be bracketed offline, with no coordinator and no
//      message sent to anyone. That split is what makes this testable inertly.
//
// It is deliberately NOT merged into .orc/plugins/hermit-dev/index.ts: that file
// was under another agent's active edit when this was written, and two agents
// mutating one file is a hard invariant violation here.
//
// POST-1.0 ORC CONTRACT (copied in force from hermit-dev/index.ts, because
// violating it is what silently killed both heartbeats there):
//   * The engine evaluates this module TWICE -- once at plugin load with full
//     capability, and again on every workflow restart under the reduced
//     `workflow` preset, where registration effects DO NOT EXIST.
//   * Calling an absent orc.* name does not return undefined; it fails dispatch
//     with "... is not a function", which crashes the restarting workflow, which
//     restarts, which re-evaluates this module, which crashes -- until the engine
//     gives up and the workflow is silently dead.
//   * Therefore: every side effect lives in registerPluginSurface(), reached
//     only through the narrow guard at the bottom, and the exported heartbeat is
//     defined at module top level with no registration of its own so a restart
//     can resolve it without touching a registration effect.
// ---------------------------------------------------------------------------

const SHOWCASE_PLUGIN_NAME = "periodic-showcase";
const SHOWCASE_WORKSPACE_SUBPATH = "work/dev-hermit";

// Null whenever this plugin is loaded by `import` from .orc/config.js rather
// than being registered as a plugin -- which is exactly how dev-hermit loads it.
// Everything downstream must survive null.
const SHOWCASE_PLUGIN_DIR = orc.pluginDir();
const SHOWCASE_WORKSPACE_ROOT = SHOWCASE_PLUGIN_DIR
  ? SHOWCASE_PLUGIN_DIR + "/../../.."
  : "$HOME/" + SHOWCASE_WORKSPACE_SUBPATH;

const SHOWCASE_SCRIPT_NAME = "hermitPeriodicShowcaseSelect";
const SHOWCASE_SELECT_COMMAND = 'cd "' + SHOWCASE_WORKSPACE_ROOT + '" && ' +
  "scripts/periodic_showcase.py select";
const SHOWCASE_WORKFLOW_NAME = "hermit-dev-periodic-showcase-v1";

// Hourly. The scorecards only change when the compat envelope runs, so polling
// faster buys nothing and costs a subprocess.
const SHOWCASE_INTERVAL_MS = 60 * 60 * 1000;

// Remembers which selection was last woken on, so a showcase that is due but
// not yet performed wakes the coordinator ONCE rather than every hour. This is
// distinct from the script's own durable "already showcased" state: this key is
// about not repeating a WAKE, that file is about not repeating a SHOWCASE.
const SHOWCASE_WAKE_CACHE_KEY = "periodic-showcase.last-wake-signature";

// Exit codes from scripts/periodic_showcase.py. Kept as named constants because
// the whole point of the repair is that "nothing newly works" is a first-class
// outcome and must never be rendered as a showcase.
const SHOWCASE_DUE = 0;
const SHOWCASE_NOTHING_NEW = 1;
const SHOWCASE_NO_HISTORY = 2;

const SHOWCASE_ABSENT_NAME_SIGNATURE = "is not a function";

// The owner-facing title. `Periodic showcase`, not `demo-presentation`.
const SHOWCASE_TITLE = "Periodic showcase";

interface ScriptResult {
  exitCode: number;
  stdout?: string;
  stderr?: string;
}

// First line of the selection is its headline; the rest is the instruction. The
// signature is what distinguishes one selection from another for wake dedupe.
export function showcaseWakeSignature(report: string): string {
  return report
    .split("\n")
    .filter((line: string) =>
      line.startsWith("  cell ") || line.startsWith("  at ") ||
      line.startsWith("  outcome ")
    )
    .join("\n");
}

export async function periodicShowcaseHeartbeat(wf: WfContext): Promise<void> {
  await wf.loop(async () => {
    const result = await orc.scripts.hermitPeriodicShowcaseSelect() as ScriptResult;
    const exitCode = Number(result.exitCode);
    const stdout = String(result.stdout || "").trim();
    const stderr = String(result.stderr || "").trim();

    if (exitCode === SHOWCASE_DUE) {
      const signature = showcaseWakeSignature(stdout);
      if (orc.kvGet(SHOWCASE_WAKE_CACHE_KEY) !== signature) {
        // Empty target list: wake the coordinator / whoever is ready. Naming an
        // agent here is the bug this workflow was rewritten to remove.
        await orc.sendWakeup([], SHOWCASE_TITLE, stdout);
        orc.kvSet(SHOWCASE_WAKE_CACHE_KEY, signature);
      }
    } else if (exitCode === SHOWCASE_NOTHING_NEW) {
      // Nothing newly works. This is a SUCCESS, not a gap to paper over: emit no
      // wake and fabricate no demo. Clearing the cache lets the next genuine
      // delta wake again even if it happens to look like an earlier one.
      orc.kvSet(SHOWCASE_WAKE_CACHE_KEY, "");
    } else if (exitCode === SHOWCASE_NO_HISTORY) {
      // Cannot decide -- no usable scorecard history. Deliberately not a wake:
      // it is indistinguishable to the owner from "nothing new", and waking on
      // it would train them to ignore the showcase.
      orc.log(
        "warn",
        SHOWCASE_PLUGIN_NAME + ": no usable compat-scorecard history; " +
          "no showcase decision was possible this cycle.",
      );
    } else {
      // A broken selector is a broken watcher: hard-warn, same as the other
      // heartbeats in this workspace do.
      await orc.sendWakeup(
        [],
        "HARD WARNING: periodic-showcase selector failed",
        ([stdout, stderr].filter(Boolean).join("\n") ||
          "periodic_showcase.py returned no diagnostic output") +
          "\nRepair scripts/periodic_showcase.py; the showcase is not running.",
      );
    }

    await wf.sleep(SHOWCASE_INTERVAL_MS);
  });
}

// --- registration ----------------------------------------------------------
// Everything with a side effect is in here, reached only through the guard.

function registerPluginSurface(): void {
  // ORDER IS LOAD-BEARING, for the same reason it is in hermit-dev:
  // orc.registerScript is the effect the reduced workflow-restart context does
  // not publish, so putting it first makes the guard below abort this function
  // immediately on a restart instead of part-way through.
  orc.registerScript(SHOWCASE_SCRIPT_NAME, {
    script: SHOWCASE_SELECT_COMMAND,
    description:
      "Decide whether a Periodic showcase is due from newly-green compat cells",
    timeoutSec: 120,
  });

  orc.workflow(
    periodicShowcaseHeartbeat,
    "Wake the coordinator when a compat cell newly works, with the showcase " +
      "evidence contract; stay silent when nothing newly works",
    {
      name: SHOWCASE_WORKFLOW_NAME,
      restartable: {
        maxRestarts: 100,
        backoffMs: 5_000,
        maxBackoffMs: 120_000,
      },
    },
  );
}

// THIS PLUGIN NEVER THROWS OUT OF MODULE SCOPE -- a deliberate difference from
// hermit-dev, which re-throws so a registration bug fails plugin load loudly.
// That is right for hermit-dev: it IS the coordinator's policy source. It is
// wrong here. .orc/config.js imports both, so an exception escaping this file
// would abort config evaluation and could take the POLICY plugin down to report
// a bug in a showcase. A decorative workflow must not be able to do that.
//
// Swallowing silently would instead recreate the exact failure this task
// repairs -- a workflow that is dead and says nothing -- so the catch is LOUD:
// every non-restart failure is logged at error level with the plugin name. That
// log is the only signal available at module scope; there is no coordinator to
// wake yet.
try {
  registerPluginSurface();
} catch (err) {
  const message = String((err && (err as { message?: unknown }).message) || err);
  if (message.indexOf(SHOWCASE_ABSENT_NAME_SIGNATURE) === -1) {
    orc.log(
      "error",
      SHOWCASE_PLUGIN_NAME + ": registration FAILED, the Periodic showcase " +
        "workflow is NOT running: " + message,
    );
  }
}
