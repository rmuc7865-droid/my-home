#!/usr/bin/env python3

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path("/opt/home-monitor")
ZERO_JSON = ROOT / "config" / "zero.json"
OUTDIR = ROOT / "config"


# Instruments explicitly approved for the Massive / Polygon
# U.S. stocks endpoint.
#
# Key: ISIN
# Value: Massive ticker symbol
MASSIVE_STOCK_TICKERS = {
    "US69608A1088": "PLTR",
    "US4576422053": "INOD",
    "CH0334081137": "CRSP",
    "US7707001027": "HOOD",
    "US48251W1045": "KKR",
    "CA2449161025": "DEFI",
    "US68389X1054": "ORCL",
    "US02079K3059": "GOOGL",
    "US30303M1027": "META",
    "US0846707026": "BRK.B",
    "US19260Q1076": "COIN",
    "US09290D1019": "BLK",
    "US67059N1081": "NTNX",
    "US8740541094": "TTWO",
    "US8522341036": "XYZ",
    "US22788C1053": "CRWD",
    "US5324571083": "LLY",
    "US44812J1043": "HUT",
    "US0420682058": "ARM",
    "US5398301094": "LMT",
    "US83088M1027": "SWKS",
    "US5128073062": "LRCX",
    "NL0010273215": "ASML",
    "US5949724083": "MSTR",
    "US11135F1012": "AVGO",
    "US26740W1099": "QBTS",
    "US36467W1099": "GME",
    "US64110L1061": "NFLX",
    "US0231351067": "AMZN",
    "US46625H1005": "JPM",
    "US5738741041": "MRVL",
    "US18915M1071": "NET",
    "US8334451098": "SNOW",
    "US02079K1079": "GOOG",
    "US0378331005": "AAPL",
    "US5949181045": "MSFT",
    "US67066G1040": "NVDA",
    "US88160R1014": "TSLA",
}


# Instruments collected through Twelve Data / international
# market feeds.
#
# Key: ISIN
TWELVE_DATA_TICKERS = {
    # Germany / Xetra
    "DE0006289382": {
        "symbol": "EXI2",
        "exchange": "XETR",
    },
    "DE000ENER6Y0": {
        "symbol": "ENR",
        "exchange": "XETR",
    },
    "DE0005110001": {
        "symbol": "A1OS",
        "exchange": "XETR",
    },
    "NL0012044747": {
        "symbol": "RDC",
        "exchange": "XETR",
    },
    "DE000A3E00M1": {
        "symbol": "IOS",
        "exchange": "XETR",
    },
    "DE000A1K0235": {
        "symbol": "SMHN",
        "exchange": "XETR",
    },
    "DE0005419105": {
        "symbol": "COK",
        "exchange": "XETR",
    },
    "DE000RENK730": {
        "symbol": "R3NK",
        "exchange": "XETR",
    },
    "DE000A1TNV91": {
        "symbol": "ADE",
        "exchange": "XETR",
    },
    "DE000A161408": {
        "symbol": "HFG",
        "exchange": "XETR",
    },
    "DE000HAG0005": {
        "symbol": "HAG",
        "exchange": "XETR",
    },

    # Italy
    "IT0003856405": {
        "symbol": "LDO",
        "exchange": "MTA",
    },
    "IT0005218380": {
        "symbol": "BAMI",
        "exchange": "MTA",
    },

    # Hong Kong
    "CNE100000296": {
        "symbol": "1211",
        "exchange": "HKEX",
    },
    "IT0003874101": {
        "symbol": "1913",
        "exchange": "HKEX",
    },

    # Japan
    "JP3122400009": {
        "symbol": "6857",
        "exchange": "JPX",
    },
    "JP3802300008": {
        "symbol": "9983",
        "exchange": "JPX",
    },

    # Denmark
    "DK0062266474": {
        "symbol": "GUBRA",
        "exchange": "OMXC",
    },
    "DK0062498333": {
        "symbol": "NOVO.B",
        "exchange": "OMXC",
    },

    # France
    "FR0000121014": {
        "symbol": "MC",
        "exchange": "EURONEXT",
    },

    # US OTC depositary receipts
    "US78392B1070": {
        "symbol": "HXSCL",
        "exchange": "OTC",
    },
    "US7960542030": {
        "symbol": "SSDIY",
        "exchange": "OTC",
    },
}


