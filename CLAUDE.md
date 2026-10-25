# Veridian

Hackathon project (Platanus Hack Caracas, AI Security track). Team: Juan (`Juanalv1`), Maikol (`maikCyphlock`), Christian (`ChristianVeneko`), José Miguel (`Josemigg16`), Carlos (`CalitoTech`).

**What it is:** detects when someone other than the owner uses a credential, and blocks it. A middleware in the customer's app reports each access to the Veridian server; a deterministic profile per user is learned; an AI judges deviations; the middleware applies blocks. Read `README.md` and `docs/` before working: decisions, findings, demo and pending work live there.

## Conventions

- Code identifiers, comments and tests in English. Team docs in neutral Spanish.
- Conventional Commits. No AI attribution, no Co-Authored-By.
- Work directly on `main`; no pull requests.
- Never claim a number without a source or a measurement; mark unverified figures as such.
- Keep scope narrow: one problem (stolen credentials used by someone else). Say no to additions that don't serve it.
