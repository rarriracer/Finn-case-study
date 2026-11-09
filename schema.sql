CREATE TABLE IF NOT EXISTS contracts (
    version VARCHAR PRIMARY KEY, contract_id VARCHAR, bank VARCHAR, filename VARCHAR, pdf BLOB
);
CREATE TABLE IF NOT EXISTS rules (
    version VARCHAR PRIMARY KEY, contract_version VARCHAR REFERENCES contracts(version),
    rule_id VARCHAR, definition JSON
);
CREATE TABLE IF NOT EXISTS plans (
    version VARCHAR PRIMARY KEY, name VARCHAR, as_of DATE, source_data JSON
);
CREATE TABLE IF NOT EXISTS plan_inputs (
    plan_version VARCHAR REFERENCES plans(version), field VARCHAR, raw JSON,
    unit VARCHAR, source VARCHAR, normalized_teuro VARCHAR,
    PRIMARY KEY (plan_version, field)
);
CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY, plan_version VARCHAR REFERENCES plans(version), checked_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS run_contracts (
    run_id UUID REFERENCES runs(id), contract_version VARCHAR REFERENCES contracts(version),
    PRIMARY KEY (run_id, contract_version)
);
CREATE TABLE IF NOT EXISTS results (
    run_id UUID REFERENCES runs(id), rule_version VARCHAR REFERENCES rules(version),
    status VARCHAR, value VARCHAR, headroom VARCHAR, details JSON,
    PRIMARY KEY (run_id, rule_version)
);
CREATE TABLE IF NOT EXISTS contract_reviews (
    id VARCHAR PRIMARY KEY, requester VARCHAR, filename VARCHAR, pdf_hash VARCHAR,
    pdf BLOB, proposal JSON, reviewed_at TIMESTAMPTZ, confirmed_at TIMESTAMPTZ
);
CREATE TABLE IF NOT EXISTS active_contracts (
    contract_id VARCHAR PRIMARY KEY, contract_version VARCHAR REFERENCES contracts(version)
);
CREATE TABLE IF NOT EXISTS initialization (
    key VARCHAR PRIMARY KEY
);
