"""Fixture table for the adversarial dedup suite (R10, R11).

Each row: (category, a, b). MERGE rows must score >= NEAR_DUPLICATE;
every other category must score < NEAR_DUPLICATE (false negatives are safe,
false merges are silent and permanent).
"""

MERGE = "restatement"

PAIRS = [
    # swapped entities
    ("entity", "deploy the api to staging before release", "deploy the api to prod before release"),
    ("entity", "the staging database uses postgres", "the prod database uses postgres"),
    ("entity", "alice owns the billing service", "bob owns the billing service"),
    # negation
    ("negation", "deploys require approval", "deploys require no approval"),
    ("negation", "the build must pass before merge", "the build must not pass before merge"),
    ("negation", "we use redis for caching", "we do not use redis for caching"),
    ("negation", "the api is thread safe", "the api isn't thread safe"),
    ("negation", "retries are enabled", "retries are never enabled"),
    # swapped identifiers
    ("identifier", "restart svc0000 after the deploy", "restart svc0101 after the deploy"),
    ("identifier", "the client calls the v1 endpoint", "the client calls the v2 endpoint"),
    ("identifier", "upload artifacts to s3 nightly", "upload artifacts to s4 nightly"),
    ("identifier", "this bug is p0 for the release", "this bug is p1 for the release"),
    ("identifier", "config lives in /etc/alethea/app.conf", "config lives in /opt/alethea/app.conf"),
    ("identifier", "call getUserName before saving", "call getUserId before saving"),
    ("identifier", "set maxRetries to the default", "set minRetries to the default"),
    # antonyms
    ("antonym", "enable auth on the gateway", "disable auth on the gateway"),
    ("antonym", "allow anonymous reads on the bucket", "deny anonymous reads on the bucket"),
    ("antonym", "add the cache layer to the service", "remove the cache layer from the service"),
    ("antonym", "turn on verbose logging for the worker", "turn off verbose logging for the worker"),
    # true restatements (must merge)
    (MERGE, "the deploy pipeline requires manual approval before production",
     "the deploy pipeline requires manual approval before production"),
    (MERGE, "Always run the migration before deploying the api.",
     "always run the migration before deploying the api"),
    (MERGE, "we use postgres for the primary datastore",
     "We use postgres for the primary datastore."),
    # unrelated
    ("unrelated", "the cat sat on the warm windowsill", "quarterly revenue grew by twelve percent"),
    ("unrelated", "rotate the signing keys every ninety days", "the office plants need watering on fridays"),
]

# Regression: blending 0.70 / 0.60 as (cos + jac) / 1.4 gives 0.93.
BLEND_COSINE, BLEND_JACCARD = 0.70, 0.60

# Long-sentence pairs: a single swapped/added token in a ~20+ token sentence.
# Raw max(cosine, jaccard) is >= 0.88 here, so ONLY the vetoes keep them apart.
_PRE = "when the release engineering team prepares the weekly rollout for the platform group they"
_POST = "before the final sign off happens on friday afternoon"


def _long(cat, x, y):
    return (cat, f"{_PRE} {x} {_POST}", f"{_PRE} {y} {_POST}")


LONG_PAIRS = [
    _long("entity", "deploy to staging", "deploy to prod"),
    _long("entity", "run the load test on staging", "run the load test on prod"),
    _long("entity", "page alice about incidents", "page bob about incidents"),
    _long("negation", "require approval", "require no approval"),
    _long("negation", "must pass review", "must not pass review"),
    _long("negation", "use the cache", "do not use the cache"),
    _long("antonym", "enable auth", "disable auth"),
    _long("antonym", "allow uploads", "deny uploads"),
    _long("antonym", "add caching", "remove caching"),
    _long("v1v2", "call the v1 endpoint", "call the v2 endpoint"),
    _long("v1v2", "migrate to the v1 schema", "migrate to the v2 schema"),
    _long("v1v2", "pin the v1 client", "pin the v2 client"),
    _long("s3", "upload to s3", "upload to s4"),
    _long("s3", "mirror the bundle to s3", "mirror the bundle to s5"),
    _long("s3", "archive logs in s3", "archive logs in s9"),
    _long("p0", "triage the p0 bug", "triage the p1 bug"),
    _long("p0", "escalate any p0 ticket", "escalate any p2 ticket"),
    _long("p0", "page oncall for p0 alerts", "page oncall for p3 alerts"),
    _long("path", "edit /etc/alethea/app.conf", "edit /opt/alethea/app.conf"),
    _long("path", "read src/core/main.py", "read src/util/main.py"),
    _long("path", "delete /var/log/app/out.log", "delete /var/tmp/app/out.log"),
    _long("camel", "call getUserName", "call getUserId"),
    _long("camel", "set maxRetries", "set minRetries"),
    _long("camel", "rename fooBar", "rename fooBaz"),
]
