"""Execution policies for `bta check` runs.

Two policies are enforced at run time:

OriginPolicy — controls which URLs the browser agent may navigate to.
  Only top-level navigation (open_url, go_back) is restricted; subresource
  requests made by the page itself (CDN images, fonts, analytics) are
  outside Playwright's navigation API and are not blocked here.  This is an
  action-level enforcement policy, not a network-level firewall — the server
  is not guaranteed to be isolated.

MutationPolicy — controls whether scenarios that modify persistent data may
  run.  When mutations are disabled a scenario marked requires_mutations=True
  is BLOCKED before execution begins.  This is reported as a policy decision;
  it does not guarantee the server is read-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from core.plan import Scenario


# ── Origin policy ─────────────────────────────────────────────────────────────

@dataclass
class OriginPolicy:
    """Restricts which origins the browser agent may navigate to.

    An empty allowed_origins list means no restriction (all origins allowed).
    The list is populated from BTA_ALLOWED_ORIGINS, defaulting to the origin
    of BTA_BASE_URL.

    Subresource requests (images, scripts, fonts loaded by the page) are not
    blocked — this policy applies only to top-level navigation actions.
    BrowserSession also applies this policy to top-level frame navigations,
    including popup pages and redirects.
    """

    allowed_origins: list[str]

    def check_navigation(self, url: str) -> tuple[bool, str]:
        """Check whether a top-level navigation to *url* is permitted.

        Returns:
            (True, reason)  when allowed.
            (False, reason) when blocked.
        """
        if not self.allowed_origins:
            return True, "no origin restrictions configured"
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False, f"URL {url!r} does not have a recognisable origin"
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self.allowed_origins:
            return True, f"origin {origin!r} is in the allowed list"
        allowed_str = ", ".join(self.allowed_origins)
        return False, (
            f"navigation to {origin!r} is outside the allowed origins "
            f"({allowed_str}) — this is an action policy, not a network firewall"
        )

    def is_subresource_allowed(self, url: str) -> bool:
        """Subresource requests (CDN assets) are always allowed.

        Top-level navigation is checked via check_navigation(); subresource
        requests made by the page (images, scripts, XHR) are not controlled
        by this policy — they go through the browser's own network stack.
        This method exists to make the distinction explicit for callers.
        """
        return True


# ── Mutation policy ───────────────────────────────────────────────────────────

@dataclass
class MutationPolicy:
    """Controls whether scenarios that modify persistent data may execute.

    When allow_mutations=False, any scenario with requires_mutations=True is
    BLOCKED before execution begins.  This is a planning-time policy decision
    based on the scenario's declared mutation requirement; it does not
    guarantee the application server is read-only.

    Authentication actions (login, storage state) are always permitted
    regardless of this policy.
    """

    allow_mutations: bool

    def check_scenario(self, scenario: Scenario) -> tuple[bool, str]:
        """Check whether *scenario* is permitted to run under this policy.

        Returns:
            (True, "")        when allowed.
            (False, reason)   when blocked.
        """
        if not scenario.requires_mutations:
            return True, ""
        if self.allow_mutations:
            return True, "mutation scenario permitted by BTA_ALLOW_MUTATIONS=true"
        return False, (
            f"scenario {scenario.id!r} ({scenario.title!r}) requires mutations "
            f"but BTA_ALLOW_MUTATIONS is false — this is an action policy, not a "
            f"guarantee that the server is read-only"
        )
