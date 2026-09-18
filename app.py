import sqlite3
import hashlib
import uuid
import datetime
import json
import os
import shutil
import time

import pandas as pd
import streamlit as st


APP_TITLE = "Campus Care"
APP_SUBTITLE = "Siliguri Institute of Technology (SIT)"

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sit_hub.db")
LOGO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "logo_small.png")

BACKUP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
MAX_BACKUPS = 10

# seed demo data on first run
AUTO_SEED_DEMO = True

# =====================================================================
# SECTION 1: CONFIG (edit me)
# =====================================================================

# departments list
DEPARTMENTS = [
    "BCA",
    "MCA",
    "Computer Science & Engineering (CSE)",
    "CSE (AI)",
    "CSE (ML)",
    "Information Technology (IT)",
    "Electronics & Communication Engineering (ECE)",
    "Electrical Engineering",
    "Civil Engineering",
    "MBA",
    "Hotel Management",
]

# parent accounts + "no branch" entries
ALL_DEPARTMENTS = "All Departments"

# roles
ROLE_STUDENT = "Student"
ROLE_STAFF = "Staff / Admin"
ROLE_PARENT = "Parent"
ROLES = [ROLE_STUDENT, ROLE_STAFF, ROLE_PARENT]

MIN_PASSWORD_LEN = 4           # minimum signup password length
PBKDF2_ITERATIONS = 100_000    # password hashing cost (higher = safer)

# wellness scale (1-10)
STRESS_MIN = 1
STRESS_MAX = 10
HIGH_STRESS_THRESHOLD = 7      # "high stress" cutoff

# grievance workflow (no skiping steps)
STATUS_WORKFLOW = ["Pending", "In Progress", "Resolved"]
GRIEVANCE_CATEGORIES = [
    "Campus Infrastructure",
    "Classrooms & Labs",
    "Gym & Sports",
    "Common Room",
    "JC Bose Hall",
    "Cafeteria & Food",
    "Safety & Security",
    "Academic / Faculty",
    "Cleanliness & Hygiene",
    "IT / Connectivity",
    "Transport",
    "Other",
]

# grievance "where is it?" dropdown
CAMPUS_PLACES = [
    "JC Bose Hall",
    "Common Room",
    "Gym",
    "Classroom / Block",
    "Computer / Science Lab",
    "Canteen",
    "Department Office",
    "Campus Grounds",
    "Other",
]
SEAL = "SIT-HUB-AUDIT-SEAL-v1"  # audit-chain secret; changing it invalidates every old chain

# meeting slots
SLOT_STATUS = ["Open", "Booked", "Taken"]   # Open -> Booked -> Taken
TIME_SLOTS_POOL = [
    "09:00 - 09:30", "09:30 - 10:00", "10:00 - 10:30", "10:30 - 11:00",
    "11:00 - 11:30", "11:30 - 12:00", "14:00 - 14:30", "14:30 - 15:00",
    "15:00 - 15:30", "15:30 - 16:00",   # available time frames
]

# milestone / workload options
WORKLOAD_LEVELS = ["Low", "Moderate", "Heavy"]
MILESTONE_STATUS = ["On Track", "At Risk", "Completed"]

# parent tab defualt text (staff can edit it);
# once saved, the DB copy beats the defaults.
CONTENT_DEFAULTS = {
    "burnout_intro": (
        "**What is burnout?**\n\n"
        "Long-term stress that has not wound down. It is different from a "
        "tough exam week - it lasts for weeks and affects energy, "
        "motivation and health. The sections below give parents practical, "
        "evidence-based guidance."),
    "burnout_signs": (
        "1. Grades or attendance dropping sharply\n"
        "2. Withdrawing from friends and group activities\n"
        "3. Trouble sleeping or sleeping all weekend\n"
        "4. Repeated physical complaints (headaches, stomach ache)\n"
        "5. Irritability, or giving up on assignments early"),
    "burnout_help": (
        "- Keep check-ins short and judgment-free ('how's the workload "
        "feeling this week?')\n"
        "- Encourage one hobby that has nothing to do with grades\n"
        "- Help them protect sleep and meals around exam season\n"
        "- Normalise asking for help - it is strength, not weakness\n"
        "- Contact the department when patterns persist for 2+ weeks"),
    "burnout_contacts": (
        "| Resource | Details |\n"
        "|---|---|\n"
        "| Student Counselling Cell | Room 201, Admin Block - Mon to Fri, "
        "10 am - 4 pm |\n"
        "| Faculty Wellness Officer | One designated staff per department |\n"
        "| Emergency / Helpline | Placeholder: 1800-XXX-XXXX |\n"
        "| Student Affairs Office | studentaffairs@sit.edu.in |"),
}

# demo logins (one-click buttons)
DEMO_ACCOUNTS = {
    "Demo Student": {
        "username": "student1",
        "password": "demo123",
        "role": ROLE_STUDENT,
        "department": "Computer Science & Engineering (CSE)",
    },
    "Demo Staff": {
        "username": "staff1",
        "password": "demo123",
        "role": ROLE_STAFF,
        "department": "Computer Science & Engineering (CSE)",
    },
    "Demo Parent": {
        "username": "parent1",
        "password": "demo123",
        "role": ROLE_PARENT,
        "department": ALL_DEPARTMENTS,
    },
}

