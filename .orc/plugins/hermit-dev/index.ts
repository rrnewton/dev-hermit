const PLUGIN_NAME = "hermit-dev";
const SKILL_NAME = "hermit-dev";
const POLICY_CACHE_KEY = "hermit-dev.agents-policy";

// AGENTS.md lives at the dev-hermit workspace root. Two install layouts must
// both resolve it:
//   1. In-repo source:  ~/work/dev-hermit/.orc/plugins/hermit-dev  → ../../../ hits the repo root.
//   2. Home copy:        ~/.orc/plugins/hermit-dev                 → ../../../ hits $HOME (wrong),
//      so fall back to the canonical workspace path under $HOME.
// The home copy exists because ORC's module sandbox rejects a symlinked plugin
// dir (its relative `import "./index.ts"` resolves outside the managed roots).
const WORKSPACE_SUBPATH = "work/dev-hermit";
const RELATIVE_POLICY_PATH = orc.pluginDir() + "/../../../AGENTS.md";
const ISSUE_CREATE_WRAPPER = orc.pluginDir() + "/gh-issue-create";

// The path AGENTS.md was last read from; defaults to the in-repo relative path
// and is updated by resolvePolicy() once a candidate is confirmed readable.
let resolvedPolicyPath: string = RELATIVE_POLICY_PATH;

async function readPolicyIfPresent(path: string): Promise<string | null> {
  try {
    const text = String(await orc.readFile(path));
    return text.trim().length > 0 ? text : null;
  } catch (_err) {
    return null;
  }
}

// Resolve AGENTS.md across both install layouts, returning the winning path and
// its contents. Tries the in-repo relative path first, then the canonical
// ~/work/dev-hermit/AGENTS.md (home derived from orc.userInfo(), not hardcoded).
async function resolvePolicy(): Promise<{ path: string; instructions: string }> {
  const candidates: string[] = [RELATIVE_POLICY_PATH];
  try {
    const info = await orc.userInfo();
    if (info && info.homeDir) {
      candidates.push(info.homeDir + "/" + WORKSPACE_SUBPATH + "/AGENTS.md");
    }
  } catch (_err) {
    // userInfo unavailable — rely on the relative candidate alone.
  }

  for (const path of candidates) {
    const instructions = await readPolicyIfPresent(path);
    if (instructions !== null) {
      resolvedPolicyPath = path;
      return { path, instructions };
    }
  }

  throw new Error(
    "hermit-dev policy file not found or empty. Tried: " + candidates.join(", "),
  );
}

const SKILL_DESCRIPTION = "Project-specific coordination, fork-only issue, " +
  "Git/PR, Reverie API, and product-vision policies for dev-hermit.";
const SKILL_FUNCTIONS = [
  PLUGIN_NAME + ".activate",
  PLUGIN_NAME + ".status",
];
const SKILL_TRIGGERS = [
  "\\bdev-hermit\\b",
  "\\brrnewton/hermit\\b",
  "\\bfacebookexperimental/hermit\\b",
  "\\brrnewton/reverie\\b",
  "\\bfacebookexperimental/reverie\\b",
  "\\bgh\\s+issue\\s+create\\b",
  "\\bReverie\\b",
];

function registerHermitDevSkill(instructions: string): void {
  orc.registerSkill(SKILL_NAME, {
    description: SKILL_DESCRIPTION,
    instructions,
    functions: SKILL_FUNCTIONS,
    triggers: SKILL_TRIGGERS,
  });
}

async function activateHermitDevPolicies(): Promise<string> {
  const { path, instructions } = await resolvePolicy();

  // Re-registering replaces the placeholder or previous policy atomically.
  registerHermitDevSkill(instructions);

  if (orc.kvGet(POLICY_CACHE_KEY) === instructions) {
    return "hermit-dev policies already activated from " + path;
  }

  const result = String(await orc.activateSkill(SKILL_NAME));
  if (!result.toLowerCase().includes("activated")) {
    throw new Error("Failed to activate hermit-dev skill: " + result);
  }

  orc.kvSet(POLICY_CACHE_KEY, instructions);
  return "hermit-dev policies activated from " + path;
}

// Top-level plugin evaluation is declaration-only. Startup replaces these
// placeholder instructions with the current canonical AGENTS.md contents.
registerHermitDevSkill(
  "The canonical dev-hermit policies are loaded from AGENTS.md during startup.",
);

orc.exposeFunction(
  PLUGIN_NAME + ".activate",
  activateHermitDevPolicies,
  {
    description: "Reload AGENTS.md and activate the canonical dev-hermit policies",
    params: [],
    sig: "await orc.hermit-dev.activate()",
  },
);

orc.exposeFunction(
  PLUGIN_NAME + ".status",
  function hermitDevStatus() {
    const cachedPolicy = orc.kvGet(POLICY_CACHE_KEY);
    return {
      plugin: PLUGIN_NAME,
      skill: SKILL_NAME,
      policyPath: resolvedPolicyPath,
      policyLoaded: typeof cachedPolicy === "string",
      policyBytes: typeof cachedPolicy === "string" ? cachedPolicy.length : 0,
      workspace: "~/work/dev-hermit",
      hermitPrimary: "rrnewton/hermit",
      hermitUpstream: "facebookexperimental/hermit",
      reverieIssueRepo: "rrnewton/reverie",
      issueCreateWrapper: ISSUE_CREATE_WRAPPER,
      maxParkedSlots: 5,
      maxActiveWorktrees: 12,
      maxAgents: 15,
    };
  },
  {
    description: "Report hermit-dev plugin registration and policy source state",
    params: [],
    sig: "orc.hermit-dev.status()",
  },
);

orc.registerStartup(PLUGIN_NAME + ".startup", async function hermitDevStartup() {
  const result = await activateHermitDevPolicies();
  orc.log("info", result);
});
