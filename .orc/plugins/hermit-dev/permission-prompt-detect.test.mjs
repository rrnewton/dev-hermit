#!/usr/bin/env node
// Both-direction fixtures for the permission-prompt detector.
//
// The negative fixtures are REAL pane text captured from the live fleet, not
// invented: the whole defect was that invented reasoning about what a pane
// contains missed the persistent footer. Where a fixture is constructed rather
// than captured it says so on the line above it.
//
// Run: node .orc/plugins/hermit-dev/permission-prompt-detect.test.mjs
// Exits 0 on pass, 1 on failure, and prints counts on BOTH sides.

import {
  decide,
  stripPaneFooter,
  hasPositivePromptEvidence,
  looksWorking,
  MAX_SENDS_PER_AGENT,
} from "./permission-prompt-detect.mjs";

let pass = 0;
let fail = 0;
const failures = [];

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected);
  if (ok) pass++;
  else {
    fail++;
    failures.push(`${name}\n    expected ${JSON.stringify(expected)}\n    actual   ${JSON.stringify(actual)}`);
  }
}

// --- CAPTURED from hermit-w1 (%1), a WORKING agent, 2026-08-07 --------------
// This is the exact shape that made 19 of 19 live matches false positives: the
// only occurrence of "permission" is the persistent mode footer.
const CAPTURED_WORKING_FOOTER_ONLY = `
● Bash(cd /tmp/w1-rev-scm; export PKG_CONFIG_PATH=/home/newton/…)
  ⎿ ↑ Running… (8s · timeout 6m 40s)
     (ctrl+b ctrl+b (twice) to run in background)

· Julienning… (2m 3s · ↓ 6.0k tokens)

  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← 1 agent
`;

// --- CAPTURED from hermit-w18 (%24), 2026-08-07 -----------------------------
// A GENUINE prompt marker on an agent that is nonetheless actively working.
// Sending here interrupts live reasoning; `codex-approval-stall-sweep` got this
// right and `permission-sweep-needs-input` had no working check at all.
const CAPTURED_PROMPT_BUT_WORKING = `
• Reviewing 2 approval requests (44s • esc to interrupt) · 1 background terminal running · /ps to view · /stop to close
  └ • /bin/bash -lc "tg search '1832 sentinel'"
    • /bin/bash -lc 'with-proxy gh pr view 1832 --repo rrnewton/hermit --json number,title'
`;

// --- CAPTURED footer, idle agent (the second live variant, 2 of 16) ---------
const CAPTURED_IDLE_FOOTER_ONLY = `
Standing by, nothing pending.

  ⏵⏵ bypass permissions on
`;

// --- CAPTURED-IN-SPIRIT: an agent DISCUSSING permissions -------------------
// Transcribed from this very session, in which the agent spent many turns
// writing about approval sweeps. Ordinary prose containing the trigger words is
// the second false-positive class after the footer.
const AGENT_DISCUSSING_PERMISSIONS = `
The sweep sends an approval to any agent matching a not-working heuristic.
It cannot distinguish BLOCKED-ON-A-PROMPT from IDLE. A bare y is an unintended
authorisation. I filed permission-sweep-approves-idle-agents to track it.
1. First finding was the footer.
`;

// --- CONSTRUCTED from the documented Claude Code approval UI ----------------
// No live pane was showing an un-answered menu at capture time, so these two
// are built from the documented shape rather than captured. Labelled because
// that is the weaker provenance of the six fixtures here.
const CONSTRUCTED_NUMBERED_MENU = `
Do you want to proceed?
❯ 1. Yes
  2. Yes, and don't ask again
  3. No, and tell Claude what to do differently

  ⏵⏵ bypass permissions on (shift+tab to cycle)
`;

const CONSTRUCTED_YN_PROMPT = `
Allow this command to run? (y/n)

  ⏵⏵ bypass permissions on (shift+tab to cycle)
`;


// --- CAPTURED, and the reason this rule got tighter -------------------------
// hermit-w17's own report at 07:41Z. The still-live sweep sent it "1" because
// the report ends with a numbered list. Verified in the coordinator session:
//   acted=["hermit-w5:1","hermit-w17:1"]
// The first version of THIS file would have done the same. A numbered list in
// prose is not a menu.
const CAPTURED_PROSE_NUMBERED_LIST = `
Open items across my claimed tasks:

1. **emitters_can_still_write** [P2] — the two skewed emitters.
2. **harness_must_not_count** [P1] — the /tmp launch-refusal trap.
3. **Install the permission-sweep fix** — the detector landed at 8e8f807.

Say which and I'll start.

  ⏵⏵ bypass permissions on (shift+tab to cycle)
`;

// ---- NEGATIVE DIRECTION: none of these may ever receive a keystroke --------

check(
  "captured working agent, footer is the ONLY 'permission' -> no send",
  decide({ pane: CAPTURED_WORKING_FOOTER_ONLY, status: "idle" }),
  { send: false, reason: "working" },
);

check(
  "captured prompt marker BUT actively working -> no send (would interrupt)",
  decide({ pane: CAPTURED_PROMPT_BUT_WORKING, status: "idle" }),
  { send: false, reason: "working" },
);

check(
  "captured idle agent with footer only -> no send",
  decide({ pane: CAPTURED_IDLE_FOOTER_ONLY, status: "idle" }),
  { send: false, reason: "no-prompt-evidence" },
);

check(
  "agent merely DISCUSSING approvals -> no send",
  decide({ pane: AGENT_DISCUSSING_PERMISSIONS, status: "idle" }),
  { send: false, reason: "no-prompt-evidence" },
);


