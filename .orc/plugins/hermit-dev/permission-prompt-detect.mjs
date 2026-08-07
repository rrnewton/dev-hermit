// Decide whether an agent pane is ACTUALLY blocked on an approval prompt.
//
// ## The defect this replaces, measured on the live fleet
//
// The `permission-sweep-needs-input` workflow selected candidates with
// `status !== "busy"` and then matched the last 400 characters of the pane
// against `/approval|permission|allow|proceed\?|\(y\/n\)|Reviewing/i`.
//
// Claude Code renders a PERSISTENT FOOTER on every pane:
//
//   ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ↰ 1 agent
//
// That footer contains the literal word "permissions", forever, on every agent
// running in bypass mode. So the tail regex matched a constant, not a state.
// Measured across the live fleet at the time of writing: **28 panes inspected,
// 19 matched the old regex, and 19 of those 19 were visibly WORKING** (an
// elapsed timer and "esc to interrupt" on screen). Every single match was a
// false positive, and the only thing standing between that and a stray
// keystroke was the coarse `status !== "busy"` field.
//
// The consequence is not merely waste. A bare `y` delivered to an agent that
// happens to be sitting on a substantive question is an UNINTENDED
// AUTHORISATION, and whether it is treated as one depends on the receiving
// agent's judgement rather than on the mechanism.
//
// ## What replaces it
//
// Two changes, both necessary:
//
// 1. **Strip the persistent footer before matching.** A constant cannot be
//    evidence of a transient state. `stripPaneFooter` removes the mode line and
//    the shortcut hints so the matcher only ever sees agent output.
// 2. **Require POSITIVE evidence of a prompt** — a numbered menu, or an
//    explicit question form — rather than the absence of activity. Absence of
//    activity cannot distinguish "blocked on a prompt" from "idle with nothing
//    to do", which is precisely the pair the old sweep conflated.
//
// A third guard is carried over from `codex-approval-stall-sweep`, which had it
// right: **never send to a pane that shows it is working.** A prompt marker and
// a live elapsed timer together mean the agent is mid-reasoning, and input
// would interrupt it. This is a real captured case, not a hypothetical --
// hermit-w18 displayed `Reviewing 2 approval requests (44s • esc to interrupt)`
// while actively running two background commands.

/// Footer fragments Claude Code renders persistently. These are MODE
/// indicators, not prompts; every one of them is present whether or not the
/// agent is blocked.
const FOOTER_PATTERNS = [
  /^\s*[⏵⏸▶]+\s*bypass permissions[^\n]*$/gim,
  /^\s*[⏵⏸▶]+\s*accept edits[^\n]*$/gim,
  /^\s*[⏵⏸▶]+\s*plan mode[^\n]*$/gim,
  /\(shift\+tab to cycle\)/gi,
  /esc to interrupt/gi,
  /ctrl\+b ctrl\+b \(twice\) to run in background/gi,
  /\/ps to view/gi,
  /\/stop to close/gi,
  /shift\+tab to cycle/gi,
];

/// Evidence that the agent is RUNNING right now. If any of these is present the
/// pane is not idle, whatever else it says, and must not be sent input.
const WORKING_PATTERNS = [
  /\(\s*\d+\s*[sm]\b/i, // "(44s", "(2m 3s"
  /esc to interrupt/i,
  /↓\s*[\d.]+k?\s*tokens/i,
  /\btokens\b/i,
  /background terminal running/i,
];

/// POSITIVE prompt evidence. Two accepted forms, deliberately narrow.
///
/// A numbered menu is the Claude Code / Codex approval UI. A line starting
/// "1." is NOT sufficient: ordinary agent prose contains numbered lists, and an
/// earlier draft of this file fired on "1. First finding was the footer." in a
/// report. Its own fixture caught that, which is the point of writing the
/// negative side first.
///
/// A real menu is therefore recognised by one of two structural markers that
/// prose does not have:
///   (a) the selection caret `❯` immediately before the option, or
///   (b) at least TWO consecutively numbered options -- a "1." line AND a
///       "2." line. A prose list can contain "1." alone; an approval menu
///       always offers at least accept and decline.
const MENU_CARET = /^\s*❯\s*(?:\d[.)]|\[\d\])\s+\S/m;
const MENU_OPTION_1 = /^\s*(?:❯\s*)?(?:1[.)]|\[1\])\s+\S/m;
const MENU_OPTION_2 = /^\s*(?:❯\s*)?(?:2[.)]|\[2\])\s+\S/m;

