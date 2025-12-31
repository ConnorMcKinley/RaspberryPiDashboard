# robinhood.py
import robin_stocks.robinhood as r
import pyotp


def get_robinhood_positions(username, password, totp_secret):
    """
    Returns a dict with 'equity' and 'positions' list.
    Positions: [{'symbol': 'AAPL', 'value': 150.00, 'pct_gain': 5.4, 'quantity': 1}]
    """
    try:
        totp = pyotp.TOTP(totp_secret).now()
        login = r.login(username, password, expiresIn=2592000, mfa_code=totp)
        if 'access_token' not in login:
            return None

        # Get total equity
        profile = r.profiles.load_portfolio_profile()
        equity = float(profile['equity']) if profile else 0.0

        # Get holdings
        # build_holdings returns a dict keyed by symbol
        # { 'AAPL': {'price': '...', 'quantity': '...', 'average_buy_price': '...', 'equity': '...', ...} }
        holdings_raw = r.build_holdings()

        positions = []
        for sym, data in holdings_raw.items():
            try:
                val = float(data.get('equity', 0))
                qty = float(data.get('quantity', 0))
                buy_price = float(data.get('average_buy_price', 0))
                price = float(data.get('price', 0))

                # Calculate Gain %
                # (Current Price - Avg Buy) / Avg Buy
                if buy_price > 0:
                    pct_gain = ((price - buy_price) / buy_price) * 100
                else:
                    pct_gain = 0.0

                positions.append({
                    "symbol": sym,
                    "value": val,
                    "pct_gain": pct_gain,
                    "quantity": qty
                })
            except Exception as e:
                print(f"Error parsing RH stock {sym}: {e}")

        # Sort by value
        positions.sort(key=lambda x: x['value'], reverse=True)

        return {
            "equity": equity,
            "positions": positions
        }

    except Exception as e:
        print(f"Robinhood error: {e}")
        return None


def get_robinhood_balance(username, password, totp_secret):
    # Backward compatibility wrapper
    data = get_robinhood_positions(username, password, totp_secret)
    return data['equity'] if data else None