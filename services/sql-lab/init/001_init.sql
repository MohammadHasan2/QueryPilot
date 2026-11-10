CREATE TABLE IF NOT EXISTS lab_status (
    id integer GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    message text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO lab_status (message)
VALUES ('QueryPilot SQL lab initialized');
