# Manual Ticker Input for Scout

This directory contains user-specific data files for feeding tickers to Scout.

## Quick Start

```bash
# 1. Screen on Finviz/TradingView/external tools
# Find: AAPL (earnings), NVDA (volume spike), UPST (news)

# 2. Add tickers to manual_tickers.txt
echo -e "AAPL\nNVDA\nUPST" > data/manual_tickers.txt

# 3. Run Scout (when implemented)
python run_scout.py

# Scout will scan: 3 manual + 4 core + 18 categories = 25 total (deduplicated)
```

## File Format: `manual_tickers.txt`

```
# Comments start with #
# One ticker per line
# Empty lines ignored
# Tickers auto-uppercased

AAPL
NVDA
UPST
```

## How It Works

### Ticker Sources (Priority Order)

Scout reads tickers from **4 sources**:

1. **Manual** (`data/manual_tickers.txt`) - HIGHEST PRIORITY
   - Your external screening results
   - Max age: 24 hours (configurable)

2. **Core** (config) - Always monitored
   - SPY, QQQ, NVDA, TSLA

3. **Categories** (config) - Pre-defined watchlists
   - Tech: 8 stocks
   - Biotech: 3 stocks
   - Momentum: 4 stocks
   - ETFs: 1 stock

4. **Screener** (future) - Automated scanning
   - Disabled until screener module built

### Deduplication

If same ticker appears in multiple sources, Scout scans it **once**.

Example:
- Manual: `NVDA, AAPL, UPST`
- Core: `SPY, QQQ, NVDA, TSLA` (NVDA duplicate)
- Result: `NVDA, AAPL, UPST, SPY, QQQ, TSLA` (NVDA from manual, not core)

### File Expiration

Manual file expires after **24 hours**. This prevents stale screening results from being used.

To change:
```yaml
# config/default.yaml
basket:
  manual:
    max_age_hours: 48  # 2 days
```

## Configuration

```yaml
# config/default.yaml
basket:
  # Enable/disable each source
  manual:
    enabled: true
    file: "data/manual_tickers.txt"
    max_age_hours: 24

  core:
    enabled: true
    tickers:
      - SPY
      - QQQ
      - NVDA
      - TSLA

  categories:
    enabled: true
    tech: [AMD, SMCI, PLTR, ...]
    biotech: [LLY, MRNA, NVO]
    momentum: [MSTR, GME, COIN]
    etfs: [IWM]

  screener:
    enabled: false  # Future

  deduplicate: true
```

## External Screening Tools

### Finviz
```
1. Go to finviz.com/screener.ashx
2. Set filters:
   - Volume: Over 1M
   - Price: Over $5
   - Options: Optionable
3. Export results
4. Copy tickers to manual_tickers.txt
```

### TradingView
```
1. Create screener with filters
2. Add tickers with alerts to manual file
```

### Manual Watchlist
```
1. Check Twitter/Discord for ideas
2. Validate with quick charts
3. Add promising tickers to manual file
```

## Tips

- **Update daily**: Refresh manual_tickers.txt each morning
- **Be selective**: Scout works best with 5-10 manual tickers, not 50
- **Use comments**: Document why you added each ticker
- **Remove stale**: Delete tickers after catalyst passes

## Example Workflow

```bash
# Monday morning: Check for catalysts
# - AAPL has earnings today
# - NVDA showing volume spike pre-market
# - UPST had news over weekend

# Add to manual file
cat > data/manual_tickers.txt << EOF
# Monday 2026-01-06 screening
AAPL   # Earnings today
NVDA   # Volume spike
UPST   # Weekend news
EOF

# Run Scout (validates catalysts, passes to Curator/Judge)
python run_scout.py

# Scout output:
# [Scout] ✅ AAPL: Earnings catalyst (call)
# [Scout] ✅ NVDA: Volume spike 3.2x (call)
# [Scout] ❌ UPST: No clear catalyst
# [Curator] Finding options for AAPL, NVDA...
# [Judge] AAPL: A-TIER (7.8/10)
# [Judge] NVDA: B-TIER (6.2/10)
# [Executor] Executing AAPL trade...
```

## Troubleshooting

**File not found:**
```bash
# Check file exists
ls -la data/manual_tickers.txt

# Check file age
stat data/manual_tickers.txt
```

**Tickers not loading:**
```python
# Debug in Python
from mike1.core.config import Config
config = Config.load()
print(config.basket._read_manual_file())  # Should show your tickers
```

**Tickers not prioritized:**
```python
# Check order
config = Config.load()
all_tickers = config.basket.all_tickers
print(all_tickers[:5])  # First 5 should include manual tickers
```

## .gitignore

`manual_tickers.txt` is gitignored (user-specific data). Each user maintains their own screening results.
