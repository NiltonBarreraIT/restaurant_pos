from flask import request, render_template, redirect, url_for
from flask_login import login_user, login_required, logout_user

from . import auth_bp


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    from app.models import User  # ✅ import local (evita import circular)

    if request.method == "GET":
        return render_template("login.html")

    # POST
    data = request.form if request.form else request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""

    user = User.query.filter_by(username=username, is_active=True).first()
    if not user or not user.check_password(password):
        return render_template("login.html", error="Credenciales inválidas")

    login_user(user)

    next_url = request.args.get("next") or url_for("pos.pos_ui")
    return redirect(next_url)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
