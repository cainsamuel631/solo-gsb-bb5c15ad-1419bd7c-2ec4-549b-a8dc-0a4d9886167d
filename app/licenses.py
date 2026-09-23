"""License normalization and compatibility matrix.

The matrix is a pragmatic, widely-used summary of common OSS license
relationships.  It is *not* legal advice; projects with real licensing
questions should consult counsel.  The table is intentionally centralised so
it can be reviewed or replaced in one place.

Direction matters: ``evaluate(consumer, dependency)`` answers the question

    "May code under ``consumer`` legally incorporate code under
    ``dependency`` (along a dependency edge)?"

Three verdicts are returned:

* ``allow``    - the combination is generally considered compatible;
* ``conflict`` - the combination is generally considered incompatible;
* ``unknown``  - at least one side is an unrecognized / unspecified license,
                 so the combination must be reviewed manually.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# License universe (canonical SPDX identifiers)
# ---------------------------------------------------------------------------

PUBLIC = ["public-domain"]
PERMISSIVE = ["MIT", "BSD-2-Clause", "BSD-3-Clause", "Apache-2.0", "ISC", "Unlicense"]
WEAK_COPYLEFT = ["MPL-2.0", "LGPL-2.1", "LGPL-3.0"]
COPYLEFT = ["GPL-2.0", "GPL-3.0"]
NETWORK_COPYLEFT = ["AGPL-3.0"]
PROPRIETARY = ["Proprietary"]

ALL_LICENSES: List[str] = (
    PUBLIC
    + PERMISSIVE
    + WEAK_COPYLEFT
    + COPYLEFT
    + NETWORK_COPYLEFT
    + PROPRIETARY
)

CATEGORY_OF: Dict[str, str] = {}
for _lic in PUBLIC:
    CATEGORY_OF[_lic] = "public-domain"
for _lic in PERMISSIVE:
    CATEGORY_OF[_lic] = "permissive"
for _lic in WEAK_COPYLEFT:
    CATEGORY_OF[_lic] = "weak-copyleft"
for _lic in COPYLEFT:
    CATEGORY_OF[_lic] = "copyleft"
for _lic in NETWORK_COPYLEFT:
    CATEGORY_OF[_lic] = "network-copyleft"
for _lic in PROPRIETARY:
    CATEGORY_OF[_lic] = "proprietary"

# ---------------------------------------------------------------------------
# Normalization: user-supplied license strings -> canonical SPDX id
# ---------------------------------------------------------------------------

_ALIASES: Dict[str, str] = {}
for _lic in ALL_LICENSES:
    _ALIASES[_lic.lower()] = _lic

_ALIASES.update(
    {
        "apache": "Apache-2.0",
        "apache2": "Apache-2.0",
        "apache-2": "Apache-2.0",
        "apache 2.0": "Apache-2.0",
        "apache license 2.0": "Apache-2.0",
        "apache license, version 2.0": "Apache-2.0",
        "bsd": "BSD-3-Clause",
        "bsd 2-clause": "BSD-2-Clause",
        "bsd 3-clause": "BSD-3-Clause",
        "new bsd": "BSD-3-Clause",
        "simplified bsd": "BSD-2-Clause",
        "gpl": "GPL-3.0",
        "gpl2": "GPL-2.0",
        "gpl-2": "GPL-2.0",
        "gplv2": "GPL-2.0",
        "gpl v2": "GPL-2.0",
        "gpl 2": "GPL-2.0",
        "gpl 2.0": "GPL-2.0",
        "gpl3": "GPL-3.0",
        "gpl-3": "GPL-3.0",
        "gplv3": "GPL-3.0",
        "gpl v3": "GPL-3.0",
        "gpl 3": "GPL-3.0",
        "gpl 3.0": "GPL-3.0",
        "lgpl": "LGPL-3.0",
        "lgpl2": "LGPL-2.1",
        "lgpl2.1": "LGPL-2.1",
        "lgpl-2.1": "LGPL-2.1",
        "lgplv2.1": "LGPL-2.1",
        "lgpl3": "LGPL-3.0",
        "lgplv3": "LGPL-3.0",
        "agpl": "AGPL-3.0",
        "agpl3": "AGPL-3.0",
        "agplv3": "AGPL-3.0",
        "agpl 3.0": "AGPL-3.0",
        "mpl": "MPL-2.0",
        "mpl2": "MPL-2.0",
        "mpl 2.0": "MPL-2.0",
        "mozilla public license 2.0": "MPL-2.0",
        "proprietary": "Proprietary",
        "commercial": "Proprietary",
        "closed": "Proprietary",
        "closed source": "Proprietary",
        "all rights reserved": "Proprietary",
        "public domain": "public-domain",
        "pd": "public-domain",
        "cc0": "public-domain",
        "cc0-1.0": "public-domain",
        "the unlicense": "Unlicense",
        "unlicensed": "Unlicense",  # note: differs from a missing license
        "isc license": "ISC",
        "mit license": "MIT",
        "x11": "MIT",
        "expat": "MIT",
    }
)

# Values that explicitly mean "no license declared" -> unknown, not an alias
_UNDECLARED = {"", "unknown", "none", "n/a", "na", "-", "?", "unspecified", "see license"}


def normalize_license(value: Optional[str]) -> Tuple[Optional[str], bool]:
    """Return ``(canonical_license_or_None, recognized)``."""
    if value is None:
        return None, False
    key = " ".join(str(value).strip().lower().split())
    if key in _UNDECLARED:
        return None, False
    canon = _ALIASES.get(key)
    if canon:
        return canon, True
    # Already an exact SPDX id with different case
    for lic in ALL_LICENSES:
        if key == lic.lower():
            return lic, True
    return str(value).strip(), False


# ---------------------------------------------------------------------------
# Compatibility evaluation
# ---------------------------------------------------------------------------

_POLYFORMS = PERMISSIVE + PUBLIC  # permissive + public domain
_GPL2_OK = set(_POLYFORMS + ["LGPL-2.1"])
_GPL3_OK = set(_POLYFORMS + ["GPL-3.0", "LGPL-3.0", "MPL-2.0", "AGPL-3.0"])
_AGPL3_OK = set(_POLYFORMS + ["GPL-3.0", "LGPL-3.0", "MPL-2.0", "AGPL-3.0"])


def evaluate(consumer: Optional[str], dependency: Optional[str]) -> Tuple[str, Optional[str]]:
    """Return ``(verdict, reason)`` for one directed edge semantics."""
    if consumer is None or dependency is None:
        missing: List[str] = []
        if consumer is None:
            missing.append("consumer")
        if dependency is None:
            missing.append("dependency")
        return (
            "unknown",
            f"License of {' and '.join(missing)} is unspecified or unrecognized; manual review required.",
        )
    if consumer not in CATEGORY_OF or dependency not in CATEGORY_OF:
        return (
            "unknown",
            f"Unrecognized license pair ({consumer} -> {dependency}); manual review required.",
        )

    # A package under a proprietary license ...
    if consumer == "Proprietary":
        if dependency in ("GPL-2.0", "GPL-3.0", "AGPL-3.0"):
            return (
                "conflict",
                f"Proprietary code cannot be combined with {dependency} (strong copyleft obligations are incompatible with closed distribution).",
            )
        if dependency in ("LGPL-2.1", "LGPL-3.0"):
            return (
                "conflict",
                f"{dependency} code linked into a Proprietary work requires relink/dynamic-linking conditions that are not satisfied in this manifest; flagged for review.",
            )
        return ("allow", None)

    # AGPL consumer may only absorb AGPL-compatible code.
    if consumer == "AGPL-3.0":
        if dependency in _AGPL3_OK or dependency == "Proprietary":
            # Proprietary dependency code added to AGPL work: allowed
            # (the combined source must still be disclosed, which is the
            # downstream problem; commonly treated as allow/needs-review).
            return ("allow", None)
        return (
            "conflict",
            f"{dependency} is not compatible with AGPL-3.0 (copyleft one-way / weak-copyleft file scope mismatch).",
        )

    # GPL-2.0 consumer.  Check the specific one-way incompatibilities BEFORE
    # the broad permissive allow-list (which otherwise swallows Apache-2.0).
    if consumer == "GPL-2.0":
        if dependency == "Apache-2.0":
            return (
                "conflict",
                "Apache-2.0 patent termination clause is incompatible with GPL-2.0-only (use GPL-2.0-or-later / GPL-3.0 to combine).",
            )
        if dependency == "GPL-3.0":
            return (
                "conflict",
                "GPL-3.0 code cannot be combined into a GPL-2.0-only work (one-way upgrade path).",
            )
        if dependency in ("LGPL-3.0", "MPL-2.0"):
            return (
                "conflict",
                f"{dependency} is not compatible with GPL-2.0-only.",
            )
        if dependency == "AGPL-3.0":
            return ("conflict", "AGPL-3.0 and GPL-2.0 are not compatible.")
        if dependency == "Proprietary":
            return ("conflict", "GPL-2.0 code cannot incorporate Proprietary code.")
        if dependency in _GPL2_OK:
            return ("allow", None)
        return ("allow", None)

    # GPL-3.0 consumer
    if consumer == "GPL-3.0":
        if dependency in _GPL3_OK:
            return ("allow", None)
        if dependency == "GPL-2.0":
            return ("allow", None)  # GPLv2 code can be upgraded into a GPLv3 project
        if dependency == "LGPL-2.1":
            return ("allow", None)  # LGPL-2.1 is upgradable to GPLv3 combination
        if dependency == "Proprietary":
            return ("conflict", "GPL-3.0 code cannot incorporate Proprietary code.")
        return ("conflict", f"{dependency} is not compatible with GPL-3.0.")

    # LGPL consumers
    if consumer in ("LGPL-2.1", "LGPL-3.0"):
        if dependency == "Proprietary":
            return ("allow", None)  # LGPL works may use proprietary libs (library side)
        if consumer == "LGPL-2.1" and dependency in (
            "GPL-3.0",
            "LGPL-3.0",
            "AGPL-3.0",
            "Apache-2.0",
            "MPL-2.0",
        ):
            return ("conflict", f"{dependency} cannot be downgraded into an LGPL-2.1 work.")
        if consumer == "LGPL-3.0":
            if dependency in ("GPL-2.0", "LGPL-2.1"):
                return (
                    "conflict",
                    f"{dependency} terms cannot be combined into an LGPL-3.0 work (version downgrade).",
                )
        return ("allow", None)

    # MPL consumer: file-level copyleft, may combine with almost anything
    # except it cannot absorb GPL-incompatible ... MPL 2.0 is GPL compatible
    # both ways at the file boundary.
    if consumer == "MPL-2.0":
        return ("allow", None)

    # Permissive / public-domain consumers absorb everything commonly,
    # except a Proprietary blob is still distributable alongside.
    return ("allow", None)


def build_matrix() -> Dict[str, Dict[str, Dict[str, Optional[str]]]]:
    """Full matrix: matrix[consumer][dependency] = {verdict, reason}."""
    matrix: Dict[str, Dict[str, Dict[str, Optional[str]]]] = {}
    for c in ALL_LICENSES:
        matrix[c] = {}
        for d in ALL_LICENSES:
            verdict, reason = evaluate(c, d)
            matrix[c][d] = {"verdict": verdict, "reason": reason}
    return matrix


def categories() -> List[Dict[str, object]]:
    return [
        {"name": "public-domain", "licenses": PUBLIC},
        {"name": "permissive", "licenses": PERMISSIVE},
        {"name": "weak-copyleft", "licenses": WEAK_COPYLEFT},
        {"name": "copyleft", "licenses": COPYLEFT},
        {"name": "network-copyleft", "licenses": NETWORK_COPYLEFT},
        {"name": "proprietary", "licenses": PROPRIETARY},
    ]