/// A numbered list in ordinary agent prose has "1." and "2." at line start too.
/// This was not hypothetical: within an hour of landing the first version, the
/// still-live sweep sent "1" to hermit-w17 because its report ended with a
/// seven-item numbered list, and the coordinator's own ad-hoc detector used the
/// same two-option rule. So two options at line start is NOT sufficient.
///
/// A real approval menu has one of two things prose does not:
///   (a) the selection caret, or
///   (b) options on ADJACENT lines directly under a QUESTION. Claude Code
///       renders "Do you want to proceed?" immediately above "1. Yes".
/// A prose list is introduced by a statement, not a question, and its items are
/// usually separated by blank lines or prose.
function looksLikeNumberedMenu(text) {
  if (MENU_CARET.test(text)) return true;
  const lines = String(text).split("\n");
  for (let i = 0; i < lines.length - 1; i++) {
    if (!MENU_OPTION_1.test(lines[i])) continue;
    // Option 2 must be the very next line: a menu is a contiguous block.
    if (!MENU_OPTION_2.test(lines[i + 1])) continue;
    // ...and a question must introduce it, within the three lines above.
    const preamble = lines.slice(Math.max(0, i - 3), i).join("\n");
    if (QUESTION_FORMS.some((p) => p.test(preamble))) return true;
  }
  return false;
}

/// An explicit question form. Each of these is a QUESTION being posed, not a
/// topic being discussed. "permission" and "approval" as bare nouns are
/// deliberately ABSENT -- they are exactly what the footer and ordinary agent
/// prose contain.
const QUESTION_FORMS = [
  /\bDo you want to\b[^?]*\?/i,
  /\bWould you like to\b[^?]*\?/i,
  /\bProceed\?/i,
  /\(y\/n\)/i,
  /\[y\/N\]/i,
  /\[Y\/n\]/i,
  /\bReviewing \d+ approval requests?\b/i,
  /\bAllow this (?:tool|command|action)\b/i,
];

/// Remove the persistent footer so the matcher sees only agent output.
export function stripPaneFooter(text) {
  let out = String(text ?? "");
  for (const pattern of FOOTER_PATTERNS) out = out.replace(pattern, " ");
  return out;
}

export function looksWorking(text) {
  const raw = String(text ?? "");
  return WORKING_PATTERNS.some((p) => p.test(raw));
}

/// Positive prompt evidence, evaluated on FOOTER-STRIPPED text.
export function hasPositivePromptEvidence(strippedText) {
  const t = String(strippedText ?? "");
  if (looksLikeNumberedMenu(t)) return "numbered";
  if (QUESTION_FORMS.some((p) => p.test(t))) return "question";
  return null;
}

/// Per-agent retry cap. Eight identical approvals to one agent is the signal
/// that the approval is not the answer -- observed on hermit-e9patch, which
/// received `y` eight times in a row and answered "nothing pending" each time.
export const MAX_SENDS_PER_AGENT = 3;

/// THE decision. Returns {send:false, reason} or {send:true, key, evidence}.
///
/// Order matters: `working` is checked BEFORE prompt evidence, because a pane
/// can legitimately show both and the working reading must win.
export function decide({ pane, status, priorSends = 0 }) {
  const raw = String(pane ?? "");
  if (looksWorking(raw)) return { send: false, reason: "working" };
  if (priorSends >= MAX_SENDS_PER_AGENT) {
    return { send: false, reason: "retry-cap" };
  }
  const stripped = stripPaneFooter(raw);
  const evidence = hasPositivePromptEvidence(stripped);
  if (!evidence) return { send: false, reason: "no-prompt-evidence" };
  // `status` is advisory only. It is recorded so a caller can see what the old
  // detector would have keyed on, but it never authorises a send by itself.
  return {
    send: true,
    key: evidence === "numbered" ? "1" : "y",
    evidence,
    status: status ?? null,
  };
}
