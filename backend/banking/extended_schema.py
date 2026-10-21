"""Additive Phase 5 banking tables; original financial tables remain intact."""
SCHEMA = """
CREATE TABLE IF NOT EXISTS card_applications(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, account_id TEXT NOT NULL,
 card_type TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS payee_contacts(payee_id TEXT PRIMARY KEY REFERENCES payees(id), phone TEXT NOT NULL,
 alias TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cards(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, account_id TEXT NOT NULL,
 card_type TEXT NOT NULL, status TEXT NOT NULL, spending_limit INTEGER NOT NULL CHECK(spending_limit>=0),
 online_enabled INTEGER NOT NULL, international_enabled INTEGER NOT NULL, contactless_enabled INTEGER NOT NULL,
 created_at REAL NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS subscriptions(id TEXT PRIMARY KEY, merchant TEXT NOT NULL, customer_id TEXT NOT NULL,
 account_id TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT NOT NULL, frequency TEXT NOT NULL,
 next_charge_at REAL NOT NULL, status TEXT NOT NULL, cancelable INTEGER NOT NULL, source TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS direct_debits(id TEXT PRIMARY KEY, merchant TEXT NOT NULL, customer_id TEXT NOT NULL,
 account_id TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT NOT NULL, frequency TEXT NOT NULL,
 next_charge_at REAL NOT NULL, status TEXT NOT NULL, cancelable INTEGER NOT NULL, source TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS recurring_charges(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, account_id TEXT NOT NULL,
 merchant TEXT NOT NULL, amount INTEGER NOT NULL, currency TEXT NOT NULL, occurred_at REAL NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS product_restrictions(product_id TEXT PRIMARY KEY REFERENCES wealth_products(id),
 purchase_enabled INTEGER NOT NULL, redemption_enabled INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio(customer_id TEXT NOT NULL, account_id TEXT NOT NULL, product_id TEXT NOT NULL,
 amount_cents INTEGER NOT NULL CHECK(amount_cents>=0), redeemable_at REAL NOT NULL, version INTEGER NOT NULL,
 PRIMARY KEY(customer_id,account_id,product_id));
CREATE TABLE IF NOT EXISTS bank_operations(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, action_type TEXT NOT NULL,
 action_hash TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS investment_transactions(id TEXT PRIMARY KEY REFERENCES bank_operations(id), customer_id TEXT NOT NULL,
 account_id TEXT NOT NULL, product_id TEXT NOT NULL, direction TEXT NOT NULL, amount INTEGER NOT NULL,
 currency TEXT NOT NULL, status TEXT NOT NULL, executed_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS scheduled_jobs(schedule_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
 execute_at REAL NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS scheduled_due ON scheduled_jobs(status,execute_at);
CREATE TABLE IF NOT EXISTS wish_funds(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, account_id TEXT NOT NULL,
 name TEXT NOT NULL, balance INTEGER NOT NULL CHECK(balance>=0), target_amount INTEGER NOT NULL CHECK(target_amount>=0),
 release_at REAL NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS merchants(id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT NOT NULL, item TEXT NOT NULL,
 price INTEGER NOT NULL CHECK(price>0), status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS life_events(id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, person TEXT NOT NULL,
 relation TEXT NOT NULL, kind TEXT NOT NULL, month INTEGER NOT NULL, day INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS payee_registrations(payee_id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS card_pin_resets(id TEXT PRIMARY KEY, card_id TEXT NOT NULL, customer_id TEXT NOT NULL,
 channel TEXT NOT NULL, expires_at REAL NOT NULL, status TEXT NOT NULL);
"""
