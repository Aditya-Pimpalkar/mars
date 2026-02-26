"""
indices/setup.py
────────────────
Creates all 6 MARS indices in Elasticsearch.
Run once before ingest:  python indices/setup.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from es_client import get_client, check_connection
from rich.console import Console
from rich.table import Table

console = Console()

INDEX_NAMES = {
    "logs":         "logs-mars",
    "metrics":      "metrics-mars",
    "deployments":  "deployments-mars",
    "incidents":    "incidents-mars",
    "runbooks":     "runbooks-mars",
    "claim_ledger": "claim-ledger-mars",
}


def load_mappings() -> dict:
    path = Path(__file__).parent / "mappings.json"
    with open(path) as f:
        return json.load(f)


def create_index(es, name: str, mapping: dict, recreate: bool = False) -> str:
    if es.indices.exists(index=name):
        if recreate:
            es.indices.delete(index=name)
            console.print(f"  🗑️  Deleted existing index: [bold]{name}[/bold]")
        else:
            return "skipped"

    es.indices.create(
        index=name,
        body={
            "mappings": mapping["mappings"],
        },
    )
    return "created"


def setup(recreate: bool = False):
    if not check_connection():
        sys.exit(1)

    es = get_client()
    mappings = load_mappings()

    table = Table(title="MARS Index Setup", show_header=True)
    table.add_column("Index", style="cyan")
    table.add_column("Status", style="green")

    for key, index_name in INDEX_NAMES.items():
        status = create_index(es, index_name, mappings[key], recreate=recreate)
        emoji = "✅" if status == "created" else "⏭️ "
        table.add_row(index_name, f"{emoji} {status}")

    console.print(table)
    console.print("\n[bold green]Index setup complete.[/bold green] Ready for ingest.\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true",
                        help="Delete and recreate all indices (clears all data)")
    args = parser.parse_args()
    setup(recreate=args.recreate)
