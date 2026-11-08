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
    chat = commands.add_parser("chat")
    chat.add_argument("text")
    chat.add_argument("--reply-to")
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
