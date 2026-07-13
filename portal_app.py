import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.secret_key = "placement-portal-simple-app"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.path.join(BASE_DIR, "placement_portal.db")
RESUME_FOLDER = os.path.join(BASE_DIR, "static", "resumes")


def get_db_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_roll_number(roll_number):
    return "".join(roll_number.strip().upper().split())


def is_password_hash(password_value):
    return password_value.startswith(("scrypt:", "pbkdf2:"))


def verify_password_and_upgrade(connection, table_name, user_id, stored_password, password):
    if is_password_hash(stored_password):
        return check_password_hash(stored_password, password)

    if stored_password != password:
        return False

    connection.execute(
        f"UPDATE {table_name} SET password = ? WHERE id = ?",
        (generate_password_hash(password), user_id),
    )
    connection.commit()
    return True


def ensure_column(cursor, table_name, column_name, column_definition):
    columns = {row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})").fetchall()}
    if column_name not in columns:
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_definition}")


def save_resume_file(file_storage, username):
    if not file_storage or file_storage.filename == "":
        return ""

    os.makedirs(RESUME_FOLDER, exist_ok=True)
    original_name = os.path.basename(file_storage.filename)
    filename = f"{username}_{int(datetime.now().timestamp())}_{original_name}"
    full_path = os.path.join(RESUME_FOLDER, filename)
    file_storage.save(full_path)
    return f"resumes/{filename}"


