#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import re
import shutil
import sys
from datetime import datetime, timezone

ROOT = Path('.').resolve()
NOTIFIER = ROOT / 'server' / 'telegram_notifier.py'
DASHBOARD = ROOT / 'dashboard' / 'streamlit_app.py'
CONFIG = ROOT / 'server' / 'telegram_notifications.yaml'
STAMP = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def replace_once(text, old, new, label):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly 1 anchor, found {count}')
    return text.replace(old, new, 1)


def make_backup(path):
    backup = path.with_name(path.name + f'.bak-max-open-{STAMP}')
    shutil.copy2(path, backup)
    return backup


def patch_config(text):
    m = re.search(r'(?ms)^buy:\n(.*?)(?=^[A-Za-z_][A-Za-z0-9_]*:\s*$|\Z)', text)
    if not m:
        raise RuntimeError('Config: top-level buy section not found')
    block = m.group(0)
    if re.search(r'(?m)^\s+max_open_tickers\s*:', block):
        return text
    lines = block.splitlines(True)
    insert_at = 1
    for i, line in enumerate(lines[1:], start=1):
        if re.match(r'\s+(minimum_closeb_percent|minimum_closeb_count|baseline_hours)\s*:', line):
            insert_at = i + 1
    lines.insert(insert_at, '  max_open_tickers: 10\n')
    new_block = ''.join(lines)
    return text[:m.start()] + new_block + text[m.end():]


def patch_notifier(text):
    if 'BUY portfolio cap reached:' not in text:
        m = re.search(
            r'(?ms)(    if isinstance\(open_payload, dict\):.*?'
            r'    else:\n        open_tickers = \{.*?\n        \}\n)\n'
            r'(    excluded_open = \[)',
            text,
        )
        if not m:
            raise RuntimeError('Notifier: open_tickers/excluded_open anchor not found')
        cap = '''\n    # BUY portfolio cap: never let a BUY batch take the simulator above the\n    # configured maximum number of simultaneously OPEN tickers.\n    max_open_tickers = max(1, int(rule.get("max_open_tickers", 10)))\n    open_ticker_count = len(open_tickers)\n    available_open_slots = max(0, max_open_tickers - open_ticker_count)\n\n    if available_open_slots <= 0:\n        logger.info(\n            "BUY portfolio cap reached: open=%d max=%d; no new BUYs",\n            open_ticker_count,\n            max_open_tickers,\n        )\n        state["buy_condition_active"] = False\n        return\n'''
        text = text[:m.end(1)] + cap + '\n' + text[m.end(1):]

    if 'buy_batch_limit = min(6, available_open_slots)' not in text:
        if '    selected = trading_eligible[:6]\n' in text:
            text = text.replace(
                '    selected = trading_eligible[:6]\n',
                '    # Keep the existing maximum-six BUY batch, but never exceed portfolio capacity.\n'
                '    buy_batch_limit = min(6, available_open_slots)\n'
                '    selected = trading_eligible[:buy_batch_limit]\n',
                1,
            )
        else:
            raise RuntimeError('Notifier: selected = trading_eligible[:6] anchor not found')

    return text