CRYPTO_TICKERS = {
    "BTC": "X:BTCUSD",
}


def write_json(
    path: Path,
    payload,
) -> None:
    path.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def load_zero() -> list[dict]:
    if not ZERO_JSON.exists():
        sys.exit(
            f"Missing generated watchlist file: "
            f"{ZERO_JSON}"
        )

    try:
        payload = json.loads(
            ZERO_JSON.read_text(
                encoding="utf-8"
            )
        )
    except Exception as exc:
        sys.exit(
            f"Cannot read {ZERO_JSON}: {exc}"
        )

    if not isinstance(payload, list):
        sys.exit(
            f"{ZERO_JSON} must contain a JSON list"
        )

    return payload


def main() -> None:
    positions = load_zero()

    massive_tickers: list[str] = []
    international_tickers: list[dict] = []
    crypto_tickers: list[str] = []
    unresolved: list[dict] = []

    seen_massive: set[str] = set()
    seen_international: set[tuple[str, str]] = set()
    seen_crypto: set[str] = set()

    for position in positions:
        ticker = str(
            position.get("Ticker") or ""
        ).strip().upper()

        isin = str(
            position.get("ISIN") or ""
        ).strip().upper()

        name = str(
            position.get("Name") or ""
        ).strip()

        if not ticker:
            unresolved.append(
                {
                    "Ticker": "",
                    "ISIN": isin,
                    "Name": name,
                    "reason": "Missing canonical ticker",
                }
            )
            continue

        #
        # Crypto
        #
        crypto_symbol = (
            CRYPTO_TICKERS.get(isin)
            or CRYPTO_TICKERS.get(ticker)
        )

        if crypto_symbol:
            if crypto_symbol not in seen_crypto:
                seen_crypto.add(
                    crypto_symbol
                )
                crypto_tickers.append(
                    crypto_symbol
                )
            continue

        #
        # Massive / Polygon stocks
        #
        massive_ticker = (
            MASSIVE_STOCK_TICKERS.get(isin)
        )

        if massive_ticker:
            if massive_ticker not in seen_massive:
                seen_massive.add(
                    massive_ticker
                )
                massive_tickers.append(
                    massive_ticker
                )
            continue

        #
        # Twelve Data / international
        #
        international = (
            TWELVE_DATA_TICKERS.get(isin)
        )

        if international:
            symbol = str(
                international["symbol"]
            )
            exchange = str(
                international["exchange"]
            )

            dedup_key = (
                symbol,
                exchange,
            )

            if dedup_key not in seen_international:
                seen_international.add(
                    dedup_key
                )

                international_tickers.append(
                    {
                        "symbol": symbol,
                        "exchange": exchange,
                        "isin": isin,
                        "name": name,
                        "zero_ticker": ticker,
                    }
                )

            continue

        unresolved.append(
            {
                "Ticker": ticker,
                "ISIN": isin,
                "Name": name,
                "reason": "No provider mapping",
            }
        )

    OUTDIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    write_json(
        OUTDIR / "tickers.json",
        {
            "tickers": massive_tickers,
        },
    )

    write_json(
        OUTDIR / "international_tickers.json",
        {
            "tickers": international_tickers,
        },
    )

    write_json(
        OUTDIR / "crypto_tickers.json",
        {
            "tickers": crypto_tickers,
        },
    )

    write_json(
        OUTDIR / "unresolved_tickers.json",
        {
            "unresolved": unresolved,
        },
    )

    print(
        f"Massive/Polygon stocks: "
        f"{len(massive_tickers)}"
    )

    print(
        f"International instruments: "
        f"{len(international_tickers)}"
    )

    print(
        f"Crypto instruments: "
        f"{len(crypto_tickers)}"
    )

    print(
        f"Unresolved instruments: "
        f"{len(unresolved)}"
    )

    if unresolved:
        print()
        print("Unresolved:")

        for row in unresolved:
            print(
                f"  {row['Ticker'] or '-'} "
                f"{row['ISIN'] or '-'} "
                f"{row['Name']}"
            )


if __name__ == "__main__":
    main()
