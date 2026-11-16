-- Transform layer. Input tables: accounts, categories, rules, overrides, balances, fx, tx_raw.
-- Everything below is plain DuckDB SQL; add your own views at the end.
--
-- Amounts: `amount` is always EUR (converted with fx.csv), `amount_orig` + `currency`
-- keep what the bank exported.

-- ------------------------------------------------------------------ tx
-- Dedup: the same transaction appears in overlapping exports. Identical
-- purchases on the same day are legit, so we number repeats within each file
-- and keep each (key, repeat number) once.
CREATE TABLE tx AS
WITH keyed AS (
    SELECT *,
           concat_ws('|', account, date, amount, currency, coalesce(payee, ''), coalesce(memo, ''),
                     coalesce(counterparty_iban, '')) AS k
    FROM tx_raw
),
numbered AS (
    SELECT *, row_number() OVER (PARTITION BY source_file, k ORDER BY line_no) AS rep
    FROM keyed
),
dedup AS (
    SELECT * FROM numbered
    QUALIFY row_number() OVER (PARTITION BY k, rep ORDER BY source_file) = 1
),
ided AS (
    SELECT *, substr(md5(k || '#' || rep), 1, 12) AS tx_id,
           -- rules match payee, memo and the counterparty IBAN
           coalesce(payee, '') || ' ' || coalesce(memo, '') || ' ' || coalesce(counterparty_iban, '') AS text
    FROM dedup
),
-- EUR conversion: latest fx.csv rate for that currency at or before the date.
converted AS (
    SELECT i.*, CASE WHEN i.currency = 'EUR' THEN 1 ELSE f.rate END AS rate
    FROM ided i ASOF LEFT JOIN fx f ON f.currency = i.currency AND i.date >= f.month
),
-- Internal transfer: the counterparty IBAN belongs to one of our accounts.
internal AS (
    SELECT i.tx_id, a.account AS cp_account
    FROM ided i JOIN accounts a ON a.iban = i.counterparty_iban AND a.account <> i.account
),
-- Rules: first matching rule (lowest rule_no) wins.
first_rule AS (
    SELECT i.tx_id, min(r.rule_no) AS rule_no
    FROM ided i JOIN rules r ON regexp_matches(i.text, r.pattern, 'i')
    GROUP BY i.tx_id
)
SELECT
    i.tx_id, i.date, date_trunc('month', i.date)::DATE AS month, i.account,
    (i.amount * i.rate)::DECIMAL(14, 2) AS amount, i.amount AS amount_orig, i.currency,
    i.payee, i.memo, i.counterparty_iban,
    coalesce(o.category,
             CASE WHEN n.cp_account IS NOT NULL THEN 'Transfer' END,
             r.category,
             'Uncategorized') AS category,
    CASE
        WHEN o.category IS NOT NULL AND o.category <> 'Transfer' THEN NULL
        ELSE coalesce(n.cp_account, CASE WHEN r.category = 'Transfer' THEN r.transfer_account END)
    END AS cp_account,
    CASE WHEN o.category IS NOT NULL THEN 'override'
         WHEN n.cp_account IS NOT NULL THEN 'iban'
         WHEN r.rule_no IS NOT NULL THEN 'rule ' || r.rule_no
    END AS matched_by,
    i.source_file
FROM converted i
LEFT JOIN internal n USING (tx_id)
LEFT JOIN first_rule fr USING (tx_id)
LEFT JOIN rules r ON r.rule_no = fr.rule_no
LEFT JOIN overrides o USING (tx_id);

-- Mirror legs: some banks leave out the counterparty IBAN (e.g. some instant transfers).
-- If the other side was recognized as a transfer from/to this account, with the opposite
-- amount within 3 days, this leg is that transfer too (also fills in the other account for
-- a leg a rule marked as Transfer without naming it).
UPDATE tx SET category = 'Transfer', cp_account = m.account, matched_by = 'mirror'
FROM (
    SELECT u.tx_id, any_value(t.account) AS account
    FROM tx u JOIN tx t
      ON t.category = 'Transfer' AND t.cp_account = u.account AND t.account <> u.account
     AND t.amount = -u.amount AND abs(date_diff('day', t.date, u.date)) <= 3
    WHERE u.category = 'Uncategorized' OR (u.category = 'Transfer' AND u.cp_account IS NULL)
    GROUP BY u.tx_id
) m
WHERE tx.tx_id = m.tx_id;

