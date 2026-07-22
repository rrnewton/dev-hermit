const PLUGIN_NAME = "hermit-dev";
const SKILL_NAME = "hermit-dev";
const POLICY_CACHE_KEY = "hermit-dev.agents-policy";
const POLICY_PATH = orc.pluginDir() + "/../../../AGENTS.md";
const ISSUE_CREATE_WRAPPER = orc.pluginDir() + "/gh-issue-create";

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
  const instructions = String(await orc.readFile(POLICY_PATH));
  if (instructions.trim().length === 0) {
    throw new Error("hermit-dev policy file is empty: " + POLICY_PATH);
  }

  // Re-registering replaces the placeholder or previous policy atomically.
  registerHermitDevSkill(instructions);

  if (orc.kvGet(POLICY_CACHE_KEY) === instructions) {
    return "hermit-dev policies already activated from " + POLICY_PATH;
  }

  const result = String(await orc.activateSkill(SKILL_NAME));
  if (!result.toLowerCase().includes("activated")) {
    throw new Error("Failed to activate hermit-dev skill: " + result);
  }

  orc.kvSet(POLICY_CACHE_KEY, instructions);
  return "hermit-dev policies activated from " + POLICY_PATH;
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
      policyPath: POLICY_PATH,
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
