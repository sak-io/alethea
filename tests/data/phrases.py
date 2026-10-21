"""Fixture phrases pinning the scoring marker lists (R12, G23)."""

# (marker name, phrase that must trigger it; each >= 4 words)
AMYGDALA_HITS = [
    ("absolute", "you must never commit secrets here"),
    ("absolute", "always run the linter first please"),
    ("incident", "that deploy broke the whole checkout"),
    ("incident", "there was an outage last night"),
    ("incident", "we had data loss on the replica"),
    ("critical", "this is a critical path for billing"),
    ("critical", "urgent fix needed for the login page"),
    ("critical", "a security hole in the upload handler"),
    ("correction", "no, that's wrong about the schema"),
    ("decision", "we decided to use sqlite for storage"),
    ("remember", "please remember this for the next session"),
    ("error", "the build throws an error on startup"),
    ("deadline", "the deadline for the beta is close"),
    ("causal", "tests flaked because the clock drifted"),
    ("deprecation", "the v1 endpoint is deprecated now"),
    ("emphasis", "do NOT TOUCH the production config"),
]

# Plain chatter: amygdala must be 0.
NEUTRAL = [
    "sounds good thanks",
    "ok let me look at that file",
    "the weather is nice today in the office",
]

# (text, quantitative?) - unit handling, longest suffix first.
QUANTITY = [
    ("the request took 250 ms on average", True),
    ("latency is 3 seconds at p99", True),
    ("cache hit rate reached 95%", True),
    ("the plan costs $20 per seat", True),
    ("we serve 1200 rps at peak", True),
    ("it waited for 5 minutes then retried", True),
    ("we have 3 seconds-ish of grace", True),
    ("the word seconds alone is not a number", False),
    ("version 5 sails along nicely", False),
    ("we met on the 5th of may", False),
]

# (text, expected kind)
CLASSIFY = [
    ("I'm the tech lead for the payments team", "identity"),
    ("my role is release manager", "identity"),
    ("run the migrations then restart the worker", "procedural"),
    ("to deploy, push a tag and wait for CI", "procedural"),
    ("the api uses sqlite because it is embedded", "semantic"),
    ("we decided on postgres for analytics", "semantic"),
    ("went for a walk and chatted with bob", "episodic"),
    ("lunch was late today", "episodic"),
]

# Durable without affect: (text) with amygdala 0 but durability >= 0.45.
DURABLE_NO_AFFECT = [
    "I'm the tech lead for the billing service",
    "the p99 latency is 250 ms for search",
    "naming convention for branches is feat slash slug",
]
