-- A person is their verified email: one person can sign in with several providers
-- (Google sub, `github:<id>`), each provider id mapping to the same person.
-- person.google_sub keeps the first provider id for compatibility.
-- No UNIQUE index on person.email on purpose: production may already hold duplicate
-- emails (the old login keyed people by provider id only); the login picks the oldest.

CREATE TABLE person_identity (
    sub TEXT PRIMARY KEY,
    person_id TEXT NOT NULL REFERENCES person (id)
);

CREATE INDEX person_identity_person ON person_identity (person_id);

INSERT INTO person_identity (sub, person_id) SELECT google_sub, id FROM person;

UPDATE person SET email = lower(email) WHERE email <> lower(email);

CREATE INDEX person_email_lower ON person (lower(email));
