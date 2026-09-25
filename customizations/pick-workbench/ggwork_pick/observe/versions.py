"""Version names frozen into every observation set (design 7.1; plan D5, D29, D36).

Three layers move independently: the collection contract (COLLECTOR_VERSION), the judgement rules below, and the
candidate engine's RULE_VERSION and ranking versions in ggwork_pick.selection (the `obs-v1` ranking belongs there, not
here). A rule change ships as a new name next to the old one in its module's dispatch table, never as an edit of what
an old name means: sets and cards keep the version they were judged with.
"""

# The collection contract: a new Trends source or a changed request shape is a new collector version, and sets from
# different collector versions never confirm one another (design 4.11). The self-check compares it with
# PICK_OBS_EXPECTED_COLLECTOR before any HTTP (D5).
COLLECTOR_VERSION = "obs-collector-v1"

TREND_RULES_VERSION = "trend-rules-v1"  # design 4.9; the full text is fixed by the stage 0 report (TR-05)
GSC_RULES_VERSION = "gsc-rules-v1"  # design 5.8, with the quality note method of D38
LINK_RULES_VERSION = "link-rules-v1"  # design 6.1, D13, D39
WATCH_RULES_VERSION = "watch-rules-v1"  # design 4.6
MARKET_MAP_VERSION = "market-map-v1"  # design 1.3
EVAL_RULES_VERSION = "eval-rules-v1"  # design 6.2, D37

# The oldest private migration the observe tables exist in. The self-check also requires the production head to be a
# revision this image's migration chain knows (D5): a newer known head passes, an unknown one exits 2.
MIN_MIGRATION_HEAD = "0007"
