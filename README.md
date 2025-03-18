# STEADYPULSE

## Trading bot running on Bybit, OKX, Bitget, GateIO, Binance, and Hyperliquid

:warning: **Use at your own risk** :warning:

v1.0.0

## Overview

STEADYPULSE is a cryptocurrency trading bot written in Python and Rust, designed to require minimal user intervention.

It operates on perpetual futures derivatives markets, automatically creating and canceling limit buy and sell orders on behalf of the user. It does not attempt to predict future price movements, use technical indicators, or follow trends. Instead, it functions as a contrarian market maker, providing resistance to price fluctuations in both directions, effectively stabilizing market prices.

STEADYPULSE allows backtesting on historical price data using its built-in backtester, which leverages Rust for high-performance computing. Additionally, an optimizer is included to fine-tune configurations by iterating thousands of backtests and using an evolutionary algorithm to find optimal parameters.

## Strategy

Inspired by the Martingale betting strategy, the bot starts with a small initial entry and increases its position size on losing trades to bring the average entry price closer to the current market price. Orders are placed in a grid, absorbing rapid price movements. After each re-entry, the bot updates its closing orders at a preset take-profit markup. Even minor market reversals allow the bot to close trades in profit before repeating the cycle.

### Trailing Orders
STEADYPULSE supports both grid-based and trailing orders.

- **Trailing Entries**: The bot waits for price movements beyond a threshold before placing a re-entry order after a retracement.
- **Trailing Closes**: The bot delays closing orders until the price has moved favorably by a threshold and then retraced.

These features help secure profits more effectively by reacting to market reversals dynamically.

### Forager
The Forager feature dynamically selects the most volatile markets for trading. Volatility is measured as the mean of the normalized relative range for the latest 1-minute candles:

```
mean((ohlcv.high - ohlcv.low) / ohlcv.close)
```

### Unstucking Mechanism
STEADYPULSE handles "stuck" positions by gradually realizing small losses. If multiple positions are stuck, it prioritizes the one closest to the market price for liquidation. Losses are controlled to ensure the account balance does not drop below a predefined percentage of the previous peak balance.

## Installation

To install STEADYPULSE and its dependencies, follow these steps:

### Step 1: Clone the Repository
```sh
git clone https://github.com/yourusername/steadypulse.git
cd steadypulse
```

### Step 2: Install Rust
STEADYPULSE relies on Rust for some of its components.

- Install Rust from: https://www.rust-lang.org/tools/install
- Follow the instructions to install Rustup.
- Restart your terminal after installation.

### Step 3: Create and Activate a Virtual Environment
```sh
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
venv\Scripts\activate  # Windows
```

### Step 4: Install Python Dependencies
```sh
pip install -r requirements.txt
```

### Step 5 (Optional): Build Rust Extensions
```sh
cd steadypulse-rust
maturin develop --release
cd ..
```

### Step 6: Add API Keys
```sh
cp api-keys.json.example api-keys.json
```
Edit `api-keys.json` with your API credentials.

### Step 7: Run STEADYPULSE
```sh
python3 src/main.py -u {account_name_from_api-keys.json}
```
Or specify a custom config file:
```sh
python3 src/main.py path/to/config.json
```

## Jupyter Lab
To use Jupyter Lab, activate the virtual environment and launch Jupyter from the STEADYPULSE root directory:
```sh
python3 -m jupyter lab
```

## Requirements

- Python >= 3.8
- Dependencies in `requirements.txt`

## Documentation
Find more details in the [docs/](docs/) directory.

