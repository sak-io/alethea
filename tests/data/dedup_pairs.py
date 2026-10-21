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
    ("identifier", "config lives in /etc/engram/app.conf", "config lives in /opt/engram/app.conf"),
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
