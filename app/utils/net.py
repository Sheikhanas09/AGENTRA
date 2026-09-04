"""
Reaching Google when IPv6 is advertised but does not work
─────────────────────────────────────────────────────────
Symptom, on a machine where everything else was fine: connecting a
Google account succeeded, and then every call that used it hung and
timed out. Fetching applications returned 500, the interview email never
sent, and the Meet link fell back to the placeholder — all with a
correctly connected account and all three scopes granted.

The cause was not in this application at all:

    www.googleapis.com   ->  8 IPv6 addresses, THEN 8 IPv4 addresses
                             every IPv6 address times out
                             the first IPv4 address connects in 0.0s

The network advertises IPv6 and cannot route it. `getaddrinfo` returns
the IPv6 records first, Python tries them IN ORDER, and eight timeouts
take far longer than the HTTP client's own timeout — so the IPv4
addresses further down the list are never reached.

It also explains why the OAuth handshake worked and nothing afterwards
did: `oauth2.googleapis.com` returned a single IPv6 address, so the
fallback had time to happen. `gmail.googleapis.com` returns eight.

═══════════════════════════════════════════════════════════
SORTING, NOT FILTERING
═══════════════════════════════════════════════════════════
This reorders the results of `getaddrinfo` so IPv4 comes first. It does
NOT remove the IPv6 addresses.

That distinction is the whole reason this is safe to leave on. On a
healthy dual-stack network both work and the order costs nothing. On a
network with broken IPv6 it saves the feature. And on an IPv6-ONLY
network — where there are no IPv4 records to promote — the list is
unchanged and everything still works. Filtering IPv6 out would break
that last case, silently, on somebody else's machine.

Set `PREFER_IPV4=false` in `.env` to switch it off.
"""

import os
import socket

from dotenv import load_dotenv

load_dotenv()

_installed = False
_original = socket.getaddrinfo


def enabled() -> bool:
    return os.getenv("PREFER_IPV4", "true").strip().lower() not in (
        "false", "0", "no", "off"
    )


def prefer_ipv4() -> bool:
    """
    Try IPv4 addresses before IPv6 for every outbound connection in this
    process. Returns whether it is now active.

    Called once at startup — from `main.py` for the API, and from the MCP
    server, which is a SEPARATE process and does its own Calendar and
    Gmail calls. A fix installed in only one of them fixes only half the
    symptoms, which is a confusing place to end up.
    """
    global _installed

    if _installed or not enabled():
        return _installed

    def getaddrinfo_v4_first(*args, **kwargs):
        results = _original(*args, **kwargs)
        # `sorted` is stable, so IPv4 and IPv6 each keep the order the
        # resolver gave them — Google's rotation between its own hosts is
        # preserved, only the families are grouped.
        return sorted(results, key=lambda r: 0 if r[0] == socket.AF_INET else 1)

    socket.getaddrinfo = getaddrinfo_v4_first
    _installed = True
    return True


def describe(host: str = "www.googleapis.com", port: int = 443) -> str:
    """One line about how this host resolves — for start-up logs and checks."""
    try:
        infos = _original(host, port, proto=socket.IPPROTO_TCP)
    except Exception as e:                                      # noqa: BLE001
        return f"{host}: DNS failed ({e})"
    v4 = sum(1 for i in infos if i[0] == socket.AF_INET)
    v6 = len(infos) - v4
    first = "IPv6" if infos and infos[0][0] == socket.AF_INET6 else "IPv4"
    return (f"{host}: {v4} IPv4, {v6} IPv6, resolver returns {first} first"
            f"{' — IPv4 preferred' if _installed else ''}")


def reachable(host: str, port: int = 443, timeout: float = 3.0):
    """
    Whether a TCP connection to `host` completes, and over which family.

    Tries ONE address per family, not every address. A first version
    walked the whole list, which on the very network this module exists
    for meant eight IPv6 timeouts per host — the diagnostic took longer
    than the problem it was diagnosing.

    Uses the ORIGINAL resolver deliberately, so it reports what the
    machine can actually do rather than inheriting the preference
    installed above. Returns (ok, description) where the description
    names both families, because "IPv4 yes, IPv6 no" is the finding.
    """
    try:
        infos = _original(host, port, proto=socket.IPPROTO_TCP)
    except Exception as e:                                      # noqa: BLE001
        return False, f"DNS failed: {e}"

    out, ok = [], False
    for family, label in ((socket.AF_INET, "IPv4"), (socket.AF_INET6, "IPv6")):
        first = next((i for i in infos if i[0] == family), None)
        if not first:
            continue
        s = socket.socket(family, socket.SOCK_STREAM)
        s.settimeout(timeout)
        try:
            s.connect(first[4])
            out.append(f"{label} ok")
            ok = True
        except Exception:                                       # noqa: BLE001
            out.append(f"{label} unreachable")
        finally:
            s.close()

    return ok, ", ".join(out) or "no addresses"
