const PLUGIN_NAME = "hermit-dev";
const SKILL_NAME = "hermit-dev";
const SPECULATIVE_ATTACK_SKILL_NAME = "hermit-parallel-speculative-attack";
const URGENT_VALIDATION_SKILL_NAME =
  "hermit-urgent-critical-path-fix-validation";
const POLICY_CACHE_KEY = "hermit-dev.agents-policy";
const WORKSPACE_SUBPATH = "work/dev-hermit";

function currentEvalDirectory(): string | null {
  const readModulePath = (orc as any).readEvalModulePath;
  const modulePath = typeof readModulePath === "function"
    ? String(readModulePath() || "")
    : "";
  const separator = modulePath.lastIndexOf("/");
  return separator > 0 ? modulePath.slice(0, separator) : null;
}

const REGISTERED_PLUGIN_DIRECTORY = orc.pluginDir();
const CONFIG_DIRECTORY = currentEvalDirectory();
const SOURCE_DIRECTORY = REGISTERED_PLUGIN_DIRECTORY ||
  (CONFIG_DIRECTORY ? CONFIG_DIRECTORY + "/plugins/hermit-dev" : null);
const WORKSPACE_ROOT = REGISTERED_PLUGIN_DIRECTORY
  ? REGISTERED_PLUGIN_DIRECTORY + "/../../.."
  : CONFIG_DIRECTORY
  ? CONFIG_DIRECTORY + "/.."
  : "$HOME/work/dev-hermit";
const RELATIVE_POLICY_PATH = WORKSPACE_ROOT + "/AGENTS.md";
const SPECULATIVE_ATTACK_SKILL_PATH =
  (SOURCE_DIRECTORY || WORKSPACE_ROOT + "/.orc/plugins/hermit-dev") +
  "/parallel-speculative-attack.md";
const URGENT_VALIDATION_SKILL_PATH =
  (SOURCE_DIRECTORY || WORKSPACE_ROOT + "/.orc/plugins/hermit-dev") +
  "/urgent-critical-path-fix-validation.md";
const ISSUE_CREATE_WRAPPER =
  (SOURCE_DIRECTORY || WORKSPACE_ROOT + "/.orc/plugins/hermit-dev") +
  "/gh-issue-create";
const PR_STATUS_COMMAND = 'cd "' + WORKSPACE_ROOT + '" && ./ci-hub/ci-hub health';
const OPERATIONAL_TICK_SCRIPT_NAME = "hermitOperationalTick";
const OPERATIONAL_TICK_COMMAND =
  'cd "' + WORKSPACE_ROOT + '" && ' +
  'HERMIT_AGENT_SNAPSHOT_JSON="$1" ./ci-hub/bin/health-tick --flush --no-header';
const OPERATIONAL_TICK_INTERVAL_MS = 5 * 60 * 1000;
const OPERATIONAL_TICK_WORKFLOW_NAME = "hermit-dev-operational-health-v1";
const LEGACY_PR_HEALTH_WORKFLOW_NAME = "hermit-dev-pr-health";

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
const SPECULATIVE_ATTACK_SKILL_DESCRIPTION =
  "Coordinator-only protocol for deadline-driven or quantified critical-path " +
  "parallel speculative attacks. Single-path execution remains the default.";
const SPECULATIVE_ATTACK_SKILL_TRIGGERS = [
  "\\bparallel[- ]speculative[- ]attack\\b",
  "\\bspeculative attack\\b",
  "\\bcompeting (?:draft )?PRs?\\b",
  "\\bdeadline[- ]driven fan[- ]out\\b",
  "\\bquantified critical[- ]path bottleneck\\b",
];
const URGENT_VALIDATION_SKILL_DESCRIPTION =
  "Coordinator-only fast validation loop for deadline-driven or quantified " +
  "critical-path fixes: parallel local and GitHub CI, active CI babysitting, " +
  "and tight local iteration on the individual failing test.";
const URGENT_VALIDATION_SKILL_TRIGGERS = [
  "\\burgent critical[- ]path (?:fix )?validation\\b",
  "\\bCI[- ]on[- ](?:the[- ])?critical[- ]path\\b",
  "\\bdeadline[- ]driven validation\\b",
  "\\bbabysit (?:the )?CI\\b",
  "\\btight local test loop\\b",
];

let resolvedPolicyPath: string = RELATIVE_POLICY_PATH;

async function readPolicyIfPresent(path: string): Promise<string | null> {
  try {
    const text = String(await orc.readFile(path));
    return text.trim().length > 0 ? text : null;
  } catch (_err) {
    return null;
  }
}

