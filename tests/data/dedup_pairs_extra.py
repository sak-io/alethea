"""Extra adversarial pairs: word-order swaps and case-only identifier differences.

Long (~15+ token) variants whose raw max(cosine, jaccard) is >= 0.88, so only a
veto keeps them apart. Same contract as dedup_pairs: all must score < NEAR_DUPLICATE.
"""

_PRE = "when the release engineering team prepares the weekly rollout for the platform group they"
_POST = "before the final sign off happens on friday afternoon"


def _long(cat, x, y):
    return (cat, f"{_PRE} {x} {_POST}", f"{_PRE} {y} {_POST}")


SHORT_PAIRS = [
    ("order", "Alice reports to Bob in the platform team", "Bob reports to Alice in the platform team"),
    ("case", "Use getUserName in the profile module", "Use getUsername in the profile module"),
    ("order", "A depends on B", "B depends on A"),
    ("case", "allow user_id", "allow userId"),
]

LONG_PAIRS_EXTRA = [
    _long("order", "make alice report to bob", "make bob report to alice"),
    _long("order", "migrate from mysql to postgres", "migrate from postgres to mysql"),
    _long("order", "note that service a depends on service b", "note that service b depends on service a"),
    _long("order", "copy the data from staging into prod", "copy the data from prod into staging"),
    _long("order", "let the frontend call the backend", "let the backend call the frontend"),
    _long("case", "call getUserName first", "call getUsername first"),
    _long("case", "rename user_id to account", "rename userId to account"),
    _long("case", "read the maxRetries setting", "read the maxretries setting"),
    _long("case", "pass the AuthToken along", "pass the Authtoken along"),
]

LONG_PAIRS_EXTRA += [
    _long("ident", "call get_user", "call get_users"),
    _long("ident", "call get_user", "call getUser"),
    _long("ident", "set max-retries", "set max-retry"),
    _long("ident", "read foo.bar", "read foo.bars"),
    _long("ident", "export DEFAULT_TIMEOUT", "export DEFAULT_TIMEOUTS"),
    _long("ident", "use the UserProfile", "use the UserProfiles"),
]
SHORT_PAIRS.append(("ident", "Call get_user before rendering the profile page",
                    "Call get_users before rendering the profile page"))
