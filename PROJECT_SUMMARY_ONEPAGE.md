# Local Buy and Sell - One-Page Summary

## What it is
A practice app for trading stocks. One trainee buys and sells pretend stocks
using pretend money, so they can learn how trading works with zero risk.

## How it is built
Three layers working together:
- **Browser** - the web pages the user clicks on.
- **Flask app (Python)** - the brain that checks the rules and does the work.
- **MySQL database** - the memory that stores money, stocks, and trade history.

Money is always exact (stored as `Decimal` like 50.00), so no cents are ever lost.

## What the user can do
- See a **dashboard** with their cash and what they own.
- Browse a **list of stocks** and open one for details.
- **Buy** shares, **sell** shares, **review** a trade before confirming, or **cancel** it.

## A quick example
Start: $10,000 cash. DEMO1 stock costs $50.
- **Buy 5 shares** -> costs $250 -> cash becomes $9,750, now owns 5 shares.
- **Sell 2 shares** -> gets $100 -> cash becomes $9,850, now owns 3 shares.

## Why trades are safe
- The app checks everything on its own side (never trusts the web page).
- A trade is **all-or-nothing** - it fully happens or fully undoes itself.
- Rules are checked twice (at review and at confirm).
- Clicking confirm twice still runs the trade only once.
- If the price changed, the app asks the user to review again.
- Bad input is rejected with a clear message (e.g. "Not enough money.").

## Does it work?
Yes. **59 automated tests all pass**, including the exact buy and sell examples
above. Saved results survive restarting the app, and refreshing a page never
re-runs a trade.

## How to run it
1. Create two databases (`sts` and `sts_test`).
2. Copy `.env.example` to `.env` and fill in the details.
3. `pip install -r requirements.txt`
4. `python scripts/init_db.py` then `python scripts/seed_demo.py`
5. Start it: set `FLASK_APP=app:create_app`, then `flask run`
6. Test it: `pytest`

## Status
Phase 1 is complete and verified. Future phases could add more users, an admin
area, and live stock prices.
