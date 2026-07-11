-- Included from ../main.sql via \ir.

CREATE TABLE audit_log (
    id     BIGSERIAL PRIMARY KEY,
    action TEXT NOT NULL
);

CREATE TRIGGER audit_orders
    AFTER INSERT ON orders
    FOR EACH ROW EXECUTE FUNCTION log_action();
