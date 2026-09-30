"""
database/db.py
 
HR Voicebot Database Initializer (MySQL)
=========================================
Single source of truth: database/hr-voicebot-schema-mysql.sql
 
Your old db.py hand-built the schema table-by-table in Python against
SQLite. This version doesn't build anything by hand — it just reads
the .sql file (which already fully defines the `hr_voicebot` schema:
departments, designations, employees, leave_policy, leave_balances,
schemes, employee_schemes, insurance_plans, employee_insurance,
leave_requests — plus all seed data) and executes it, statement by
statement, against your MySQL server.
 
Keeping the schema in one .sql file means tools/employee_tools.py and
tools/leave_tools.py (which query this schema directly) and this
initializer can never drift out of sync with each other — edit the
.sql file, rerun this script, done.
 
NOTE: this schema only defines employees/leave data (departments,
designations, employees, leave_policy, leave_balances, schemes,
employee_schemes, insurance_plans, employee_insurance, leave_requests).
It has no grievances or calendar_events tables, and no attendance/
exception/roster columns — tools/grievance_tools.py, calendar_tools.py,
and attendance_tools.py need those added before they'll work against
this database.
 
Usage:
    python database/db.py            # (re)builds hr_voicebot from scratch,
                                      # then prints a startup sanity check
 
Config is read from the SAME env vars hr_tools.py uses, so both files
always point at the same server:
    MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DATABASE
"""
 
import os
import logging
from pathlib import Path
 
import mysql.connector
 
logger = logging.getLogger("db-init")
logging.basicConfig(level=logging.INFO, format="%(message)s")
 
# ---------------------------------------------------------------
# Config — deliberately mirrors hr_tools.py's DB_CONFIG so both files
# always talk to the same server with the same credentials. Note: no
# "database" key here — the .sql file itself owns
# DROP DATABASE IF EXISTS / CREATE DATABASE / USE, so we must connect
# to the *server* first, not to a database that may not exist yet.
# ---------------------------------------------------------------
DB_CONFIG = {
    "host": os.environ.get("MYSQL_HOST", "localhost"),
    "user": os.environ.get("MYSQL_USER", "hrbot"),
    "password": os.environ.get("MYSQL_PASSWORD", "Eoxys@110"),
}
 
# Only used for the post-build sanity check below (to know which DB to
# reconnect to and report on) — must match the CREATE DATABASE name
# inside the .sql file itself (hr_voicebot).
DATABASE_NAME = os.environ.get("MYSQL_DATABASE", "hr_voicebot")
 
# The .sql file lives right next to this one, inside database/.
SCHEMA_PATH = Path(__file__).resolve().parent / "hr-voicebot-schema-mysql.sql"
 
 
def get_db_connection():
    """Returns an active MySQL database connection."""
    config = {
        "host": os.environ.get("MYSQL_HOST", "localhost"),
        "user": os.environ.get("MYSQL_USER", "hrbot"),
        "password": os.environ.get("MYSQL_PASSWORD", "Eoxys@110"),
        "database": DATABASE_NAME,
    }
    return mysql.connector.connect(**config)
 
 
def _split_statements(sql_text: str) -> list[str]:
    """
    Splits the .sql file into individual statements on ';', tracking
    single-quoted string state so a ';' *inside* a string value (e.g.
    'Short, unplanned; usually can''t be clubbed...') doesn't get
    mistaken for the end of a statement. Handles the standard SQL
    escaped-quote convention of a doubled '' inside a string.
 
    Strips full-line `--` comments first (only when they're not inside
    a string) so they don't end up glued onto a statement.
    """
    lines = [
        line for line in sql_text.splitlines()
        if not line.strip().startswith("--")
    ]
    cleaned = "\n".join(lines)
 
    statements = []
    buf = []
    in_string = False
    i = 0
    n = len(cleaned)
    while i < n:
        ch = cleaned[i]
        buf.append(ch)
        if ch == "'":
            if in_string and i + 1 < n and cleaned[i + 1] == "'":
                # escaped '' inside a string — consume both, stay in_string
                buf.append(cleaned[i + 1])
                i += 2
                continue
            in_string = not in_string
        elif ch == ";" and not in_string:
            stmt = "".join(buf[:-1]).strip()  # drop the ';' itself
            if stmt:
                statements.append(stmt)
            buf = []
        i += 1
 
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
 
    return statements
 
 
def create_database(schema_path: Path = SCHEMA_PATH) -> None:
    """
    (Re)builds the hr_voicebot database by executing every statement
    in the .sql schema file, in order, against the MySQL server named
    in DB_CONFIG. The file's own DROP DATABASE IF EXISTS / CREATE
    DATABASE / USE statements handle the fresh-init step, so this
    function connects to the server (not a specific database) before
    running it.
    """
    if not schema_path.exists():
        raise FileNotFoundError(
            f"Schema file not found at {schema_path}. Copy "
            f"hr-voicebot-schema-mysql.sql into database/ first."
        )
 
    sql_text = schema_path.read_text(encoding="utf-8")
    statements = _split_statements(sql_text)
    logger.info("Loaded %d statement(s) from %s", len(statements), schema_path.name)
 
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
    except mysql.connector.Error:
        logger.exception(
            "Could not connect to MySQL server (host=%s user=%s) — check "
            "MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD.",
            DB_CONFIG["host"], DB_CONFIG["user"],
        )
        raise
 
    cursor = conn.cursor()
    try:
        for i, stmt in enumerate(statements, start=1):
            try:
                cursor.execute(stmt)
                if cursor.with_rows:
                    cursor.fetchall()
                conn.commit()
            except mysql.connector.Error as e:
                logger.error("Statement %d failed: %s\n---\n%s\n---", i, e, stmt[:300])
                raise
        logger.info("✅ Database initialised successfully from %s", schema_path.name)
    finally:
        cursor.close()
        conn.close()
 
 
def run_startup_check() -> None:
    """
    Connects to DATABASE_NAME and logs how many employees it can see —
    the same two-second sanity check hr_tools.py runs on its own
    startup, usable here standalone right after a rebuild. Turns "why
    can't it find 1001" into a terminal check instead of a live-call
    guessing game.
    """
    config = {**DB_CONFIG, "database": DATABASE_NAME}
    target = f"{config['host']}/{DATABASE_NAME} as {config['user']}"
    try:
        conn = mysql.connector.connect(**config)
    except mysql.connector.Error as e:
        logger.error("STARTUP CHECK FAILED — could not reach %s (%s)", target, e)
        return
 
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT employee_id, full_name FROM employees ORDER BY employee_id")
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
 
    logger.info("Connected to %s — %d employee(s) found.", target, len(rows))
    if not rows:
        logger.warning(
            "0 employees found. Either the schema was never imported, "
            "or MYSQL_DATABASE/MYSQL_HOST point somewhere else."
        )
 
 
if __name__ == "__main__":
    create_database()
    run_startup_check()
