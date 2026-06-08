"""Tests for ``audit._audit_mash`` — specifically that brewbridge's
own native mash format isn't flagged as broken.

Background: BeerSmith embeds the mash-steps array as a string with
unescaped inner quotes (e.g. ``"steps":"[{"_Schema_":"7432",...}]"``).
That's invalid by standard JSON spec, but it's the only layout
BeerSmith's reader accepts as authoritative — and brewbridge writes
exactly that shape by design (see ``core/beersmith.py`` format quirks).

An earlier version of ``_audit_mash`` ran ``json.loads`` on this blob,
hit the parse error, and emitted an ``INFO`` issue per recipe —
22 false positives per audit (one per imported brew.is recipe).

Current implementation uses regex extraction instead of ``json.loads``,
so the BeerSmith-native format doesn't trip a false positive while
genuinely-broken mash data (no grain weight, no steps) is still caught.
"""
from __future__ import annotations

from brewbridge.core.audit import _audit_mash


# A real mash blob in BeerSmith-native format, taken from the wild
# (the "Brew.is einfaldur" profile brewbridge writes). The trailing
# ``"steps":"[{...}]"`` field has unescaped inner quotes, which json.loads
# refuses to parse.
NATIVE_MASH = (
    '{"_Schema_":"7434","F_MH_NAME":"Brew.is einfaldur",'
    '"F_MH_GRAIN_WEIGHT":"160.0000000","F_MH_GRAIN_TEMP":"72.0000000",'
    '"F_MH_BOIL_TEMP":"212.0000000","F_MH_TUN_TEMP":"72.0000000",'
    '"F_MH_PH":"5.4000000","F_MH_SPARGE_TEMP":"168.0000000",'
    '"steps":"[{"_Schema_":"7432","F_MS_NAME":"Mash In","F_MS_TYPE":"0",'
    '"F_MS_INFUSION":"400.0000000","F_MS_STEP_TEMP":"152.6000000"}]"}'
)


def test_native_mash_format_not_flagged():
    """The 22-false-positive bug: brewbridge's own write format used
    to trigger an INFO "non-standard mash JSON" issue per recipe.
    Regex extraction bypasses the parser, so this returns no issues."""
    issues = _audit_mash("Some Recipe", NATIVE_MASH)
    assert issues == [], (
        f"Native BeerSmith mash format should not generate any audit "
        f"issues; got: {[(i.severity, i.message) for i in issues]}")


def test_empty_mash_flagged_critical():
    """Genuine missing content still surfaces as CRIT."""
    issues = _audit_mash("Some Recipe", None)
    assert len(issues) == 1
    assert issues[0].severity == "CRIT"
    assert "no F_R_MASH" in issues[0].message


def test_zero_grain_weight_flagged_critical():
    """Grain weight = 0 means the mash math will go off the rails —
    BeerSmith won't compute strike-water amounts correctly. CRIT."""
    blob = (
        '{"F_MH_NAME":"Empty","F_MH_GRAIN_WEIGHT":"0.0000000",'
        '"steps":"[{"F_MS_NAME":"Mash In"}]"}'
    )
    issues = _audit_mash("Some Recipe", blob)
    assert any(i.severity == "CRIT" and "grain weight" in i.message
                for i in issues), \
        f"Expected a CRIT 'grain weight' issue, got: {issues}"


def test_no_steps_flagged_critical():
    """Mash blob with grain weight but no steps array — recipe is
    missing data, BeerSmith will display 'no profile / no steps'.
    CRIT."""
    blob = (
        '{"F_MH_NAME":"Stepless","F_MH_GRAIN_WEIGHT":"160.0",'
        '"steps":"[]"}'
    )
    issues = _audit_mash("Some Recipe", blob)
    assert any(i.severity == "CRIT" and "no mash steps" in i.message
                for i in issues), \
        f"Expected a CRIT 'no mash steps' issue, got: {issues}"


def test_grain_weight_unquoted_form_accepted():
    """Tolerate both ``"F_MH_GRAIN_WEIGHT":"160.0"`` (BeerSmith style)
    and ``"F_MH_GRAIN_WEIGHT":160.0`` (standard JSON style). Either
    should be parseable for the audit's purposes."""
    bare = (
        '{"F_MH_NAME":"Bare","F_MH_GRAIN_WEIGHT":160.0,'
        '"steps":"[{"F_MS_NAME":"Mash In"}]"}'
    )
    quoted = (
        '{"F_MH_NAME":"Quoted","F_MH_GRAIN_WEIGHT":"160.0",'
        '"steps":"[{"F_MS_NAME":"Mash In"}]"}'
    )
    assert _audit_mash("R1", bare) == []
    assert _audit_mash("R2", quoted) == []
