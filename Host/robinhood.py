# robinhood.py
import robin_stocks.robinhood as r
import pyotp
import requests


def get_robinhood_positions(username, password, totp_secret):
    """
    Returns a dict with 'equity' and 'positions' list.
    """
    try:
        totp = pyotp.TOTP(totp_secret).now()
        # Robinhood login call.
        login = r.login(username, password, expiresIn=2592000, mfa_code=totp)

        # Check if login dict is valid or if it failed completely
        if not login or 'access_token' not in login:
            print("[Robinhood] Login returned invalid response (possibly 2FA failed).")
            return None

        # Get total equity
        profile = r.profiles.load_portfolio_profile()
        equity = float(profile['equity']) if profile else 0.0

        # Get holdings
        holdings_raw = r.build_holdings()

        positions = []
        for sym, data in holdings_raw.items():
            try:
                val = float(data.get('equity', 0))
                qty = float(data.get('quantity', 0))
                buy_price = float(data.get('average_buy_price', 0))
                price = float(data.get('price', 0))

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
                print(f"[Robinhood] Error parsing stock {sym}: {e}")

        positions.sort(key=lambda x: x['value'], reverse=True)

        return {
            "equity": equity,
            "positions": positions
        }

    except Exception as e:
        # Re-raise the exception so app.py can see the 429 details
        raise e


def get_robinhood_balance(username, password, totp_secret):
    # Backward compatibility wrapper
    try:
        data = get_robinhood_positions(username, password, totp_secret)
        return data['equity'] if data else None
    except:
        return None