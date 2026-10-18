# Requirements Document

## Introduction

This document defines the requirements for Milestone / Phase 1 of the Stock Trading System (STS), a training platform that lets trainee stockbrokers practice buying and selling stocks with simulated money. This milestone delivers a working buy-and-sell workflow that runs locally on a single laptop using a three-tier architecture (browser → Flask application → MySQL database).

The scope of this milestone is deliberately narrow: a single preloaded trainee user with one practice account trades whole shares of fictional stocks at fixed prices. The system guides the trainee through a dashboard, a stock list, a stock details page, an order review page, and a confirmation page. Every record-changing action is validated before and at execution, applied as a single all-or-nothing transaction, and persisted so that balances remain correct after page refresh and application restart.

Features such as authentication, deposits and withdrawals, administrator tools, market-hours enforcement, automatic pricing, and cloud deployment are explicitly deferred and documented in the "Scope and Deferred Features" section.

## Glossary

- **STS (System)**: The Stock Trading System application delivered in this milestone, comprising the browser interface, the Flask application, and the MySQL database.
- **Trade_Engine**: The backend component of the System that performs pre-execution checks, applies trade changes, and commits or rolls back the transaction.
- **Demo_User**: The single preloaded trainee user identity that the backend selects for all operations in this milestone.
- **Account**: The single preloaded practice account that belongs to the Demo_User and holds the simulated cash balance.
- **Stock**: A fictional tradable security identified by a company name and ticker, with a fixed current price and a count of available shares.
- **Position**: A record of the number of whole shares of a specific Stock that the Account currently owns.
- **Stock_Inventory**: The count of shares of a Stock that are available for trainees to buy.
- **Order**: A record representing a trade under review, with a status of PENDING, COMPLETED, or CANCELLED.
- **Transaction**: A record of one completed buy or sell trade, created only when an Order reaches COMPLETED status.
- **EventLog**: A timestamped record of system activity connected to the Demo_User.
- **Buy**: A trade action in which the Account acquires shares of a Stock and spends cash.
- **Sell**: A trade action in which the Account disposes of shares of a Stock and receives cash.
- **Whole_Share**: A positive integer quantity of shares; fractional quantities are not permitted in this milestone.
- **Dashboard**: The page that displays the Account, the cash balance, and the current holdings.
- **Stock_List_Page**: The page that lists company names, tickers, current prices, and available shares for all Stocks.
- **Stock_Details_Page**: The page that displays a selected Stock, the shares owned, and the buy and sell forms.
- **Order_Review_Page**: The page that displays a proposed trade and the Confirm and Cancel controls before final execution.
- **Confirmation_Page**: The page that displays a completed trade and the actual saved balances.
- **Pre_Execution_Checks**: The set of validations the Trade_Engine performs when preparing the Order_Review_Page and again at confirmation.

## Requirements

### Requirement 1: Preloaded Demo Identity Selection

**User Story:** As a trainee stockbroker, I want the System to provide a ready-to-use practice identity, so that I can begin practicing trades immediately without signing in or choosing an account.

#### Acceptance Criteria

1. THE STS SHALL provide exactly one preloaded Demo_User and exactly one preloaded Account that belongs to that Demo_User.
2. WHEN the System processes any request, THE STS SHALL select the Demo_User and the Account on the backend.
3. THE STS SHALL ignore any user identity or account identity supplied by the browser.
4. THE STS SHALL initialize the Account cash balance to $10,000.00.
5. THE STS SHALL preload at least three fictional Stocks, each with a company name, a ticker, a fixed current price, and a count of available shares.
6. THE STS SHALL preload a Stock named DEMO1 with a fixed price of $50.00 per share and 1,000 available shares.
7. THE STS SHALL initialize the Position for DEMO1 at 0 shares for the Demo_User.
8. THE STS SHALL preload an existing Position in a Stock other than DEMO1 so that a Sell can be demonstrated at the start of a session.

### Requirement 2: Dashboard Display

**User Story:** As a trainee stockbroker, I want a dashboard that shows my account and holdings, so that I can see my current financial position at a glance.

#### Acceptance Criteria

1. WHEN the trainee opens the Dashboard, THE STS SHALL display the Account identifier.
2. WHEN the trainee opens the Dashboard, THE STS SHALL display the current cash balance of the Account read from the database.
3. WHEN the trainee opens the Dashboard, THE STS SHALL display each current Position with its Stock and the number of shares owned.
4. WHEN the trainee opens the Dashboard, THE STS SHALL read all displayed values from the database.