-- ------------------------------------------------------------------ transfers
-- One row per transfer between two of our accounts. Prefer the outgoing leg;
-- use the incoming leg only when the sending account isn't exported.
-- (Currency exchanges inside one account use category 'Exchange' and are not transfers.)
CREATE VIEW transfers AS
WITH t AS (SELECT * FROM tx WHERE category = 'Transfer'),
outs AS (SELECT * FROM t WHERE amount < 0),
ins AS (SELECT * FROM t WHERE amount > 0),
matched_in AS (
    SELECT DISTINCT i.tx_id
    FROM ins i JOIN outs o
      ON o.account = i.cp_account AND o.cp_account = i.account
     AND o.amount = -i.amount AND abs(date_diff('day', o.date, i.date)) <= 3
)
SELECT date, month, account AS from_account, coalesce(cp_account, 'unknown') AS to_account,
       -amount AS amount, tx_id
FROM outs
UNION ALL
SELECT date, month, coalesce(cp_account, 'unknown'), account, amount, tx_id
FROM ins WHERE tx_id NOT IN (SELECT tx_id FROM matched_in);

-- ------------------------------------------------------------------ holdings
-- Shares held (holdings.csv) x month-end price (prices.csv) x fx, per month.
-- Units carry forward until the next holdings line for that account/symbol/status.
CREATE VIEW holdings_monthly AS
WITH bounds AS (
    SELECT (SELECT date_trunc('month', min(date)) FROM holdings) AS first_m,
           greatest((SELECT max(month) FROM tx), (SELECT date_trunc('month', max(date)) FROM balances),
                    (SELECT date_trunc('month', max(date)) FROM holdings)) AS last_m
),
months AS (SELECT unnest(generate_series(first_m, last_m, INTERVAL 1 MONTH))::DATE AS month FROM bounds WHERE first_m IS NOT NULL),
h AS (
    SELECT date_trunc('month', date)::DATE AS month, account, symbol, status, currency, units
    FROM holdings
    QUALIFY row_number() OVER (PARTITION BY date_trunc('month', date), account, symbol, status ORDER BY date DESC) = 1
),
keys AS (SELECT account, symbol, status, any_value(currency) AS currency, min(month) AS first_month FROM h GROUP BY ALL),
grid AS (SELECT m.month, k.* FROM months m JOIN keys k ON m.month >= k.first_month),
filled AS (
    SELECT g.month, g.account, g.symbol, g.status, g.currency, h.units,
           lag(h.units) OVER (PARTITION BY g.account, g.symbol, g.status ORDER BY g.month) AS prev_units
    FROM grid g ASOF LEFT JOIN h ON h.account = g.account AND h.symbol = g.symbol AND h.status = g.status AND g.month >= h.month
),
-- A symbol equal to its currency (e.g. USD) is a plain amount: price 1. Use it when the
-- broker only shows a value, not the number of shares.
priced AS (
    SELECT f.*, CASE WHEN f.symbol = f.currency THEN 1 ELSE p.price END AS price, p.month AS price_month
    FROM filled f ASOF LEFT JOIN prices p ON p.symbol = f.symbol AND f.month >= p.month
)
SELECT p.month, p.account, p.symbol, p.status, p.currency, p.units, p.prev_units, p.price, p.price_month,
       p.units * p.price AS value,
       (p.units * p.price * CASE WHEN p.currency = 'EUR' THEN 1 ELSE x.rate END)::DECIMAL(14, 2) AS value_eur,
       (CASE WHEN p.symbol = p.currency THEN 0 ELSE greatest(p.units - coalesce(p.prev_units, p.units), 0) END * p.price
        * CASE WHEN p.currency = 'EUR' THEN 1 ELSE x.rate END)::DECIMAL(14, 2) AS added_eur
FROM priced p ASOF LEFT JOIN fx x ON x.currency = p.currency AND p.month >= x.month;

-- Owned shares become month-end balances, like a line in balances.csv.
INSERT INTO balances
SELECT last_day(month), account, sum(value)::DECIMAL(14, 2), currency
FROM holdings_monthly WHERE status = 'owned' AND value IS NOT NULL
GROUP BY ALL;