check(
  "a NUMBERED LIST in ordinary prose is not a menu (real: w17 was sent '1' for this)",
  decide({ pane: CAPTURED_PROSE_NUMBERED_LIST, status: "idle" }),
  { send: false, reason: "no-prompt-evidence" },
);

check(
  "empty pane -> no send",
  decide({ pane: "", status: "idle" }),
  { send: false, reason: "no-prompt-evidence" },
);

// The retry cap. Eight identical approvals to hermit-e9patch is the observation
// that motivated it; the cap must bite before the prompt check, so a pane that
// genuinely looks like a prompt still stops being poked.
check(
  `retry cap bites at ${MAX_SENDS_PER_AGENT} even on a real prompt`,
  decide({ pane: CONSTRUCTED_NUMBERED_MENU, status: "idle", priorSends: MAX_SENDS_PER_AGENT }),
  { send: false, reason: "retry-cap" },
);

// ---- POSITIVE DIRECTION: the detector must not be inert --------------------

check(
  "genuine numbered menu, not working -> send '1'",
  decide({ pane: CONSTRUCTED_NUMBERED_MENU, status: "idle" }),
  { send: true, key: "1", evidence: "numbered", status: "idle" },
);

check(
  "genuine (y/n) question, not working -> send 'y'",
  decide({ pane: CONSTRUCTED_YN_PROMPT, status: "idle" }),
  { send: true, key: "y", evidence: "question", status: "idle" },
);

check(
  "under the cap, a real prompt still sends",
  decide({ pane: CONSTRUCTED_YN_PROMPT, status: "idle", priorSends: MAX_SENDS_PER_AGENT - 1 }).send,
  true,
);

// ---- the footer strip itself, bracketed both ways --------------------------

check(
  "stripPaneFooter removes the mode line's 'permissions'",
  /permission/i.test(stripPaneFooter(CAPTURED_WORKING_FOOTER_ONLY)),
  false,
);

check(
  "...and the raw text DID contain it (the strip is not inert)",
  /permission/i.test(CAPTURED_WORKING_FOOTER_ONLY),
  true,
);

check(
  "stripPaneFooter does NOT eat a real question that follows the footer",
  hasPositivePromptEvidence(stripPaneFooter(CONSTRUCTED_YN_PROMPT)),
  "question",
);

// ---- the working detector, bracketed both ways -----------------------------

check("looksWorking fires on a captured working pane", looksWorking(CAPTURED_WORKING_FOOTER_ONLY), true);
check("looksWorking fires on the prompt-but-working pane", looksWorking(CAPTURED_PROMPT_BUT_WORKING), true);
check("looksWorking is silent on a genuine idle prompt", looksWorking(CONSTRUCTED_YN_PROMPT), false);

// ---- the OLD detector, reproduced, to show what changed --------------------
// This is the regression guard: if someone reverts to substring matching, the
// negative fixtures above stop being negative. Asserting the old behaviour
// explicitly makes that visible rather than implicit.
const OLD_REGEX = /approval|permission|allow|proceed\?|\(y\/n\)|Reviewing/i;
check(
  "OLD detector fired on the captured WORKING pane (this is the bug)",
  OLD_REGEX.test(CAPTURED_WORKING_FOOTER_ONLY.slice(-400)),
  true,
);
check(
  "NEW detector does not",
  decide({ pane: CAPTURED_WORKING_FOOTER_ONLY, status: "idle" }).send,
  false,
);

// ---------------------------------------------------------------------------

// --- CAPTURED LIVE 2026-08-07 from orc-hermit:4.1, a WORKING agent ----------
// THE SECOND-ORDER BUG. The footer fix stopped the detector matching the pane
// FOOTER; this pane shows it still matched the pane's own PROSE. The agent was
// writing *about* prompt handling, so its scrollback contained the literal
// forms, and the whole-text `QUESTION_FORMS.some(...)` fired -> send=true key=y
// against an agent that was working. First it matched the footer's vocabulary,
// then it matched its own.
//
// The discriminator is grammatical: a prompt AWAITS AN ANSWER so its question
// form ends the line; prose mentions the form and keeps going.
const CAPTURED_PROSE_ABOUT_PROMPTS = `
● The predicate maps each form to a key:
  - (y/n) → key=y · [y/N] → key=y
  - a numbered menu → key=1
✻ Baked for 3s
`;
check(
  "prose ABOUT (y/n) in a working pane is not a prompt (live regression)",
  decide({ pane: CAPTURED_PROSE_ABOUT_PROMPTS, status: "unknown", priorSends: 0 }),
  { send: false, reason: "no-prompt-evidence" },
);
check(
  "a question form mid-line with content after it is prose, not a prompt",
  hasPositivePromptEvidence("the (y/n) form is what we match on"),
  null,
);
// POSITIVE CONTROL for the same rule: the guard must not eat real prompts whose
// form carries trailing punctuation or a caret.
check(
  "real prompt, form ends the line",
  hasPositivePromptEvidence("Allow this command to run? (y/n)"),
  "question",
);
check(
  "real prompt, bare Allow-this-tool with trailing ?",
  hasPositivePromptEvidence("Allow this tool?"),
  "question",
);
check(
  "real prompt, trailing caret after the form",
  hasPositivePromptEvidence("Overwrite the file? [y/N] >"),
  "question",
);

const negatives = 9;
const positives = 6;
console.log(
  `permission-prompt-detect: ${pass} passed, ${fail} failed ` +
    `(${negatives} negative-direction fixtures, ${positives} positive-direction, ` +
    `plus strip/working/old-detector brackets)`,
);
if (fail) {
  for (const f of failures) console.error("  FAIL " + f);
  process.exit(1);
}
process.exit(0);
