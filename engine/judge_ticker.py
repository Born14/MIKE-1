#!/usr/bin/env python
"""
Judge a ticker manually.

Usage:
    python judge_ticker.py NVDA call
    python judge_ticker.py SPY put --strike 580 --expiration 2026-01-10
    python judge_ticker.py TSLA call --no-llm
"""

import argparse
import sys
import os

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv
load_dotenv()

from mike1.modules.broker_factory import BrokerFactory
from mike1.modules.judge import Judge
from mike1.modules.llm_client import get_llm_client


def main():
    parser = argparse.ArgumentParser(description="Judge a trade candidate")
    parser.add_argument("symbol", help="Ticker symbol (e.g., NVDA)")
    parser.add_argument("direction", choices=["call", "put"], help="Trade direction")
    parser.add_argument("--strike", type=float, help="Strike price (optional)")
    parser.add_argument("--expiration", help="Expiration date YYYY-MM-DD (optional)")
    parser.add_argument("--no-llm", action="store_true", help="Skip LLM catalyst scoring")
    parser.add_argument("--paper-broker", action="store_true", help="Use paper broker instead of Alpaca")

    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"MIKE-1 JUDGE - Evaluating {args.symbol} {args.direction.upper()}")
    print(f"{'='*60}\n")

    # Create broker
    if args.paper_broker:
        from mike1.modules.broker import PaperBroker
        broker = PaperBroker()
        broker.connect()
        print("Using paper broker (limited data)")
    else:
        broker = BrokerFactory.create("alpaca")
        if not broker.connect():
            print("ERROR: Failed to connect to broker. Check your .env file.")
            sys.exit(1)
        print(f"Connected to Alpaca broker")

    # Create LLM client
    llm_client = None
    if not args.no_llm:
        llm_client = get_llm_client()
        if llm_client:
            print("LLM client ready (Gemini)")
        else:
            print("LLM not configured - catalyst scoring disabled")
            print("Set GEMINI_API_KEY in .env to enable")

    # Create Judge
    judge = Judge(broker, llm_client)

    # Get verdict
    print(f"\nFetching data for {args.symbol}...\n")

    verdict = judge.grade(
        symbol=args.symbol,
        direction=args.direction,
        strike=args.strike,
        expiration=args.expiration
    )

    # Print explanation
    print(judge.explain(verdict))

    # Print raw technical data if available
    if verdict.technical:
        tech = verdict.technical
        print(f"\n--- Technical Data ---")
        print(f"Price:        ${tech.current_price:.2f}")
        print(f"Volume:       {tech.current_volume:,} ({tech.volume_ratio:.1f}x avg)")
        print(f"VWAP:         ${tech.vwap:.2f} (price {'+' if tech.price_vs_vwap > 0 else ''}{tech.price_vs_vwap:.1f}%)")
        print(f"RSI(14):      {tech.rsi_14:.1f}")

    # Print liquidity data if available
    if verdict.liquidity:
        liq = verdict.liquidity
        print(f"\n--- Liquidity Data ---")
        print(f"Strike:       ${liq.strike:.2f} {liq.option_type}")
        print(f"Expiration:   {liq.expiration}")
        print(f"Open Interest:{liq.open_interest:,}")
        print(f"Volume:       {liq.volume:,}")
        if liq.vol_oi_ratio > 0:
            unusual_flag = " ** UNUSUAL **" if liq.is_unusual_activity else ""
            print(f"Vol/OI Ratio: {liq.vol_oi_ratio:.2f}x{unusual_flag}")
        print(f"Bid/Ask:      ${liq.bid:.2f} / ${liq.ask:.2f}")
        print(f"Spread:       ${liq.spread:.2f} ({liq.spread_pct:.1f}%)")
        print(f"Delta:        {liq.delta:.2f}")

    # Print catalyst data if available
    if verdict.catalyst:
        cat = verdict.catalyst
        print(f"\n--- Catalyst Data ---")

        # Social sentiment (always show if we have data)
        if cat.social_volume > 0:
            print(f"StockTwits:   {cat.social_volume} msgs ({cat.social_bullish_pct:.0f}% bullish)")
        if cat.reddit_volume > 0:
            print(f"Reddit:       {cat.reddit_volume} posts ({cat.reddit_bullish_pct:.0f}% bullish)")

        if cat.has_catalyst:
            print(f"Mention Type: {cat.mention_type.upper()}")
            print(f"Summary:      {cat.catalyst_summary}")
            print(f"Sentiment:    {cat.sentiment} ({cat.confidence:.0%} confidence)")
            if cat.reasoning:
                print(f"Reasoning:    {cat.reasoning}")
        else:
            print(f"News:         No significant catalyst detected")

    print(f"\n{'='*60}")
    print(f"VERDICT: {verdict.grade.value}-TIER ({verdict.score:.1f}/10)")
    print(f"{'='*60}\n")

    return 0 if verdict.grade.value != "NO" else 1


if __name__ == "__main__":
    sys.exit(main())
