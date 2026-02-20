from flask import request, render_template, redirect, url_for
from flask_login import login_user, login_required, logout_user

from . import auth_bp
from ..extensions import db
from ..models import User


def is_first_run() -> bool:
    """True si no hay usuarios en la BD."""
    return (User.query.count() or 0) == 0


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    show_bootstrap = is_first_run()

    if request.method == "GET":
        return render_template("login.html", show_bootstrap=show_bootstrap)

    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    user = User.query.filter_by(username=username, is_active=True).first()
    if not user or not user.check_password(password):
        return render_template(
            "login.html",
            error="Credenciales inválidas",
            show_bootstrap=show_bootstrap
        )

    login_user(user)
    return redirect(request.args.get("next") or url_for("pos.pos_ui"))


@auth_bp.route("/bootstrap-admin", methods=["POST"])
def bootstrap_admin():
    # Solo se permite si es primer inicio (BD vacía)
    if not is_first_run():
        return render_template(
            "login.html",
            show_bootstrap=False,
            bootstrap_error="Ya existe un usuario en el sistema. No se puede crear otro admin inicial."
        )

    username = (request.form.get("username") or "").strip()
    email = (request.form.get("email") or "").strip()
    password = request.form.get("password") or ""

    if not username:
        return render_template("login.html", show_bootstrap=True, bootstrap_error="Debes ingresar un usuario admin.")
    if len(password) < 4:
        return render_template("login.html", show_bootstrap=True, bootstrap_error="La contraseña debe tener al menos 4 caracteres.")

    u = User(
        username=username,
        email=email or None,
        role="admin",
        is_active=True
    )
    u.set_password(password)

    try:
        db.session.add(u)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return render_template(
            "login.html",
            show_bootstrap=True,
            bootstrap_error=f"Error creando admin: {e}"
        )

    return render_template(
        "login.html",
        show_bootstrap=False,
        bootstrap_ok="✅ Admin creado. Ahora inicia sesión."
    )


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))