-- ------------------------------------------------------------------ derived balances
-- For accounts with transaction exports, month-end balances are calculated from the latest
-- known balance (the "anchor", from balances.csv) and the transactions:
--   balance(month end) = anchor + transactions after the anchor up to month end
--                               - transactions after month end up to the anchor
-- per currency. A balance you entered yourself for that month end (or later in it) wins.
INSERT INTO balances
WITH anchors AS (
    SELECT b.account, b.currency, b.date AS adate, b.balance AS abal
    FROM balances b JOIN accounts a USING (account)
    WHERE a.format IS NOT NULL
    QUALIFY row_number() OVER (PARTITION BY b.account, b.currency ORDER BY b.date DESC) = 1
),
months AS (
    SELECT unnest(generate_series((SELECT min(month) FROM tx WHERE source_file IS NOT NULL),
                                  (SELECT max(month) FROM tx), INTERVAL 1 MONTH))::DATE AS month
),
grid AS (
    SELECT last_day(m.month) AS mend, an.*
    FROM months m JOIN anchors an
      ON m.month >= (SELECT min(t.month) FROM tx t WHERE t.account = an.account AND t.source_file IS NOT NULL)
),
calc AS (
    SELECT g.mend, g.account, g.currency,
           g.abal + coalesce(sum(t.amount_orig) FILTER (t.date > g.adate AND t.date <= g.mend), 0)
                  - coalesce(sum(t.amount_orig) FILTER (t.date > g.mend AND t.date <= g.adate), 0) AS bal
    FROM grid g
    LEFT JOIN tx t ON t.account = g.account AND t.currency = g.currency AND t.source_file IS NOT NULL
    GROUP BY g.mend, g.account, g.currency, g.abal, g.adate
)
SELECT mend, account, bal::DECIMAL(14, 2), currency FROM calc c
WHERE NOT EXISTS (SELECT 1 FROM balances b WHERE b.account = c.account AND b.currency = c.currency
                  AND date_trunc('month', b.date) = date_trunc('month', c.mend) AND b.date >= c.mend);

-- ------------------------------------------------------------------ balances
-- Latest balance per account, currency and month (balances.csv is the source of truth).
-- Type 'reserved' = money set aside for a purpose (e.g. equity for a home): counted in net worth, not as cash.
-- A balance is carried forward until a newer one is entered (e.g. a yearly home value);
-- `as_of` is the month it was actually entered.
CREATE VIEW balances_monthly AS
WITH latest AS (
    SELECT date_trunc('month', date)::DATE AS month, account, currency, balance
    FROM balances
    QUALIFY row_number() OVER (PARTITION BY date_trunc('month', date), account, currency ORDER BY date DESC) = 1
),
months AS (
    SELECT unnest(generate_series(min(month), max(month), INTERVAL 1 MONTH))::DATE AS month FROM latest
),
keys AS (SELECT account, currency, min(month) AS first_month FROM latest GROUP BY ALL),
grid AS (SELECT m.month, k.account, k.currency FROM months m JOIN keys k ON m.month >= k.first_month),
filled AS (
    SELECT g.month, g.account, g.currency, l.balance, l.month AS as_of
    FROM grid g ASOF LEFT JOIN latest l
      ON l.account = g.account AND l.currency = g.currency AND g.month >= l.month
),
converted AS (
    SELECT l.*, CASE WHEN l.currency = 'EUR' THEN 1 ELSE f.rate END AS rate
    FROM filled l ASOF LEFT JOIN fx f ON f.currency = l.currency AND l.month >= f.month
)
SELECT c.month, c.account, a.name, a.type, a.owner,
       CASE WHEN a.type = 'reserved' THEN 'Reserved'
            WHEN a.type IN ('checking', 'savings', 'cash') THEN 'Cash'
            WHEN a.type IN ('investment', 'pension') THEN 'Investments'
            WHEN a.type IN ('credit_card', 'loan', 'mortgage') THEN 'Debt'
            WHEN a.type IN ('property', 'real_estate') THEN 'Property'
            ELSE 'Other' END AS bucket,
       c.currency, c.balance, (c.balance * c.rate)::DECIMAL(14, 2) AS balance_eur, c.as_of
FROM converted c JOIN accounts a USING (account);

CREATE VIEW net_worth AS
SELECT month,
       sum(balance_eur) AS net_worth,
       coalesce(sum(balance_eur) FILTER (bucket = 'Cash'), 0) AS cash,
       coalesce(sum(balance_eur) FILTER (bucket = 'Reserved'), 0) AS reserved,
       coalesce(sum(balance_eur) FILTER (bucket = 'Investments'), 0) AS investments,
       coalesce(sum(balance_eur) FILTER (bucket = 'Debt'), 0) AS debt,
       coalesce(sum(balance_eur) FILTER (bucket = 'Property'), 0) AS property,
       coalesce(sum(balance_eur) FILTER (bucket = 'Other'), 0) AS other