# =====================================================================
# SECTION 2: DATABASE LAYER
# =====================================================================
class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        try:
            self.create_tables()
            self.migrate()
            self.ensure_content_defaults()
            if AUTO_SEED_DEMO:
                self.seed_demo_data()
        except Exception as e:
            raise RuntimeError(
                f"Database bootstrap failed for {path}: "
                f"{type(e).__name__}: {e}"
            ) from e

    # small db helpers
    def run(self, sql, params=()):
        self.conn.execute(sql, params)
        self.conn.commit()

    def fetch(self, sql, params=()):
        rows = self.conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def fetch_one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    # tables
    def create_tables(self):
        self.run("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                username    TEXT UNIQUE NOT NULL,
                email       TEXT UNIQUE NOT NULL,
                role        TEXT NOT NULL,
                department  TEXT NOT NULL,
                salt        TEXT NOT NULL,
                password_hash TEXT NOT NULL,
                created_at  TEXT NOT NULL
            )
        """)
        self.run("""
            CREATE TABLE IF NOT EXISTS wellness_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                token_id    TEXT NOT NULL,
                department  TEXT NOT NULL,
                stress_lvl  INTEGER NOT NULL,
                notes       TEXT,
                log_time    TEXT NOT NULL
            )
        """)
        self.run("""
            CREATE TABLE IF NOT EXISTS milestones (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                department        TEXT NOT NULL,
                project_name      TEXT,
                milestone_title   TEXT NOT NULL,
                deadline          TEXT NOT NULL,
                workload_feedback TEXT,
                status            TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                approved          INTEGER NOT NULL DEFAULT 0
            )
        """)
        self.run("""
            CREATE TABLE IF NOT EXISTS grievances (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_no    TEXT UNIQUE NOT NULL,
                department   TEXT NOT NULL,
                category     TEXT NOT NULL,
                title        TEXT NOT NULL,
                description  TEXT,
                location     TEXT,
                status       TEXT NOT NULL,
                notes        TEXT,
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL
            )
        """)
        self.run("""
            CREATE TABLE IF NOT EXISTS grievance_audit (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_no   TEXT NOT NULL,
                old_status  TEXT NOT NULL,
                new_status  TEXT NOT NULL,
                changed_by  TEXT NOT NULL,
                change_time TEXT NOT NULL,
                prev_hash   TEXT NOT NULL,
                audit_hash  TEXT NOT NULL
            )
        """)
        self.run("""
            CREATE TABLE IF NOT EXISTS meeting_slots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                department   TEXT NOT NULL,
                slot_date    TEXT NOT NULL,
                time_slot    TEXT NOT NULL,
                purpose      TEXT,
                booked_by    TEXT,
                status       TEXT NOT NULL
            )
        """)
        self.run("""
            CREATE TABLE IF NOT EXISTS academic_deadlines (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                department  TEXT NOT NULL,
                due_date    TEXT NOT NULL,
                description TEXT,
                status      TEXT NOT NULL
            )
        """)
        self.run("""
            CREATE TABLE IF NOT EXISTS app_meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        self.run("""
            CREATE TABLE IF NOT EXISTS notices (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT NOT NULL,
                body        TEXT,
                department  TEXT NOT NULL,
                posted_on   TEXT NOT NULL,
                created_by  TEXT,
                visible     INTEGER NOT NULL DEFAULT 1
            )
        """)
        self.run("""
            CREATE TABLE IF NOT EXISTS site_content (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

    def migrate(self):
        cols = [r["name"] for r in
                self.fetch("PRAGMA table_info(milestones)")]
        if "approved" not in cols:
            self.run(
                "ALTER TABLE milestones "
                "ADD COLUMN approved INTEGER NOT NULL DEFAULT 0")

    def ensure_content_defaults(self):
        for key, value in CONTENT_DEFAULTS.items():
            self.conn.execute(
                "INSERT OR IGNORE INTO site_content (key, value) "
                "VALUES (?, ?)", (key, value))
        self.conn.commit()

    # time helpers
    @staticmethod
    def now_iso():
        return datetime.datetime.now().replace(microsecond=0).isoformat(
            timespec="seconds")

    @staticmethod
    def day_iso(days_from_today):
        return (datetime.date.today()
                + datetime.timedelta(days=days_from_today)).isoformat()

    # auth
    def create_user(self, username, email, role, department, salt, password_hash):
        self.run(
            """INSERT INTO users
               (username, email, role, department, salt, password_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (username, email, role, department, salt, password_hash,
             self.now_iso()),
        )

    def find_user_by_username(self, username):
        return self.fetch_one("SELECT * FROM users WHERE username = ?", (username,))

    def user_exists(self, username, email):
        return self.fetch_one(
            "SELECT 1 FROM users WHERE username = ? OR email = ?",
            (username, email),
        ) is not None

    # annonymous wellness logs (no names stored)
    def add_wellness_log(self, token_id, department, stress_lvl, notes):
        self.run(
            """INSERT INTO wellness_logs
               (token_id, department, stress_lvl, notes, log_time)
               VALUES (?, ?, ?, ?, ?)""",
            (token_id, department, stress_lvl, notes, self.now_iso()),
        )

    def get_wellness_logs(self):
        return self.fetch("SELECT * FROM wellness_logs ORDER BY id DESC")

    # student milestones
    def add_milestone(self, department, project_name, milestone_title,
                      deadline, workload_feedback, status, approved=0):
        self.run(
            """INSERT INTO milestones
               (department, project_name, milestone_title, deadline,
                workload_feedback, status, created_at, approved)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (department, project_name, milestone_title, deadline,
             workload_feedback, status, self.now_iso(), approved),
        )

    def get_milestones(self):
        return self.fetch("SELECT * FROM milestones ORDER BY deadline ASC, id DESC")

    def set_milestone_approved(self, milestone_id, approved=1):
        self.run("UPDATE milestones SET approved = ? WHERE id = ?",
                 (approved, milestone_id))

    def delete_milestone(self, milestone_id):
        self.run("DELETE FROM milestones WHERE id = ?", (milestone_id,))

    # grievances + tamper-proof audit chain (dont tamper)
    def next_ticket_no(self):
        # MAX(id), not COUNT(*): a deleted ticket's number
        # is gone forever - the UNIQUE ticket_no blows up otherwise.
        row = self.fetch_one(
            "SELECT COALESCE(MAX(id), 0) AS n FROM grievances")
        return f"SIT-{1000 + row['n']:04d}"

    def add_grievance(self, department, category, title, description, location):
        ticket_no = self.next_ticket_no()
        stamp = self.now_iso()
        self.run(
            """INSERT INTO grievances
               (ticket_no, department, category, title, description, location,
                status, notes, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ticket_no, department, category, title, description, location,
             STATUS_WORKFLOW[0], None, stamp, stamp),
        )
        return ticket_no

    def last_audit_hash(self):
        row = self.fetch_one(
            "SELECT audit_hash FROM grievance_audit ORDER BY id DESC LIMIT 1")
        return row["audit_hash"] if row else ""

    def append_audit(self, ticket_no, old_status, new_status, changed_by,
                     change_time, prev_hash=None):
        prev = prev_hash if prev_hash is not None else self.last_audit_hash()
        audit_hash = compute_audit_hash(
            prev, ticket_no, new_status, changed_by, change_time)
        self.run(
            """INSERT INTO grievance_audit
               (ticket_no, old_status, new_status, changed_by,
                change_time, prev_hash, audit_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (ticket_no, old_status, new_status, changed_by,
             change_time, prev, audit_hash),
        )
        return audit_hash

    def get_grievances(self):
        return self.fetch("SELECT * FROM grievances ORDER BY id ASC")

    def get_audit_entries(self):
        return self.fetch("SELECT * FROM grievance_audit ORDER BY id ASC")

    def update_grievance_status(self, ticket_no, new_status, changed_by,
                                note=None):
        change_time = self.now_iso()
        old_status = self.fetch_one(
            "SELECT status FROM grievances WHERE ticket_no = ?",
            (ticket_no,))["status"]

        if note:
            previous = self.fetch_one(
                "SELECT notes FROM grievances WHERE ticket_no = ?",
                (ticket_no,))["notes"] or ""
            combined = (previous + f"\n[{change_time}] {changed_by}: "
                        + note.strip()).strip()
            self.run("UPDATE grievances SET notes = ? WHERE ticket_no = ?",
                     (combined, ticket_no))

        self.run(
            """UPDATE grievances
               SET status = ?, updated_at = ?
               WHERE ticket_no = ?""",
            (new_status, change_time, ticket_no))

        # each change chains to the previous hash;
        # editing one row makes the whole trail scream.
        self.append_audit(ticket_no, old_status, new_status,
                          changed_by, change_time)
        return change_time

    def audit_chain_verified(self):
        rows = self.get_audit_entries()
        if not rows:
            return True, "No audit entries exist yet."
        prev = ""
        for r in rows:
            if r["prev_hash"] != prev:
                return False, (
                    f"Chain broken at ticket {r['ticket_no']}: previous link "
                    "was altered.")
            expected = compute_audit_hash(
                prev, r["ticket_no"], r["new_status"],
                r["changed_by"], r["change_time"])
            if r["audit_hash"] != expected:
                return False, (
                    f"Tampering detected on ticket {r['ticket_no']} at "
                    f"{r['change_time']}.")
            prev = r["audit_hash"]
        return True, (
            f"{len(rows)} entries verified. Chain hash: {prev[:16]}...")

    # 1:1 counselling slots
    def add_slot(self, department, slot_date, time_slot, purpose):
        self.run(
            """INSERT INTO meeting_slots
               (department, slot_date, time_slot, purpose, booked_by, status)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (department, slot_date, time_slot, purpose, None, SLOT_STATUS[0]),
        )

    def update_slot(self, slot_id, status, booked_by):
        self.run(
            "UPDATE meeting_slots SET status = ?, booked_by = ? WHERE id = ?",
            (status, booked_by, slot_id),
        )

    def get_slots(self):
        return self.fetch(
            "SELECT * FROM meeting_slots ORDER BY slot_date ASC, time_slot ASC")

    # academic deadlines
    def add_deadline(self, title, department, due_date, description, status):
        self.run(
            """INSERT INTO academic_deadlines
               (title, department, due_date, description, status)
               VALUES (?, ?, ?, ?, ?)""",
            (title, department, due_date, description, status),
        )

    def get_deadlines(self):
        return self.fetch(
            "SELECT * FROM academic_deadlines ORDER BY due_date ASC")

    def update_deadline(self, deadline_id, title, department, due_date,
                        description, status):
        self.run(
            """UPDATE academic_deadlines
               SET title = ?, department = ?, due_date = ?, description = ?,
                   status = ?
               WHERE id = ?""",
            (title, department, due_date, description, status, deadline_id),
        )

    def delete_deadline(self, deadline_id):
        self.run("DELETE FROM academic_deadlines WHERE id = ?",
                 (deadline_id,))

    # notices - parent anouncements
    def add_notice(self, title, body, department, visible=1,
                   created_by="SIT Admin"):
        self.run(
            """INSERT INTO notices
               (title, body, department, posted_on, created_by, visible)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (title, body, department, self.now_iso(), created_by, visible),
        )

    def get_notices(self, visible_only=True):
        sql = ("SELECT * FROM notices WHERE visible = 1 "
               "ORDER BY posted_on DESC, id DESC" if visible_only
               else "SELECT * FROM notices ORDER BY posted_on DESC, id DESC")
        return self.fetch(sql)

    def update_notice(self, notice_id, title, body, department, visible):
        self.run(
            """UPDATE notices
               SET title = ?, body = ?, department = ?, visible = ?
               WHERE id = ?""",
            (title, body, department, visible, notice_id),
        )

    def delete_notice(self, notice_id):
        self.run("DELETE FROM notices WHERE id = ?", (notice_id,))

    # parent text, staff-editable
    def set_content(self, key, value):
        self.run(
            """INSERT INTO site_content (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )

    def get_content(self, key):
        row = self.fetch_one(
            "SELECT value FROM site_content WHERE key = ?", (key,))
        return row["value"] if row else None

    # one-time demo seed (app_meta guards it)
    def seed_demo_data(self):
        if self.fetch_one("SELECT value FROM app_meta WHERE key='seeded'"):
            return

        for cfg in DEMO_ACCOUNTS.values():
            if not self.find_user_by_username(cfg["username"]):
                salt = uuid.uuid4().hex
                self.create_user(
                    cfg["username"],
                    f"{cfg['username']}@sit.edu.in",
                    cfg["role"], cfg["department"], salt,
                    hash_password(cfg["password"], salt),
                )

        today = datetime.date.today()

        note_pool = [
            "Heavy assignment load this week.",
            "Felt okay after the group discussion.",
            "Too many back-to-back classes today.",
            "Preparing for the internal assessment.",
            "Late-night coding, feeling tired.",
            "Good session with the project guide.",
            "Commute and campus noise making it hard to focus.",
            "Feeling confident about the seminar.",
        ]
        base_stress = [5, 6, 4, 5, 4, 3, 5, 6, 4]
        for dept_idx, dept in enumerate(DEPARTMENTS):
            for days_ago in range(0, 14):
                for hour in (10, 16):
                    if (dept_idx * 3 + days_ago + hour) % 10 >= 6:
                        continue
                    stress = max(
                        STRESS_MIN,
                        min(STRESS_MAX,
                            base_stress[dept_idx % len(base_stress)]
                            + ((dept_idx + days_ago + hour) % 5) - 2))
                    stamp = (datetime.datetime.combine(today, datetime.time.min)
                             - datetime.timedelta(days=days_ago)
                             + datetime.timedelta(hours=hour)).isoformat(
                                 timespec="seconds")
                    token = hashlib.sha256(
                        f"demo|{dept}|{days_ago}|{hour}".encode()).hexdigest()
                    self.run(
                        """INSERT INTO wellness_logs
                           (token_id, department, stress_lvl, notes, log_time)
                           VALUES (?, ?, ?, ?, ?)""",
                        (token, dept, stress,
                         note_pool[(dept_idx + days_ago) % len(note_pool)],
                         stamp))

        milestone_seeds = [
            ("Final Year Project", "Submit abstract", 10, "Moderate",
             MILESTONE_STATUS[0], 0),
            ("Final Year Project", "Literature review draft", 18, "Heavy",
             MILESTONE_STATUS[0], 0),
            ("Final Year Project", "Mid-term demo", 35, "Heavy",
             MILESTONE_STATUS[0], 0),
            ("Database Lab", "ER diagram submission", -2, "Low",
             MILESTONE_STATUS[2], 1),
        ]
        for (proj, mile, offset, load, status, dept_idx) in milestone_seeds:
            self.add_milestone(
                DEPARTMENTS[dept_idx], proj, mile,
                self.day_iso(offset), load, status, approved=1)

        grievance_seeds = [
            ("Computer Science & Engineering (CSE)",
             "Campus Infrastructure", "Broken Fan in Room 341",
             "The fan in the room 341 is not working for many days.",
             "Block C, 3rd floor", "Resolved", 12),
            ("BCA",
             "IT / Connectivity", "Unstable Wi-Fi",
             "Connection drops every few minutes",
             "BCA Building", "In Progress", 6),
            ("Hotel Management",
             "Cafeteria & Food", "Long queue at peak hours",
             "MY ORDER GETS VERY LATE !!!.",
             "Central canteen", "Resolved", 4),
            ("Civil Engineering",
             "Cleanliness & Hygiene", "Washroom not being cleaned regularly",
             "Cleaning schedule not being followed near the workshop block.",
             "toilet near MBA Building", "Pending", 2),
            ("MBA",
             "Academic / Faculty", "AC not Working",
             "Assignment results pending for three weeks now.",
             "Department office", "Pending", 1),
            ("Electronics & Communication Engineering (ECE)",
             "Common Room", "Broken Fan in the Common Room",
             "the fan in common room has stopped working",
             "Common Room", "In Progress", 3),
        ]
        chain_prev = ""
        for (dept, cat, title, desc, loc, status, age) in grievance_seeds:
            created = (datetime.datetime.combine(today, datetime.time.min)
                       - datetime.timedelta(days=age)
                       + datetime.timedelta(hours=9)).isoformat(
                           timespec="seconds")
            ticket = self.next_ticket_no()
            self.run(
                """INSERT INTO grievances
                   (ticket_no, department, category, title, description,
                    location, status, notes, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ticket, dept, cat, title, desc, loc,
                 status, None, created, created))

            if status == "In Progress":
                chain_prev = self.append_audit(
                    ticket, "Pending", "In Progress", "staff1",
                    (datetime.datetime.fromisoformat(created)
                     + datetime.timedelta(days=1)).isoformat(
                         timespec="seconds"), chain_prev)
            elif status == "Resolved":
                chain_prev = self.append_audit(
                    ticket, "Pending", "In Progress", "staff1",
                    (datetime.datetime.fromisoformat(created)
                     + datetime.timedelta(days=1)).isoformat(
                         timespec="seconds"), chain_prev)
                resolved_at = (datetime.datetime.fromisoformat(created)
                               + datetime.timedelta(days=3)).isoformat(
                                   timespec="seconds")
                chain_prev = self.append_audit(
                    ticket, "In Progress", "Resolved", "staff1",
                    resolved_at, chain_prev)
                self.run(
                    "UPDATE grievances SET updated_at = ? WHERE ticket_no = ?",
                    (resolved_at, ticket))

        slot_seeds = [
            (0, 2, "09:00 - 09:30", "Project guidance", None, "Open"),
            (0, 1, "11:00 - 11:30", "Wellness check-in", "student1", "Booked"),
            (0, 3, "14:30 - 15:00", "Career counselling", None, "Open"),
            (1, 4, "10:00 - 10:30", "Capstone review", None, "Open"),
        ]
        for (dept_idx, days, time_slot, purpose, booked_by,
             status) in slot_seeds:
            self.run(
                """INSERT INTO meeting_slots
                   (department, slot_date, time_slot, purpose, booked_by, status)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (DEPARTMENTS[dept_idx], self.day_iso(days), time_slot,
                 purpose, booked_by, status))

        deadline_seeds = [
            ("Course registration - spring semester", ALL_DEPARTMENTS, 5,
             "Pick electives on the student portal.", "Open"),
            ("Mid-term internal assessments begin", ALL_DEPARTMENTS, 12,
             "Check the academic calendar for your slot.", "Open"),
            ("Final Year Project abstracts", DEPARTMENTS[1], 18,
             "Submit through the department office.", "Upcoming"),
            ("Industry guest lecture: AI in Healthcare", DEPARTMENTS[2], 7,
             "Auditorium, attendance counts.", "Open"),
            ("Result publication - previous semester", ALL_DEPARTMENTS, -5,
             "Available on the results portal.", "Closed"),
        ]
        for (title, dept, offset, desc, status) in deadline_seeds:
            self.add_deadline(
                title, dept, self.day_iso(offset), desc, status)

        notice_seeds = [
            ("Welcome to the SIT Campus Care hub",
             "This public page keeps parents informed about deadlines, "
             "events, and how the institute is working through issues.",
             ALL_DEPARTMENTS),
            ("End-semester exam schedule announced",
             "The timetable has been published. Contact your department "
             "office for any clashes or queries.",
             ALL_DEPARTMENTS),
        ]
        for (title, body, dept) in notice_seeds:
            self.add_notice(title, body, dept)

        self.run(
            "INSERT OR REPLACE INTO app_meta (key, value) VALUES ('seeded', ?)",
            (self.now_iso(),))

# =====================================================================
# SECTION 3: AUTH HELPERS
# =====================================================================
def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    ).hex()


def authenticate_user(db, username, password):
    user = db.find_user_by_username(username)
    if user is None:
        return False, "No account found with that username.", {}
    stored = hash_password(password, user["salt"])
    if stored != user["password_hash"]:
        return False, "Incorrect password - please try again.", {}
    return True, f"Welcome back, {user['username']}!", user


def register_user(db, username, email, password, role, department):
    username = (username or "").strip()
    email = (email or "").strip()

    if len(username) < 3:
        return False, "Username must be at least 3 characters."
    if "@" not in email or "." not in email:
        return False, "Please enter a valid email address."
    if len(password) < MIN_PASSWORD_LEN:
        return False, f"Password must be at least {MIN_PASSWORD_LEN} characters."

    if db.user_exists(username, email):
        return False, "That username or email is already registered."

    salt = uuid.uuid4().hex
    db.create_user(username, email, role, department,
                   salt, hash_password(password, salt))
    return True, "Account created - you're logged in. Welcome!"


def log_user_in(user):
    st.session_state["auth"] = {
        "logged_in": True,
        "username": user["username"],
        "role": user["role"],
        "department": user["department"],
        "email": user["email"],
    }


# =====================================================================
# SECTION 4: STATE INIT
# =====================================================================
def get_db():
    return DB(DB_FILE)


def init_state():
    st.session_state.setdefault("auth", {"logged_in": False})


def auto_backup_db():
    if os.environ.get("SPACE_ID"):
        return
    if not os.path.isfile(DB_FILE):
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
        dest = os.path.join(BACKUP_DIR, f"sit_hub-{stamp}.db")
        shutil.copy2(DB_FILE, dest)
        kept = sorted(
            f for f in os.listdir(BACKUP_DIR)
            if f.startswith("sit_hub-") and f.endswith(".db"))
        for old in kept[:-MAX_BACKUPS]:
            try:
                os.remove(os.path.join(BACKUP_DIR, old))
            except OSError:
                pass
    except OSError:
        pass


def export_db_json(db):
    tables = [
        "users", "wellness_logs", "milestones", "grievances",
        "grievance_audit", "meeting_slots", "academic_deadlines",
    ]
    data = {}
    for t in tables:
        rows = db.fetch(f"SELECT * FROM {t}")
        if t == "users":
            for r in rows:
                r.pop("salt", None)
                r.pop("password_hash", None)
        data[t] = rows
    data["exported_at"] = db.now_iso()
    data["app"] = {"title": APP_TITLE, "seal": SEAL}
    return json.dumps(data, indent=2, sort_keys=True)


# =====================================================================
# SECTION 5: SHARED HELPERS
# =====================================================================
def compute_audit_hash(prev_hash, ticket_no, new_status, changed_by, change_time):
    raw = (f"{prev_hash}|{ticket_no}|{new_status}|{changed_by}"
           f"|{change_time}|{SEAL}")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_token(db, username):
    # fresh uuid every time - tokens cant be traced or matched
    raw = f"{username}|{db.now_iso()}|{uuid.uuid4()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_dataframe(rows):
    return pd.DataFrame(rows) if rows else pd.DataFrame()


def build_wellness_insights(db, selected_departments):
    logs = make_dataframe(db.get_wellness_logs())
    if logs.empty:
        return None

    logs["log_time"] = pd.to_datetime(logs["log_time"])
    if selected_departments:
        logs = logs[logs["department"].isin(selected_departments)]
    if logs.empty:
        return None

    summary = {
        "total": len(logs),
        "avg_stress": round(float(logs["stress_lvl"].mean()), 2),
        "high_stress": int((logs["stress_lvl"] >= HIGH_STRESS_THRESHOLD).sum()),
        "departments": logs["department"].nunique(),
    }

    avg_by_dept = logs.groupby("department")["stress_lvl"].mean().sort_values()
    daily_trend = (logs.set_index("log_time")["stress_lvl"]
                   .resample("D").mean().dropna())

    detail = (logs.groupby("department")["stress_lvl"]
              .agg(total="count", avg="mean", lowest="min", highest="max")
              .round(2).sort_values("total", ascending=False))
    return summary, avg_by_dept, daily_trend, detail


def resolution_metrics(db):
    tickets = make_dataframe(db.get_grievances())
    if tickets.empty:
        return None

    total = len(tickets)
    resolved = int((tickets["status"] == "Resolved").sum())
    in_progress = int((tickets["status"] == "In Progress").sum())
    pending = int((tickets["status"] == "Pending").sum())
    rate = round(100.0 * resolved / total, 1) if total else 0.0

    avg_days = None
    done = tickets[tickets["status"] == "Resolved"].copy()
    if not done.empty:
        done["created"] = pd.to_datetime(done["created_at"])
        done["updated"] = pd.to_datetime(done["updated_at"])
        avg_days = round(float((done["updated"] - done["created"])
                               .dt.total_seconds().mean() / 86400.0), 1)

    per_dept = tickets.groupby("department")["status"].apply(
        lambda s: pd.Series({
            "total": len(s),
            "resolved": int((s == "Resolved").sum()),
            "resolution_rate": round(
                100.0 * (s == "Resolved").sum() / len(s), 1),
        })).unstack().sort_values("total", ascending=False)

    return {"total": total, "resolved": resolved, "in_progress": in_progress,
            "pending": pending, "rate": rate, "avg_days": avg_days,
            "per_dept": per_dept}


def status_badge(status):
    colors = {
        "Pending": "gray",
        "In Progress": "orange",
        "Resolved": "green",
    }
    return f":{colors.get(status, 'gray')}[{status}]"


def render_audit_badge(db):
    ok, msg = db.audit_chain_verified()
    if ok:
        st.success(f"Audit chain verified - {msg}")
    else:
        st.error(f"Tampering detected - {msg}")

    with st.expander("Open the audit chain (every status change)"):
        audit = make_dataframe(db.get_audit_entries())
        if audit.empty:
            st.info("No status changes have been recorded yet.")
        else:
            st.dataframe(
                audit[["id", "ticket_no", "old_status", "new_status",
                       "changed_by", "change_time", "prev_hash", "audit_hash"]],
                use_container_width=True, hide_index=True,
                column_config={
                    "prev_hash": st.column_config.TextColumn(
                        "Previous Hash", width="small"),
                    "audit_hash": st.column_config.TextColumn(
                        "Sealed Hash", width="small"),
                })


def render_footer():
    st.divider()
    st.caption(
        "Campus Care - built for the SIT exhibition. Everything stays in "
        "sit_hub.db on this device and nothing leaves it. Wellness logs are "
        "anonymous; grievance trails are tamper-proof.")

# =====================================================================
# SECTION 6: SIDEBAR  (brand, auth, demo logins, logout)
# =====================================================================
def render_sidebar(db):
    with st.sidebar:
        st.markdown(f"### {APP_TITLE}")
        st.caption(APP_SUBTITLE)

        auth = st.session_state["auth"]

        if not auth["logged_in"]:
            tab_login, tab_register = st.tabs(["Login", "Register"])

            with tab_login:
                with st.form("login_form"):
                    login_user = st.text_input("Username")
                    login_pass = st.text_input(
                        "Password", type="password")
                    if st.form_submit_button("Login", type="primary"):
                        ok, msg, user = authenticate_user(
                            db, login_user.strip(), login_pass)
                        if ok:
                            log_user_in(user)
                            st.session_state["toast"] = msg
                            st.rerun()
                        else:
                            st.error(msg)

            with tab_register:
                with st.form("register_form"):
                    new_user = st.text_input("Pick a username (3+ letters)")
                    new_email = st.text_input("Email")
                    new_pass = st.text_input(
                        "Password (4+ characters)", type="password")
                    register_role = st.selectbox(
                        "I am a", ROLES,
                        help="Demo mode - any role can self-register.",
                    )
                    register_dept = st.selectbox(
                        "Your department",
                        DEPARTMENTS + [ALL_DEPARTMENTS])
                    if st.form_submit_button("Create my account"):
                        ok, msg = register_user(
                            db, new_user, new_email, new_pass,
                            register_role, register_dept)
                        st.session_state["toast"] = msg
                        if ok:
                            log_user_in(
                                db.find_user_by_username(new_user.strip()))
                        st.rerun()

            st.divider()
            st.markdown("**Try it as...**")
            demo_cols = st.columns(len(DEMO_ACCOUNTS))
            for col, (label, cfg) in zip(demo_cols, DEMO_ACCOUNTS.items()):
                if col.button(label, use_container_width=True):
                    user = db.find_user_by_username(cfg["username"])
                    log_user_in(user)
                    st.session_state["toast"] = f"Logged in as {label}"
                    st.rerun()

        else:
            st.markdown(
                f"**Logged in as:** {auth['username']}\n\n"
                f"Role: {auth['role']}\n\n"
                f"Department: {auth['department']}")
            if st.button("Logout", use_container_width=True):
                st.session_state["auth"] = {"logged_in": False}
                st.session_state["toast"] = "You've been logged out. See you soon!"
                st.rerun()

        st.divider()
        export_json = export_db_json(db)
        st.download_button(
            "Export data (JSON)",
            data=export_json,
            file_name=(f"campus-care-"
                       f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
                       f".json"),
            mime="application/json",
            use_container_width=True,
            help="Full snapshot (tickets, audit trail, wellness logs, "
                 "registrations). Password hashes excluded.",
        )

    if "toast" in st.session_state:
        st.toast(st.session_state.pop("toast"))

# =====================================================================
# SECTION 7: STUDENT VIEW
# =====================================================================
def render_student_view(db):
    auth = st.session_state["auth"]
    st.header(f"Hey {auth['username']}!")
    st.caption(f"Branch: {auth['department']}")

    form_department = st.selectbox(
        "Department this entry belongs to",
        DEPARTMENTS, index=DEPARTMENTS.index(auth["department"])
        if auth["department"] in DEPARTMENTS else 0,
    )

    tab_wellness, tab_milestones, tab_grievance = st.tabs(
        ["Wellness Log", "Milestone Tracker", "Grievance Form"])

    # Tab 1 - Wellness Log
    with tab_wellness:
        st.subheader("How are you feeling today?")
        with st.form("wellness_form", clear_on_submit=True):
            stress = st.slider(
                "How stressed have you felt lately? (1 = calm, 10 = overwhelmed)",
                STRESS_MIN, STRESS_MAX, value=5)
            notes = st.text_area(
                "Anything on your mind? (optional)",
                placeholder="Heavy assignments, group friction, exam pressure...")
            if st.form_submit_button("Log how I'm feeling", type="primary"):
                token = make_token(db, auth["username"])
                db.add_wellness_log(token, form_department, stress, notes.strip())
                st.session_state["last_token"] = token
                st.rerun()

        if "last_token" in st.session_state:
            st.success("Got it - thanks for checking in.")
            st.write("Your private entry token (save it as proof if you'd like):")
            st.code(st.session_state["last_token"])
            st.caption(
                "Nobody can trace this token back to you - it's your "
                "anonymous receipt.")

    # Tab 2 - Milestone Tracker
    with tab_milestones:
        st.subheader("Add a milestone or deadline")
        with st.form("milestone_form", clear_on_submit=True):
            project = st.text_input(
                "Project / subject name",
                placeholder="e.g. Final Year Project")
            milestone = st.text_input(
                "Milestone / work item",
                placeholder="e.g. Submit literature review")
            deadline = st.date_input(
                "Deadline", value=datetime.date.today()
                + datetime.timedelta(days=7))
            workload = st.selectbox("Workload right now", WORKLOAD_LEVELS)
            if st.form_submit_button("Save milestone", type="primary"):
                db.add_milestone(
                    form_department, project.strip() or "Project",
                    milestone.strip(), deadline.isoformat(), workload,
                    MILESTONE_STATUS[0])
                st.session_state["toast"] = "Milestone added."
                st.rerun()

        st.subheader("Deadlines you've logged")
        milestones = make_dataframe(db.get_milestones())
        if milestones.empty:
            st.info("No milestones logged yet.")
        else:
            view = milestones[milestones["department"] == form_department]
            if view.empty:
                st.info(f"No milestones logged for {form_department} yet.")
            else:
                view["deadline"] = pd.to_datetime(view["deadline"]).dt.date
                now = datetime.date.today()
                for _, row in view.head(8).iterrows():
                    overdue = row["deadline"] < now
                    due_label = ("OVERDUE - " if overdue else "")
                    with st.expander(
                            f"{due_label}{row['milestone_title']} "
                            f"({row['deadline']})"):
                        st.write(f"**Project:** {row['project_name']}")
                        st.write(f"**Deadline:** {row['deadline']} "
                                 f"({workload_text(row['deadline'], now)})")
                        st.progress(
                            1.0 if overdue else
                            deadline_progress(row["deadline"], now))
                        st.caption(
                            f"Workload feedback: {row['workload_feedback']} | "
                            f"Status: {row['status']}"
                            + (" | Pending approval" if row["approved"] == 0
                               else " | Approved"))

    # Tab 3 - Grievance Form
    with tab_grievance:
        st.subheader("Report a campus or facility issue")
        st.caption(
            "It goes straight to the right department - and every update is "
            "sealed in a tamper-proof audit trail.")
        with st.form("grievance_form", clear_on_submit=True):
            g_cat = st.selectbox("Category", GRIEVANCE_CATEGORIES)
            g_title = st.text_input(
                "Short title", placeholder="e.g. Broken fan in Room 341")
            g_desc = st.text_area(
                "Describe the issue",
                placeholder="What happened, since when, how does it affect "
                            "your day...?")
            g_loc = st.selectbox("Where is it?", CAMPUS_PLACES)
            if st.form_submit_button("Raise this ticket", type="primary"):
                if not g_title.strip():
                    st.error("A short title helps us route it - please add one.")
                else:
                    ticket = db.add_grievance(
                        form_department, g_cat, g_title.strip(),
                        g_desc.strip(), g_loc.strip())
                    st.session_state["toast"] = (
                        f"Ticket {ticket} opened - routed to the "
                        f"{form_department} staff queue - status: Pending.")
                    st.rerun()


def workload_text(deadline_date, today):
    days = (deadline_date - today).days
    if days < 0:
        return "overdue by " + str(-days) + " day(s)"
    if days == 0:
        return "due today"
    return "due in " + str(days) + " day(s)"


def deadline_progress(deadline_date, today):
    # ramps 0..1 across a 90-day window
    days = (deadline_date - today).days
    if days <= 0:
        return 1.0
    if days >= 90:
        return 0.0
    return round(1 - days / 90.0, 2)


# =====================================================================
# SECTION 8: STAFF / ADMIN VIEW
# =====================================================================
def render_staff_view(db):
    auth = st.session_state["auth"]
    st.header(f"Staff dashboard - {auth['username']}")

    staff_dept = auth["department"]
    selected = st.multiselect(
        "Departments to view (leave empty to see all)",
        DEPARTMENTS + [ALL_DEPARTMENTS],
        default=[staff_dept] if staff_dept in DEPARTMENTS else [])
    if ALL_DEPARTMENTS in selected:
        selected = [d for d in selected if d != ALL_DEPARTMENTS]

    tab_analytics, tab_meetings, tab_grievances, tab_public = st.tabs(
        ["Wellness Analytics", "Meeting Manager", "Grievance Ticket Manager",
         "News & Public Content"])

    # Tab 1 - Wellness Analytics
    with tab_analytics:
        st.subheader("Wellness pulse for your departments")
        result = build_wellness_insights(db, selected)
        if result is None:
            st.info("No wellness data matches your selection.")
        else:
            summary, avg_by_dept, daily_trend, detail = result
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Check-ins logged", summary["total"])
            c2.metric("Average stress", f'{summary["avg_stress"]:.1f} / 10')
            c3.metric("High-stress logs", summary["high_stress"])
            c4.metric("Departments reporting", summary["departments"])

            col_a, col_b = st.columns(2)
            with col_a:
                st.markdown("**Average stress, per department**")
                st.bar_chart(avg_by_dept)
            with col_b:
                st.markdown("**Stress trend over time**")
                st.line_chart(daily_trend)

            st.markdown("**Per-department breakdown**")
            st.dataframe(detail, use_container_width=True, hide_index=False)
            st.caption(
                "Average at 7+ or climbing week after week means that branch "
                "likely needs counselling support or workload relief.")

    # Tab 2 - Meeting Manager
    with tab_meetings:
        st.subheader("Open a new slot")
        with st.form("slot_form", clear_on_submit=True):
            m_dept = st.selectbox("Department", DEPARTMENTS)
            m_date = st.date_input("Date", value=datetime.date.today()
                                   + datetime.timedelta(days=1))
            m_time = st.selectbox("Time", TIME_SLOTS_POOL)
            m_purpose = st.text_input(
                "Purpose", placeholder="e.g. Project guidance, counselling")
            if st.form_submit_button("Open slot", type="primary"):
                db.add_slot(m_dept, m_date.isoformat(), m_time,
                            m_purpose.strip() or "General meeting")
                st.session_state["toast"] = "Slot opened."
                st.rerun()

        st.subheader("Existing slots")
        slots = make_dataframe(db.get_slots())
        if slots.empty:
            st.info("No slots created yet.")
        else:
            list_view = slots
            if selected:
                list_view = slots[slots["department"].isin(selected)]
            if list_view.empty:
                st.info("No slots match the department filter.")
            else:
                for _, s in list_view.iterrows():
                    cols = st.columns([1, 2, 2, 1])
                    cols[0].write(
                        f"**{s['department']}**\n\n{s['slot_date']} "
                        f"{s['time_slot']}\n\n_{s['purpose']}_")
                    with cols[1]:
                        new_status = st.selectbox(
                            "Status",
                            SLOT_STATUS,
                            index=SLOT_STATUS.index(s["status"]),
                            key=f"slot_status_{s['id']}")
                    with cols[2]:
                        booked_by = st.text_input(
                            "Booked by",
                            value=s["booked_by"] or "",
                            key=f"slot_booked_{s['id']}")
                    if cols[3].button("Update", key=f"slot_btn_{s['id']}"):
                        if new_status != s["status"] or booked_by != (
                                s["booked_by"] or ""):
                            db.update_slot(
                                s["id"], new_status,
                                booked_by.strip() or None)
                            st.session_state["toast"] = (
                                f"Slot updated to '{new_status}'.")
                            st.rerun()

    # Tab 3 - Grievance Ticket Manager
    with tab_grievances:
        st.subheader("Grievance tickets")
        tickets = make_dataframe(db.get_grievances())
        if tickets.empty:
            st.info("No tickets submitted yet.")
        else:
            ticket_depts = st.multiselect(
                "Filter by department (leave empty to see all)",
                DEPARTMENTS + [ALL_DEPARTMENTS])
            if ALL_DEPARTMENTS in ticket_depts:
                ticket_depts = [d for d in ticket_depts
                                if d != ALL_DEPARTMENTS]

            list_view = tickets
            if ticket_depts:
                list_view = tickets[
                    tickets["department"].isin(ticket_depts)]
            if list_view.empty:
                st.info("No tickets match the department filter.")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Pending", int((list_view["status"] == "Pending").sum()))
                c2.metric("In Progress", int((list_view["status"] == "In Progress").sum()))
                c3.metric("Resolved", int((list_view["status"] == "Resolved").sum()))

                st.markdown(
                    "Badge legend: "
                    f"{status_badge('Pending')} / "
                    f"{status_badge('In Progress')} / "
                    f"{status_badge('Resolved')}")

                view = st.radio(
                    "View", ["Open", "All", "Resolved"],
                    horizontal=True,
                    help="Open = Pending + In Progress. Resolved is read-only.",
                )
                if view == "Open":
                    list_view = list_view[list_view["status"].isin(
                        STATUS_WORKFLOW[:2])]
                elif view == "Resolved":
                    list_view = list_view[list_view["status"] == "Resolved"]

                if list_view.empty:
                    st.info("No tickets match this view and filter.")
                else:
                    for _, t in list_view.iterrows():
                        with st.container(border=True):
                            st.markdown(
                                f"**{t['ticket_no']}** - {t['title']} "
                                f"{status_badge(t['status'])}")
                            closed = (" | Closed: " + t["updated_at"][:10]) \
                                if t["status"] == "Resolved" else ""
                            st.caption(
                                f"{t['department']} | {t['category']} | "
                                f"Location: {t['location'] or '-'} | "
                                f"Opened: {t['created_at'][:10]}{closed}")

                            editable = t["status"] != "Resolved"
                            with st.expander(
                                    "Review & update" if editable else "Details"):
                                st.write(t["description"] or "-")
                                if t["notes"]:
                                    st.markdown("**Staff notes:**")
                                    st.write(t["notes"])

                                if editable:
                                    idx = STATUS_WORKFLOW.index(t["status"])
                                    options = STATUS_WORKFLOW[idx:]
                                    new_status = st.selectbox(
                                        "Set status to", options,
                                        index=1 if len(options) > 1 else 0,
                                        key=f"ticket_status_{t['id']}")
                                    staff_name = st.text_input(
                                        "Your name (goes on the record)",
                                        value=auth["username"],
                                        key=f"ticket_staff_{t['id']}")
                                    note_text = st.text_input(
                                        "Note for this update (optional)",
                                        key=f"ticket_note_{t['id']}")
                                    update_clicked = st.button(
                                        f"Move to {new_status}", type="primary",
                                        disabled=new_status == t["status"],
                                        key=f"ticket_btn_{t['id']}")
                                    if update_clicked:
                                        db.update_grievance_status(
                                            t["ticket_no"], new_status,
                                            staff_name.strip() or "Unknown",
                                            note_text.strip() or None)
                                        st.session_state["toast"] = (
                                            f"{t['ticket_no']} is now "
                                            f"'{new_status}', sealed into "
                                            "the audit trail.")
                                        st.rerun()

        st.divider()
        st.subheader("Audit trail")
        render_audit_badge(db)

    # Tab 4 - News & Public Content (edit what parents see)
    with tab_public:
        st.subheader("Notices & announcements")
        st.caption(
            "Published notices appear at the top of the Parent tab's "
            "Student Timeline.")
        with st.form("notice_form", clear_on_submit=True):
            n_title = st.text_input(
                "Notice title",
                placeholder="e.g. Exam schedule published")
            n_body = st.text_area(
                "Notice body", placeholder="Details parents should see...")
            n_dept = st.selectbox(
                "Target department", DEPARTMENTS + [ALL_DEPARTMENTS],
                index=DEPARTMENTS.index(staff_dept)
                if staff_dept in DEPARTMENTS else len(DEPARTMENTS))
            if st.form_submit_button("Publish notice", type="primary"):
                if n_title.strip():
                    db.add_notice(
                        n_title.strip(), n_body.strip(),
                        ALL_DEPARTMENTS if n_dept == ALL_DEPARTMENTS
                        else n_dept)
                    st.session_state["toast"] = "Notice published."
                    st.rerun()
                else:
                    st.error("A title is required for the notice.")

        notices = make_dataframe(db.get_notices(visible_only=False))
        if not notices.empty:
            st.markdown("**Manage published notices**")
            for _, n in notices.head(10).iterrows():
                with st.expander(
                        f"{n['posted_on'][:10]} - {n['title']} "
                        f"({n['department']})"
                        + ("  [HIDDEN]" if n["visible"] == 0 else "")):
                    st.write(n["body"] or "-")
                    cA, cB, cC = st.columns([1, 2, 1])
                    hide_btn = ("Hide" if n["visible"] == 1 else "Show")
                    if cA.button(hide_btn, key=f"note_tgl_{n['id']}"):
                        new_vis = 0 if n["visible"] == 1 else 1
                        db.update_notice(n["id"], n["title"], n["body"],
                                         n["department"], new_vis)
                        st.session_state["toast"] = (
                            "Notice hidden from the parent tab."
                            if new_vis == 0 else "Notice is visible again.")
                        st.rerun()
                    if cB.button("Edit", key=f"note_edit_{n['id']}"):
                        st.session_state["editing_notice"] = n["id"]
                        st.rerun()

            edit_id = st.session_state.get("editing_notice")
            if edit_id is not None:
                match = notices[notices["id"] == edit_id]
                if not match.empty:
                    cur = match.iloc[0]
                    st.markdown("**Editing notice**")
                    with st.form("notice_edit_form"):
                        e_title = st.text_input(
                            "Notice title", value=cur["title"],
                            key="ne_title")
                        e_body = st.text_area(
                            "Notice body", value=cur["body"] or "",
                            key="ne_body")
                        e_dept = st.selectbox(
                            "Target department",
                            DEPARTMENTS + [ALL_DEPARTMENTS],
                            index=(
                                DEPARTMENTS + [ALL_DEPARTMENTS]).index(
                                    cur["department"]),
                            key="ne_dept")
                        save_clicked = st.form_submit_button(
                            "Save changes", type="primary")
                        cancel_clicked = st.form_submit_button("Cancel")
                    if save_clicked:
                        db.update_notice(
                            edit_id, e_title.strip(), e_body.strip(),
                            ALL_DEPARTMENTS if e_dept == ALL_DEPARTMENTS
                            else e_dept, cur["visible"])
                        del st.session_state["editing_notice"]
                        st.session_state["toast"] = "Notice updated."
                        st.rerun()
                    if cancel_clicked:
                        del st.session_state["editing_notice"]
                        st.rerun()
        else:
            st.info("No notices published yet.")

        st.divider()
        st.subheader("Deadlines & events")
        st.caption("These appear in the Student Timeline on the parent tab.")
        with st.form("deadline_form", clear_on_submit=True):
            d_title = st.text_input(
                "Event / deadline title",
                placeholder="e.g. Industry guest lecture")
            d_desc = st.text_input("Short description (optional)")
            d_dept = st.selectbox(
                "Department", DEPARTMENTS + [ALL_DEPARTMENTS],
                index=DEPARTMENTS.index(staff_dept)
                if staff_dept in DEPARTMENTS else len(DEPARTMENTS),
                key="dl_dept")
            d_date = st.date_input(
                "Due date",
                value=datetime.date.today() + datetime.timedelta(days=7),
                key="dl_date")
            if st.form_submit_button("Publish deadline", type="primary"):
                if d_title.strip():
                    db.add_deadline(
                        d_title.strip(),
                        ALL_DEPARTMENTS if d_dept == ALL_DEPARTMENTS
                        else d_dept,
                        d_date.isoformat(), d_desc.strip(), "Published")
                    st.session_state["toast"] = "Deadline published."
                    st.rerun()
                else:
                    st.error("A title is required for the deadline.")

        deadlines = make_dataframe(db.get_deadlines())
        if not deadlines.empty:
            st.markdown("**Manage published deadlines**")
            for _, d in deadlines.head(12).iterrows():
                with st.expander(
                        f"{d['due_date']} - {d['title']} ({d['department']})"):
                    st.write(d["description"] or "-")
                    if st.button("Delete", key=f"dl_del_{d['id']}"):
                        db.delete_deadline(d["id"])
                        st.session_state["toast"] = "Deadline removed."
                        st.rerun()
        else:
            st.info("No deadlines published yet.")

        st.divider()
        st.subheader("Milestone approvals")
        st.caption(
            "Student-submitted milestones only reach the parent timeline "
            "once approved here.")
        all_miles = make_dataframe(db.get_milestones())
        if all_miles.empty:
            st.info("No milestones to review.")
        else:
            pending = all_miles[all_miles["approved"] == 0]
            if selected:
                pending = pending[pending["department"].isin(selected)]
            if pending.empty:
                st.info("No milestones waiting for approval.")
            else:
                for _, m in pending.head(12).iterrows():
                    cols = st.columns([3, 1, 1])
                    cols[0].markdown(
                        f"**{m['milestone_title']}** ({m['deadline']})  \n"
                        f"_{m['project_name']} | {m['department']} | "
                        f"Status: {m['status']} | "
                        f"Load: {m['workload_feedback']}_")
                    if cols[1].button(
                            "Approve", type="primary",
                            key=f"ms_app_{m['id']}"):
                        db.set_milestone_approved(m["id"])
                        st.session_state["toast"] = (
                            f"'{m['milestone_title']}' approved and live on "
                            "the parent timeline.")
                        st.rerun()
                    if cols[2].button("Remove", key=f"ms_rem_{m['id']}"):
                        db.delete_milestone(m["id"])
                        st.session_state["toast"] = "Milestone removed."
                        st.rerun()

        st.divider()
        st.subheader("Parent tab text (Burnout Guide)")
        st.caption(
            "These fill the written sections parents see. The transparency "
            "ledger stays auto-computed.")
        with st.form("content_form"):
            c_intro = st.text_area(
                "Intro", value=db.get_content("burnout_intro")
                or CONTENT_DEFAULTS["burnout_intro"], height=90)
            c_signs = st.text_area(
                "Early warning signs",
                value=db.get_content("burnout_signs")
                or CONTENT_DEFAULTS["burnout_signs"], height=150)
            c_help = st.text_area(
                "How parents can help",
                value=db.get_content("burnout_help")
                or CONTENT_DEFAULTS["burnout_help"], height=150)
            c_contacts = st.text_area(
                "Campus contacts (markdown table)",
                value=db.get_content("burnout_contacts")
                or CONTENT_DEFAULTS["burnout_contacts"], height=150)
            if st.form_submit_button("Save parent page text", type="primary"):
                db.set_content("burnout_intro", c_intro.strip())
                db.set_content("burnout_signs", c_signs.strip())
                db.set_content("burnout_help", c_help.strip())
                db.set_content("burnout_contacts", c_contacts.strip())
                st.session_state["toast"] = (
                    "Parent page text saved - visible immediately.")
                st.rerun()


# =====================================================================
# SECTION 9: PARENT / PUBLIC VIEW
# =====================================================================
def render_parent_view(db):
    st.header("For parents & the public")

    tab_timeline, tab_burnout, tab_ledger = st.tabs(
        ["Student Timeline", "Burnout Guide", "Transparency Ledger"])

    # Tab 1 - Student Timeline
    with tab_timeline:
        notices = make_dataframe(db.get_notices())
        if not notices.empty:
            st.markdown("**Notices & announcements**")
            for _, n in notices.iterrows():
                with st.container(border=True):
                    st.markdown(f"**{n['title']}**")
                    st.caption(
                        f"{n['posted_on'][:10]} | {n['department']}")
                    st.write(n["body"] or "")
            st.divider()

        st.subheader("Academic deadlines & upcoming events")
        deadlines = make_dataframe(db.get_deadlines())
        if deadlines.empty:
            st.info("No deadlines published yet.")
        else:
            view = pd.DataFrame(deadlines)
            view["due_date"] = pd.to_datetime(view["due_date"]).dt.date
            view = view.sort_values("due_date")
            today = datetime.date.today()
            for _, row in view.iterrows():
                days = (row["due_date"] - today).days
                color = ("red" if days < 0 else
                         "orange" if days <= 7 else "green")
                icon = "OVERDUE" if days < 0 else ("THIS WEEK" if days <= 7
                                                   else "UPCOMING")
                st.markdown(
                    f":{color}[{icon}] **{row['title']}** - "
                    f"{row['due_date']} "
                    f"(department: {row['department']})")
                st.caption(row["description"] or "")
            st.divider()
            st.markdown("**Upcoming student milestones**")
            milestones = make_dataframe(db.get_milestones())
            if not milestones.empty:
                milestones = milestones[milestones["approved"] == 1]
            upcoming = milestones[
                pd.to_datetime(milestones["deadline"])
                >= pd.to_datetime(datetime.date.today())] if not milestones.empty \
                else milestones
            if upcoming.empty:
                st.caption("No upcoming student milestones.")
            else:
                up = upcoming.copy()
                up["deadline"] = pd.to_datetime(up["deadline"]).dt.date
                up = up.sort_values("deadline")
                st.dataframe(
                    up[["milestone_title", "project_name", "department",
                        "deadline", "workload_feedback"]],
                    use_container_width=True, hide_index=True)

    # Tab 2 - Burnout Guide
    with tab_burnout:
        st.subheader("Helping students manage pressure")

        def content(key):
            return db.get_content(key) or CONTENT_DEFAULTS[key]

        st.markdown(content("burnout_intro"))

        with st.expander("Early warning signs - what to watch for"):
            st.markdown(content("burnout_signs"))

        with st.expander("How parents can help (positive, practical steps)"):
            st.markdown(content("burnout_help"))

        with st.expander("Campus contacts"):
            st.markdown(content("burnout_contacts"))

        st.success(
            "Remember: stress is common, burnout is not a sign of weakness. "
            "The Wellness Log data is reviewed confidentially by the "
            "department.")

    # Tab 3 - Transparency Ledger
    with tab_ledger:
        st.subheader("Accountability, in numbers")
        metrics = resolution_metrics(db)
        if metrics is None:
            st.info("No grievance data to display yet.")
        else:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Tickets raised", metrics["total"])
            c2.metric("Resolved", metrics["resolved"])
            c3.metric("Resolution rate",
                      f'{metrics["rate"]:.1f}%')
            c4.metric("Avg days to resolve",
                      f'{metrics["avg_days"]:.1f}' if metrics["avg_days"]
                      is not None else "-")

            st.markdown("**How each department is doing**")
            for dept, row in metrics["per_dept"].iterrows():
                st.markdown(f"{dept} - "
                            f"{row['resolved']}/{row['total']} resolved "
                            f"({row['resolution_rate']:.0f}%)")
                st.progress(row["resolved"] / row["total"]
                            if row["total"] else 0.0)

            st.markdown("**Recent activity**")
            tickets = make_dataframe(db.get_grievances())
            tickets = tickets.sort_values("id", ascending=False).head(8)
            tickets["opened"] = pd.to_datetime(
                tickets["created_at"]).dt.date
            tickets["last_update"] = pd.to_datetime(
                tickets["updated_at"]).dt.date
            st.dataframe(
                tickets[["ticket_no", "department", "category", "title",
                         "status", "opened", "last_update"]],
                use_container_width=True, hide_index=True)

        st.markdown("**Our pledge**")
        st.caption(
            "Every ticket update is hash-sealed into one unbroken chain - "
            "parents can verify this page is tamper-evident, not just a "
            "pretty screenshot.")
        render_audit_badge(db)


# =====================================================================
# SECTION 10: APP ENTRY POINT
# =====================================================================
def render_splash():
    import base64

    card = st.empty()
    hud = st.empty()
    if os.path.isfile(LOGO_FILE):
        with open(LOGO_FILE, "rb") as fh:
            img_b64 = base64.b64encode(fh.read()).decode("ascii")
        logo_html = (
            f'<img src="data:image/png;base64,{img_b64}" '
            'style="width:min(150px,58vw);height:auto;max-width:100%;'
            'object-fit:contain;display:block;" />')
    else:
        logo_html = (
            '<div style="width:min(150px,58vw);height:auto;aspect-ratio:1/1;'
            'max-width:100%;display:flex;align-items:center;justify-content:'
            'center;color:#fff;font-size:38px;font-weight:800;border-radius:16px;'
            'background:linear-gradient(135deg,#1F406D,#274C79);">SIT</div>')

    taglines = [
        "Securing the audit trail...",
        "Loading wellness data...",
        "Checking meeting slots...",
        "Warming up analytics...",
        "Almost ready...",
    ]

    card.markdown(
        """
        <style>
          @keyframes logoIn { 0% { opacity:0; transform:scale(.6) rotate(-6deg); }
            100% { opacity:1; transform:scale(1) rotate(0deg); } }
          @keyframes logoGlow { 0%,100% { box-shadow:0 0 0 0 rgba(74,122,181,0),
            0 14px 34px rgba(31,64,109,.28); }
            50% { box-shadow:0 0 0 16px rgba(74,122,181,.07),
            0 14px 34px rgba(31,64,109,.28); } }
          @keyframes titleShimmer { 0% { background-position:-200% 0; }
            100% { background-position:200% 0; } }
          @keyframes riseIn { 0% { opacity:0; transform:translateY(14px); }
            100% { opacity:1; transform:translateY(0); } }
          @keyframes floatCard { 0%,100% { transform:translateY(0); }
            50% { transform:translateY(-8px); } }
          @keyframes bubbleUp { 0% { transform:translateY(0) scale(1); opacity:0; }
            15% { opacity:.55; } 100% { transform:translateY(-360px) scale(1.5);
            opacity:0; } }
          @keyframes fillBar { from { width:0%; } to { width:100%; } }
          @keyframes sheen { 0% { transform:translateX(-100%); }
            100% { transform:translateX(280%); } }
          .splash-card { display:flex; flex-direction:column;
            align-items:center; justify-content:center; min-height:74vh;
            width:100%; padding:0 12px; box-sizing:border-box;
            font-family:Arial,Helvetica,sans-serif; position:relative;
            overflow:hidden; }
          .splash-panel { background:linear-gradient(160deg,#ffffff,#eef3f9);
            border:1px solid #dbe4f0; border-radius:22px;
            padding:34px clamp(18px,7vw,54px); max-width:100%;
            text-align:center; position:relative; z-index:1;
            box-shadow:0 18px 50px rgba(31,64,109,.16);
            animation:floatCard 5s ease-in-out infinite; }
          .splash-logo { display:inline-block; background:#fff; padding:12px;
            border-radius:22px; animation:logoIn .9s ease-out both,
            logoGlow 3s ease-in-out 1s infinite; }
          .splash-title { margin-top:20px; font-size:clamp(27px,9vw,38px); font-weight:800;
            letter-spacing:.5px; color:#1F406D;
            background:linear-gradient(90deg,#1F406D,#4A7AB5,#A8C6EE,#4A7AB5,#1F406D);
            background-size:200% auto; -webkit-background-clip:text;
            background-clip:text; -webkit-text-fill-color:transparent;
            animation:titleShimmer 3.5s linear infinite; }
          .splash-sub { margin-top:8px; font-size:16px; color:#5b6b7b;
            animation:riseIn .6s ease-out .5s both; }
          .splash-track { width:min(300px,72vw); max-width:100%; height:10px; margin-top:26px;
            border-radius:6px; background:#d9e2ec;
            box-shadow:inset 0 1px 3px rgba(31,64,109,.25); overflow:hidden;
            position:relative; animation:riseIn .6s ease-out .7s both; }
          .splash-fill { height:100%; border-radius:6px;
            background:linear-gradient(90deg,#1F406D,#4A7AB5,#A8C6EE);
            box-shadow:0 0 15px rgba(74,122,181,.75); position:relative;
            overflow:hidden; animation:fillBar 4s cubic-bezier(.4,0,.2,1) forwards; }
          .splash-fill::after { content:""; position:absolute; top:0; left:0;
            height:100%; width:60%;
            background:linear-gradient(90deg,transparent,rgba(255,255,255,.75),transparent);
            animation:sheen 1.1s linear infinite; }
          .bubble { position:absolute; width:14px; height:14px; border-radius:50%;
            background:rgba(74,122,181,.28); animation:bubbleUp 6s ease-in infinite; }
          .b1 { left:14%; bottom:-30px; animation-duration:6.5s; }
          .b2 { left:36%; bottom:-30px; width:9px; height:9px;
            animation-duration:8s; animation-delay:1.4s; }
          .b3 { left:63%; bottom:-30px; width:18px; height:18px;
            animation-duration:7s; animation-delay:.7s; }
          .b4 { left:84%; bottom:-30px; width:8px; height:8px;
            animation-duration:9s; animation-delay:2.1s; }
        </style>
        <div class="splash-card">
          <div class="bubble b1"></div>
          <div class="bubble b2"></div>
          <div class="bubble b3"></div>
          <div class="bubble b4"></div>
          <div class="splash-panel">
            <div class="splash-logo">__LOGO__</div>
            <div class="splash-title">__TITLE__</div>
            <div class="splash-sub">__SUBTITLE__</div>
            <div class="splash-track"><div class="splash-fill"></div></div>
          </div>
        </div>
        """.replace("__LOGO__", logo_html).replace(
            "__TITLE__", APP_TITLE).replace(
            "__SUBTITLE__", APP_SUBTITLE),
        unsafe_allow_html=True)

    total_ticks = 40
    for step in range(total_ticks + 1):
        pct = int(step / total_ticks * 100)
        tag = taglines[min(step // (total_ticks // len(taglines)),
                           len(taglines) - 1)]
        hud.markdown(
            f'<div style="text-align:center;font-family:Arial,Helvetica,'
            f'sans-serif;color:#7a8ba0;font-size:13px;margin-top:16px;">'
            f'<span style="color:#1F406D;font-weight:700;font-size:15px;">'
            f'{pct}%</span>&nbsp;&nbsp;{tag}</div>',
            unsafe_allow_html=True)
        time.sleep(0.1)

    card.empty()
    hud.empty()


def main():
    page_cfg = dict(page_title=APP_TITLE, layout="wide",
                    initial_sidebar_state="expanded")
    if os.path.isfile(LOGO_FILE):
        page_cfg["page_icon"] = LOGO_FILE
    st.set_page_config(**page_cfg)

    init_state()

    if not st.session_state.get("splash_seen"):
        render_splash()
        st.session_state["splash_seen"] = True

    db = get_db()
    if not st.session_state.get("db_backed_up", False):
        auto_backup_db()
        st.session_state["db_backed_up"] = True
    render_sidebar(db)

    if not st.session_state["auth"]["logged_in"]:
        st.title(APP_TITLE)
        st.markdown(
            f"### {APP_SUBTITLE}\n\n"
            "Student wellness, deadlines, grievance redressal and public "
            "accountability - one offline hub.\n\n"
            "**Sign in from the sidebar** - or use a demo account to explore "
            "instantly.\n\n"
            "| You are | You can see |\n"
            "|---|---|\n"
            "| Student | Anonymous wellness log, milestone tracker, grievance "
            "form |\n"
            "| Staff / Admin | Wellness analytics, meeting manager, grievance "
            "ticket manager with a tamper-evident audit trail |\n"
            "| Parent | Student timeline, burnout guide, transparency ledger |"
        )
        render_footer()
        return

    role = st.session_state["auth"]["role"]
    try:
        if role == ROLE_STUDENT:
            render_student_view(db)
        elif role == ROLE_STAFF:
            render_staff_view(db)
        elif role == ROLE_PARENT:
            render_parent_view(db)
        else:
            st.error(f"Unknown role: {role}")
    except Exception as e:
        st.error("An unexpected error occurred while rendering this view.")
        st.exception(e)

    render_footer()


# double-click app.py -> relaunches throught streamlit
if __name__ == "__main__":
    if st.runtime.exists():
        main()
    else:
        import subprocess
        import sys
        print("Starting SIT Campus Care Hub... browser auto-opens shortly.")
        print("Close this window (or press CTRL+C) to stop the server.")
        sys.exit(subprocess.call(
            [sys.executable, "-m", "streamlit", "run", __file__]))