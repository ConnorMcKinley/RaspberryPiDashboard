# robinhood.py
import robin_stocks.robinhood as r
import pyotp
import getpass

def get_robinhood_balance(username, password, totp_secret):
    try:
        totp = pyotp.TOTP(totp_secret).now()
        login = r.login(username, password, expiresIn=2592000, mfa_code=totp)
        if 'access_token' not in login:
            return None
        profile = r.profiles.load_portfolio_profile()
        equity = float(profile['equity'])
        return equity
    except Exception as e:
        print(f"Robinhood error: {e}")
        return None

def login_robinhood(username, password, totp_secret):
    # Generate TOTP code
    totp = pyotp.TOTP(totp_secret).now()
    print(f"Using TOTP Code: {totp}")

    # Login with TOTP
    login = r.login(username, password, expiresIn=2592000, mfa_code=totp)
    if 'access_token' not in login:
        raise Exception(f"Login failed: {login}")
    print("Login successful.")

def get_balance():
    profile = r.profiles.load_portfolio_profile()
    equity = float(profile['equity'])
    return equity