FROM balances_monthly GROUP BY month;

-- ------------------------------------------------------------------ loans
-- The installment is a transfer to the loan account. Its interest part is a
-- real expense: interest = installment - (debt last month - debt this month).
-- Loan balances are entered as negative numbers.
CREATE VIEW loan_monthly AS
WITH loans AS (SELECT account FROM accounts WHERE type IN ('loan', 'mortgage')),
bal AS (
    SELECT month, account, balance_eur AS balance,
           lag(balance_eur) OVER (PARTITION BY account ORDER BY month) AS prev_balance
    FROM balances_monthly WHERE account IN (SELECT account FROM loans) AND as_of = month
),
paid AS (
    SELECT month, to_account AS account, sum(amount) AS installment
    FROM transfers WHERE to_account IN (SELECT account FROM loans) GROUP BY ALL
)
SELECT b.month, b.account, -b.balance AS remaining,
       coalesce(p.installment, 0) AS installment,
       b.balance - b.prev_balance AS principal,
       coalesce(p.installment, 0) - (b.balance - b.prev_balance) AS interest
FROM bal b LEFT JOIN paid p USING (month, account)
WHERE b.prev_balance IS NOT NULL;

-- Calculated interest becomes a transaction on the loan account.
INSERT INTO tx BY NAME
SELECT 'interest-' || account || '-' || strftime(month, '%Y-%m') AS tx_id,
       last_day(month) AS date, month, account,
       (-interest)::DECIMAL(14, 2) AS amount, (-interest)::DECIMAL(14, 2) AS amount_orig, 'EUR' AS currency,
       'Loan interest (calculated)' AS payee, 'installment minus principal repaid' AS memo,
       'Loan interest' AS category, 'loan calc' AS matched_by
FROM loan_monthly WHERE interest > 0;

-- ------------------------------------------------------------------ enriched
-- Category metadata. Uncategorized counts as income or expense by sign so totals stay complete.
CREATE VIEW tx_enriched AS
SELECT t.*,
       coalesce(c."group", 'Other') AS "group",
       coalesce(c.kind, CASE WHEN t.amount > 0 THEN 'income' ELSE 'expense' END) AS kind,
       coalesce(c.fixed, 'variable') AS fixed,
       a.name AS account_name, a.owner, a.type AS account_type,
       (SELECT any_value(tr.name) FROM trips tr WHERE t.date BETWEEN tr.start_date AND tr.end_date) AS trip
FROM tx t
LEFT JOIN categories c USING (category)
JOIN accounts a USING (account);

-- ------------------------------------------------------------------ monthly
CREATE VIEW monthly AS
SELECT month,
       coalesce(sum(amount) FILTER (kind = 'income'), 0) AS income,
       coalesce(-sum(amount) FILTER (kind = 'expense'), 0) AS expenses,
       coalesce(-sum(amount) FILTER (kind = 'expense' AND fixed = 'fixed'), 0) AS fixed_expenses,
       coalesce(-sum(amount) FILTER (kind = 'expense' AND fixed <> 'fixed'), 0) AS variable_expenses
FROM tx_enriched
GROUP BY month;

CREATE VIEW monthly_kpis AS
SELECT month, income, expenses, fixed_expenses, variable_expenses,
       income - expenses AS saved,
       CASE WHEN income > 0 THEN (income - expenses) / income END AS savings_rate,
       sum(income - expenses) OVER w12 / nullif(sum(income) OVER w12, 0) AS savings_rate_12m,
       avg(expenses) OVER w12 AS expenses_avg_12m
FROM monthly
WINDOW w12 AS (ORDER BY month ROWS BETWEEN 11 PRECEDING AND CURRENT ROW);

CREATE VIEW category_monthly AS
SELECT month, category, "group", fixed, -sum(amount) AS spent, count(*) AS n
FROM tx_enriched WHERE kind = 'expense'
GROUP BY ALL;

