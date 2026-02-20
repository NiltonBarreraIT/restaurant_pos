from flask import request, jsonify
from flask_login import login_required

from app.extensions import db
from app.utils import require_roles
from . import admin_bp


@admin_bp.post("/products")
@login_required
@require_roles("admin")
def create_product():
    from app.models import Product  # ✅ import local

    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name obligatorio"}), 400

    p = Product(
        sku=(data.get("sku") or "").strip() or None,
        name=name,
        category=(data.get("category") or "").strip() or None,
        price=data.get("price") or 0,
        active=True
    )
    db.session.add(p)
    db.session.commit()
    return jsonify({"ok": True, "id": p.id}), 201


@admin_bp.post("/users")
@login_required
@require_roles("admin")
def create_user():
    from app.models import User, Role  # ✅ import local

    data = request.get_json(force=True)
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    role = (data.get("role") or Role.CASHIER.value).strip()

    if not username or not password:
        return jsonify({"ok": False, "error": "username y password obligatorios"}), 400
    if role not in (Role.ADMIN.value, Role.CASHIER.value, Role.KITCHEN.value):
        return jsonify({"ok": False, "error": "role inválido"}), 400

    if User.query.filter_by(username=username).first():
        return jsonify({"ok": False, "error": "username ya existe"}), 409

    u = User(
        username=username,
        email=(data.get("email") or "").strip() or None,
        role=role,
        is_active=True
    )
    u.set_password(password)

    db.session.add(u)
    db.session.commit()
    return jsonify({"ok": True, "id": u.id}), 201
