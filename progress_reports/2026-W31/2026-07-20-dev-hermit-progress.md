# Progress — Monday, July 20, 2026

**Headline:** Hermit came out of dormancy today. The project was set up for automated development, its automated test system was brought back to full coverage after a long period of neglect, and one real crash was fixed in the deterministic engine.

## What shipped
- **The automated test system is running again at full coverage.** It had rotted while the project was dormant — tests disabled, flaky, or not running at all. Today it was restored end to end, including repairing missing build tools and a broken library link that were stopping it from even starting. This is the foundation the rest of the work depends on: from here, changes can be checked automatically instead of by hand.
- **Fixed a crash on unrecognized CPU queries.** When a program asked the processor an out-of-range question, Hermit was killing the entire program instead of answering. It now returns the same empty answer real hardware gives — removing a whole category of spurious failures for real-world programs, without weakening the guarantee that a program runs the same way every time.

## What it means
Hermit's core promise is running a Linux program so it behaves identically on every run. Both of today's changes serve that: the test system is how we keep that promise from regressing, and the crash fix lets more real programs reach the point where the promise even applies.

## What's stuck
Getting the automated development environment itself to start reliably took most of the day — several restarts over startup and configuration before it was stable. No product work was lost to this; it was setup cost, now paid.
