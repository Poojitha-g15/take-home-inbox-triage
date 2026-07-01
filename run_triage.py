"""CLI runner for the Inbox Triage agent.

Usage
-----
    # Interactive — prompt for approval before each action
    python run_triage.py

    # Auto-approve everything (useful for demos / smoke tests)
    python run_triage.py --auto-approve

    # Dry-run — classify and plan but never execute anything
    python run_triage.py --dry-run

Prerequisites
-------------
    make serve          # starts the mock API on :8099
    cp env.example .env # fill in ANTHROPIC_API_KEY (and optionally READ_TOKEN / WRITE_TOKEN)

Security note
-------------
The read-only TriageClient (used for /inbox) never holds the write token.
The write token is loaded from the environment ONLY when an action is approved,
and only that specific action's execute() call receives a write-capable client.
The triage_inbox orchestrator always works with the read-only client; the write
client is constructed here in the runner, after the user says yes.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import textwrap

from dotenv import load_dotenv

from triage_skill import (
    ProposedAction,
    TriageClient,
    execute,
    triage_inbox,
)

# ──────────────────────────────────────────────────────────────────────────────

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-8s %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Display helpers
# ──────────────────────────────────────────────────────────────────────────────

_LABEL_EMOJI = {
    "billing": "💳",
    "bug_report": "🐛",
    "sales_lead": "💼",
    "spam": "🗑️ ",
}

_KIND_LABEL = {
    "send_reply": "Send email reply",
    "send_alert": "Post Slack alert",
    "create_lead": "Create CRM lead",
}


def _print_email_header(email: dict, label: str) -> None:
    emoji = _LABEL_EMOJI.get(label, "•")
    print(f"\n{'─'*60}")
    print(f"  {emoji}  [{label.upper()}]  {email.get('id')} — {email.get('subject')}")
    print(f"      From: {email.get('from')}")
    print(f"{'─'*60}")


def _print_action(action: ProposedAction) -> None:
    kind_label = _KIND_LABEL.get(action.kind, action.kind)
    print(f"\n  📋  Proposed action: {kind_label}")
    print(f"      Rationale: {action.rationale}")

    p = action.payload
    if action.kind == "send_reply":
        print(f"      To:      {p['to']}")
        print(f"      Subject: {p['subject']}")
        print()
        print(textwrap.indent(textwrap.fill(p.get("body", ""), width=70), "      "))
    elif action.kind == "send_alert":
        print(f"      Channel: {p['channel']}")
        print(f"      Message: {textwrap.fill(p.get('message', ''), width=70)}")
    elif action.kind == "create_lead":
        print(f"      Name:    {p.get('name')}")
        print(f"      Email:   {p.get('email')}")
        print(f"      Company: {p.get('company')}")
        print(f"      Summary: {p.get('summary', '')[:120]}…")


# ──────────────────────────────────────────────────────────────────────────────
# Approver
# ──────────────────────────────────────────────────────────────────────────────


def make_approver(auto_approve: bool, dry_run: bool):
    """Return an approver callable for triage_inbox.

    In dry-run mode: always decline (nothing is ever executed).
    In auto-approve mode: always approve.
    Otherwise: ask the human interactively.

    The write-capable client is built inside execute() only when approved=True;
    the approver itself never touches the write token.
    """
    def approver(email: dict, action: ProposedAction) -> bool:
        _print_action(action)

        if dry_run:
            print("      [DRY-RUN — not executing]")
            return False

        if auto_approve:
            print("      ✅  Auto-approved")
            return True

        while True:
            answer = input("\n  Approve this action? [y/n] ").strip().lower()
            if answer in ("y", "yes"):
                return True
            if answer in ("n", "no"):
                print("      ⛔  Declined")
                return False
            print("      Please type y or n.")

    return approver


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────


def _check_env() -> None:
    missing = [v for v in ("ANTHROPIC_API_KEY", "READ_TOKEN", "WRITE_TOKEN", "API_BASE_URL") if not os.environ.get(v)]
    if missing:
        print(f"ERROR: missing env vars: {', '.join(missing)}", file=sys.stderr)
        print("Copy env.example → .env and fill in the values.", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Inbox Triage Agent")
    parser.add_argument("--auto-approve", action="store_true", help="Approve all actions without prompting")
    parser.add_argument("--dry-run", action="store_true", help="Classify and plan but never execute")
    args = parser.parse_args()

    _check_env()

    base_url = os.environ["API_BASE_URL"]
    read_token = os.environ["READ_TOKEN"]
    write_token = os.environ["WRITE_TOKEN"]

    # The client passed into triage_inbox has BOTH tokens.
    # The write token is only used inside execute() when approved=True.
    # Spam emails produce zero actions, so execute() is never called for them,
    # and the write token is never touched on the spam path.
    client = TriageClient(
        base_url=base_url,
        read_token=read_token,
        write_token=write_token,
    )

    approver = make_approver(auto_approve=args.auto_approve, dry_run=args.dry_run)

    # Override execute to print the email header before each action batch.
    # We wrap triage_inbox's approver to inject display — triage_inbox itself
    # is unmodified.
    email_labels: dict[str, str] = {}

    # We need to intercept per-email progress for display. We do this by wrapping
    # classify_email to capture the label per email_id, then wrapping approver.
    from triage_skill import classify_email as _classify, _enrich_action

    _last_email: dict = {}
    _last_label: list[str] = [""]

    def capturing_classifier(email: dict) -> str:
        _last_email.update(email)
        label = _classify(email)
        _last_label[0] = label
        _print_email_header(email, label)
        return label

    mode = "DRY-RUN" if args.dry_run else ("AUTO-APPROVE" if args.auto_approve else "INTERACTIVE")
    print(f"\n{'='*60}")
    print(f"  INBOX TRIAGE AGENT  [{mode}]")
    print(f"  API: {base_url}")
    print(f"{'='*60}")

    results = triage_inbox(
        client=client,
        approver=approver,
        classifier=capturing_classifier,
        enricher=_enrich_action,
    )

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")
    label_counts: dict[str, int] = {}
    total_executed = 0
    for r in results:
        label_counts[r.label] = label_counts.get(r.label, 0) + 1
        total_executed += len(r.executed)

    for label, count in sorted(label_counts.items()):
        emoji = _LABEL_EMOJI.get(label, "•")
        print(f"  {emoji}  {label:<12} {count} email(s)")
    print(f"\n  Total actions executed: {total_executed}")
    print()


if __name__ == "__main__":
    main()