### Requirement 3: Stock List Display

**User Story:** As a trainee stockbroker, I want a list of available stocks, so that I can decide which stock to trade.

#### Acceptance Criteria

1. WHEN the trainee opens the Stock_List_Page, THE STS SHALL display each Stock with its company name, its ticker, its current price, and its count of available shares.
2. WHEN the trainee opens the Stock_List_Page, THE STS SHALL read all displayed values from the database.

### Requirement 4: Stock Details Display

**User Story:** As a trainee stockbroker, I want a detail page for a selected stock, so that I can see what I own and start a buy or sell order.

#### Acceptance Criteria

1. WHEN the trainee opens the Stock_Details_Page for a selected Stock, THE STS SHALL display the Stock company name, the ticker, and the current price read from the database.
2. WHEN the trainee opens the Stock_Details_Page for a selected Stock, THE STS SHALL display the number of shares the Account currently owns of that Stock.
3. WHEN the trainee opens the Stock_Details_Page for a selected Stock, THE STS SHALL display a buy form and a sell form.

### Requirement 5: Trade Entry

**User Story:** As a trainee stockbroker, I want to enter a buy or sell quantity and see the calculated cost, so that I understand the dollar impact before I commit.

#### Acceptance Criteria

1. WHEN the trainee submits a trade entry, THE STS SHALL accept a selected action of either Buy or Sell.
2. WHEN the trainee submits a trade entry, THE STS SHALL accept a quantity of Whole_Shares.
3. WHEN the trainee submits a valid trade entry, THE STS SHALL calculate the total dollar amount as the current database price multiplied by the quantity.
4. IF the current database price of the selected Stock is zero or unavailable, THEN THE STS SHALL block the trade and display a message that the stock price is unavailable.
5. WHEN the trainee submits a trade entry, THE STS SHALL use the HTTP POST method for the record-changing action.
6. WHEN the trainee opens or refreshes a page, THE STS SHALL display information without executing a trade.

### Requirement 6: Order Review

**User Story:** As a trainee stockbroker, I want to review a proposed trade before confirming, so that I can verify the details and avoid mistakes.

#### Acceptance Criteria

1. WHEN the trainee submits a valid trade entry, THE STS SHALL create an Order with a status of PENDING for the trade under review.
2. WHEN the Order_Review_Page is displayed, THE STS SHALL display the company name and the ticker of the selected Stock.
3. WHEN the Order_Review_Page is displayed, THE STS SHALL display the Buy or Sell action.
4. WHEN the Order_Review_Page is displayed, THE STS SHALL display the number of shares.
5. WHEN the Order_Review_Page is displayed, THE STS SHALL display the price per share.
6. WHEN the Order_Review_Page is displayed, THE STS SHALL display the total cost for a Buy or the total proceeds for a Sell.
7. WHEN the Order_Review_Page is displayed, THE STS SHALL display the expected cash balance of the Account after the trade.
8. WHEN the Order_Review_Page is displayed, THE STS SHALL display a Confirm control and a Cancel control.

### Requirement 7: Order Cancellation

**User Story:** As a trainee stockbroker, I want to cancel a pending order before confirming, so that I can back out of a trade without affecting my account.

#### Acceptance Criteria

1. WHEN the trainee activates the Cancel control for a PENDING Order, THE STS SHALL set the Order status to CANCELLED.
2. WHEN the trainee activates the Cancel control for a PENDING Order, THE STS SHALL return the trainee to the Stock_Details_Page.
3. WHEN an Order is CANCELLED, THE STS SHALL leave the cash balance, the Positions, and the Stock_Inventory unchanged.
4. WHEN an Order is CANCELLED, THE STS SHALL leave the Transaction records unchanged.
5. WHEN an Order has been submitted for final confirmation, THE STS SHALL make the Cancel control unavailable for that Order.

### Requirement 8: Pre-Execution Validation

**User Story:** As a trainee stockbroker, I want the System to validate every trade against trusted data, so that only valid trades are executed and my practice account stays accurate.

#### Acceptance Criteria

