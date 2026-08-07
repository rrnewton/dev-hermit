//! A count that cannot exist without the size of the thing it counted.
//!
//! THE RULE, MADE STRUCTURAL: a count is self-describing only if it travels with
//! its denominator. This is a TYPE rather than a convention on purpose. The
//! audit found our hardening tracks exactly with how much each surface has been
//! attacked -- the landing gate is the most hardened, the prefix-depth ratchet
//! the least -- which means every NEW measurement surface starts unhardened and
//! fixing today's instances only defers the next batch. A convention degrades
//! the same way every warning we wrote degraded. A type does not: it turns a
//! review checklist item into a compile error, and the check happens at write
//! time whether or not anyone remembers the rule.
//!
//! It also kills the worse subclass the audit named -- a denominator that is
//! COMPUTED AND THEN NOT CONSULTED. Under this type the value cannot exist
//! without the denominator attached, so there is no way to compute one, drop it,
//! and still emit the number.
//!
//! WHAT IS ENFORCED AT COMPILE TIME:
//!   * `value` and `denominator` are private; there is no public field access,
//!     no `Default`, no `From<u64>`, and no constructor taking a value alone.
//!   * The only way to obtain a `Measured` is [`Measured::of`], which requires
//!     all three of value, denominator, and conditions.
//!   * Therefore `Measured::of(5)` and `let m: Measured = 5;` do not compile.
//!
//! WHAT IS NOT ENFORCED, stated so nobody over-trusts it: nothing stops a caller
//! passing a WRONG denominator, or the string "unknown" as conditions. This type
//! removes the ABSENT denominator, which is the failure the audit actually
//! found; it does not and cannot adjudicate whether the denominator is the right
//! one. That remains a review question.
//!
//! Scope: NEW measurement code -- the ratchet, scorecard emitters, ci-hub
//! metrics. Retrofitting existing emitters is deliberately a separate task so
//! two agents do not edit the same files.

// Allowed at MODULE scope, not at each call site: this type is deliberately published ahead
// of its consumers so new measurement code can adopt it, and the retrofit of existing
// emitters is a separate task. Without this, every ci-hub invocation prints a dead-code
// warning to every agent on the box.
#![allow(dead_code)]

use std::fmt;

/// A measured quantity that carries what it measured against.
///
/// Construct with [`Measured::of`]. There is deliberately no other way.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Measured {
    /// The count itself. Private: a bare count is the thing this type exists to
    /// prevent, so it must never be readable or writable in isolation from the
    /// denominator that gives it meaning.
    value: u64,
    /// The size of the population `value` was counted out of. Private for the
    /// same reason: the pair is the unit of meaning, not either half.
    denominator: u64,
    /// What was true when this was measured -- the profile, selection mode,
    /// host, or filter that produced it. Free text, because the useful
    /// conditions differ per surface, but REQUIRED, because "12 of 400" is still
    /// ambiguous when the reader cannot tell which 400.
    conditions: String,
}

impl Measured {
    /// The only constructor. Requires the denominator and the conditions, so a
    /// bare count cannot be expressed.
    ///
    /// ```ignore
    /// let executed = Measured::of(348, 607, "schema>=3, full profile");
    /// ```
    pub fn of(value: u64, denominator: u64, conditions: impl Into<String>) -> Self {
        Self {
            value,
            denominator,
            conditions: conditions.into(),
        }
    }

    /// The count. Reading it requires already holding the qualified value, so a
    /// caller cannot obtain the number without also having had the denominator.
    pub fn value(&self) -> u64 {
        self.value
    }

    /// The population the count was taken from.
    pub fn denominator(&self) -> u64 {
        self.denominator
    }

    /// What was true when the measurement was taken.
    pub fn conditions(&self) -> &str {
        &self.conditions
    }

    /// True when nothing was counted AND there was nothing to count.
    ///
    /// This is the distinction the audit's "ambiguous zero" is about: `0 of 0`
    /// means the population was empty, `0 of 400` means a real population
    /// produced no hits. The first is a no-result, the second is a result. A
    /// bare `0` cannot tell them apart, and callers routinely treated both as
    /// success.
    pub fn is_empty_population(&self) -> bool {
        self.denominator == 0
    }

    /// True when a non-empty population produced no hits -- a real, informative
    /// zero, as distinct from [`Self::is_empty_population`].
    pub fn is_informative_zero(&self) -> bool {
        self.value == 0 && self.denominator > 0
    }
}

/// Renders as `value/denominator (conditions)` so the denominator cannot be
/// dropped on the way to a log line either. A `Display` that printed only the
/// value would reintroduce the bare count at the last step.
impl fmt::Display for Measured {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{}/{} ({})",
            self.value, self.denominator, self.conditions
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn carries_its_denominator_and_conditions() {
        let m = Measured::of(348, 607, "schema>=3, full profile");
        assert_eq!(m.value(), 348);
        assert_eq!(m.denominator(), 607);
        assert_eq!(m.conditions(), "schema>=3, full profile");
    }

    #[test]
    fn display_cannot_drop_the_denominator() {
        // The rendered form is where a bare count usually re-enters: someone
        // logs `{}` and the qualification is gone. Display carries all three.
        let m = Measured::of(12, 400, "portable lane");
        assert_eq!(m.to_string(), "12/400 (portable lane)");
    }

    #[test]
    fn distinguishes_the_two_zeros() {
        // The whole point of the audit's ambiguous-zero finding.
        let nothing_to_count = Measured::of(0, 0, "no cells selected");
        let counted_nothing = Measured::of(0, 400, "400 cells, none matched");

        assert!(nothing_to_count.is_empty_population());
        assert!(!nothing_to_count.is_informative_zero());

        assert!(!counted_nothing.is_empty_population());
        assert!(counted_nothing.is_informative_zero());

        // And they do not render the same, so the distinction survives logging.
        assert_ne!(nothing_to_count.to_string(), counted_nothing.to_string());
    }
}
