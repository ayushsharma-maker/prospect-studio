"""Demo data for a fresh deployment.

When the app runs in demo mode (DEMO_MODE=1, or a deployment with no DATABASE_URL), the screens would
otherwise be empty. `seed(db)` writes a small, clearly fictional dataset into the (ephemeral) SQLite
database: every company is invented and every domain ends in `.example`, a reserved TLD that can never
resolve, so nothing here can be mistaken for a real prospect or emailed by accident.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

CAPS = {"max_passes": 3, "max_pages_per_company": 8, "max_free_api_calls_per_company": 16,
        "max_llm_calls_per_company": 12}


def _fact(i, key, quote, url, date, kind="news"):
    return {"id": i, "key": key, "value": True, "quote": quote, "source_url": url, "date": date, "source_type": kind}


def _research(passes, pages, api, llm, exit_reason, actions, planner):
    return {"exit_reason": exit_reason,
            "usage": {"passes": passes, "pages": pages, "free_api_calls": api, "llm_calls": llm, "caps": CAPS},
            "actions": actions, "gate_history": []}


def _go(domain, name, run_id, facts, contact, draft, score, factors, considered=()):
    gates = {
        "G1": {"status": "pass", "reason": "Homepage sells a cloud platform to business teams.", "evidence_fact_ids": [1]},
        "G5": {"status": "pass", "reason": "No SOC 2 Type II or ISO 27001 claim on site, trust page or news.",
               "evidence_fact_ids": [2]},
        "G3": {"status": "pass", "reason": "Stated headcount sits inside 100–1,000.", "evidence_fact_ids": [3]},
        "G2": {"status": "pass", "reason": "Latest priced round is within Series A–C.", "evidence_fact_ids": [4]},
        "G4": {"status": "pass", "reason": "Open compliance role found on the job board.", "evidence_fact_ids": [5]},
    }
    actions = [
        {"pass": 1, "action": "news:g2_raises", "outcome": "ok", "note": "2 dated articles"},
        {"pass": 1, "action": "wikidata_lookup", "outcome": "not_found", "note": "no entity"},
        {"pass": 1, "action": "job_board_lookup", "outcome": "ok", "note": "greenhouse board"},
        {"pass": 1, "action": "news:g5_soc2", "outcome": "ok", "note": "no attestation"},
        {"pass": 2, "action": "scrape_homepage", "outcome": "ok", "note": "200"},
        {"pass": 2, "action": "scrape_trust", "outcome": "ok", "note": "/trust"},
        {"pass": 3, "action": "hunter_company_lookup", "outcome": "cache", "note": "201-500"},
    ]
    planner = [{"pass": 1, "decided_by": "seed", "reasoning": ""},
               {"pass": 2, "decided_by": "planner",
                "reasoning": "G1 and G5 still open; the homepage and trust page settle both in one pass."},
               {"pass": 3, "decided_by": "planner", "reasoning": "Only G3 left; Hunter's size bucket is cheapest."}]
    return {
        "domain": domain, "run_id": run_id, "company": {"name": name},
        "final_status": "go", "final_reason": "every fit gate passed",
        "gates": gates, "facts": facts, "dropped_facts": [{"reason": "quote not found in source"}],
        "planner_decisions": planner,
        "research": _research(3, 4, 9, 7, "all_gates_resolved", actions, planner),
        "qualification": {"status": "go", "failing_gate": None,
                          "weighted_score": {"total": score, "factors": factors}},
        "contact": {"chosen": contact, "why": f"{contact['position']} ranks highest among verified contacts.",
                    "considered": list(considered)},
        "email": {"status": "drafted", "draft": draft},
    }


def _stop(domain, name, run_id, status, gate, reason, quote, url, date):
    gates = {"G1": {"status": "pass", "reason": "Sells software to businesses.", "evidence_fact_ids": [1]}}
    if gate:
        gates[gate] = {"status": "fail" if status == "no_go" else "unknown", "reason": reason,
                       "evidence_fact_ids": [2] if quote else []}
    facts = [_fact(1, "is_software_product", f"{name} is the platform operations teams use to run their day.",
                   f"https://{domain}/", "2026-10-30", "site")]
    if quote:
        facts.append(_fact(2, "fact", quote, url, date))
    final_reason = f"{gate} failed: {reason}" if status == "no_go" else f"unanswered after loop: {gate} {reason}"
    return {
        "domain": domain, "run_id": run_id, "company": {"name": name}, "final_status": status,
        "final_reason": final_reason, "gates": gates, "facts": facts, "dropped_facts": [],
        "planner_decisions": [{"pass": 1, "decided_by": "seed", "reasoning": ""}],
        "research": _research(1 if status == "no_go" else 3, 2, 8, 4,
                              "gate_failed" if status == "no_go" else "caps_reached",
                              [{"pass": 1, "action": "news:g2_raises", "outcome": "ok", "note": ""},
                               {"pass": 1, "action": "scrape_homepage", "outcome": "ok", "note": ""}], []),
        "qualification": {"status": status, "failing_gate": gate if status == "no_go" else None},
    }


def dataset() -> dict:
    today = datetime.now(timezone.utc)
    run = today.strftime("%Y%m%d-090000")
    ledgerly = _go(
        "ledgerly.example", "Ledgerly", run,
        [_fact(1, "is_software_product", "Ledgerly is the finance automation platform for growing B2B teams.",
               "https://ledgerly.example/", "2026-11-02", "site"),
         _fact(2, "soc2_iso_status", "We are working towards our SOC 2 Type II report, expected next year.",
               "https://ledgerly.example/trust", "2026-11-02", "site"),
         _fact(3, "employee_count", "The company now employs around 240 people across three offices.",
               "https://news.example/ledgerly-series-b", "2026-09-14"),
         _fact(4, "funding_round", "Ledgerly raises $28M Series B to expand into Europe.",
               "https://news.example/ledgerly-series-b", "2026-09-14"),
         _fact(5, "compliance_job_titles", "Senior GRC Analyst (Remote)",
               "https://jobs.example/ledgerly/grc-analyst", "2026-11-01", "job_board")],
        {"first_name": "Priya", "last_name": "Natarajan", "email": "priya@ledgerly.example",
         "position": "VP Engineering"},
        {"subject": "Ledgerly's SOC 2 before the EU push",
         "body": ("Hi Priya,\n\nCongrats on the Series B. Expanding into Europe usually means the first enterprise "
                  "security questionnaires land before the SOC 2 report does.\n\nI saw you're hiring a GRC "
                  "analyst, which suggests the audit work is landing on engineering today. Teams your size "
                  "typically lose a sprint a quarter to evidence collection.\n\nSprinto automates that evidence "
                  "across your cloud, HR and code tools and gets first-time SOC 2 audit-ready in weeks.\n\n"
                  "Worth a 20-minute look next week?\n\nBest,\nAlex"),
         "hook_url": "https://news.example/ledgerly-series-b",
         "hook_quote": "Ledgerly raises $28M Series B to expand into Europe.",
         "pain_points": [{"pain": "Questionnaires before the report",
                          "why_for_role": "Enterprise deals in the EU stall on security reviews engineering must answer."},
                         {"pain": "Evidence collection eats sprints",
                          "why_for_role": "Without a GRC team, screenshots and exports fall to engineers."}],
         "reasoning": "Hook on the dated Series B; the GRC opening grounds the second pain.",
         "cta": "20-minute call", "lessons_used": [1]},
        11, {"W1": {"name": "Upmarket sales motion", "points": 3},
             "W2": {"name": "Regulated / data-sensitive vertical (fintech)", "points": 3},
             "W3": {"name": "Funding round in the last 12 months", "points": 2},
             "G4": {"name": "Compliance pressure", "points": 3}},
        considered=[{"name": "Marco Silva", "position": "CTO", "email": "marco@ledgerly.example", "rank": 6,
                     "verification": "accept_all", "rejected": "verification status accept_all not accepted"}])
    shiftwise = _go(
        "shiftwise.example", "Shiftwise", run,
        [_fact(1, "is_software_product", "Shiftwise helps HR teams schedule and pay frontline staff.",
               "https://shiftwise.example/", "2026-11-03", "site"),
         _fact(2, "soc2_iso_status", "Security is a priority; certification is on our roadmap.",
               "https://shiftwise.example/security", "2026-11-03", "site"),
         _fact(3, "employee_count", "Shiftwise has grown to 410 employees.",
               "https://news.example/shiftwise-a", "2026-06-20"),
         _fact(4, "funding_round", "HR platform Shiftwise closes $15M Series A.",
               "https://news.example/shiftwise-a", "2026-06-20"),
         _fact(5, "security_leader_change", "Shiftwise appoints Dana Ortiz as Head of Security.",
               "https://news.example/shiftwise-hire", "2026-08-02")],
        {"first_name": "Dana", "last_name": "Ortiz", "email": "dana@shiftwise.example", "position": "Head of Security"},
        {"subject": "First 90 days as Head of Security",
         "body": ("Hi Dana,\n\nCongrats on the new role at Shiftwise. Most security leaders joining an HR platform "
                  "inherit two things at once: payroll data and a customer asking for SOC 2.\n\nSprinto runs "
                  "continuous control monitoring so a lean team stays audit-ready without a full GRC hire, and "
                  "answers questionnaires from one source of truth.\n\nOpen to comparing notes for 20 minutes?\n\n"
                  "Best,\nAlex"),
         "hook_url": "https://news.example/shiftwise-hire",
         "hook_quote": "Shiftwise appoints Dana Ortiz as Head of Security.",
         "pain_points": [{"pain": "PII-heavy data from day one",
                          "why_for_role": "Payroll and HR records raise the bar for every customer review."},
                         {"pain": "No GRC team yet",
                          "why_for_role": "A new security head is usually a team of one."}],
         "reasoning": "A leadership change is the strongest timing signal available.",
         "cta": "20-minute call", "lessons_used": []},
        9, {"W2": {"name": "Regulated / data-sensitive vertical (HR)", "points": 3},
            "W3": {"name": "Funding round in the last 12 months", "points": 2},
            "W4": {"name": "EU / GDPR exposure", "points": 1},
            "G4": {"name": "Compliance pressure", "points": 3}})
    routeline = _go(
        "routeline.example", "Routeline", run,
        [_fact(1, "is_software_product", "Routeline is fleet visibility software for logistics companies.",
               "https://routeline.example/", "2026-11-04", "site"),
         _fact(2, "soc2_iso_status", "SOC 2 Type I completed in 2026.", "https://routeline.example/trust",
               "2026-11-04", "site"),
         _fact(3, "employee_count", "a team of about 150", "https://news.example/routeline", "2026-03-11"),
         _fact(4, "funding_round", "Routeline secures Series A led by Northwind Ventures.",
               "https://news.example/routeline", "2026-03-11"),
         _fact(5, "compliance_job_titles", "Security Engineer", "https://jobs.example/routeline/sec", "2026-10-28",
               "job_board")],
        {"first_name": "Sam", "last_name": "Iyer", "email": "sam@routeline.example", "position": "CTO"},
        {"subject": "From Type I to Type II at Routeline",
         "body": ("Hi Sam,\n\nSaw Routeline already has SOC 2 Type I. The jump to Type II is where most teams "
                  "feel it: an observation window and evidence for every control, every month.\n\nSprinto keeps "
                  "that evidence flowing automatically from your cloud and code tools.\n\nWorth a quick look?\n\n"
                  "Best,\nAlex"),
         "hook_url": "https://routeline.example/trust", "hook_quote": "SOC 2 Type I completed in 2026.",
         "pain_points": [{"pain": "Type II observation window", "why_for_role": "Months of continuous evidence."},
                         {"pain": "Security hire is still open", "why_for_role": "The CTO owns it until then."}],
         "reasoning": "Type I only means a clear upgrade path (W5).", "cta": "quick call", "lessons_used": [1]},
        7, {"W3": {"name": "Funding round in the last 12 months", "points": 2},
            "W5": {"name": "SOC 2 Type I only (upgrade path)", "points": 1},
            "G4": {"name": "Compliance pressure", "points": 3},
            "W4": {"name": "EU / GDPR exposure", "points": 1}})
    stops = [
        _stop("bigbox.example", "Bigbox Retail Cloud", run, "no_go", "G3", "headcount 4,200 is above 1,000",
              "Bigbox employs more than 4,200 people worldwide.", "https://news.example/bigbox", "2026-07-01"),
        _stop("trustwell.example", "Trustwell", run, "no_go", "G5", "SOC 2 Type II attested",
              "Trustwell has achieved SOC 2 Type II and ISO 27001.", "https://trustwell.example/trust", "2026-05-09"),
        _stop("latestage.example", "Latestage", run, "no_go", "G2", "latest round is Series E",
              "Latestage raises $210M Series E.", "https://news.example/latestage", "2026-04-18"),
        _stop("quietco.example", "QuietCo", run, "needs_review", "G2", "no dated funding round found",
              None, None, None),
        _stop("jsonly.example", "JSOnly Analytics", run, "needs_review", "G5",
              "trust page is JavaScript-only, so SOC 2 status can't be read", None, None, None),
    ]
    sent_at = today - timedelta(hours=30)
    proposed = [(today + timedelta(days=d)).replace(hour=5, minute=0, second=0, microsecond=0).isoformat()
                for d in (1, 2, 3)]
    thread = {
        "reply_status": "interested", "calendar_checked": True, "times_proposed": proposed, "reply_round": 0,
        "meeting_status": "pending_confirmation", "crm_logged": False, "domain": "ledgerly.example",
        "company": "Ledgerly", "contact": {"first_name": "Priya", "last_name": "Natarajan",
                                            "position": "VP Engineering", "email": "priya@ledgerly.example"},
        "thread_id": "demo-thread-1", "last_rfc_id": "", "last_sent_at": sent_at.isoformat(), "seen_reply_ids": ["r1"],
        "offered_history": proposed, "requested_time": "", "confirmed_time": "",
        "last_reply_text": "Sounds useful, what times work next week?", "escalation_reason": "",
        "thread_status": "active", "lessons_used": [1],
    }
    escalated = {
        "reply_status": "unclear", "calendar_checked": False, "times_proposed": [], "reply_round": 0,
        "meeting_status": "unscheduled", "crm_logged": False, "domain": "shiftwise.example", "company": "Shiftwise",
        "contact": {"first_name": "Dana", "last_name": "Ortiz", "position": "Head of Security",
                    "email": "dana@shiftwise.example"},
        "thread_id": "demo-thread-2", "last_rfc_id": "", "last_sent_at": sent_at.isoformat(), "seen_reply_ids": ["r2"],
        "offered_history": [], "requested_time": "", "confirmed_time": "",
        "last_reply_text": "Loop in my colleague, she owns vendors.",
        "escalation_reason": "read_inbox: reply could not be classified with confidence", "thread_status": "escalated",
        "lessons_used": [],
    }
    events = {
        "ledgerly.example": [("start", "send_email", "ok", None),
                             ("read_inbox", None, "no_new_reply", None),
                             ("read_inbox", None, "classified", "interested: asks what times work next week"),
                             ("check_calendar", "check_calendar", "ok", "3 free slots"),
                             ("propose_time", "propose_time", "ok", "3 slots offered")],
        "shiftwise.example": [("start", "send_email", "ok", None),
                              ("read_inbox", None, "classified", "unclear: redirects to a colleague"),
                              ("escalate", "send_email", "ok", "owner notified")],
    }
    return {"trails": [ledgerly, shiftwise, routeline, *stops],
            "threads": [("ledgerly.example", thread, "active"), ("shiftwise.example", escalated, "escalated")],
            "events": events,
            "feedback": [("ledgerly.example", 4, None, "Strong hook, trim the middle paragraph.", None, []),
                         ("routeline.example", 5, None, "Type I angle is exactly right.", None, [1]),
                         ("ledgerly.example", None, None, None, "interested", [1])],
            "lessons": [("Open with one dated, cited trigger and keep the body under 100 words.",
                         "Both top-rated drafts led with a single dated fact.", [1, 2], "approved"),
                        ("Name the specific compliance hire when one is open.",
                         "The reply that converted referenced the GRC opening.", [3], "pending")]}


def seed(db) -> bool:
    """Write the demo dataset once. Returns True when it wrote anything."""
    if db.list_trails(slim=True):
        return False
    d = dataset()
    for t in d["trails"]:
        db.save_trail(t)
    for domain, state, status in d["threads"]:
        db.put_thread(domain, state, status)
        for node, tool, outcome, detail in d["events"].get(domain, []):
            db.event(domain, node, outcome, tool, detail)
    for row in d["feedback"]:
        db.add_feedback(*row)
    for text, why, ev, status in d["lessons"]:
        lid = db.add_lesson(text, why, ev)
        if status != "pending":
            db.decide_lesson(lid, status)
    return True
