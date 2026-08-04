"""Inspect normalized team game-level hitting data for one team and season.

Examples
--------
poetry run python scripts/inspect_game_logs.py --team-id 136 --season 2025
poetry run python scripts/inspect_game_logs.py --team-id 136 --season 2025 --limit 5
poetry run python scripts/inspect_game_logs.py --team-id 136 --season 2025 --format json

This script calls the live MLB Stats API through python-mlb-statsapi and is not
part of the automated test suite.
"""

import argparse
import json
import sys

from app.schemas.games import TeamGameBattingLine
from app.services.team_game_logs import TeamGameLogError, get_team_game_batting_lines


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--team-id", type=int, required=True, help="MLB team id, e.g. 136."
    )
    parser.add_argument(
        "--season", type=int, required=True, help="Season year, e.g. 2025."
    )
    parser.add_argument(
        "--limit", type=int, default=None, help="Show only the first N games."
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="Output format (default: table).",
    )
    return parser


def format_row(line: TeamGameBattingLine) -> str:
    """Format one batting line as a human-readable row."""
    game_id = (
        f"{line.game_pk} (G{line.game_number})"
        if line.doubleheader
        else str(line.game_pk)
    )
    versus = "vs" if line.home_away == "home" else "at"
    return (
        f"{line.game_date.isoformat()} | {game_id} | "
        f"{line.team_name} {versus} {line.opponent_name} | {line.home_away} | "
        f"H: {line.hits} | R: {line.runs} | {line.status}"
    )


def format_summary(
    lines: list[TeamGameBattingLine],
    team_id: int,
    season: int,
    displayed: int,
) -> str:
    """Format the inspection summary for a full set of retrieved batting lines."""
    team_name = lines[0].team_name if lines else f"team {team_id}"
    total_hits = sum(line.hits for line in lines)
    rows = [
        f"Team: {team_name}",
        f"Season: {season}",
        f"Completed games: {len(lines)}",
    ]
    if displayed != len(lines):
        rows.append(f"Displayed games: {displayed}")
    rows.append(f"Total hits: {total_hits}")
    if lines:
        rows.append(f"Average hits per game: {total_hits / len(lines):.2f}")
    return "\n".join(rows)


def format_json(lines: list[TeamGameBattingLine]) -> str:
    """Serialize batting lines as JSON using Pydantic serialization."""
    return json.dumps([line.model_dump(mode="json") for line in lines], indent=2)


def main(argv: list[str] | None = None) -> int:
    """Run the inspection command and return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be 1 or greater")

    try:
        lines = get_team_game_batting_lines(args.team_id, args.season)
    except TeamGameLogError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    selected = lines if args.limit is None else lines[: args.limit]

    if args.format == "json":
        print(format_json(selected))
        return 0

    for line in selected:
        print(format_row(line))
    if selected:
        print()
    print(format_summary(lines, args.team_id, args.season, len(selected)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
