"""Quick test to verify Alpaca connection works."""

import os
import sys

# Add the src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from dotenv import load_dotenv

# Load .env from project root
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

def test_connection():
    """Test Alpaca paper trading connection."""
    from mike1.modules.broker_alpaca import AlpacaBroker

    api_key = os.getenv("ALPACA_API_KEY")
    secret_key = os.getenv("ALPACA_SECRET_KEY")

    if not api_key or not secret_key:
        print("ERROR: Missing ALPACA_API_KEY or ALPACA_SECRET_KEY in .env")
        return False

    print(f"API Key: {api_key[:8]}...")
    print(f"Secret Key: {secret_key[:8]}...")
    print()

    # Create broker instance
    broker = AlpacaBroker(
        api_key=api_key,
        secret_key=secret_key,
        paper=True
    )

    # Try to connect
    print("Connecting to Alpaca Paper Trading...")
    if broker.connect():
        print("SUCCESS: Connected to Alpaca!")
        print()

        # Get account info
        account = broker.get_account_info()
        print("Account Info:")
        print(f"  Buying Power: ${account.get('buying_power', 'N/A')}")
        print(f"  Cash: ${account.get('cash', 'N/A')}")
        print(f"  Portfolio Value: ${account.get('portfolio_value', 'N/A')}")
        print(f"  Options Enabled: {account.get('options_trading_level', 'N/A')}")
        print()

        # Get a stock price
        print("Testing stock price fetch...")
        try:
            price = broker.get_stock_price("SPY")
            print(f"  SPY Price: ${price}")
        except Exception as e:
            print(f"  Stock price error: {e}")

        broker.disconnect()
        return True
    else:
        print("FAILED: Could not connect to Alpaca")
        return False

if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)