1. WHEN the Trade_Engine prepares the Order_Review_Page, THE Trade_Engine SHALL run the Pre_Execution_Checks.
2. WHEN the Trade_Engine processes a confirmation, THE Trade_Engine SHALL run the Pre_Execution_Checks again.
3. WHEN the Trade_Engine runs the Pre_Execution_Checks, THE Trade_Engine SHALL confirm that the Demo_User exists.
4. WHEN the Trade_Engine runs the Pre_Execution_Checks, THE Trade_Engine SHALL confirm that the Account exists and belongs to the Demo_User.
5. WHEN the Trade_Engine runs the Pre_Execution_Checks, THE Trade_Engine SHALL confirm that the selected Stock exists.
6. WHEN the Trade_Engine runs the Pre_Execution_Checks, THE Trade_Engine SHALL confirm that the action is Buy or Sell.
7. WHEN the Trade_Engine runs the Pre_Execution_Checks, THE Trade_Engine SHALL confirm that the quantity is a positive whole number.
8. WHEN the Trade_Engine runs the Pre_Execution_Checks, THE Trade_Engine SHALL confirm that the current database price of the Stock is greater than zero.
9. WHEN the action is Buy, THE Trade_Engine SHALL confirm that the Account cash balance is sufficient for the total cost.
10. WHEN the action is Buy, THE Trade_Engine SHALL confirm that the Stock_Inventory holds enough shares for the purchase.
11. WHEN the action is Sell, THE Trade_Engine SHALL confirm that the Position holds enough shares for the sale.
12. WHEN the Trade_Engine runs the Pre_Execution_Checks, THE Trade_Engine SHALL confirm that the Order belongs to the Account.
13. WHEN the Trade_Engine runs the Pre_Execution_Checks, THE Trade_Engine SHALL confirm that the Order status is PENDING.
14. WHEN the Trade_Engine runs the Pre_Execution_Checks, THE Trade_Engine SHALL read prices, ownership, balances, and inventory from the database.
15. THE Trade_Engine SHALL ignore price, ownership, balance, and inventory values supplied by the browser.
16. IF the current database price differs from the price shown on the reviewed Order, THEN THE Trade_Engine SHALL require a new Order_Review_Page before execution.

### Requirement 9: Failed Trade Handling

**User Story:** As a trainee stockbroker, I want a clear message and my input preserved when a trade cannot proceed, so that I can understand the problem and correct it.

#### Acceptance Criteria

1. IF a Pre_Execution_Check fails, THEN THE STS SHALL display a plain message that describes the failure.
2. IF the Account cash balance is insufficient for a Buy, THEN THE STS SHALL display the message "Not enough money."
3. IF the Position holds fewer shares than a Sell quantity, THEN THE STS SHALL display the message "Not enough shares."
4. IF the Stock_Inventory holds fewer shares than a Buy quantity, THEN THE STS SHALL display the message "Not enough shares available to buy."
5. IF the quantity is not a positive whole number, THEN THE STS SHALL display the message "Enter a positive whole number."
6. IF the Order status is not PENDING, THEN THE STS SHALL display the message "This order has already been completed or cancelled."
7. IF a Pre_Execution_Check fails, THEN THE STS SHALL retain the submitted form input so that the trainee can correct it.
8. IF a trade fails, THEN THE STS SHALL leave the cash balance, the Positions, the Stock_Inventory, and the Transaction records unchanged.
9. IF a trade fails, THEN THE STS SHALL leave the Order status unchanged from PENDING.

### Requirement 10: Successful Trade Execution

**User Story:** As a trainee stockbroker, I want a confirmed trade to apply every change together, so that my account, holdings, and records stay consistent.

#### Acceptance Criteria

