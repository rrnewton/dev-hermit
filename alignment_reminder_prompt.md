This is your periodic reminder to make sure your running state is aligned with your overarching goals, reread that immediately from `~/work/dev-hermit/PROJECT_VISION.md`. If the state of you and your agent fleet is already on track, good, otherwise repair.  On this large dev machine you should target ~10-15 agents and that or fewer worktrees. If you've been given a headline goal from the human for today, keep your main focus on that.

For overnight from July 23 to July 24, your mission is to:
 - keep debugging and pushing forward the qemu linux boot/snapshot-resume work, building observability capabilites and debugging skills as needed.
 - keep adding syscalls, fixes, features to unblock that headline goal. Use stringent audits of determinism logic and ensure that we maintain principles -- e.g. don't, e.g. call blocking syscalls from detcore.
 - keep driving all reverie backends, one agent each of DBI, KVM, and Sabre
 - one agent on liteinst2 revival and port
 - keep evaluating our compat envelop, enshrining it in validat.sh/CI, and expanding it systematically (general compat)

And remember general regulation:
 - keep 10-15 agents busy working productively
 - keep PRs landing quickly and scan for PRs languishing and get them a shepherd
 - keep CI healthy with green action runs supplemented by locally-validated labels when CI queue is long (add monitoring scripts for this as needed)
 - prevent our common pitfalls, lack of agent clarity about what was run/accomplished, goal post moving etc. Set stringent completion criteria for tasks and be skeptical of agent claims, demanding evidence.
