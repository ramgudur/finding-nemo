"""`agents/budget.py`: how agents share one provider's rate budget under load (#221).

Every agent shares one token bucket per provider (`LlmRouter`, ~40 RPM on the free tier). With
several active apps, each with a Critic and a POI Labeler running on intervals and triggers, plus
the Scout and dev compiles, that bucket can run dry. Two policies keep the platform degrading
gracefully instead of skipping runs mid-way:

- **Admission before a run** (`LlmRouter.admit`): a scheduler starts an episode only if the bucket
  holds enough tokens for the whole run (`EST_CALLS`) plus headroom for its priority. Compiles (a
  dev is waiting) need no headroom; triggered runs leave `HEADROOM["triggered"]` of capacity;
  scheduled checks leave `HEADROOM["scheduled"]`. So routine checks back off first and never
  starve a compile or a triggered run, and a run rarely dies half-way. A run that isn't admitted
  is deferred to the next poll, not skipped.
- **Scaled intervals** (`scaled_interval`): an agent's scheduled interval stretches with the number
  of active apps so its planned scheduled load stays within `SCHEDULED_SHARE` of the RPM.

All values are neutral defaults (module constants, no env setting).
"""

from __future__ import annotations

from typing import Literal

Priority = Literal["interactive", "triggered", "scheduled"]

# fraction of bucket capacity a run must leave untouched to be admitted
HEADROOM: dict[Priority, float] = {"interactive": 0.0, "triggered": 0.15, "scheduled": 0.40}

# typical model calls per episode (iteration caps are higher; these are what runs usually take)
EST_CALLS: dict[str, int] = {"critic": 4, "poi": 4, "scout": 6, "compiler": 4}

# share of the provider's RPM each agent's *scheduled* checks may plan for, across all apps
SCHEDULED_SHARE: dict[str, float] = {"critic": 0.30, "poi": 0.20}


def scaled_interval(base_s: float, n_apps: int, agent: str, rpm: float) -> float:
    """Seconds between scheduled runs per app: at least `base_s`, stretched so that `n_apps`
    apps' scheduled runs of `agent` use at most its `SCHEDULED_SHARE` of `rpm`."""
    share = SCHEDULED_SHARE.get(agent, 0.2)
    if n_apps <= 0 or rpm <= 0 or share <= 0:
        return base_s
    needed = 60.0 * n_apps * EST_CALLS.get(agent, 4) / (share * rpm)
    return max(base_s, needed)
