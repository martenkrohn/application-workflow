"""Commands for a local, agent-neutral review workflow."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_system.application.portfolio import PortfolioWorkflow


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Application Workflow — local human/agent review"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--workspace", help="separate local dataset; e.g. review")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser(
        "demo", help="run fictional data and explicitly simulated agent results"
    )
    collect = commands.add_parser(
        "collect", help="collect local example HTML or use a trusted private adapter"
    )
    collect.add_argument(
        "--adapter", help="trusted importable module:factory; executes local Python"
    )
    commands.add_parser("status", help="show jobs, batches and mail reviews as JSON")
    assessment = commands.add_parser("assessment").add_subparsers(
        dest="action", required=True
    )
    prepare = assessment.add_parser(
        "prepare", help="prepare only explicitly chosen source:job-id entries"
    )
    prepare.add_argument("--ids", nargs="+", required=True)
    for action in ["validate", "finish", "instructions"]:
        assessment.add_parser(action).add_argument("batch_id")
    agent = commands.add_parser("agent").add_subparsers(
        dest="agent_action", required=True
    )
    example = agent.add_parser(
        "example-results",
        help="SIMULATE results for a prepared batch; no model invocation",
    )
    example.add_argument("batch_id")
    decision = commands.add_parser(
        "decision", help="record the human's decision; no messages are sent"
    )
    decision.add_argument("source")
    decision.add_argument("external_id")
    decision.add_argument("status")
    mail = commands.add_parser("mail").add_subparsers(dest="mail_action", required=True)
    mail.add_parser("prepare")
    mail.add_parser("finish")
    dashboard = commands.add_parser("dashboard").add_subparsers(
        dest="dashboard_action", required=True
    )
    serve = dashboard.add_parser("serve")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    try:
        workflow = PortfolioWorkflow(args.root, args.workspace)
        if args.command == "demo":
            result = workflow.demo()
        elif args.command == "collect":
            result = workflow.collect(args.adapter)
        elif args.command == "status":
            result = workflow.snapshot()
        elif args.command == "decision":
            result = workflow.decide(args.source, args.external_id, args.status)
        elif args.command == "agent":
            result = workflow.simulate(args.batch_id)
        elif args.command == "assessment":
            if args.action == "prepare":
                selected = []
                for value in args.ids:
                    source, sep, identity = value.partition(":")
                    if not sep or not source or not identity:
                        raise ValueError("use source:job-id")
                    selected.append({"source": source, "external_id": identity})
                result = workflow.prepare(selected)
            elif args.action == "validate":
                result = workflow.validate(args.batch_id)
                print(json.dumps(result, indent=2, ensure_ascii=False))
                return 2 if result["rejected"] else 0
            elif args.action == "finish":
                result = workflow.finish(args.batch_id)
            else:
                paths = workflow.batch_paths(args.batch_id)
                print(
                    (workflow.root / paths["manifest"])
                    .with_name("HANDOFF.md")
                    .read_text(encoding="utf-8")
                )
                return 0
        elif args.command == "mail":
            from job_system.application.mail_demo import finish_mail, prepare_mail

            result = (
                prepare_mail(workflow.root, workflow.settings.data_dir)
                if args.mail_action == "prepare"
                else finish_mail(workflow)
            )
        else:
            from job_system.web.server import serve_dashboard

            serve_dashboard(workflow, args.port)
            return 0
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