def init_db():
    os.makedirs(RESUME_FOLDER, exist_ok=True)

    with get_db_connection() as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                full_name TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                roll_number TEXT UNIQUE,
                name TEXT NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                phone TEXT NOT NULL,
                department TEXT NOT NULL,
                study_year TEXT NOT NULL,
                gender TEXT NOT NULL,
                cgpa TEXT NOT NULL,
                resume_path TEXT,
                account_status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                phone TEXT NOT NULL,
                website TEXT,
                details TEXT NOT NULL,
                approval_status TEXT NOT NULL DEFAULT 'pending',
                account_status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS drives (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                job_role TEXT NOT NULL,
                description TEXT NOT NULL,
                location TEXT NOT NULL,
                salary TEXT NOT NULL,
                drive_date TEXT NOT NULL,
                last_date_to_apply TEXT NOT NULL,
                approval_status TEXT NOT NULL DEFAULT 'pending',
                lifecycle_status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT NOT NULL,
                FOREIGN KEY (company_id) REFERENCES companies (id)
            )
            """
        )

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drive_id INTEGER NOT NULL,
                student_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'Applied',
                remark TEXT DEFAULT '',
                company_name_snapshot TEXT,
                drive_title_snapshot TEXT,
                job_role_snapshot TEXT,
                location_snapshot TEXT,
                salary_snapshot TEXT,
                applied_at TEXT NOT NULL,
                UNIQUE (drive_id, student_id),
                FOREIGN KEY (drive_id) REFERENCES drives (id),
                FOREIGN KEY (student_id) REFERENCES students (id)
            )
            """
        )

        admins = [
            ("admin", "admin123", "Placement Admin"),
            ("prakash_mohit", "1@Hellord", "Placement Admin"),
        ]
        for username, password, full_name in admins:
            cursor.execute(
                "INSERT OR IGNORE INTO admins (username, password, full_name) VALUES (?, ?, ?)",
                (username, generate_password_hash(password), full_name),
            )

        student_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(students)").fetchall()
        }
        if "roll_number" not in student_columns:
            cursor.execute("ALTER TABLE students ADD COLUMN roll_number TEXT")

        ensure_column(cursor, "applications", "company_name_snapshot", "company_name_snapshot TEXT")
        ensure_column(cursor, "applications", "drive_title_snapshot", "drive_title_snapshot TEXT")
        ensure_column(cursor, "applications", "job_role_snapshot", "job_role_snapshot TEXT")
        ensure_column(cursor, "applications", "location_snapshot", "location_snapshot TEXT")
        ensure_column(cursor, "applications", "salary_snapshot", "salary_snapshot TEXT")

        cursor.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_students_roll_number_unique
            ON students (roll_number)
            WHERE roll_number IS NOT NULL
            """
        )

        students_without_roll = cursor.execute(
            "SELECT id FROM students WHERE roll_number IS NULL OR TRIM(roll_number) = ''"
        ).fetchall()
        for student in students_without_roll:
            cursor.execute(
                "UPDATE students SET roll_number = ? WHERE id = ?",
                (f"LEGACY{student['id']:04d}", student["id"]),
            )

        existing_departments = cursor.execute(
            "SELECT DISTINCT department FROM students WHERE TRIM(COALESCE(department, '')) != ''"
        ).fetchall()
        for department in existing_departments:
            cursor.execute(
                "INSERT OR IGNORE INTO departments (name, created_at) VALUES (?, ?)",
                (department["department"].strip(), now_text()),
            )

        cursor.execute(
            """
            UPDATE applications
            SET
                company_name_snapshot = COALESCE(
                    NULLIF(company_name_snapshot, ''),
                    (
                        SELECT companies.name
                        FROM drives
                        JOIN companies ON companies.id = drives.company_id
                        WHERE drives.id = applications.drive_id
                    )
                ),
                drive_title_snapshot = COALESCE(
                    NULLIF(drive_title_snapshot, ''),
                    (SELECT drives.title FROM drives WHERE drives.id = applications.drive_id)
                ),
                job_role_snapshot = COALESCE(
                    NULLIF(job_role_snapshot, ''),
                    (SELECT drives.job_role FROM drives WHERE drives.id = applications.drive_id)
                ),
                location_snapshot = COALESCE(
                    NULLIF(location_snapshot, ''),
                    (SELECT drives.location FROM drives WHERE drives.id = applications.drive_id)
                ),
                salary_snapshot = COALESCE(
                    NULLIF(salary_snapshot, ''),
                    (SELECT drives.salary FROM drives WHERE drives.id = applications.drive_id)
                )
            """
        )

        connection.commit()


def login_required(role):
    def decorator(route_function):
        @wraps(route_function)
        def wrapper(*args, **kwargs):
            if session.get("role") != role:
                flash("Please login first.")
                return redirect(url_for(f"{role}_login"))
            return route_function(*args, **kwargs)

        return wrapper

    return decorator


def delete_student(connection, student_id):
    connection.execute("DELETE FROM applications WHERE student_id = ?", (student_id,))
    connection.execute("DELETE FROM students WHERE id = ?", (student_id,))


def delete_company(connection, company_id):
    connection.execute("DELETE FROM drives WHERE company_id = ?", (company_id,))
    connection.execute("DELETE FROM companies WHERE id = ?", (company_id,))


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.")
    return redirect(url_for("home"))


@app.route("/student_register", methods=["GET", "POST"])
def student_register():
    if request.method == "POST":
        form = request.form
        password = form["password"].strip()
        confirm_password = form["confirm_password"].strip()
        roll_number = normalize_roll_number(form["roll_number"])

        if password != confirm_password:
            flash("Password and confirm password must match.")
            return redirect(url_for("student_register"))

        with get_db_connection() as connection:
            department_exists = connection.execute(
                "SELECT id FROM departments WHERE name = ?",
                (form["department"].strip(),),
            ).fetchone()

        if not department_exists:
            flash("Please choose a valid department.")
            return redirect(url_for("student_register"))

        resume_path = save_resume_file(request.files.get("resume"), form["username"].strip())

        try:
            with get_db_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO students
                    (roll_number, name, username, password, email, phone, department, study_year, gender, cgpa, resume_path, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        roll_number,
                        form["name"].strip(),
                        form["username"].strip(),
                        generate_password_hash(password),
                        form["email"].strip(),
                        form["phone"].strip(),
                        form["department"].strip(),
                        form["study_year"].strip(),
                        form["gender"].strip(),
                        form["cgpa"].strip(),
                        resume_path,
                        now_text(),
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError:
            flash("Student roll number, username, or email already exists.")
            return redirect(url_for("student_register"))

        flash("Student registration complete. Please login.")
        return redirect(url_for("student_login"))

    with get_db_connection() as connection:
        departments = connection.execute(
            "SELECT * FROM departments ORDER BY name ASC"
        ).fetchall()

    return render_template("student_register.html", departments=departments)


@app.route("/student_login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        with get_db_connection() as connection:
            student = connection.execute(
                "SELECT * FROM students WHERE username = ?",
                (username,),
            ).fetchone()

            valid_password = bool(student) and verify_password_and_upgrade(
                connection,
                "students",
                student["id"],
                student["password"],
                password,
            )

        if not valid_password:
            flash("Invalid student login.")
            return redirect(url_for("student_login"))

        if student["account_status"] == "deactivated":
            flash("Your account is deactivated. Please contact admin.")
            return redirect(url_for("student_login"))

        session["role"] = "student"
        session["user_id"] = student["id"]
        session["name"] = student["name"]

        if student["account_status"] == "blacklisted":
            flash("Your account is blacklisted. You can sign in, but you cannot apply for drives.")

        return redirect(url_for("student_dash"))

    return render_template("student_login.html")


@app.route("/company_register", methods=["GET", "POST"])
def company_register():
    if request.method == "POST":
        form = request.form
        password = form["password"].strip()
        confirm_password = form["confirm_password"].strip()

        if password != confirm_password:
            flash("Password and confirm password must match.")
            return redirect(url_for("company_register"))

        try:
            with get_db_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO companies
                    (name, username, password, email, phone, website, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        form["name"].strip(),
                        form["username"].strip(),
                        generate_password_hash(password),
                        form["email"].strip(),
                        form["phone"].strip(),
                        form["website"].strip(),
                        form["details"].strip(),
                        now_text(),
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError:
            flash("Company username or email already exists.")
            return redirect(url_for("company_register"))

        flash("Company registration submitted. Wait for admin approval.")
        return redirect(url_for("company_login"))

    return render_template("company_register.html")


@app.route("/company_login", methods=["GET", "POST"])
def company_login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        with get_db_connection() as connection:
            company = connection.execute(
                "SELECT * FROM companies WHERE username = ?",
                (username,),
            ).fetchone()

            valid_password = bool(company) and verify_password_and_upgrade(
                connection,
                "companies",
                company["id"],
                company["password"],
                password,
            )

        if not valid_password:
            flash("Invalid company login.")
            return redirect(url_for("company_login"))

        if company["account_status"] == "deactivated":
            flash("Company account is deactivated. Please contact admin.")
            return redirect(url_for("company_login"))

        session["role"] = "company"
        session["user_id"] = company["id"]
        session["name"] = company["name"]

        if company["account_status"] == "blacklisted":
            flash("Your account is blacklisted. You can sign in and edit your profile, but you cannot do company actions.")

        return redirect(url_for("company_dash"))

    return render_template("company_login.html")


@app.route("/admin_login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        username = request.form["username"].strip()
        password = request.form["password"].strip()

        with get_db_connection() as connection:
            admin = connection.execute(
                "SELECT * FROM admins WHERE username = ?",
                (username,),
            ).fetchone()

            valid_password = bool(admin) and verify_password_and_upgrade(
                connection,
                "admins",
                admin["id"],
                admin["password"],
                password,
            )

        if not valid_password:
            flash("Invalid admin login.")
            return redirect(url_for("admin_login"))

        session["role"] = "admin"
        session["user_id"] = admin["id"]
        session["name"] = admin["full_name"]
        return redirect(url_for("admin_dash"))

    return render_template("admin_login.html")


@app.route("/admin_dash", methods=["GET", "POST"])
@login_required("admin")
def admin_dash():
    with get_db_connection() as connection:
        if request.method == "POST":
            action = request.form["action"]

            if action == "company_approval":
                connection.execute(
                    "UPDATE companies SET approval_status = ? WHERE id = ?",
                    (request.form["decision"], request.form["company_id"]),
                )
                flash("Company approval updated.")

            elif action == "drive_approval":
                connection.execute(
                    "UPDATE drives SET approval_status = ? WHERE id = ?",
                    (request.form["decision"], request.form["drive_id"]),
                )
                flash("Drive approval updated.")

            elif action == "student_account":
                connection.execute(
                    "UPDATE students SET account_status = ? WHERE id = ?",
                    (request.form["status"], request.form["student_id"]),
                )
                flash("Student account updated.")

            elif action == "company_account":
                connection.execute(
                    "UPDATE companies SET account_status = ? WHERE id = ?",
                    (request.form["status"], request.form["company_id"]),
                )
                flash("Company account updated.")

            elif action == "company_status":
                connection.execute(
                    "UPDATE companies SET approval_status = ? WHERE id = ?",
                    (request.form["status"], request.form["company_id"]),
                )
                flash("Company approval status updated.")

            elif action == "delete_student":
                delete_student(connection, request.form["student_id"])
                flash("Student deleted.")

            elif action == "delete_company":
                delete_company(connection, request.form["company_id"])
                flash("Company deleted.")

            elif action == "create_department":
                department_name = request.form["department_name"].strip()
                if department_name:
                    try:
                        connection.execute(
                            "INSERT INTO departments (name, created_at) VALUES (?, ?)",
                            (department_name, now_text()),
                        )
                        flash("Department created.")
                    except sqlite3.IntegrityError:
                        flash("Department already exists.")

            elif action == "delete_department":
                connection.execute(
                    "DELETE FROM departments WHERE id = ?",
                    (request.form["department_id"],),
                )
                flash("Department deleted.")

            connection.commit()
            return redirect(url_for("admin_dash"))

        student_search = request.args.get("student_search", "").strip()
        company_search = request.args.get("company_search", "").strip()
        show_student_results_only = bool(student_search)
        show_company_results_only = bool(company_search)

        students = connection.execute(
            """
            SELECT * FROM students
            WHERE
                ? = ''
                OR name LIKE ?
                OR roll_number LIKE ?
                OR phone LIKE ?
            ORDER BY created_at DESC
            """,
            (
                student_search,
                f"%{student_search}%",
                f"%{student_search}%",
                f"%{student_search}%",
            ),
        ).fetchall()

        companies = connection.execute(
            """
            SELECT * FROM companies
            WHERE ? = '' OR name LIKE ?
            ORDER BY created_at DESC
            """,
            (company_search, f"%{company_search}%"),
        ).fetchall()

        metrics = {
            "students": connection.execute("SELECT COUNT(*) FROM students").fetchone()[0],
            "companies": connection.execute("SELECT COUNT(*) FROM companies").fetchone()[0],
            "applications": connection.execute("SELECT COUNT(*) FROM applications").fetchone()[0],
            "drives": connection.execute("SELECT COUNT(*) FROM drives").fetchone()[0],
        }

        departments = connection.execute(
            """
            SELECT departments.*, COUNT(students.id) AS student_count
            FROM departments
            LEFT JOIN students ON students.department = departments.name
            GROUP BY departments.id
            ORDER BY departments.name ASC
            """
        ).fetchall()

        pending_companies = connection.execute(
            "SELECT * FROM companies WHERE approval_status = 'pending' ORDER BY created_at DESC"
        ).fetchall()

        pending_drives = connection.execute(
            """
            SELECT drives.*, companies.name AS company_name
            FROM drives
            JOIN companies ON companies.id = drives.company_id
            WHERE drives.approval_status = 'pending'
            ORDER BY drives.created_at DESC
            """
        ).fetchall()

        all_drives = connection.execute(
            """
            SELECT drives.*, companies.name AS company_name, companies.account_status AS company_account_status
            FROM drives
            JOIN companies ON companies.id = drives.company_id
            ORDER BY drives.created_at DESC
            """
        ).fetchall()

        all_applications = connection.execute(
            """
            SELECT
                applications.*,
                students.name AS student_name,
                students.department,
                COALESCE(applications.company_name_snapshot, companies.name, 'Deleted company') AS company_name,
                COALESCE(applications.drive_title_snapshot, drives.title, 'Deleted drive') AS drive_title,
                COALESCE(applications.job_role_snapshot, drives.job_role, '-') AS job_role
            FROM applications
            JOIN students ON students.id = applications.student_id
            LEFT JOIN drives ON drives.id = applications.drive_id
            LEFT JOIN companies ON companies.id = drives.company_id
            ORDER BY applications.applied_at DESC
            """
        ).fetchall()

    return render_template(
        "admin_dash.html",
        metrics=metrics,
        departments=departments,
        students=students,
        companies=companies,
        pending_companies=pending_companies,
        pending_drives=pending_drives,
        all_drives=all_drives,
        all_applications=all_applications,
        student_search=student_search,
        company_search=company_search,
        show_student_results_only=show_student_results_only,
        show_company_results_only=show_company_results_only,
    )


@app.route("/admin/student/<int:student_id>")
@login_required("admin")
def admin_student_profile(student_id):
    with get_db_connection() as connection:
        student = connection.execute(
            """
            SELECT students.*, COUNT(applications.id) AS application_count
            FROM students
            LEFT JOIN applications ON applications.student_id = students.id
            WHERE students.id = ?
            GROUP BY students.id
            """,
            (student_id,),
        ).fetchone()

        if not student:
            flash("Student not found.")
            return redirect(url_for("admin_dash"))

        applications = connection.execute(
            """
            SELECT
                applications.*,
                COALESCE(applications.company_name_snapshot, companies.name, 'Deleted company') AS company_name,
                COALESCE(applications.drive_title_snapshot, drives.title, 'Deleted drive') AS drive_title,
                COALESCE(applications.job_role_snapshot, drives.job_role, '-') AS job_role,
                COALESCE(applications.location_snapshot, drives.location, '-') AS location,
                COALESCE(applications.salary_snapshot, drives.salary, '-') AS salary
            FROM applications
            LEFT JOIN drives ON drives.id = applications.drive_id
            LEFT JOIN companies ON companies.id = drives.company_id
            WHERE applications.student_id = ?
            ORDER BY applications.applied_at DESC
            """,
            (student_id,),
        ).fetchall()

    return render_template(
        "admin_profile.html",
        profile_type="student",
        profile=student,
        applications=applications,
    )


@app.route("/admin/company/<int:company_id>")
@login_required("admin")
def admin_company_profile(company_id):
    with get_db_connection() as connection:
        company = connection.execute(
            """
            SELECT companies.*,
                   COUNT(DISTINCT drives.id) AS drive_count,
                   COUNT(applications.id) AS application_count
            FROM companies
            LEFT JOIN drives ON drives.company_id = companies.id
            LEFT JOIN applications ON applications.drive_id = drives.id
            WHERE companies.id = ?
            GROUP BY companies.id
            """,
            (company_id,),
        ).fetchone()

        if not company:
            flash("Company not found.")
            return redirect(url_for("admin_dash"))

        drives = connection.execute(
            """
            SELECT drives.*, COUNT(applications.id) AS applicant_count
            FROM drives
            LEFT JOIN applications ON applications.drive_id = drives.id
            WHERE drives.company_id = ?
            GROUP BY drives.id
            ORDER BY drives.created_at DESC
            """,
            (company_id,),
        ).fetchall()

    return render_template(
        "admin_profile.html",
        profile_type="company",
        profile=company,
        drives=drives,
    )


@app.route("/company_dash", methods=["GET", "POST"])
@login_required("company")
def company_dash():
    company_id = session["user_id"]

    with get_db_connection() as connection:
        company = connection.execute(
            "SELECT * FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()

        if not company:
            session.clear()
            flash("Company account no longer exists.")
            return redirect(url_for("company_login"))

        if company["account_status"] == "deactivated":
            session.clear()
            flash("Company account is deactivated. Please contact admin.")
            return redirect(url_for("company_login"))

        company_is_limited = company["account_status"] == "blacklisted"

        if request.method == "POST":
            action = request.form["action"]

            if action == "update_profile":
                connection.execute(
                    """
                    UPDATE companies
                    SET name = ?, email = ?, phone = ?, website = ?, details = ?
                    WHERE id = ?
                    """,
                    (
                        request.form["name"].strip(),
                        request.form["email"].strip(),
                        request.form["phone"].strip(),
                        request.form["website"].strip(),
                        request.form["details"].strip(),
                        company_id,
                    ),
                )
                flash("Company profile updated.")

            elif action == "create_drive":
                if company_is_limited:
                    flash("Blacklisted companies can edit their profile only.")
                    return redirect(url_for("company_dash"))

                if company["approval_status"] != "approved" or company["account_status"] != "active":
                    flash("Only approved active companies can create drives.")
                    return redirect(url_for("company_dash"))

                connection.execute(
                    """
                    INSERT INTO drives
                    (company_id, title, job_role, description, location, salary, drive_date, last_date_to_apply, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        company_id,
                        request.form["title"].strip(),
                        request.form["job_role"].strip(),
                        request.form["description"].strip(),
                        request.form["location"].strip(),
                        request.form["salary"].strip(),
                        request.form["drive_date"].strip(),
                        request.form["last_date_to_apply"].strip(),
                        now_text(),
                    ),
                )
                flash("Drive created and sent for admin approval.")

            elif action == "edit_drive":
                if company_is_limited:
                    flash("Blacklisted companies can edit their profile only.")
                    return redirect(url_for("company_dash"))

                connection.execute(
                    """
                    UPDATE drives
                    SET title = ?, job_role = ?, description = ?, location = ?, salary = ?, drive_date = ?, last_date_to_apply = ?
                    WHERE id = ? AND company_id = ?
                    """,
                    (
                        request.form["title"].strip(),
                        request.form["job_role"].strip(),
                        request.form["description"].strip(),
                        request.form["location"].strip(),
                        request.form["salary"].strip(),
                        request.form["drive_date"].strip(),
                        request.form["last_date_to_apply"].strip(),
                        request.form["drive_id"],
                        company_id,
                    ),
                )
                flash("Drive updated.")

            elif action == "close_drive":
                if company_is_limited:
                    flash("Blacklisted companies can edit their profile only.")
                    return redirect(url_for("company_dash"))

                connection.execute(
                    "UPDATE drives SET lifecycle_status = 'closed' WHERE id = ? AND company_id = ?",
                    (request.form["drive_id"], company_id),
                )
                flash("Drive closed.")

            elif action == "delete_drive":
                if company_is_limited:
                    flash("Blacklisted companies can edit their profile only.")
                    return redirect(url_for("company_dash"))

                drive_id = request.form["drive_id"]
                connection.execute(
                    "DELETE FROM drives WHERE id = ? AND company_id = ?",
                    (drive_id, company_id),
                )
                flash("Drive removed.")

            elif action == "update_application_status":
                if company_is_limited:
                    flash("Blacklisted companies can edit their profile only.")
                    return redirect(url_for("company_dash"))

                connection.execute(
                    """
                    UPDATE applications
                    SET status = ?, remark = ?
                    WHERE id = ? AND drive_id IN (SELECT id FROM drives WHERE company_id = ?)
                    """,
                    (
                        request.form["status"],
                        request.form["remark"].strip(),
                        request.form["application_id"],
                        company_id,
                    ),
                )
                flash("Application status updated.")

            connection.commit()
            return redirect(url_for("company_dash"))

        company = connection.execute(
            "SELECT * FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()

        drives = connection.execute(
            """
            SELECT drives.*, COUNT(applications.id) AS applicant_count
            FROM drives
            LEFT JOIN applications ON applications.drive_id = drives.id
            WHERE drives.company_id = ?
            GROUP BY drives.id
            ORDER BY drives.created_at DESC
            """,
            (company_id,),
        ).fetchall()

        applications = connection.execute(
            """
            SELECT
                applications.*,
                students.name AS student_name,
                students.department,
                students.cgpa,
                students.resume_path,
                drives.title AS drive_title,
                drives.job_role
            FROM applications
            JOIN students ON students.id = applications.student_id
            JOIN drives ON drives.id = applications.drive_id
            WHERE drives.company_id = ?
            ORDER BY applications.applied_at DESC
            """,
            (company_id,),
        ).fetchall()

    return render_template(
        "company_dash.html",
        company=company,
        drives=drives,
        applications=applications,
        company_is_limited=company_is_limited,
    )


@app.route("/student_dash", methods=["GET", "POST"])
@login_required("student")
def student_dash():
    student_id = session["user_id"]

    with get_db_connection() as connection:
        student = connection.execute(
            "SELECT * FROM students WHERE id = ?",
            (student_id,),
        ).fetchone()

        if not student:
            session.clear()
            flash("Student account no longer exists.")
            return redirect(url_for("student_login"))

        if student["account_status"] == "deactivated":
            session.clear()
            flash("Your account is deactivated. Please contact admin.")
            return redirect(url_for("student_login"))

        if request.method == "POST":
            action = request.form["action"]

            if action == "update_profile":
                resume_path = student["resume_path"]
                new_resume_path = save_resume_file(request.files.get("resume"), student["username"])
                if new_resume_path:
                    resume_path = new_resume_path

                department_exists = connection.execute(
                    "SELECT id FROM departments WHERE name = ?",
                    (request.form["department"].strip(),),
                ).fetchone()
                if not department_exists:
                    flash("Please choose a valid department.")
                    return redirect(url_for("student_dash"))

                connection.execute(
                    """
                    UPDATE students
                    SET name = ?, email = ?, phone = ?, department = ?, study_year = ?, gender = ?, cgpa = ?, resume_path = ?
                    WHERE id = ?
                    """,
                    (
                        request.form["name"].strip(),
                        request.form["email"].strip(),
                        request.form["phone"].strip(),
                        request.form["department"].strip(),
                        request.form["study_year"].strip(),
                        request.form["gender"].strip(),
                        request.form["cgpa"].strip(),
                        resume_path,
                        student_id,
                    ),
                )
                flash("Student profile updated.")

            elif action == "apply_drive":
                drive_id = request.form["drive_id"]

                if student["account_status"] == "blacklisted":
                    flash("Blacklisted students cannot apply for placement drives.")
                    return redirect(url_for("student_dash"))

                existing_application = connection.execute(
                    "SELECT id FROM applications WHERE drive_id = ? AND student_id = ?",
                    (drive_id, student_id),
                ).fetchone()

                if existing_application:
                    flash("You have already applied for this drive.")
                    return redirect(url_for("student_dash"))

                drive = connection.execute(
                    """
                    SELECT drives.id, companies.name AS company_name, drives.title, drives.job_role, drives.location, drives.salary
                    FROM drives
                    JOIN companies ON companies.id = drives.company_id
                    WHERE drives.id = ?
                    AND drives.approval_status = 'approved'
                    AND drives.lifecycle_status = 'open'
                    AND companies.approval_status = 'approved'
                    AND companies.account_status = 'active'
                    """,
                    (drive_id,),
                ).fetchone()

                if not drive:
                    flash("This drive is not open for application.")
                    return redirect(url_for("student_dash"))

                connection.execute(
                    """
                    INSERT INTO applications (
                        drive_id,
                        student_id,
                        company_name_snapshot,
                        drive_title_snapshot,
                        job_role_snapshot,
                        location_snapshot,
                        salary_snapshot,
                        applied_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        drive_id,
                        student_id,
                        drive["company_name"],
                        drive["title"],
                        drive["job_role"],
                        drive["location"],
                        drive["salary"],
                        now_text(),
                    ),
                )
                flash("Application submitted.")

            connection.commit()
            return redirect(url_for("student_dash"))

        student = connection.execute(
            "SELECT * FROM students WHERE id = ?",
            (student_id,),
        ).fetchone()

        departments = connection.execute(
            "SELECT * FROM departments ORDER BY name ASC"
        ).fetchall()

        available_drives = connection.execute(
            """
            SELECT drives.*, companies.name AS company_name, applications.id AS application_id
            FROM drives
            JOIN companies ON companies.id = drives.company_id
            LEFT JOIN applications
                ON applications.drive_id = drives.id
               AND applications.student_id = ?
            WHERE drives.approval_status = 'approved'
            AND drives.lifecycle_status = 'open'
            AND companies.approval_status = 'approved'
            AND companies.account_status = 'active'
            ORDER BY drives.drive_date ASC
            """
            ,
            (student_id,),
        ).fetchall()

        applied_drives = connection.execute(
            """
            SELECT
                applications.*,
                COALESCE(applications.company_name_snapshot, companies.name, 'Deleted company') AS company_name,
                COALESCE(applications.drive_title_snapshot, drives.title, 'Deleted drive') AS drive_title,
                COALESCE(applications.job_role_snapshot, drives.job_role, '-') AS job_role,
                drives.drive_date,
                COALESCE(applications.location_snapshot, drives.location, '-') AS location,
                COALESCE(applications.salary_snapshot, drives.salary, '-') AS salary
            FROM applications
            LEFT JOIN drives ON drives.id = applications.drive_id
            LEFT JOIN companies ON companies.id = drives.company_id
            WHERE applications.student_id = ?
            ORDER BY applications.applied_at DESC
            """,
            (student_id,),
        ).fetchall()

    return render_template(
        "student_dash.html",
        student=student,
        departments=departments,
        available_drives=available_drives,
        applied_drives=applied_drives,
    )


init_db()
