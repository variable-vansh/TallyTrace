"""The config files are the contract. If they drift, everything downstream lies."""

from __future__ import annotations

from decimal import Decimal

from pipeline import config
from pipeline.models import Cause, Channel, ResolutionClass


def test_cause_enum_matches_the_frozen_list() -> None:
    """config/causes.yaml and the Cause enum must never diverge."""
    assert {entry["cause"] for entry in config.causes()["causes"]} == {c.value for c in Cause}


def test_every_cause_routes_to_a_known_resolution_class() -> None:
    valid = {c.value for c in ResolutionClass}
    assert set(config.resolution_class_by_cause().values()) <= valid


def test_channel_mix_sums_to_one() -> None:
    assert sum(config.channels()["channel_mix"].values()) == Decimal("1.0")


def test_every_channel_in_the_mix_is_configured() -> None:
    assert set(config.channels()["channel_mix"]) == {c.value for c in Channel}
    for channel in Channel:
        cfg = config.channel_config(channel.value)
        assert cfg["categories"], f"{channel.value} has no commission categories"
        assert cfg["payout_cadence"] in {"daily", "weekly"}


def test_config_numbers_are_decimal_not_float() -> None:
    """A rate read as a float would put binary rounding error into every fee."""
    for cfg in config.channels()["channels"].values():
        for rate in cfg["categories"].values():
            assert isinstance(rate, Decimal)
    matching = config.thresholds()["matching"]
    assert isinstance(matching["rounding_tolerance_inr"], Decimal)
    assert isinstance(matching["fee_variance_tolerance_pct"], Decimal)


def test_thresholds_expose_everything_the_build_depends_on() -> None:
    thresholds = config.thresholds()
    assert {"matching", "auto_resolution", "rule_lifecycle", "claims"} <= set(thresholds)
    assert thresholds["auto_resolution"]["never_auto_resolve_causes"]
    for cause in thresholds["auto_resolution"]["never_auto_resolve_causes"]:
        assert cause in config.resolution_class_by_cause()