async function resolvePolicy(): Promise<{ path: string; instructions: string }> {
  const candidates: string[] = [RELATIVE_POLICY_PATH];
  try {
    const info = await orc.userInfo();
    if (info && info.homeDir) {
      candidates.push(info.homeDir + "/" + WORKSPACE_SUBPATH + "/AGENTS.md");
    }
  } catch (_err) {
    // userInfo may be unavailable in constrained plugin tests.
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

function registerHermitDevSkill(instructions: string): void {
  orc.registerSkill(SKILL_NAME, {
    description: SKILL_DESCRIPTION,
    instructions,
    functions: SKILL_FUNCTIONS,
    triggers: SKILL_TRIGGERS,
  });
}

function registerSpeculativeAttackSkill(instructions: string): void {
  orc.registerSkill(SPECULATIVE_ATTACK_SKILL_NAME, {
    description: SPECULATIVE_ATTACK_SKILL_DESCRIPTION,
    instructions,
    triggers: SPECULATIVE_ATTACK_SKILL_TRIGGERS,
  });
}

async function loadSpeculativeAttackSkill(): Promise<void> {
  const instructions = await readPolicyIfPresent(SPECULATIVE_ATTACK_SKILL_PATH);
  if (instructions === null) {
    throw new Error(
      "hermit-dev coordinator skill not found or empty: " +
        SPECULATIVE_ATTACK_SKILL_PATH,
    );
  }
  registerSpeculativeAttackSkill(instructions);
}

function registerUrgentValidationSkill(instructions: string): void {
  orc.registerSkill(URGENT_VALIDATION_SKILL_NAME, {
    description: URGENT_VALIDATION_SKILL_DESCRIPTION,
    instructions,
    triggers: URGENT_VALIDATION_SKILL_TRIGGERS,
  });
}

async function loadUrgentValidationSkill(): Promise<void> {
  const instructions = await readPolicyIfPresent(URGENT_VALIDATION_SKILL_PATH);
  if (instructions === null) {
    throw new Error(
      "hermit-dev coordinator skill not found or empty: " +
        URGENT_VALIDATION_SKILL_PATH,
    );
  }
  registerUrgentValidationSkill(instructions);
}

async function activateHermitDevPolicies(): Promise<string> {
  const { path, instructions } = await resolvePolicy();

  // Re-registering replaces the placeholder or previous policy atomically.
  registerHermitDevSkill(instructions);
  await loadSpeculativeAttackSkill();
  await loadUrgentValidationSkill();

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

function actionableTickLines(report: string): string[] {
  return report.split("\n").filter((line) => {
    if (line.startsWith("ACTION:") || line.startsWith("ERROR:")) {
      return true;
    }
    return line.startsWith("HEALTH:") && !line.includes(" ok ");
  });
}

export async function operationalHealthHeartbeat(wf: WfContext): Promise<void> {
  await wf.loop(async () => {
    const agents = await orc.listAgents();
    const result = await orc.scripts.hermitOperationalTick(
      JSON.stringify(agents),
    ) as {
      exitCode: number;
      stdout?: string;
      stderr?: string;
    };
    const exitCode = Number(result.exitCode);
    const stdout = String(result.stdout || "").trim();
    const stderr = String(result.stderr || "").trim();
    const report = [stdout, stderr].filter(Boolean).join("\n");
    const actionable = actionableTickLines(stdout);
    if (exitCode !== 0 || actionable.length > 0) {
      const title = exitCode === 0
        ? "HARD WARNING: operational health requires action"
        : "HARD WARNING: operational health poll failed";
      await orc.sendWakeup(
        [],
        title,
        (report || "tick-hub returned no diagnostic output") +
          "\nRun " + PR_STATUS_COMMAND + " for the full GitHub/PR report.",
      );
    }
    await wf.sleep(OPERATIONAL_TICK_INTERVAL_MS);
  });
}

// Top-level plugin evaluation registers the placeholder skill and durable
// PR-health heartbeat. Startup replaces the placeholder with current policy.
registerHermitDevSkill(
  "The canonical dev-hermit policies are loaded from AGENTS.md during startup.",
);
registerSpeculativeAttackSkill(
  "The parallel speculative attack protocol is loaded during plugin startup.",
);
registerUrgentValidationSkill(
  "The urgent critical-path validation protocol is loaded during plugin " +
    "startup.",
);

orc.registerScript(OPERATIONAL_TICK_SCRIPT_NAME, {
  script: OPERATIONAL_TICK_COMMAND,
  description: "Run the version-pinned dev-hermit tick-hub operational poll",
  timeoutSec: 180,
});

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
      coordinatorSkills: [
        SPECULATIVE_ATTACK_SKILL_NAME,
        URGENT_VALIDATION_SKILL_NAME,
      ],
      policyPath: resolvedPolicyPath,
      speculativeAttackSkillPath: SPECULATIVE_ATTACK_SKILL_PATH,
      urgentValidationSkillPath: URGENT_VALIDATION_SKILL_PATH,
      policyLoaded: typeof cachedPolicy === "string",
      policyBytes: typeof cachedPolicy === "string" ? cachedPolicy.length : 0,
      workspace: WORKSPACE_ROOT,
      hermitPrimary: "rrnewton/hermit",
      hermitUpstream: "facebookexperimental/hermit",
      reverieIssueRepo: "rrnewton/reverie",
      issueCreateWrapper: ISSUE_CREATE_WRAPPER,
      prStatusCommand: PR_STATUS_COMMAND,
      operationalTickCommand: OPERATIONAL_TICK_COMMAND,
      operationalTickConfig: WORKSPACE_ROOT + "/ci-hub/health/tick-hub.yaml",
      operationalTickIntervalMinutes: OPERATIONAL_TICK_INTERVAL_MS / 60000,
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

orc.workflow(
  operationalHealthHeartbeat,
  "Run tick-hub operational checks and hard-warn the coordinator on failures",
  {
    name: OPERATIONAL_TICK_WORKFLOW_NAME,
    restartable: {} as any,
  },
);

orc.registerStartup(PLUGIN_NAME + ".startup", async function hermitDevStartup() {
  try {
    await orc.killWorkflow(LEGACY_PR_HEALTH_WORKFLOW_NAME);
  } catch (_err) {
    // The legacy workflow is absent in new sessions.
  }
  const result = await activateHermitDevPolicies();
  orc.log("info", result);
});
