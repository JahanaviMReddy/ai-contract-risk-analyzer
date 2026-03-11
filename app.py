import json
from io import BytesIO
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_login import LoginManager, login_user, login_required, logout_user, current_user
import os

# Optional reportlab support for PDF report export.
try:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    _has_reportlab = True
except ImportError:
    _has_reportlab = False

from analyzer import extract_text_from_pdf, analyze_contract_text
from database import (
    init_db,
    get_db_connection,
    User,
    get_user_by_email,
    get_user_by_username,
    create_user,
    save_contract_analysis,
    get_user_contracts,
)


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")
    app.config["UPLOAD_FOLDER"] = os.path.join(os.path.dirname(__file__), "uploads")
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    init_db()

    login_manager = LoginManager()
    login_manager.login_view = "login"
    login_manager.init_app(app)

    @login_manager.user_loader
    def load_user(user_id):
        conn = get_db_connection()
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        conn.close()
        if row:
            return User.from_row(row)
        return None

    def _build_report_text(row_dict):
        analysis = row_dict
        lines = [
            f"Contract: {row_dict.get('filename', 'Unknown')}",
            f"Type: {analysis.get('contract_type', 'General Contract')}",
            f"Detected Language: {analysis.get('analysis_language', 'en')}",
            f"Recommendation Language: {analysis.get('recommendation_language', 'en')}",
            "",
            "Summary:",
            analysis.get('summary', 'N/A'),
            "",
            f"Risk Score: {analysis.get('risk_score', 'N/A')}/100",
            "",
            "Risky Clauses:",
        ]

        risk_clauses = [c for c in analysis.get('clause_classification', []) if c.get('risk_flag')]
        if risk_clauses:
            for i, clause in enumerate(risk_clauses, 1):
                lines.append(f"{i}. {clause.get('sentence')} ({clause.get('clause_type')})")
        else:
            lines.append("None detected")

        lines.append("")
        lines.append("Recommendations:")
        recs = analysis.get('recommendations', [])
        if isinstance(recs, list):
            for r in recs:
                lines.append(f"- {r}")
        else:
            lines.append(str(recs))

        return "\n".join(lines)

    def _build_report_pdf(row_dict):
        if not _has_reportlab:
            return None
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=letter)
        styles = getSampleStyleSheet()
        story = []

        story.append(Paragraph(f"Contract Risk Report: {row_dict.get('filename', 'Unknown')}", styles['Title']))
        story.append(Spacer(1, 12))

        story.append(Paragraph(f"Type: {row_dict.get('contract_type', 'General Contract')}", styles['Normal']))
        story.append(Paragraph(f"Detected Language: {row_dict.get('analysis_language', 'en')}", styles['Normal']))
        story.append(Paragraph(f"Recommendation Language: {row_dict.get('recommendation_language', 'en')}", styles['Normal']))
        story.append(Spacer(1, 12))

        story.append(Paragraph("Summary:", styles['Heading2']))
        story.append(Paragraph(row_dict.get('summary', 'N/A'), styles['BodyText']))
        story.append(Spacer(1, 12))

        story.append(Paragraph(f"Risk Score: {row_dict.get('risk_score', 'N/A')}/100", styles['Normal']))
        story.append(Spacer(1, 12))

        story.append(Paragraph("Risky Clauses:", styles['Heading2']))
        risky_clauses = [c for c in row_dict.get('clause_classification', []) if c.get('risk_flag')]
        if not risky_clauses:
            story.append(Paragraph("None detected", styles['BodyText']))
        else:
            for i, clause in enumerate(risky_clauses, 1):
                story.append(Paragraph(f"{i}. {clause.get('sentence')} ({clause.get('clause_type')})", styles['BodyText']))
        story.append(Spacer(1, 12))

        story.append(Paragraph("Recommendations:", styles['Heading2']))
        recs = row_dict.get('recommendations', [])
        if isinstance(recs, list):
            for r in recs:
                story.append(Paragraph(f"- {r}", styles['BodyText']))
        else:
            story.append(Paragraph(str(recs), styles['BodyText']))

        doc.build(story)
        buffer.seek(0)
        return buffer

    @app.route("/")
    def index():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        return redirect(url_for("login"))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = get_user_by_email(email)
            if user and user.check_password(password):
                login_user(user)
                flash("Logged in successfully.", "success")
                return redirect(url_for("dashboard"))
            flash("Invalid email or password.", "danger")
        return render_template("login.html")

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm = request.form.get("confirm_password", "")

            if not username or not email or not password:
                flash("All fields are required.", "warning")
            elif password != confirm:
                flash("Passwords do not match.", "warning")
            elif get_user_by_username(username):
                flash("That username is already taken.", "warning")
            elif get_user_by_email(email):
                flash("An account with that email already exists.", "warning")
            else:
                create_user(username=username, email=email, password=password)
                flash("Account created. Please log in.", "success")
                return redirect(url_for("login"))

        return render_template("signup.html")

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    @app.route("/dashboard")
    @login_required
    def dashboard():
        contract_type = request.args.get("type", "all")
        contracts = get_user_contracts(current_user.id)
        if contract_type and contract_type != "all":
            contracts = [c for c in contracts if c.get("contract_type", "General Contract") == contract_type]
        unique_types = sorted({c.get("contract_type", "General Contract") for c in get_user_contracts(current_user.id)})
        return render_template("dashboard.html", contracts=contracts, selected_type=contract_type, contract_types=unique_types)

    @app.route("/delete_contract/<int:contract_id>", methods=["POST"])
    @login_required
    def delete_contract(contract_id):
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM contracts WHERE id = ? AND user_id = ?",
            (contract_id, current_user.id),
        ).fetchone()
        if not row:
            conn.close()
            flash("Contract not found or permission denied.", "warning")
            return redirect(url_for("dashboard"))

        # Delete file from uploads folder if exists
        filename = row["filename"]
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception:
            # Continue even if file deletion fails.
            pass

        conn.execute("DELETE FROM contracts WHERE id = ?", (contract_id,))
        conn.commit()
        conn.close()

        flash("Contract deleted successfully.", "success")
        return redirect(url_for("dashboard"))

    @app.route("/upload", methods=["GET", "POST"])
    @login_required
    def upload():
        if request.method == "POST":
            file = request.files.get("contract")
            if not file or file.filename == "":
                flash("Please select a PDF file to upload.", "warning")
                return redirect(request.url)

            if not file.filename.lower().endswith(".pdf"):
                flash("Only PDF files are supported.", "warning")
                return redirect(request.url)

            filename = file.filename
            save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)

            # Ensure unique filename
            base, ext = os.path.splitext(filename)
            counter = 1
            while os.path.exists(save_path):
                filename = f"{base}_{counter}{ext}"
                save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                counter += 1

            file.save(save_path)

            recommendation_language = request.form.get("recommendation_language", "en")

            try:
                text = extract_text_from_pdf(save_path)
                analysis = analyze_contract_text(text, recommendation_language=recommendation_language)
            except Exception as e:
                flash(f"Error analyzing contract: {e}", "danger")
                return redirect(request.url)

            contract_id = save_contract_analysis(
                user_id=current_user.id,
                filename=filename,
                summary=analysis["summary"],
                risk_score=analysis["risk_score"],
                contract_type=analysis.get("contract_type", "General Contract"),
                risk_factors=analysis["risk_factors"],
                recommendations=analysis["recommendations"],
                clause_classification=analysis.get("clause_classification"),
                analysis_result=analysis,
            )

            return redirect(url_for("results", contract_id=contract_id))

        return render_template("upload.html")

    @app.route("/results/<int:contract_id>")
    @login_required
    def results(contract_id):
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM contracts WHERE id = ? AND user_id = ?",
            (contract_id, current_user.id),
        ).fetchone()
        conn.close()
        if not row:
            flash("Contract analysis not found.", "warning")
            return redirect(url_for("dashboard"))

        row_dict = dict(row)

        analysis_result = row_dict.get("analysis_result")
        if analysis_result and isinstance(analysis_result, str):
            try:
                analysis_json = json.loads(analysis_result)
            except (json.JSONDecodeError, TypeError):
                analysis_json = {}
        else:
            analysis_json = {}

        # Merge stored analysis JSON on top of row fields.
        if isinstance(analysis_json, dict):
            row_dict.update(analysis_json)

        clause_classification = row_dict.get("clause_classification")
        if clause_classification and isinstance(clause_classification, str):
            try:
                row_dict["clause_classification"] = json.loads(clause_classification)
            except (json.JSONDecodeError, TypeError):
                row_dict["clause_classification"] = []
        elif not clause_classification:
            row_dict["clause_classification"] = []

        return render_template(
            "results.html",
            analysis=row_dict,
        )

    @app.route("/download_report/<int:contract_id>")
    @login_required
    def download_report(contract_id):
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM contracts WHERE id = ? AND user_id = ?",
            (contract_id, current_user.id),
        ).fetchone()
        conn.close()

        if not row:
            flash("Contract report not found.", "warning")
            return redirect(url_for("dashboard"))

        analysis_result = row["analysis_result"]
        if analysis_result and isinstance(analysis_result, str):
            try:
                analysis = json.loads(analysis_result)
            except (json.JSONDecodeError, TypeError):
                analysis = {}
        elif isinstance(analysis_result, dict):
            analysis = analysis_result
        else:
            analysis = {}

        summary_text = analysis.get("summary", "N/A")
        risk_score = analysis.get("risk_score", "N/A")
        clause_classification = analysis.get("clause_classification", [])
        recommendations = analysis.get("recommendations", [])
        contract_type = analysis.get("contract_type", "Unknown")
        language = analysis.get("analysis_language", "en")

        lines = [
            f"Contract: {row['filename']}",
            f"Type: {contract_type}",
            f"Detected Language: {language}",
            "", "Summary:", summary_text,
            "", f"Risk Score: {risk_score}/100", "",
            "Risky Clauses:",
        ]

        for idx, c in enumerate(clause_classification, 1):
            if c.get("risk_flag"):
                lines.append(f"{idx}. {c.get('sentence')} ({c.get('clause_type')})")

        if not any(c.get("risk_flag") for c in clause_classification):
            lines.append("None detected")

        lines.extend(["", "Recommendations:"])
        if isinstance(recommendations, list):
            for r in recommendations:
                lines.append(f"- {r}")
        else:
            lines.append(str(recommendations))

        report_text = "\n".join(lines)
        filename = f"contract_report_{contract_id}.txt"

        return (
            report_text,
            200,
            {
                "Content-Type": "text/plain; charset=utf-8",
                "Content-Disposition": f"attachment; filename={filename}",
            },
        )

    @app.route("/download_report_pdf/<int:contract_id>")
    @login_required
    def download_report_pdf(contract_id):
        conn = get_db_connection()
        row = conn.execute(
            "SELECT * FROM contracts WHERE id = ? AND user_id = ?",
            (contract_id, current_user.id),
        ).fetchone()
        conn.close()

        if not row:
            flash("Contract report not found.", "warning")
            return redirect(url_for("dashboard"))

        analysis_result = row["analysis_result"]
        if analysis_result and isinstance(analysis_result, str):
            try:
                analysis = json.loads(analysis_result)
            except (json.JSONDecodeError, TypeError):
                analysis = {}
        elif isinstance(analysis_result, dict):
            analysis = analysis_result
        else:
            analysis = {}

        row_dict = dict(row)
        if isinstance(analysis, dict):
            row_dict.update(analysis)

        if not _has_reportlab:
            flash("PDF export requires reportlab library. Install reportlab and retry.", "warning")
            return redirect(url_for("results", contract_id=contract_id))

        pdf_buffer = _build_report_pdf(row_dict)
        if pdf_buffer is None:
            flash("Unable to generate PDF report.", "danger")
            return redirect(url_for("results", contract_id=contract_id))

        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f"contract_report_{contract_id}.pdf",
        )

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)

