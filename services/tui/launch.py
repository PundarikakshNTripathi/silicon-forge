# SPDX-License-Identifier: AGPL-3.0-or-later
"""Start the chat TUI for a workspace: the same session factory `siliconforge chat` uses, with the UI as its approver."""
from __future__ import annotations

import argparse

from services.orchestrator.agent import create_session
from services.orchestrator.agent.store import SessionStore
from services.orchestrator.workspace import find_workspace, remember_workspace
from services.tui.chat import ChatApp


def launch(args: argparse.Namespace) -> int:
    ws = find_workspace(args.workspace)
    remember_workspace(ws)
    store = SessionStore(ws.state_dir / "sessions") if (args.save or args.resume is not None) else None

    def factory(on_event, approver):
        session = create_session(ws, profile=args.profile or ws.profile, mode=args.mode, approver=approver, on_event=on_event,
                                 max_steps=args.max_steps)
        if store is not None:
            session.save_to(store, None if args.resume in (None, "latest") else args.resume, resume=args.resume is not None)
        return session

    ChatApp(ws, session_factory=factory, animate=not args.no_anim, theme="siliconforge-light" if args.light else "siliconforge-dark",
            store=store).run()
    return 0
