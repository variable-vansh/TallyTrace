"""The thresholds a rule cannot out-confidence.

Guardrails run **after** a rule matches and they override it. That ordering is the
point of the whole design: a rule's confidence is an opinion about a pattern, and a
threshold is a decision about risk. The opinion never wins.

Three of them, all from ``config/thresholds.yaml``:

1. **max_variance_inr** -- above this many rupees, never auto-resolve. Size of the
   error, not size of the sale. One number is a policy about the average case and
   there is no average case, so the business sets a default and may set a different
   ceiling per cause, per channel, or per both: see :class:`VarianceCeiling`.
2. **never_auto_resolve_causes** -- TCS and TDS timing and chargebacks, regardless of
   how well a rule predicts them.
3. **resolution class** -- ``tax_review`` and ``investigate`` are always human. A
   ``counterparty_claim`` is not auto-*resolved* either; it is routed to the claims
   queue, because closing a row someone else owes money on is not a resolution, it is
   a write-off nobody authorised.

Every evaluation is recorded, pass or fail, and travels with the decision. "Which
guardrails did you check?" is a question an auditor asks about the resolutions that
went through, not only the ones that were held -- and once the ceiling is settable,
"under whose ceiling, and who set it?" is the next question. So the ceiling check
records the scope it resolved to and the person who set it, not only the number.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any

from pipeline.cases import CaseFeatures
from pipeline.config import channels, resolution_class_by_cause
from pipeline.rules.models import Rule

PASS = "pass"
HOLD = "hold"
ZERO = Decimal("0.00")

#: Classes that never auto-resolve whatever a rule believes. ``counterparty_claim``
#: is here because money owed by someone else is a claim to be worked, not a
#: difference to be closed.
ALWAYS_HUMAN_CLASSES = frozenset({"tax_review", "investigate", "counterparty_claim"})

#: The dimensions an operator may scope a ceiling to. Both are frozen enums, and both
#: are properties of the *phenomenon* rather than identifiers -- the same constraint
#: that keeps a learned rule from memorising a transaction keeps a ceiling from being
#: written for one order.
SCOPE_FIELDS = ("cause", "channel")
OVERRIDE_FIELDS = frozenset({*SCOPE_FIELDS, "max_variance_inr", "set_by", "note"})


@dataclass(frozen=True)
class VarianceCeiling:
    """One rupee ceiling and the scope the business set it for.

    The default names no scope and governs everything no override claims. An override
    names a cause, a channel or both, and carries who set it and why, so a resolution
    can say under whose policy it closed rather than only that it closed.
    """

    max_variance_inr: Decimal
    cause: str | None = None
    channel: str | None = None
    set_by: str | None = None
    note: str = ""

    @property
    def specificity(self) -> int:
        """How many dimensions this ceiling names. The most specific match governs."""
        return sum(1 for value in (self.cause, self.channel) if value is not None)

    @property
    def is_default(self) -> bool:
        return self.specificity == 0

    def matches(self, rule: Rule, features: CaseFeatures) -> bool:
        """Does this ceiling govern this case?

        The cause comes off the rule that already won and the channel off the case,
        which is the same split the rest of the guardrail uses: what is being claimed
        about the row, and what the row actually is.
        """
        return (self.cause is None or self.cause == rule.cause) and (
            self.channel is None or self.channel == features.channel
        )

    @property
    def key(self) -> tuple[str | None, ...]:
        """What makes two ceilings the same ceiling: the scope, never the number."""
        return (self.cause, self.channel)

    @property
    def scope(self) -> str:
        """``cause=x, channel=y`` -- empty for the default."""
        return ", ".join(
            f"{name}={value}"
            for name, value in zip(SCOPE_FIELDS, (self.cause, self.channel))
            if value is not None
        )

    def describe(self) -> str:
        """How the ceiling names itself inside a guardrail detail line."""
        if self.is_default:
            return "default ceiling"
        who = f", set by {self.set_by}" if self.set_by else ""
        return f"ceiling for {self.scope}{who}"

    def to_json(self) -> dict[str, Any]:
        return {
            "max_variance_inr": str(self.max_variance_inr),
            "cause": self.cause,
            "channel": self.channel,
            "set_by": self.set_by,
            "note": self.note,
            "scope": self.scope,
            "is_default": self.is_default,
        }


@dataclass(frozen=True)
class GuardrailConfig:
    """The auto-resolution policy in force for a run."""

    default_ceiling: VarianceCeiling
    overrides: tuple[VarianceCeiling, ...]
    never_auto_resolve_causes: frozenset[str]

    def ceiling_for(self, rule: Rule, features: CaseFeatures) -> VarianceCeiling:
        """The ceiling governing one case. Most specific wins; a tie goes to the lowest.

        A cause-scoped ceiling and a channel-scoped one are equally specific and can
        meet on a single case -- ``commission_rate_stale`` at ₹1,500 and ``offline`` at
        ₹0 meet on an offline commission variance. Both are the operator's policy and
        neither is more precisely aimed than the other, so the case is governed by the
        stricter of the two.

        That is the same shape as ``predicates.select``, which refuses to choose between
        two equally specific *rules* and sends the case to a human. Both resolve a tie
        in the direction of the person: a rule tie has no safe merge, so it escalates;
        a ceiling tie does, so it takes it. What neither does is let file order decide
        -- specificity, then amount, is a total order over a case's candidates, so the
        same case gets the same ceiling whatever order the YAML was written in.
        """
        governing = [c for c in self.overrides if c.matches(rule, features)]
        if not governing:
            return self.default_ceiling
        finest = max(c.specificity for c in governing)
        return min(
            (c for c in governing if c.specificity == finest),
            key=lambda ceiling: (ceiling.max_variance_inr, ceiling.scope),
        )

    def with_default_ceiling(self, amount: Decimal) -> "GuardrailConfig":
        """The same policy at a different default, for a what-if run.

        Overrides are kept: they are the standing policy, and a question about the
        default is a question about the cases nobody has written a ceiling for.
        """
        if amount < ZERO:
            raise ValueError(f"a variance ceiling cannot be negative: {amount}")
        return replace(self, default_ceiling=VarianceCeiling(max_variance_inr=amount))

    def to_json(self) -> dict[str, Any]:
        return {
            "default": self.default_ceiling.to_json(),
            "overrides": [ceiling.to_json() for ceiling in self.overrides],
            "never_auto_resolve_causes": sorted(self.never_auto_resolve_causes),
            "always_human_classes": sorted(ALWAYS_HUMAN_CLASSES),
        }


# --------------------------------------------------------------------------- #
# Loading, and refusing a policy that cannot be read one way
# --------------------------------------------------------------------------- #


def _amount(raw: Any, where: str) -> Decimal:
    try:
        value = Decimal(str(raw))
    except Exception as exc:                      # noqa: BLE001 -- reported with context
        raise ValueError(f"{where}: {raw!r} is not a number of rupees") from exc
    if value < ZERO:
        raise ValueError(f"{where}: a variance ceiling cannot be negative ({value})")
    return value


def _override(entry: Any, index: int) -> VarianceCeiling:
    """One row of ``max_variance_overrides``, validated against the frozen enums.

    A cause or a channel that does not exist is a typo that would otherwise present
    as a ceiling that silently never fires, which is the worst way for a risk
    threshold to be wrong.
    """
    where = f"config/thresholds.yaml auto_resolution.max_variance_overrides[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where}: expected a mapping, got {type(entry).__name__}")

    unknown = sorted(set(entry) - OVERRIDE_FIELDS)
    if unknown:
        raise ValueError(f"{where}: unknown field(s) {unknown}; allowed: {sorted(OVERRIDE_FIELDS)}")
    if "max_variance_inr" not in entry:
        raise ValueError(f"{where}: no max_variance_inr -- an override must name its number")

    cause, channel = entry.get("cause"), entry.get("channel")
    if cause is None and channel is None:
        raise ValueError(
            f"{where}: names neither a cause nor a channel. An override that scopes to "
            "nothing is the default -- set auto_resolution.max_variance_inr instead."
        )
    if cause is not None and cause not in resolution_class_by_cause():
        raise ValueError(f"{where}: {cause!r} is not a cause in config/causes.yaml")
    if channel is not None and channel not in channels()["channels"]:
        raise ValueError(f"{where}: {channel!r} is not a channel in config/channels.yaml")

    return VarianceCeiling(
        max_variance_inr=_amount(entry["max_variance_inr"], where),
        cause=cause,
        channel=channel,
        set_by=entry.get("set_by"),
        note=str(entry.get("note", "")).strip(),
    )


def _refuse_duplicates(overrides: tuple[VarianceCeiling, ...]) -> None:
    """One scope, one number.

    Two rows for the same scope is someone editing a ceiling by adding a line instead
    of changing one, and the file then holds two answers to the same question. Ties
    *between* scopes are resolved by :meth:`GuardrailConfig.ceiling_for`; a tie within
    one scope has no principled resolution, so it is a load error naming the scope.
    """
    seen: dict[tuple[str | None, ...], VarianceCeiling] = {}
    for ceiling in overrides:
        if ceiling.key in seen:
            raise ValueError(
                "config/thresholds.yaml auto_resolution.max_variance_overrides: "
                f"[{ceiling.scope}] is set twice, at ₹{seen[ceiling.key].max_variance_inr} "
                f"and ₹{ceiling.max_variance_inr}. One scope, one number -- edit the "
                "existing row rather than adding a second."
            )
        seen[ceiling.key] = ceiling


def guardrail_config_from(thresholds: dict[str, Any]) -> GuardrailConfig:
    section = thresholds["auto_resolution"]
    overrides = tuple(
        _override(entry, index)
        for index, entry in enumerate(section.get("max_variance_overrides") or [])
    )
    _refuse_duplicates(overrides)
    return GuardrailConfig(
        default_ceiling=VarianceCeiling(
            max_variance_inr=_amount(
                section["max_variance_inr"],
                "config/thresholds.yaml auto_resolution.max_variance_inr",
            )
        ),
        overrides=overrides,
        never_auto_resolve_causes=frozenset(section["never_auto_resolve_causes"]),
    )


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GuardrailCheck:
    name: str
    outcome: str          # pass | hold
    detail: str

    def render(self) -> str:
        return f"{self.name}:{self.outcome}"


@dataclass(frozen=True)
class GuardrailResult:
    checks: tuple[GuardrailCheck, ...]
    #: The ceiling that governed this case. Carried so a proposal card, the report and
    #: the decision path can name the policy rather than restate a number from the
    #: README that may no longer be the one in force.
    ceiling: VarianceCeiling

    @property
    def held(self) -> bool:
        return any(check.outcome == HOLD for check in self.checks)

    @property
    def held_by(self) -> tuple[GuardrailCheck, ...]:
        return tuple(check for check in self.checks if check.outcome == HOLD)

    @property
    def rendered(self) -> tuple[str, ...]:
        return tuple(check.render() for check in self.checks)

    @property
    def reason(self) -> str:
        return "; ".join(check.detail for check in self.held_by)


def evaluate(rule: Rule, features: CaseFeatures, cfg: GuardrailConfig) -> GuardrailResult:
    """Run every guardrail. All of them, always -- a short circuit loses the record."""
    ceiling = cfg.ceiling_for(rule, features)
    over = features.variance_inr > ceiling.max_variance_inr
    blocked = rule.cause in cfg.never_auto_resolve_causes
    human = rule.resolution_class in ALWAYS_HUMAN_CLASSES

    return GuardrailResult(
        ceiling=ceiling,
        checks=(
            GuardrailCheck(
                "max_variance_inr",
                HOLD if over else PASS,
                f"₹{features.variance_inr} is {'above' if over else 'within'} the "
                f"₹{ceiling.max_variance_inr} {ceiling.describe()}",
            ),
            GuardrailCheck(
                "never_auto_resolve",
                HOLD if blocked else PASS,
                f"{rule.cause} is on the never-auto-resolve list" if blocked
                else f"{rule.cause} is not on the never-auto-resolve list",
            ),
            GuardrailCheck(
                "resolution_class",
                HOLD if human else PASS,
                f"{rule.resolution_class} is always human" if human
                else f"{rule.resolution_class} may be automated",
            ),
        ),
    )
