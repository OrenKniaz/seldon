"""CLI input and argument parsing; no continuation decisions."""

import argparse
import json
from pathlib import Path


def read_json(value):
    return json.loads(
        Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value
    )


def parser():
    result = argparse.ArgumentParser(
        description="Seldon durable run commands and canonical inspection"
    )
    result.add_argument("--workspace", default=".")
    result.add_argument("--json", action="store_true")
    commands = result.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start")
    start.add_argument("--name", required=True)
    start.add_argument("--idea", required=True)
    start.add_argument("--role-tuples")
    answer = commands.add_parser("answer")
    answer.add_argument("--request-id", required=True)
    answer.add_argument("--answer", required=True)
    answer.add_argument("--source", default="cli-semantic-answer")
    steer = commands.add_parser("steer")
    steer.add_argument(
        "direction",
        choices=["continue", "repair", "replan", "research", "ask_user", "pause", "stop"],
    )
    steer.add_argument("--rationale", required=True)
    steer.add_argument("--content")
    steer.add_argument("--user-words")
    steer.add_argument("--resume", action="store_true")
    commands.add_parser("status")
    schema = commands.add_parser("schema")
    schema.add_argument("--tool")
    schema.add_argument("--kind")
    nodes = commands.add_parser("nodes")
    nodes.add_argument("--kind")
    show = commands.add_parser("show")
    show.add_argument("id")
    commands.add_parser("mail")
    return result
