# Validation record

The final JUnit files and logs are authoritative for PASS/FAIL counts. Unit and
API/persistence selections are disjoint; do not add earlier retries to their
counts. All experimental measurements were already finished before these tests.

During continuation, the first new parity test failed because its source-count
assertion tried to hash a StockCandidate containing a mapping. The assertion was
corrected to use stock.identity. The original and accelerated geometry checks
had both passed before that assertion. The final unit selection passed 568 tests.

API collection initially found missing openpyxl and python-multipart in the local
environment. The declared requirements.txt dependencies were installed after the
measurement battery. No measurement was repeated or its recorded environment
changed. Reporting uses a separate Python environment with matplotlib/python-docx.

The oracle parity tests compare 200 reproducible masks plus edge cases and full
validation of small rectangles, asymmetric triangles and mixed shapes, multiple
sources, margin and separation. Both validators also reject controlled invalid
placements. This is a methodological validation-oracle optimization; it is not
an engine speed improvement.
