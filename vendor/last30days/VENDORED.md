# Vendored: last30days

Source: https://github.com/mvanhorn/last30days-skill (MIT, © Matt Van Horn)
Pinned commit: 62072dacfeab00082bc5eb5fa84e7d22bc47fce7
Vendored: 2026-06-26

Used HEADLESS, free keyless sources only (reddit/hackernews/github), via
scripts/social_trends.py which runs scripts/last30days.py as a subprocess and
parses its --emit json output. Zero runtime deps (pure stdlib). Do not edit
in place — re-vendor from upstream to update.
