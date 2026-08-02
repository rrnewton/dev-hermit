# Deprecated docs (retained for history, not for use)

Documents here are **superseded and must not be copied or cited as current
guidance**. They are kept only so past reports and links remain resolvable and
so the reasoning behind their retirement is discoverable. Each file carries a
dated retirement banner explaining what replaced it and why.

Do not add front-matter staleness tracking here — these docs are intentionally
frozen, not maintained. If you are looking for the live equivalent, follow the
"superseded by" pointer in the file's banner.

## Contents

- `progress_report_template.md` — the old hand-filled backend progress-report
  template. Retired by the anti-fakery gate (#152): its prose "cells" were
  copied by hand and invited un-backed parity/determinism claims for backends
  that were not actually run. Superseded by the automated **compat-envelope**
  scorecard system (`compat-envelope/`), where every cell is a side effect of
  actually running that cell and a not-run cell reads as 0%, never an optimistic
  estimate.