-- ------------------------------------------------------------------ recurring
-- Same payee (digits stripped) in >= 4 of the last 6 months with stable amounts.
CREATE VIEW recurring AS
WITH base AS (
    SELECT regexp_replace(lower(payee), '[0-9*#.]+', '', 'g') AS payee_key, *
    FROM tx_enriched
    WHERE kind = 'expense' AND source_file IS NOT NULL
      AND month > (SELECT max(month) FROM tx) - INTERVAL 6 MONTH
),
per_month AS (
    SELECT payee_key, month, any_value(payee) AS payee, any_value(category) AS category,
           any_value(account_name) AS account, -sum(amount) AS amount
    FROM base GROUP BY payee_key, month
)
SELECT any_value(payee) AS payee, any_value(category) AS category, any_value(account) AS account,
       count(*) AS months, round(avg(amount), 2) AS avg_amount, max(month) AS last_month
FROM per_month
GROUP BY payee_key
HAVING count(*) >= 4 AND stddev_pop(amount) / avg(amount) < 0.15
ORDER BY avg_amount DESC;

-- ------------------------------------------------------------------ investments
-- Per investment account and month: what you put in vs. what the market did.
--   deposits = transfers in - transfers out (EUR)
--   added    = value of newly added shares without a cash transfer (vests), from holdings.csv
--   market   = change in value - deposits - added
CREATE VIEW investment_monthly AS
WITH inv AS (SELECT account FROM accounts WHERE type IN ('investment', 'pension')),
val AS (
    SELECT month, account, sum(balance_eur) AS value
    FROM balances_monthly WHERE account IN (SELECT account FROM inv) GROUP BY ALL
),
flows AS (
    SELECT month, account, sum(v) AS deposits FROM (
        SELECT month, to_account AS account, amount AS v FROM transfers
        UNION ALL SELECT month, from_account, -amount FROM transfers
    ) WHERE account IN (SELECT account FROM inv) GROUP BY ALL
),
added AS (
    SELECT month, account, sum(added_eur) AS added FROM holdings_monthly WHERE status = 'owned' GROUP BY ALL
)
SELECT v.month, v.account, v.value,
       v.value - lag(v.value) OVER (PARTITION BY v.account ORDER BY v.month) AS change,
       coalesce(f.deposits, 0) AS deposits, coalesce(a.added, 0) AS added,
       v.value - lag(v.value) OVER (PARTITION BY v.account ORDER BY v.month)
           - coalesce(f.deposits, 0) - coalesce(a.added, 0) AS market
FROM val v
LEFT JOIN flows f USING (month, account)
LEFT JOIN added a USING (month, account);

CREATE VIEW unvested_monthly AS
SELECT month, account, sum(value_eur) AS value_eur
FROM holdings_monthly WHERE status = 'unvested' GROUP BY ALL;

-- ------------------------------------------------------------------ unusual
-- Expenses worth a second look (from €250). Recurring payments (same payee and amount at
-- least 3 times) are never flagged. Otherwise, in this order:
--   not categorized yet | much larger than this payee's usual | only payment to this payee |
--   much larger than usual for a variable category (not Travel: vacations are lumpy by nature).
--   Hide checked ones via reviewed.csv.
CREATE VIEW unusual AS
WITH e AS (
    SELECT *, regexp_replace(lower(payee), '[0-9*#.]+', '', 'g') AS payee_key, -amount AS spent
    FROM tx_enriched WHERE kind = 'expense' AND source_file IS NOT NULL
),
recurring_amounts AS (SELECT payee_key, round(spent) AS amt FROM e GROUP BY ALL HAVING count(*) >= 3),
payee AS (SELECT payee_key, count(*) AS n, median(spent) AS med FROM e GROUP BY 1),
cat AS (SELECT category, count(*) AS n, median(spent) AS med FROM e GROUP BY 1),
flagged AS (
    SELECT e.*, CASE
        WHEN e.category = 'Uncategorized' THEN 'large, not categorized yet'
        WHEN p.n >= 3 AND e.spent >= 4 * p.med THEN 'much more than usual for this payee (~€' || round(p.med)::INT || ')'
        WHEN p.n = 1 THEN 'only payment to this payee'
        WHEN e.fixed <> 'fixed' AND e.category <> 'Travel' AND c.n >= 5 AND e.spent >= 4 * c.med THEN 'large for ' || e.category || ' (usually ~€' || round(c.med)::INT || ')'
    END AS reason
    FROM e JOIN payee p USING (payee_key) JOIN cat c USING (category)
    WHERE e.spent >= 250
      AND (e.payee_key, round(e.spent)) NOT IN (SELECT payee_key, amt FROM recurring_amounts)
      AND e.tx_id NOT IN (SELECT tx_id FROM reviewed)
)
SELECT tx_id, date, month, account_name, payee, category, spent, trip, reason FROM flagged WHERE reason IS NOT NULL;
