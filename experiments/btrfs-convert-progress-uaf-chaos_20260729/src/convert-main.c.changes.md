# `convert/main.c` changes (btrfs-progs v7.1)

These three edits to `convert/main.c` are identical in the **buggy** and
**fixed** variants (only `common/task-utils.c` differs between them — see the
`buggy/` and `fixed/` directories here). Apply them to a pristine
btrfs-progs **v7.1** `convert/main.c`, then build with ASAN (recipe in the
experiment `README.md`).

## 1. Bake ASAN options into the binary

Under hermit the guest environment is controlled by hermit, so an
`ASAN_OPTIONS` env var does not reach the guest. Define
`__asan_default_options()` instead. Insert after the `#include` block near the
top of the file (just before `extern const struct btrfs_convert_operations
ext2_convert_ops;`):

```c
/* DEMO08: bake ASAN options into the binary so they apply under hermit
 * (which controls the guest environment). disable_coredump=0 avoids the
 * setrlimit(RLIMIT_CORE) that hermit rejects with EPERM; leak detection
 * is off so only the use-after-free aborts. */
#if defined(__SANITIZE_ADDRESS__) || defined(__has_feature)
const char *__asan_default_options(void);
const char *__asan_default_options(void)
{
	return "detect_leaks=0:disable_coredump=0:abort_on_error=1:handle_segv=1:allocator_may_return_null=1";
}
#endif
```

## 2. Progress threadfn observes the teardown flag

In `print_copied_inodes()` the loop must terminate on the shared `stop` flag
instead of relying on `pthread_cancel`. The load of `priv->info->periodic.stop`
after `task_period_wait()` is a second use-after-free site in the buggy variant
(the first is the store in `task_period_wait()` itself). The loop becomes:

```c
	task_period_start(priv->info, 1 /* period now irrelevant; see task-utils.c */);
	while (1) {
		count++;
		pthread_mutex_lock(&priv->mutex);
		printf("Copy inodes [%c] [%10llu/%10llu]\r",
		       work_indicator[count % 4],
		       priv->cur_copy_inodes, priv->max_copy_inodes);
		pthread_mutex_unlock(&priv->mutex);
		fflush(stdout);
		task_period_wait(priv->info);
		/* DEMO08: read the teardown flag out of *info. In the buggy variant
		 * task_stop() has freed info without joining this detached thread, so
		 * this load races free(); the fixed variant joins first. */
		if (priv->info->periodic.stop)
			break;
	}
```

(This replaces the historical loop that had no `stop` check and was terminated
externally.)

## 3. No other main.c changes

`do_convert()`'s existing `task_init` → `task_start` → `copy_inodes` →
`task_stop` → `task_deinit` sequence is unchanged; the use-after-free lives
entirely in `common/task-utils.c`'s detach/no-join teardown (buggy) versus
no-detach/join teardown (fixed), plus the observability harness (timerfd → pipe)
that both variants share.
