"""(1.55) The SQLite FaceSort uses: SQLCipher's build when it's installed (needed for encrypted
libraries; it opens ordinary libraries exactly like SQLite does), else Python's own sqlite3."""
try:
    import sqlcipher3.dbapi2 as sqlite3      # noqa: F401
    CIPHER = True
except Exception:                            # (not installed: libraries can't be encrypted)
    import sqlite3                           # noqa: F401
    CIPHER = False
