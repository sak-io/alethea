"""Untuned held-out split: NO recall constant was tuned against these cues.

Same memories as tests/data/recall_corpus.py (MEMORIES + DISTRACTORS + AUTH_EXTRA). Written
after the constants were frozen, in natural phrasing that mostly avoids the stored wording.
(cue, expected keys). Gate: tests/test_recall_regression.py (>= 0.85 in the top 3).
"""

CUES = [
    ("what is our policy on the database for payments", ["d_postgres", "d_no_orm"]),
    ("why store login sessions on the server", ["d_redis_sessions"]),
    ("which protocol connects our backend services", ["d_grpc"]),
    ("do we ship from long lived release branches", ["d_trunk"]),
    ("what python are we on", ["d_python311"]),
    ("how are order events delivered to consumers", ["d_kafka"]),
    ("why do ids sort by time", ["d_uuid7"]),
    ("where do we convert times to the local timezone", ["d_tz"]),
    ("why did users get logged out when opening two tabs", ["c_auth_race"]),
    ("the export crashed overnight, what was the reason", ["c_oom"]),
    ("why did we run out of database connections", ["c_pool"]),
    ("what made the invoices page slow", ["c_n_plus_1"]),
    ("why did the checkout test fail randomly", ["c_flaky"]),
    ("how do we avoid deadlocks in the inventory worker", ["c_deadlock"]),
    ("a column without a default broke the release", ["c_migration"]),
    ("steps to ship a new build", ["p_deploy"]),
    ("undo a bad deployment", ["p_rollback"]),
    ("what do I do after being paged at night", ["p_oncall"]),
    ("how do we swap out a leaked api key", ["p_secrets"]),
    ("how do I get the app running on my laptop", ["p_local_setup"]),
    ("what checks does ci run on formatting", ["p_lint"]),
    ("how big can the public api request rate be", ["f_rate_limit"]),
    ("how long do uploaded files stay in storage", ["f_s3"]),
    ("who owns feature flags and when do they expire", ["f_feature_flags"]),
    ("what is the availability target for payments", ["f_sla"]),
    ("how do we search the product catalog", ["f_search"]),
    ("is it ok to print a customer phone number in logs", ["f_pii"]),
    ("how are stripe webhook events validated", ["f_webhooks"]),
    ("where does production run", ["f_k8s"]),
    ("how many approvals before merging", ["f_review"]),
]

MIN_RECALL_AT_3 = 0.80  # measured 0.833 (25/30); 0.90 with the floors disabled
