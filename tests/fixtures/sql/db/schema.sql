-- Core schema for the fixture database.
-- Exercises tables, a view, a dollar-quoted function, and an index.

CREATE TABLE users (
    id         SERIAL PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,   -- note: ';' never appears mid-string here
    created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE orders (
    id      SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users (id),
    total   NUMERIC(10, 2)
);

CREATE VIEW active_users AS
    SELECT id, email
    FROM users
    WHERE created_at > now() - INTERVAL '30 days';

-- Dollar-quoted body: the inner ';' must NOT terminate the statement.
CREATE OR REPLACE FUNCTION user_count() RETURNS integer AS $$
    SELECT count(*)::integer FROM users;
$$ LANGUAGE sql;

CREATE INDEX idx_orders_user ON orders (user_id);