1. WHEN the Trade_Engine executes a confirmed Buy, THE Trade_Engine SHALL subtract the total cost from the Account cash balance.
2. WHEN the Trade_Engine executes a confirmed Sell, THE Trade_Engine SHALL add the total proceeds to the Account cash balance.
3. WHEN the Trade_Engine executes a confirmed Buy, THE Trade_Engine SHALL create the Position if it does not exist and increase the Position by the quantity.
4. WHEN the Trade_Engine executes a confirmed Sell, THE Trade_Engine SHALL reduce the Position by the quantity.
5. WHEN a Sell reduces a Position to zero shares, THE Trade_Engine SHALL remove the Position.
6. WHEN the Trade_Engine executes a confirmed Buy, THE Trade_Engine SHALL subtract the quantity from the Stock_Inventory.
7. WHEN the Trade_Engine executes a confirmed Sell, THE Trade_Engine SHALL add the quantity to the Stock_Inventory.
8. WHEN the Trade_Engine executes a confirmed trade, THE Trade_Engine SHALL set the Order status from PENDING to COMPLETED with execution details.
9. WHEN the Trade_Engine executes a confirmed trade, THE Trade_Engine SHALL create exactly one Transaction record containing the Order, the Account, the Stock, the action, the quantity, the execution price, the total amount, and the completion time.
10. WHEN the Trade_Engine executes a confirmed trade, THE Trade_Engine SHALL create exactly one timestamped EventLog entry for the trade connected to the Demo_User.
11. WHEN the Trade_Engine executes a confirmed trade, THE Trade_Engine SHALL apply the cash change, the Position change, the Stock_Inventory change, the Order update, the Transaction record, and the EventLog entry within a single transaction.
12. IF any step of a confirmed trade fails, THEN THE Trade_Engine SHALL roll back every change made by that trade.
13. WHEN an Order is CANCELLED or a trade fails, THE Trade_Engine SHALL leave the Transaction records free of any completed-trade record for that Order.

### Requirement 11: Confirmation and Persistence

**User Story:** As a trainee stockbroker, I want to see the saved results of a completed trade, so that I can trust that my practice account is accurate.

#### Acceptance Criteria

1. WHEN a trade is executed, THE STS SHALL display the Confirmation_Page with the completed trade details.
2. WHEN the Confirmation_Page is displayed, THE STS SHALL display the actual saved cash balance and holdings read from the database.
3. WHEN the trainee refreshes the Confirmation_Page, THE STS SHALL display the saved results without executing the trade again.
4. WHEN the application is restarted, THE STS SHALL display cash balances, Positions, Stock_Inventory, Orders, Transactions, and EventLog entries that match the saved results.

### Requirement 12: Idempotency and Concurrency

**User Story:** As a trainee stockbroker, I want repeated or competing confirmations handled safely, so that I never spend the same money or shares twice.

#### Acceptance Criteria

1. WHEN two or more confirmation requests are received for the same Order, THE Trade_Engine SHALL execute the Order at most once.
2. WHEN two or more trade requests compete for the same cash or shares, THE Trade_Engine SHALL keep the cash balance, the Positions, and the Stock_Inventory correct.
3. IF a confirmation request is received for an Order that is not PENDING, THEN THE Trade_Engine SHALL reject the request and leave all balances, Positions, and inventory unchanged.

### Requirement 13: Buy Verification Scenario

**User Story:** As a trainer reviewing the system, I want a known buy scenario to produce exact results, so that I can verify the buy workflow is correct.

#### Acceptance Criteria

1. WHEN the trainee confirms a Buy of 5 DEMO1 shares at $50.00 per share from a starting cash balance of $10,000.00, THE STS SHALL set the Account cash balance to $9,750.00.
2. WHEN the trainee confirms a Buy of 5 DEMO1 shares at $50.00 per share, THE STS SHALL set the DEMO1 Position to 5 shares owned.
3. WHEN the trainee confirms a Buy of 5 DEMO1 shares from a starting DEMO1 inventory of 1,000, THE STS SHALL set the DEMO1 Stock_Inventory to 995 shares.
4. WHEN the trainee confirms a Buy of 5 DEMO1 shares, THE STS SHALL set the Order status to COMPLETED.
5. WHEN the trainee confirms a Buy of 5 DEMO1 shares, THE STS SHALL create exactly one purchase Transaction and exactly one trade EventLog entry.

### Requirement 14: Sell Verification Scenario

**User Story:** As a trainer reviewing the system, I want a known sell scenario to produce exact results, so that I can verify the sell workflow is correct.

#### Acceptance Criteria

1. WHEN the trainee confirms a Sell of 2 DEMO1 shares at $50.00 per share while owning 5 DEMO1 shares and holding a cash balance of $9,750.00, THE STS SHALL set the Account cash balance to $9,850.00.
2. WHEN the trainee confirms a Sell of 2 DEMO1 shares while owning 5 DEMO1 shares, THE STS SHALL set the DEMO1 Position to 3 shares owned.
3. WHEN the trainee confirms a Sell of 2 DEMO1 shares from a DEMO1 inventory of 995, THE STS SHALL set the DEMO1 Stock_Inventory to 997 shares.
4. WHEN the trainee confirms a Sell of 2 DEMO1 shares, THE STS SHALL set the Order status to COMPLETED.
5. WHEN the trainee confirms a Sell of 2 DEMO1 shares, THE STS SHALL create exactly one sale Transaction and exactly one trade EventLog entry.

