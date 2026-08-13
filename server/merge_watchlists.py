from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

WATCHLIST_DIR = (
    ROOT / "config" / "watchlists"
)

INSTRUMENTS_JSON = (
    ROOT / "config" / "instruments.json"
)

ZERO_CSV = (
    ROOT / "config" / "zero.csv"
)

ZERO_JSON = (
    ROOT / "config" / "zero.json"
)

MEMBERSHIP_JSON = (
    ROOT
    / "config"
    / "watchlist_membership.json"
)


def normalize(
    value: str | None,
) -> str:
    return (value or "").strip()


def load_instruments() -> dict[str, dict]:
    if not INSTRUMENTS_JSON.exists():
        raise ValueError(
            f"Instrument master not found: "
            f"{INSTRUMENTS_JSON}"
        )

    with INSTRUMENTS_JSON.open(
        "r",
        encoding="utf-8",
    ) as handle:
        payload = json.load(handle)

    instruments = {}

    for row in payload:
        isin = normalize(
            row.get("ISIN")
        ).upper()

        ticker = normalize(
            row.get("Ticker")
        ).upper()

        name = normalize(
            row.get("Name")
        )

        if not isin or not ticker:
            continue

        if isin in instruments:
            existing = instruments[isin]

            if existing["Ticker"] != ticker:
                raise ValueError(
                    f"Duplicate ISIN {isin} "
                    f"with conflicting tickers: "
                    f"{existing['Ticker']} / {ticker}"
                )

        instruments[isin] = {
            "Ticker": ticker,
            "ISIN": isin,
            "Name": name,
        }

    if not instruments:
        raise ValueError(
            "Instrument master contains "
            "no usable instruments"
        )

    return instruments


def load_watchlist(
    path: Path,
) -> list[dict]:
    with path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as handle:

        reader = csv.DictReader(
            handle,
            delimiter=";",
        )

        fields = set(
            reader.fieldnames or []
        )

        required = {
            "Name",
            "ISIN/Kuerzel",
        }

        missing = required - fields

        if missing:
            raise ValueError(
                f"{path.name}: missing columns "
                f"{sorted(missing)}"
            )

        rows = []

        for line_number, row in enumerate(
            reader,
            start=2,
        ):
            name = normalize(
                row.get("Name")
            )

            identifier = normalize(
                row.get("ISIN/Kuerzel")
            ).upper()

            asset_type = normalize(
                row.get("Art")
            )

            if not identifier:
                continue

            rows.append(
                {
                    "Name": name,
                    "Identifier": identifier,
                    "Art": asset_type,
                    "Line": line_number,
                }
            )

        return rows


def main() -> int:
    instruments = load_instruments()

    files = sorted(
        WATCHLIST_DIR.glob("*.csv")
    )

    if not files:
        raise ValueError(
            f"No watchlists found in "
            f"{WATCHLIST_DIR}"
        )

    combined = {}

    membership = defaultdict(set)

    unresolved = []

    for path in files:
        user = path.stem.lower()

        print(
            f"Loading {path.name} "
            f"for user {user}"
        )

        rows = load_watchlist(path)

        for source_row in rows:
            identifier = (
                source_row["Identifier"]
            )

            instrument = (
                instruments.get(identifier)
            )

            if instrument is None:
                unresolved.append(
                    {
                        "user": user,
                        "file": path.name,
                        "line": source_row["Line"],
                        "identifier": identifier,
                        "name": source_row["Name"],
                    }
                )
                continue

            ticker = instrument["Ticker"]

            combined[ticker] = {
                "Ticker": ticker,
                "ISIN": instrument["ISIN"],
                "Name": instrument["Name"],
            }

            membership[ticker].add(
                user
            )

    if unresolved:
        print(
            "\nERROR: unresolved instruments:",
            file=sys.stderr,
        )

        for row in unresolved:
            print(
                (
                    f"  {row['file']}:"
                    f"{row['line']} "
                    f"{row['identifier']} "
                    f"{row['name']}"
                ),
                file=sys.stderr,
            )

        print(
            "\nAdd these instruments to "
            "config/instruments.json "
            "before regenerating zero.json.",
            file=sys.stderr,
        )

        return 1

    merged_rows = [
        combined[ticker]
        for ticker in sorted(combined)
    ]

    ZERO_CSV.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with ZERO_CSV.open(
        "w",
        encoding="utf-8",
        newline="",
    ) as handle:

        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Ticker",
                "ISIN",
                "Name",
            ],
        )

        writer.writeheader()
        writer.writerows(
            merged_rows
        )

    with ZERO_JSON.open(
        "w",
        encoding="utf-8",
    ) as handle:

        json.dump(
            merged_rows,
            handle,
            indent=2,
            ensure_ascii=False,
        )

        handle.write("\n")

    membership_payload = {
        ticker: sorted(users)
        for ticker, users
        in sorted(
            membership.items()
        )
    }

    with MEMBERSHIP_JSON.open(
        "w",
        encoding="utf-8",
    ) as handle:

        json.dump(
            membership_payload,
            handle,
            indent=2,
            ensure_ascii=False,
        )

        handle.write("\n")

    print()
    print(
        f"Generated {ZERO_CSV} "
        f"with {len(merged_rows)} tickers"
    )

    print(
        f"Generated {ZERO_JSON}"
    )

    print(
        f"Generated {MEMBERSHIP_JSON}"
    )

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(
            main()
        )
    except Exception as exc:
        print(
            f"ERROR: {exc}",
            file=sys.stderr,
        )
        raise SystemExit(1)