"""HTTP API + page for Prospect Studio. The same Handler runs locally (src/webapp.py) and on Vercel
(api/index.py). Every request is self-contained: no background threads, no in-memory jobs.

Auth: when APP_PASSWORD is set (always on Vercel), every /api call needs the session cookie from
POST /api/login. The scheduler calls POST /api/agent/tick with `Authorization: Bearer $CRON_SECRET`.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
import traceback
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .appdata import AppData
from .config import env, load_env, load_icp
from .draft_email import render_for_review
from .feedback import approved_lessons, propose_lessons
from .shortlist import load_exclusions, normalise

PAGE = Path(__file__).parent / "web" / "index.html"
SESSION_DAYS = 7
MAX_BODY_BYTES = 1_000_000
VERSION = "2.0.0"
_ICP = None
_tl = threading.local()             # stores opened during one request, closed when it ends
_seed_lock = threading.Lock()
_seeded = False


def icp() -> dict:
    global _ICP
    if _ICP is None:
        load_env()
        cfg = load_icp()
        cfg["llm"] = resolve_llm(cfg["llm"])
        _ICP = cfg
    return _ICP


def resolve_llm(llm_cfg: dict) -> dict:
    """Environment overrides for the model provider, so a deployment can switch without editing YAML.
    LLM_PROVIDER=gemini|anthropic wins; otherwise a config asking for Gemini with only an Anthropic key
    set falls over to Anthropic (and vice versa) instead of failing every call."""
    out = dict(llm_cfg)
    want = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if want in ("gemini", "anthropic"):
        out["provider"] = want
    else:
        has_g, has_a = bool(env("GEMINI_API_KEY")), bool(env("ANTHROPIC_API_KEY"))
        if out.get("provider") == "gemini" and not has_g and has_a:
            out["provider"] = "anthropic"
        elif out.get("provider") == "anthropic" and not has_a and has_g:
            out["provider"] = "gemini"
    if env("ANTHROPIC_MODEL"):
        out["model"] = env("ANTHROPIC_MODEL")
    if env("GEMINI_MODELS"):
        out["gemini_models"] = [m.strip() for m in env("GEMINI_MODELS").split(",") if m.strip()]
    return out


def demo_mode() -> bool:
    """Demo mode: DEMO_MODE=1, or a deployment with no Postgres (its SQLite in /tmp is ephemeral, so an
    empty app would be all it could ever show). Seeds fictional data and opens read access."""
    if os.environ.get("DEMO_MODE") in ("1", "true", "yes"):
        return True
    if os.environ.get("DEMO_MODE") in ("0", "false", "no"):
        return False
    return bool(os.environ.get("VERCEL")) and not os.environ.get("DATABASE_URL")


def data() -> AppData:
    global _seeded
    db = AppData(run_id=datetime.now().strftime("%Y%m%d-%H%M%S"))
    opened = getattr(_tl, "open", None)
    if opened is not None:
        opened.append(db)
    if demo_mode() and not _seeded:
        with _seed_lock:
            if not _seeded:
                from .demo import seed
                try:
                    seed(db)
                finally:
                    _seeded = True
    return db


def _close_request_stores() -> None:
    for db in getattr(_tl, "open", None) or []:
        db.close()
    _tl.open = []


# ------------------------------------------------------------------ what's configured
def integrations() -> dict:
    """Which outside services this deployment can reach. Booleans only; never a secret."""
    llm = icp()["llm"]
    provider = llm.get("provider", "anthropic")
    llm_ok = bool(env("GEMINI_API_KEY") if provider == "gemini" else env("ANTHROPIC_API_KEY"))
    google = bool(os.environ.get("GOOGLE_TOKEN_JSON"))
    if not google:
        from .google_client import TOKEN_FILE
        google = TOKEN_FILE.exists()
    return {
        "llm": {"ok": llm_ok, "provider": provider, "needs": "GEMINI_API_KEY" if provider == "gemini" else "ANTHROPIC_API_KEY",
                "used_for": "Reading sources, planning research, drafting and classifying replies"},
        "hunter": {"ok": bool(env("HUNTER_API_KEY")), "needs": "HUNTER_API_KEY",
                   "used_for": "Finding and verifying decision-maker emails"},
        "database": {"ok": bool(os.environ.get("DATABASE_URL")), "needs": "DATABASE_URL",
                     "used_for": "Keeping accounts, threads and lessons between requests (Postgres / Supabase)"},
        "google": {"ok": google, "needs": "GOOGLE_TOKEN_JSON",
                   "used_for": "Sending drafts to your inbox, reading replies, checking and booking your calendar"},
        "own_email": {"ok": bool(env("OWN_EMAIL")), "needs": "OWN_EMAIL",
                      "used_for": "The only address the system may ever send to"},
        "crm": {"ok": bool(env("HUBSPOT_TOKEN")), "needs": "HUBSPOT_TOKEN", "optional": True,
                "used_for": "Logging outcomes on HubSpot company records"},
        "scheduler": {"ok": bool(env("CRON_SECRET")), "needs": "CRON_SECRET", "optional": True,
                      "used_for": "Lets the GitHub Actions job check replies every 15 minutes"},
        "auth": {"ok": bool(env("APP_PASSWORD")) or open_access() or demo_mode(), "needs": "APP_PASSWORD",
                 "used_for": "Protects the workspace; OPEN_ACCESS=1 makes it public"},
    }


class NotConfigured(RuntimeError):
    pass


def require(*names: str) -> None:
    """Raise a clear, fixable error before an action that would fail on a missing integration."""
    st = integrations()
    missing = [st[n]["needs"] for n in names if not st[n]["ok"]]
    if missing:
        raise NotConfigured(f"Not configured yet: set {', '.join(missing)} in your environment "
                            f"(Vercel → Project → Settings → Environment Variables), then redeploy.")


# ------------------------------------------------------------------ helpers shared with the page
def match_seller(cfg: dict, name: str) -> dict | None:
    s = cfg.get("seller") or {}
    q = (name or "").strip().lower()
    names = {s.get("name", "").lower(), *(a.lower() for a in s.get("aliases", []))}
    return s if q and q in names else None


def _people(t: dict) -> list[dict]:
    c = t.get("contact") or {}
    out, seen = [], set()
    ch = c.get("chosen")
    if ch:
        out.append({"name": f"{ch.get('first_name', '')} {ch.get('last_name') or ''}".strip(),
                    "title": ch.get("position"), "email": ch.get("email"), "role": "primary",
                    "verification": "valid", "why": c.get("why"), "warning": ch.get("name_warning")})
        seen.add((ch.get("email") or "").lower())
    for r in c.get("considered") or []:
        e = (r.get("email") or "").lower()
        if not e or e in seen or r.get("rank") is None:
            continue
        seen.add(e)
        out.append({"name": r.get("name"), "title": r.get("position"), "email": r.get("email"), "role": "other",
                    "verification": r.get("verification"), "why": r.get("rejected") or "ranked title"})
    def same_person(a: str, b: str) -> bool:      # "Ana Rao" == "Ana Maria Rao"
        x, y = (a or "").lower().split(), (b or "").lower().split()
        return bool(x and y) and x[0] == y[0] and x[-1] == y[-1]

    for tr in (c.get("leader_fallback") or {}).get("tried", []):
        if not any(same_person(p["name"], tr["name"]) for p in out):
            out.append({"name": tr["name"], "title": tr["role"], "email": None, "role": "named in news",
                        "verification": tr.get("verification"), "why": f"Email Finder: {tr.get('finder')}"})
    return out


def account_summary(t: dict) -> dict:
    run = t.get("run_id") or ""
    em = t.get("email") or {}
    d = em.get("draft")
    return {
        "domain": t["domain"], "name": (t.get("company") or {}).get("name") or t["domain"],
        "status": t.get("final_status"), "reason": t.get("final_reason"),
        "run_date": f"{run[:4]}-{run[4:6]}-{run[6:8]}" if len(run) >= 8 else None,
        "gates": {g: {"status": v.get("status"), "reason": v.get("reason")} for g, v in (t.get("gates") or {}).items()},
        "score": ((t.get("qualification") or {}).get("weighted_score") or {}).get("total"),
        "people": _people(t),
        "draft": {"subject": d["subject"], "body": d["body"], "hook_url": d.get("hook_url"),
                  "hook_quote": d.get("hook_quote"), "lessons_used": d.get("lessons_used") or []} if d else None,
        "sent": (t.get("send") or {}).get("mode"),
    }


def sort_accounts(rows: list[dict]) -> list[dict]:
    order = {"go": 0, "needs_review": 1, "retry": 2, "no_go": 3}
    return sorted(rows, key=lambda a: (order.get(a["status"], 9), -(a["score"] or 0), a["name"].lower()))


GATE_LABELS = {"G1": "B2B software", "G5": "No SOC 2 / ISO yet", "G3": "100–1,000 staff",
               "G2": "Series A–C", "G4": "Compliance pressure (+3)"}


def overview_data(db: AppData) -> dict:
    """Numbers for the Overview screen: funnel, rejection reasons, attention items."""
    trails = db.list_trails(slim=True)
    threads = db.list_threads()
    by_status: dict[str, int] = {}
    rejected_by: dict[str, int] = {}
    for t in trails:
        st = t.get("final_status") or "unknown"
        by_status[st] = by_status.get(st, 0) + 1
        g = (t.get("qualification") or {}).get("failing_gate")
        if st == "no_go" and g:
            rejected_by[g] = rejected_by.get(g, 0) + 1
    replied = [x for x in threads if (x["state"].get("reply_status") or "no_reply") != "no_reply"]
    booked = [x for x in threads if x["state"].get("meeting_status") == "confirmed"]
    lessons = db.lessons()
    with_contact = sum(1 for t in trails if ((t.get("contact") or {}).get("chosen")))
    return {
        "funnel": [
            {"key": "researched", "label": "Researched", "n": len(trails)},
            {"key": "qualified", "label": "Qualified (go)", "n": by_status.get("go", 0)},
            {"key": "contact", "label": "Verified contact", "n": with_contact},
            {"key": "contacted", "label": "Emailed", "n": len(threads)},
            {"key": "replied", "label": "Replied", "n": len(replied)},
            {"key": "booked", "label": "Meeting booked", "n": len(booked)},
        ],
        "by_status": by_status,
        "rejected_by": [{"gate": g, "label": GATE_LABELS.get(g, g), "n": n}
                        for g, n in sorted(rejected_by.items(), key=lambda x: -x[1])],
        "attention": {
            "escalated": [{"domain": x["domain"], "company": x["state"].get("company"),
                           "reason": x["state"].get("escalation_reason")} for x in threads if x["status"] == "escalated"],
            "active_threads": sum(1 for x in threads if x["status"] == "active"),
            "pending_lessons": sum(1 for l in lessons if l["status"] == "pending"),
            "needs_review": by_status.get("needs_review", 0) + by_status.get("retry", 0),
        },
        "lessons": {"approved": sum(1 for l in lessons if l["status"] == "approved"),
                    "pending": sum(1 for l in lessons if l["status"] == "pending")},
        "feedback_items": len(db.feedback_rows(500)),
    }


def account_detail(db: AppData, domain: str) -> dict | None:
    t = db.get_trail(domain)
    if not t:
        return None
    facts = {f.get("id"): f for f in t.get("facts") or []}
    base = account_summary(t)
    gates = []
    for g, v in (t.get("gates") or {}).items():
        ev = [facts[i] for i in v.get("evidence_fact_ids") or [] if i in facts]
        gates.append({"id": g, "label": GATE_LABELS.get(g, g), "status": v.get("status"), "reason": v.get("reason"),
                      "evidence": [{"quote": f.get("quote"), "url": f.get("source_url"), "date": f.get("date"),
                                    "source_type": f.get("source_type")} for f in ev[:3]]})
    order = icp()["gate_order"]
    gates.sort(key=lambda x: order.index(x["id"]) if x["id"] in order else 99)
    r = t.get("research") or {}
    passes: dict[int, list] = {}
    for a in r.get("actions") or []:
        passes.setdefault(a.get("pass", 0), []).append({"action": a.get("action"), "outcome": a.get("outcome"),
                                                       "note": a.get("note")})
    planner = {p.get("pass"): {"decided_by": p.get("decided_by"), "reasoning": p.get("reasoning")}
               for p in t.get("planner_decisions") or []}
    q = t.get("qualification") or {}
    factors = [{"id": k, "name": v.get("name"), "points": v.get("points")}
               for k, v in ((q.get("weighted_score") or {}).get("factors") or {}).items()]
    d = (t.get("email") or {}).get("draft") or {}
    th = db.get_thread(domain)
    return {**base,
            "gates": gates,
            "score_factors": factors,
            "research": {"exit_reason": r.get("exit_reason"), "usage": r.get("usage"),
                         "passes": [{"pass": n, "actions": acts, **planner.get(n, {})} for n, acts in sorted(passes.items())],
                         "gate_history": r.get("gate_history"), "facts": len(facts),
                         "dropped": len(t.get("dropped_facts") or [])},
            "contact_why": (t.get("contact") or {}).get("why"),
            "email_detail": {"pain_points": d.get("pain_points") or [], "reasoning": d.get("reasoning"),
                             "cta": d.get("cta")} if d else None,
            "thread": ({"status": th["status"], "state": th["state"], "events": db.events(domain, 60)} if th else None)}


def agent_meta() -> dict:
    from .agent.graph import MAX_ROUNDS
    from .agent.tools import RETRY_TABLE, TOOL_DESCRIPTIONS
    return {"tools": [{"name": n, "description": d, "retry": RETRY_TABLE[n]} for n, d in TOOL_DESCRIPTIONS.items()],
            "max_rounds": MAX_ROUNDS, "no_reply_hours": float(os.environ.get("AGENT_NO_REPLY_HOURS", 48)),
            "caps": icp()["agent_loop"]}


def health() -> dict:
    """Liveness plus a database round-trip. Public: reveals only booleans."""
    db_ok, db_err = True, None
    try:
        with AppData() as db:
            db._x("SELECT 1").fetchone()
    except Exception as e:                       # report, don't crash the probe
        db_ok, db_err = False, type(e).__name__
    return {"ok": db_ok, "version": VERSION, "demo": demo_mode(),
            "database": "postgres" if os.environ.get("DATABASE_URL") else "sqlite",
            "database_ok": db_ok, "database_error": db_err,
            "integrations": {k: v["ok"] for k, v in integrations().items()}}


def settings_data() -> dict:
    cfg = icp()
    return {"integrations": integrations(), "demo": demo_mode(),
            "gates": [{"id": g, "name": cfg["gates"][g]["name"], "scoring_only": bool(cfg["gates"][g].get("scoring_only"))}
                      for g in cfg["gate_order"]],
            "weighted": [{"id": k, "name": v["name"], "weight": v["weight"]} for k, v in (cfg.get("weighted") or {}).items()],
            "caps": cfg["agent_loop"], "max_companies_per_run": cfg["run"]["max_companies_per_run"],
            "titles": cfg["contact"]["title_ranking"], "email": {"max_words": cfg["email"]["max_words"]},
            "llm": {"provider": cfg["llm"].get("provider"), "models": cfg["llm"].get("gemini_models")
                    if cfg["llm"].get("provider") == "gemini" else [cfg["llm"].get("model")]}}


# ------------------------------------------------------------------ auth
def _secret() -> bytes:
    s = os.environ.get("APP_SECRET") or ("pd:" + (os.environ.get("APP_PASSWORD") or ""))
    return hashlib.sha256(s.encode()).digest()


def make_session(ts: int | None = None) -> str:
    ts = ts or int(time.time())
    sig = hmac.new(_secret(), str(ts).encode(), hashlib.sha256).hexdigest()
    return f"{ts}.{sig}"


def open_access() -> bool:
    """OPEN_ACCESS=1 turns the password off: anyone with the link can use the app (owner's choice,
    2026-11-18). Sending still only ever reaches OWN_EMAIL. Remove the variable to require the password again."""
    return os.environ.get("OPEN_ACCESS") == "1"


def session_ok(cookie: str | None) -> bool:
    if open_access() or demo_mode():
        return True
    if not os.environ.get("APP_PASSWORD"):
        # Local use may skip the password; a deployment never runs open.
        return not os.environ.get("VERCEL")
    try:
        ts, sig = (cookie or "").split(".", 1)
        good = hmac.compare_digest(sig, hmac.new(_secret(), ts.encode(), hashlib.sha256).hexdigest())
        return good and time.time() - int(ts) < SESSION_DAYS * 86400
    except ValueError:
        return False


# ------------------------------------------------------------------ actions
_login_hits: dict[str, list[float]] = {}


def login_allowed(ip: str, window: int = 600, limit: int = 10) -> bool:
    """Best-effort brute-force brake: at most `limit` attempts per IP per window (per instance)."""
    nowt = time.time()
    hits = [t for t in _login_hits.get(ip, []) if nowt - t < window]
    if len(hits) >= limit:
        _login_hits[ip] = hits
        return False
    hits.append(nowt)
    _login_hits[ip] = hits
    if len(_login_hits) > 5000:                # keep memory bounded on long-lived instances
        _login_hits.clear()
    return True


def research_one(dom: str, name: str | None) -> dict:
    from .llm import make_llm
    from .pipeline import process_domain
    from .poc_hunter import account_credits, effective_quota
    from .research.fetch import Fetcher
    from .research.news import Throttle
    cfg = icp()
    excl = load_exclusions(cfg["run"]["shortlist_exclusions"])
    if dom in excl:
        return {"domain": dom, "status": "excluded", "reason": f"In {excl[dom]}"}
    require("llm")
    db = data()
    store_run = db.run_id
    db.start_run(True)
    lc = cfg["agent_loop"]
    remaining, _ = account_credits(env("HUNTER_API_KEY"))
    quota = effective_quota(db, cfg["contact"]["hunter_monthly_quota"], remaining, date.today())
    try:
        trail = process_domain(dom, cfg, fetcher=Fetcher(lc["user_agent"], lc["http_timeout_seconds"]),
                               llm=make_llm(cfg["llm"]), store=db, dry_run=True, review=False, today=date.today(),
                               news_throttle=Throttle(lc.get("news_min_interval_seconds", 2)),
                               company_name=name, hunter_quota=quota, print_fn=lambda *_: None,
                               lessons=approved_lessons(db))
    except Exception as e:
        trail = {"domain": dom, "run_id": store_run, "final_status": "needs_review",
                 "final_reason": f"pipeline error: {type(e).__name__}: {e}", "llm_calls": None,
                 "research": {"exit_reason": "error"}}
    trail.pop("send", None)                            # research never sends; sending is a separate click
    db.save_trail(trail)
    return {"domain": dom, "status": trail["final_status"], "reason": trail["final_reason"][:200]}


def send_draft(dom: str) -> dict:
    from .google_client import send_email
    require("google", "own_email")
    db = data()
    t = db.get_trail(dom)
    d, c = ((t or {}).get("email") or {}).get("draft"), ((t or {}).get("contact") or {}).get("chosen")
    if not d or not c:
        return {"ok": False, "error": "This account has no drafted email."}
    gates = "; ".join(f"{g} {v['status']}" for g, v in t["gates"].items())
    body = render_for_review(dom, c, d, t["contact"]["why"], gates)
    try:
        sent = send_email(env("OWN_EMAIL") or "", f"[{icp()['seller']['name']} draft → {c['first_name']} @ {dom}] {d['subject']}", body)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    t["send"] = {"mode": "sent", "to": "OWN_EMAIL", "message_id": sent["id"]}
    db.save_trail(t)
    return {"ok": True, "mode": "sent"}


# ------------------------------------------------------------------ HTTP
def original_url(raw_path: str):
    """On Vercel every request is rewritten to /api/index?__path=/<original> (vercel.json).
    Return the parsed original URL, with the caller's own query string kept."""
    u = urlparse(raw_path)
    if u.path.rstrip("/") != "/api/index":
        return u
    q = parse_qs(u.query, keep_blank_values=True)
    path = (q.pop("__path", ["/"])[0] or "/")
    from urllib.parse import urlencode
    return urlparse(path + ("?" + urlencode(q, doseq=True) if q else ""))


CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' "
       "https://fonts.googleapis.com; font-src https://fonts.gstatic.com data:; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


class Handler(BaseHTTPRequestHandler):
    local_only = False            # src/webapp.py sets this for the localhost server
    server_version = "OutreachDesk"
    sys_version = ""

    def log_message(self, fmt, *args):
        pass

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("X-Robots-Tag", "noindex, nofollow")
        if os.environ.get("VERCEL"):
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    def _json(self, code, obj, cookie: str | None = None):
        body = json.dumps(obj, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        if cookie is not None:
            secure = "; Secure" if os.environ.get("VERCEL") else ""
            self.send_header("Set-Cookie", f"pd_session={cookie}; Path=/; HttpOnly; SameSite=Lax; "
                                           f"Max-Age={SESSION_DAYS * 86400}{secure}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict | None:
        """The JSON body, {} when empty, or None when it is too large (the caller answers 413)."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n > MAX_BODY_BYTES:
            return None
        try:
            v = json.loads(self.rfile.read(n) or b"{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return v if isinstance(v, dict) else {}

    def _ip(self) -> str:
        fwd = self.headers.get("X-Forwarded-For") or ""
        return fwd.split(",")[0].strip() or (self.client_address[0] if self.client_address else "?")

    def _cookie(self) -> str | None:
        for part in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == "pd_session":
                return v
        return None

    def _authed(self) -> bool:
        return session_ok(self._cookie())

    def _cron_ok(self) -> bool:
        secret = os.environ.get("CRON_SECRET")
        return bool(secret) and hmac.compare_digest(self.headers.get("Authorization") or "", f"Bearer {secret}")

    def _guard(self) -> bool:
        if self.local_only:
            host = (self.headers.get("Host") or "").split(":")[0]
            if host not in ("127.0.0.1", "localhost"):
                self._json(403, {"error": "This app only answers on localhost."})
                return False
        return True

    def _page(self, head_only=False):
        body = PAGE.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Security-Policy", CSP)
        self._security_headers()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_HEAD(self):
        if self._guard():
            self._page(head_only=True)

    def do_GET(self):
        _tl.open = []
        try:
            self._get()
        finally:
            _close_request_stores()

    def do_POST(self):
        _tl.open = []
        try:
            self._post_entry()
        finally:
            _close_request_stores()

    def _get(self):
        if not self._guard():
            return
        u = original_url(self.path)
        try:
            if u.path in ("/", "/index.html"):
                return self._page()
            if u.path in ("/favicon.ico", "/robots.txt"):
                if u.path == "/robots.txt":
                    body = b"User-agent: *\nDisallow: /\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_response(204)
                self.end_headers()
                return
            if u.path == "/api/me":
                pw = bool(os.environ.get("APP_PASSWORD"))
                return self._json(200, {"authed": self._authed(),
                                       "password_required": pw and not open_access() and not demo_mode(),
                                       "setup_required": bool(os.environ.get("VERCEL")) and not pw
                                       and not open_access() and not demo_mode(),
                                       "demo": demo_mode()})
            if u.path in ("/api/health", "/api/healthz"):
                return self._json(200, health())
            if u.path == "/api/config":
                cfg = icp()
                return self._json(200, {"app_name": env("APP_NAME") or "Prospect Studio", "version": VERSION,
                                       "seller": {"name": cfg["seller"]["name"], "domain": cfg["seller"]["domain"],
                                                  "pitch": cfg["seller"]["pitch"]},
                                       "links": {"repo": env("REPO_URL"), "report": env("REPORT_URL")},
                                       "demo": demo_mode(), "timezone": env("AGENT_TIMEZONE") or "Asia/Kolkata"})
            if not self._authed():
                return self._json(401, {"error": "Sign in first."})
            if u.path == "/api/seller":
                return self._seller(parse_qs(u.query).get("name", [""])[0])
            if u.path == "/api/overview":
                return self._json(200, overview_data(data()))
            if u.path == "/api/account":
                det = account_detail(data(), normalise(parse_qs(u.query).get("domain", [""])[0]) or "")
                return self._json(200, det) if det else self._json(404, {"error": "Unknown account."})
            if u.path == "/api/agent/meta":
                return self._json(200, agent_meta())
            if u.path == "/api/accounts":
                return self._json(200, {"accounts": sort_accounts([account_summary(t) for t in data().list_trails(slim=True)])})
            if u.path == "/api/agent/threads":
                db = data()
                rows = []
                for t in db.list_threads():
                    rows.append({**t, "events": db.events(t["domain"], 30)})
                return self._json(200, {"threads": rows})
            if u.path == "/api/lessons":
                db = data()
                return self._json(200, {"lessons": db.lessons(), "feedback": db.feedback_rows(50)})
            if u.path == "/api/settings":
                return self._json(200, settings_data())
            return self._json(404, {"error": "Not found."})
        except NotConfigured as e:
            return self._json(503, {"error": str(e), "code": "not_configured"})
        except Exception as e:
            traceback.print_exc()
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _seller(self, name):
        from .poc_hunter import account_credits
        cfg = icp()
        s = match_seller(cfg, name)
        if not s:
            return self._json(404, {"error": f"“{name}” isn't set up yet. Only {cfg['seller']['name']} "
                                             f"has a researched ideal-customer profile so far."})
        gates = [{"id": g, "name": cfg["gates"][g]["name"], "scoring_only": bool(cfg["gates"][g].get("scoring_only"))}
                 for g in cfg["gate_order"]]
        remaining, reset = account_credits(env("HUNTER_API_KEY"))
        self._json(200, {"name": s["name"], "domain": s["domain"], "pitch": s["pitch"], "gates": gates,
                         "hunter": {"remaining": remaining, "reset": reset}})

    def _post_entry(self):
        if not self._guard():
            return
        u, b = original_url(self.path), self._body()
        if b is None:
            self.close_connection = True               # the unread body can't be reused on this socket
            return self._json(413, {"error": "Request body is too large."})
        try:
            if u.path == "/api/login":
                if not login_allowed(self._ip()):
                    return self._json(429, {"error": "Too many attempts. Wait ten minutes and try again."})
                pw = os.environ.get("APP_PASSWORD")
                if pw and hmac.compare_digest(str(b.get("password") or ""), pw):
                    return self._json(200, {"ok": True}, cookie=make_session())
                return self._json(401, {"error": "Wrong password."})
            if u.path == "/api/agent/tick" and self._cron_ok():
                from .agent.runner import tick
                return self._json(200, {"ticked": tick(data())})
            if not self._authed():
                return self._json(401, {"error": "Sign in first."})
            return self._post(u.path, b)
        except NotConfigured as e:
            return self._json(503, {"error": str(e), "code": "not_configured"})
        except Exception as e:
            traceback.print_exc()
            return self._json(500, {"error": f"{type(e).__name__}: {e}"})

    def _post(self, path, b):
        if path == "/api/discover":
            require("llm")
            from .discover import discover
            from .llm import make_llm
            from .research.fetch import Fetcher
            from .research.news import Throttle
            cfg, lc = icp(), icp()["agent_loop"]
            excl = load_exclusions(cfg["run"]["shortlist_exclusions"])
            researched = {t["domain"] for t in data().list_trails()}
            res = discover(Fetcher(lc["user_agent"], lc["http_timeout_seconds"]), make_llm(cfg["llm"]), cfg["seller"],
                           lc["user_agent"], excl, researched, throttle=Throttle(lc.get("news_min_interval_seconds", 2)))
            return self._json(200, res)
        if path == "/api/research":
            dom = normalise(b.get("domain") or "")
            if not dom:
                return self._json(400, {"error": "Give a valid domain, like acme.com."})
            return self._json(200, research_one(dom, (b.get("name") or "").strip() or None))
        if path == "/api/send":
            dom = normalise(b.get("domain") or "")
            return self._json(200, send_draft(dom)) if dom else self._json(400, {"error": "Unknown account."})
        if path == "/api/agent/start":
            from .agent.runner import start_thread
            db = data()
            dom = normalise(b.get("domain") or "")
            t = db.get_trail(dom) if dom else None
            if not t:
                return self._json(404, {"error": "Unknown account."})
            require("google", "own_email", "llm")
            used = ((t.get("email") or {}).get("draft") or {}).get("lessons_used") or []
            return self._json(200, start_thread(db, t, lessons_used=used))
        if path == "/api/agent/tick":
            from .agent.runner import tick
            dom = normalise(b.get("domain") or "") or None
            db = data()
            if any(not dom or t["domain"] == dom for t in db.list_threads("active")):
                require("google", "llm")
            return self._json(200, {"ticked": tick(db, domain=dom)})
        if path == "/api/feedback":
            db = data()
            dom = normalise(b.get("domain") or "")
            t = db.get_trail(dom) if dom else None
            if not t:
                return self._json(404, {"error": "Unknown account."})
            rating = b.get("rating")
            rating = int(rating) if str(rating or "").isdigit() and 1 <= int(rating) <= 5 else None
            edited = (str(b.get("edited_body") or "")).strip()[:8000] or None
            b["notes"] = str(b.get("notes") or "")[:1000]
            if not rating and not edited and not (b.get("notes") or "").strip():
                return self._json(400, {"error": "Give a rating, an edited version, or a note."})
            used = ((t.get("email") or {}).get("draft") or {}).get("lessons_used") or []
            fid = db.add_feedback(dom, rating, edited, (b.get("notes") or "").strip() or None, None, used)
            return self._json(200, {"ok": True, "id": fid})
        if path == "/api/lessons/propose":
            require("llm")
            from .llm import make_llm
            return self._json(200, propose_lessons(data(), make_llm(icp()["llm"])))
        if path.startswith("/api/lessons/"):
            try:
                lid = int(path.rsplit("/", 1)[-1])
                data().decide_lesson(lid, b.get("status") or "")
            except (ValueError, TypeError) as e:
                return self._json(400, {"error": str(e)})
            return self._json(200, {"ok": True})
        return self._json(404, {"error": "Not found."})