### Requirement 15: Invalid Quantity Rejection

**User Story:** As a trainee stockbroker, I want invalid quantities rejected clearly, so that I only submit valid trades.

#### Acceptance Criteria

1. IF the submitted quantity is blank, THEN THE STS SHALL reject the trade and display the message "Enter a positive whole number."
2. IF the submitted quantity is zero, THEN THE STS SHALL reject the trade and display the message "Enter a positive whole number."
3. IF the submitted quantity is negative, THEN THE STS SHALL reject the trade and display the message "Enter a positive whole number."
4. IF the submitted quantity is fractional, THEN THE STS SHALL reject the trade and display the message "Enter a positive whole number."
5. IF the submitted quantity is nonnumeric, THEN THE STS SHALL reject the trade and display the message "Enter a positive whole number."

### Requirement 16: Invalid Trade Target Rejection

**User Story:** As a trainee stockbroker, I want trades against missing or unauthorized records rejected, so that only valid, authorized trades proceed.

#### Acceptance Criteria

1. IF the selected Stock does not exist, THEN THE STS SHALL reject the trade and display a plain failure message.
2. IF the Account does not exist, THEN THE STS SHALL reject the trade and display a plain failure message.
3. IF the Order belongs to an Account other than the Demo_User Account, THEN THE STS SHALL reject the trade and display a plain failure message.
4. IF a Buy requires more cash than the Account cash balance, THEN THE STS SHALL reject the trade and display the message "Not enough money."
5. IF a Buy requires more shares than the Stock_Inventory, THEN THE STS SHALL reject the trade and display the message "Not enough shares available to buy."
6. IF a Sell requires more shares than the Position holds, THEN THE STS SHALL reject the trade and display the message "Not enough shares."

### Requirement 17: Full-Holding Sale and Rollback Scenarios

**User Story:** As a trainer reviewing the system, I want edge-case and failure scenarios handled correctly, so that I can trust the system under stress.

#### Acceptance Criteria

1. WHEN the trainee confirms a Sell of an entire Position, THE STS SHALL set the Position to zero shares and remove the Position.
2. WHEN a confirmation request is repeated for an already COMPLETED Order, THE STS SHALL leave the cash balance, the Positions, and the Stock_Inventory unchanged.
3. WHEN the trainee refreshes the Confirmation_Page after a completed trade, THE STS SHALL leave the cash balance, the Positions, and the Stock_Inventory unchanged.
4. IF a database error occurs during the execution of a confirmed trade, THEN THE Trade_Engine SHALL roll back every change made by that trade.
5. WHEN competing trade requests are processed, THE STS SHALL keep the cash balance, the Positions, and the Stock_Inventory correct.

## Scope and Deferred Features

### In Scope for This Milestone

- One preloaded Demo_User and one preloaded practice Account, selected by the backend.
- Fixed Stock prices, whole-share trades, and local execution on a single laptop.
- Dashboard, Stock_List_Page, Stock_Details_Page, Order_Review_Page, and Confirmation_Page.
- Buy and Sell workflow with a PENDING → COMPLETED/CANCELLED Order review flow.
- Pre_Execution_Checks run at review and again at confirmation, reading trusted values from the database.
- Single-transaction execution with full rollback on failure.
- Transaction records, EventLog entries, and Stock_Inventory changes, because they support correct buying and selling.
- A configurable database connection to allow a later move to a hosted database.
- Data entities proposed for traceability only: User, Account, Stock, Order, Transaction, Position, EventLog. Only tables needed for this milestone are created.

### Deferred Features (Future / Out of Scope)

The following are documented as future work and are not built in this milestone:

- Login, registration, password recovery, and account-management pages.
- Deposits and withdrawals.
- Switching or managing multiple accounts.
- Administrator pages and tools.
- Market-hours enforcement, trading schedules, and holiday calendars.
- Automatic price changes and live market data connections.
- Dollar-based order entry and fractional shares.
- Stock search.
- Separate transaction-history and event-log pages.
- Exports, charts, and portfolio analysis.
- AWS provisioning, deployment, and cloud architecture changes.
- Rewriting the Systems Analysis and Design (SAD) paper.

## Source Traceability

The requirements in this document trace to the following project sources. These source files are referenced for context and are not modified by this spec.