def patch_dashboard(text):
    if 'BUY_MAX_OPEN_TICKERS' not in text:
        anchor = 'BUY_MIN_CLOSEB_PERCENT = float(BUY_CONFIG.get("minimum_closeb_percent", 2.0))'
        if anchor not in text:
            raise RuntimeError('Dashboard: BUY_MIN_CLOSEB_PERCENT anchor not found')
        text = text.replace(
            anchor,
            anchor + '\nBUY_MAX_OPEN_TICKERS = max(1, int(BUY_CONFIG.get("max_open_tickers", 10)))',
            1,
        )

    # Settings page.
    if 'Portfolio: maximum OPEN tickers' not in text:
        anchor = '        sell_col1, sell_col2 = st.columns(2)\n'
        block = '''        new_max_open_tickers = st.number_input(\n            "Portfolio: maximum OPEN tickers",\n            min_value=1,\n            max_value=5000,\n            value=int(editable_buy.get("max_open_tickers", 10)),\n            step=1,\n            help=(\n                "Hard cap for simultaneously OPEN simulated positions. "\n                "If 8 are OPEN and the maximum is 10, at most 2 new BUYs "\n                "can be created in the next BUY batch."\n            ),\n        )\n\n'''
        text = replace_once(text, anchor, block + anchor, 'Settings max-open input')

        validation = '        if new_closeb_count < 1:\n            errors.append("C2 minimum ticker count must be at least 1.")\n'
        text = replace_once(
            text,
            validation,
            validation + '        if new_max_open_tickers < 1:\n            errors.append("Portfolio maximum OPEN tickers must be at least 1.")\n',
            'Settings max-open validation',
        )

        save_anchor = '            editable_buy["minimum_closeb_percent"] = float(new_closeb_percent)\n'
        text = replace_once(
            text,
            save_anchor,
            save_anchor + '            editable_buy["max_open_tickers"] = int(new_max_open_tickers)\n',
            'Settings max-open save',
        )

    # Zero-Trading page.
    zstart = text.find('if page == "Zero-Trading":')
    zend = text.find('elif page == "Last Data":', zstart)
    if zstart != -1 and zend != -1:
        section = text[zstart:zend]
        if 'zero_available_open_slots' not in section:
            c2_anchor = '''            c2_group_count = int(\n                (closeb_numeric >= BUY_MIN_CLOSEB_PERCENT).sum()\n            )\n'''
            portfolio = c2_anchor + '''\n            try:\n                zero_sim_payload = load_simulation_payload_cached()\n                if isinstance(zero_sim_payload, dict):\n                    zero_sim_rows = (\n                        zero_sim_payload.get("trades")\n                        or zero_sim_payload.get("rows")\n                        or zero_sim_payload.get("items")\n                        or []\n                    )\n                else:\n                    zero_sim_rows = zero_sim_payload or []\n                zero_open_count = sum(\n                    1\n                    for trade in zero_sim_rows\n                    if (\n                        str(trade.get("Status") or "").strip().upper() == "OPEN"\n                        or not trade.get("SellTime")\n                    )\n                )\n            except Exception:\n                zero_open_count = 0\n\n            zero_max_open = BUY_MAX_OPEN_TICKERS\n            zero_available_open_slots = max(0, zero_max_open - zero_open_count)\n            portfolio_cols = st.columns(3)\n            portfolio_cols[0].metric("OPEN tickers", zero_open_count)\n            portfolio_cols[1].metric("Maximum OPEN", zero_max_open)\n            portfolio_cols[2].metric("Available BUY slots", zero_available_open_slots)\n            if zero_available_open_slots <= 0:\n                st.warning(\n                    f"BUY portfolio limit reached: {zero_open_count}/{zero_max_open} OPEN tickers. "\n                    "New BUYs are blocked until a position is closed or the limit is increased in Settings."\n                )\n'''
            section = replace_once(section, c2_anchor, portfolio, 'Zero-Trading portfolio metrics')

            sell_anchor = '            if "ShouldSell" in advisor_live.columns:\n'
            cap_block = '''            if zero_available_open_slots <= 0:\n                buy_decision = pd.Series(False, index=advisor_live.index)\n            else:\n                buy_candidates = advisor_live.loc[buy_decision].copy()\n                if not buy_candidates.empty:\n                    buy_candidates["_PortfolioCloseBSort"] = pd.to_numeric(\n                        buy_candidates.get("CloseB"), errors="coerce"\n                    )\n                    allowed_buy_indexes = set(\n                        buy_candidates.sort_values(\n                            ["_PortfolioCloseBSort", "Ticker"],\n                            ascending=[False, True],\n                            na_position="last",\n                        )\n                        .head(zero_available_open_slots)\n                        .index\n                        .tolist()\n                    )\n                    buy_decision = pd.Series(\n                        [\n                            bool(buy_decision.loc[idx]) and idx in allowed_buy_indexes\n                            for idx in advisor_live.index\n                        ],\n                        index=advisor_live.index,\n                    )\n\n'''
            section = replace_once(section, sell_anchor, cap_block + sell_anchor, 'Zero-Trading BUY cap')
        text = text[:zstart] + section + text[zend:]

    # Sim-Trading page metrics.
    sstart = text.find('elif page == "Sim-Trading":')
    if sstart != -1:
        ends = [x for x in [
            text.find('elif page == "Effective Trading":', sstart),
            text.find('elif page == "Resources":', sstart),
            text.find('elif page == "System Health":', sstart),
        ] if x != -1]
        send = min(ends) if ends else len(text)
        section = text[sstart:send]
        if 'current_available_slots' not in section:
            sim_anchor = '''            shown["SimStatus"] = shown["Status"].map(\n                lambda value:\n                str(value).strip().upper()\n                if pd.notna(value) and str(value).strip()\n                else "NO TRADE"\n            )\n\n'''
            metrics = sim_anchor + '''            current_open_count = int((shown["SimStatus"] == "OPEN").sum())\n            current_available_slots = max(0, BUY_MAX_OPEN_TICKERS - current_open_count)\n            portfolio_cols = st.columns(3)\n            portfolio_cols[0].metric("OPEN tickers", current_open_count)\n            portfolio_cols[1].metric("Maximum OPEN", BUY_MAX_OPEN_TICKERS)\n            portfolio_cols[2].metric("Available BUY slots", current_available_slots)\n\n'''
            if sim_anchor in section:
                section = section.replace(sim_anchor, metrics, 1)
            else:
                raise RuntimeError('Dashboard: SimStatus anchor not found')
        text = text[:sstart] + section + text[send:]

    return text


def main():
    for p in (NOTIFIER, DASHBOARD, CONFIG):
        if not p.exists():
            raise RuntimeError(f'Missing required file: {p}')

    originals = {
        CONFIG: CONFIG.read_text(encoding='utf-8'),
        NOTIFIER: NOTIFIER.read_text(encoding='utf-8'),
        DASHBOARD: DASHBOARD.read_text(encoding='utf-8'),
    }
    updated = {
        CONFIG: patch_config(originals[CONFIG]),
        NOTIFIER: patch_notifier(originals[NOTIFIER]),
        DASHBOARD: patch_dashboard(originals[DASHBOARD]),
    }
    changed = [p for p in updated if updated[p] != originals[p]]
    if not changed:
        print('No changes required; feature already installed.')
        return 0

    backups = {}
    try:
        for p in changed:
            backups[p] = make_backup(p)
        for p in changed:
            p.write_text(updated[p], encoding='utf-8')
    except Exception:
        for p, b in backups.items():
            if b.exists():
                shutil.copy2(b, p)
        raise

    print('SUCCESS: maximum OPEN ticker feature installed.')
    print('Default max_open_tickers: 10')
    for p in changed:
        print('Changed:', p.relative_to(ROOT))
    for p, b in backups.items():
        print('Backup:', b.relative_to(ROOT))
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        raise