### Source Documents

- **IFT 401 STS Business Case Updated 20260905.docx**: Finished-product vision, the rule that sold shares are returned to inventory, event logging, and the three-tier browser → Flask → MySQL architecture.
- **Systems Analysis Design Paper.docx (SAD)**: ERD using Users, Administrators, Stocks, Market_Schedule, Transactions, Holdings, and Event_Log; cash held on Users; buy/sell process maps and wireframes.
- **sad updates needed.docx**: Grading notes identifying the missing buy-order mockup and review/confirmation pages, and the requirements-writing style rules (complete sentences, one behavior each, no conjunctions, no negatives, no design-detail leakage).
- **prompt questions.docx**: Team worksheet answers that set milestone limits (cash on Account, Position instead of Holdings, add Order, preloaded user, fixed prices, whole shares, local execution, defer list, run-together-or-rollback).

### Traceability Mapping (Requirement to Source)

| Requirement | Primary Source(s) | Notes |
|-------------|-------------------|-------|
| 1 Preloaded Demo Identity | prompt questions.docx | Preloaded user, cash on Account, fixed prices, backend-selected identity. |
| 2 Dashboard | SAD wireframes; Business Case | Account, cash, holdings display. |
| 3 Stock List | SAD wireframes; Business Case | Company, ticker, price, available shares. |
| 4 Stock Details | sad updates needed.docx | Added page addressing missing mockups. |
| 5 Trade Entry | prompt questions.docx | Whole shares, POST for changes, no execution on refresh. |
| 6 Order Review | sad updates needed.docx; prompt questions.docx | Added review page; PENDING Order flow. |
| 7 Order Cancellation | prompt questions.docx | PENDING → CANCELLED, no state change. |
| 8 Pre-Execution Validation | prompt questions.docx; Business Case | Trusted database reads; re-check at confirmation. |
| 9 Failed Trade Handling | sad updates needed.docx; prompt questions.docx | Plain messages, preserved input, no state change. |
| 10 Successful Trade Execution | Business Case; prompt questions.docx | Run-together-or-rollback; inventory return; event logging. |
| 11 Confirmation and Persistence | Business Case; prompt questions.docx | Saved results survive refresh and restart. |
| 12 Idempotency and Concurrency | prompt questions.docx | No double execution; no double spend. |
| 13 Buy Verification | prompt questions.docx | Exact milestone verification values. |
| 14 Sell Verification | prompt questions.docx; Business Case | Exact values; inventory return on sell. |
| 15 Invalid Quantity Rejection | sad updates needed.docx | Clear validation messages. |
| 16 Invalid Trade Target Rejection | prompt questions.docx | Missing records, wrong account, insufficient funds/shares. |
| 17 Full-Holding Sale and Rollback | prompt questions.docx; Business Case | Position removal at zero; forced-error rollback. |

### Worksheet Question Linkage

- Milestone limits (cash on Account, Position instead of Holdings, add Order, preloaded user, fixed prices, whole shares, local execution, defer list): prompt questions.docx — Requirements 1, 5, 6, 7.
- "What changes on a trade" and run-together-or-rollback: prompt questions.docx (#29) — Requirement 10.
- Required EventLog entry per successful trade: prompt questions.docx (#30) and the Business Case event-logging requirement — Requirements 10 and 14.

## Conflicts and Resolutions (Deltas from the SAD)

The resolution for each conflict below is already decided for this milestone. They are documented so reviewers can see where this spec intentionally differs from the SAD.

- **Cash location**: The team model places cash on the Account, whereas the SAD places cash on Users. This milestone uses cash on the Account.
- **Position vs Holdings**: The team model uses a Position entity in place of the SAD's Holdings entity. This milestone creates Position only and does not create Holdings.
- **Order entity added**: The SAD does not include an Order entity. This milestone adds Order to support the PENDING, COMPLETED, and CANCELLED review flow.
- **EventLog per successful trade**: An EventLog entry is required for each successful trade, consistent with the Part 2 table and worksheet #30, even though worksheet #29's shorter "what changes" table omitted it.
- **Deferred SAD tables**: The Administrators and Market_Schedule tables are deferred. The trainee and administrator distinction is represented only as a role on User.
- **Added mockups and pages**: The buy-order mockup and the order-review and confirmation pages are added, using existing SAD wireframes as a visual guide. The SAD itself is not modified.
