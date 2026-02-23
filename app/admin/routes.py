from flask import request, jsonify, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from decimal import Decimal
from datetime import datetime, timedelta

from sqlalchemy import or_, func

from app.extensions import db
from app.utils import require_roles
from . import admin_bp


# =========================================================
# SETTINGS (key/value)
# =========================================================
def get_setting(key: str, default: str = "") -> str:
    from app.models import AppSetting  # import local para evitar ciclos
    s = AppSetting.query.get(key)
    return (s.value if s and s.value is not None else default)


def set_setting(key: str, value: str) -> None:
    from app.models import AppSetting  # import local
    s = AppSetting.query.get(key)
    if not s:
        s = AppSetting(key=key, value=value)
        db.session.add(s)
    else:
        s.value = value
    db.session.commit()


def _dec(v, default="0"):
    try:
        return Decimal(str(v if v is not None else default))
    except Exception:
        return Decimal(default)


# =========================================================
# ADMIN UI (HTML)
# =========================================================
@admin_bp.get("/")
@login_required
@require_roles("admin")
def admin_dashboard():
    business_name = get_setting("business_name", "POS Barra")
    receipt_footer = get_setting("receipt_footer", "Gracias por su compra")
    receipt_autoprint = get_setting("receipt_autoprint", "1")
    qr_size = get_setting("qr_size", "120")

    return render_template(
        "admin/dashboard.html",
        business_name=business_name,
        receipt_footer=receipt_footer,
        receipt_autoprint=receipt_autoprint,
        qr_size=qr_size,
    )


@admin_bp.route("/settings", methods=["GET", "POST"])
@login_required
@require_roles("admin")
def admin_settings():
    if request.method == "POST":
        business_name = (request.form.get("business_name") or "").strip() or "POS Barra"
        receipt_footer = (request.form.get("receipt_footer") or "").strip()
        receipt_autoprint = "1" if request.form.get("receipt_autoprint") == "on" else "0"
        qr_size = (request.form.get("qr_size") or "120").strip() or "120"

        # Validación simple de qr_size
        try:
            n = int(qr_size)
            if n < 80:
                n = 80
            if n > 400:
                n = 400
            qr_size = str(n)
        except Exception:
            qr_size = "120"

        set_setting("business_name", business_name)
        set_setting("receipt_footer", receipt_footer)
        set_setting("receipt_autoprint", receipt_autoprint)
        set_setting("qr_size", qr_size)

        flash("✅ Configuración guardada", "success")
        return redirect(url_for("admin.admin_settings"))

    # GET
    data = {
        "business_name": get_setting("business_name", "POS Barra"),
        "receipt_footer": get_setting("receipt_footer", "Gracias por su compra"),
        "receipt_autoprint": get_setting("receipt_autoprint", "1"),
        "qr_size": get_setting("qr_size", "120"),
    }
    return render_template("admin/settings.html", **data)


@admin_bp.get("/cash")
@login_required
@require_roles("admin")
def admin_cash():
    business_name = get_setting("business_name", "POS Barra")
    return render_template("admin/cash.html", business_name=business_name)


# =========================================================
# ADMIN UI - PRODUCTOS (HTML)
# =========================================================
@admin_bp.get("/products/ui")
@login_required
@require_roles("admin")
def products_ui():
    business_name = get_setting("business_name", "POS Barra")
    return render_template("admin/products.html", business_name=business_name)


# =========================================================
# ADMIN UI - COMPRAS (HTML)
# =========================================================
@admin_bp.get("/purchases/ui")
@login_required
@require_roles("admin")
def purchases_ui():
    business_name = get_setting("business_name", "POS Barra")
    return render_template("admin/purchases.html", business_name=business_name)


# =========================================================
# ADMIN API (JSON) - PRODUCTOS (CREAR)
# =========================================================
@admin_bp.post("/products")
@login_required
@require_roles("admin")
def create_product():
    from app.models import Product  # import local

    data = request.get_json(force=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "name obligatorio"}), 400

    # precio venta
    try:
        price = _dec(data.get("price"), "0")
    except Exception:
        return jsonify({"ok": False, "error": "price inválido"}), 400

    # NUEVOS CAMPOS (no rompen si no existen)
    product_type = (data.get("product_type") or "sale").strip().lower()
    if product_type not in ("sale", "supply"):
        product_type = "sale"

    show_in_pos = bool(data.get("show_in_pos", True))
    # Si es insumo, forzamos a NO mostrar en POS (para evitar errores humanos)
    if product_type == "supply":
        show_in_pos = False

    unit = (data.get("unit") or "UN").strip().upper()[:10] or "UN"

    # inventario (si ya agregaste columnas al modelo Product)
    track_stock = bool(data.get("track_stock", True))
if product_type == "supply":
    track_stock = True
    stock_qty = _dec(data.get("stock_qty"), "0")
    stock_min_qty = _dec(data.get("stock_min_qty"), "0")
    avg_cost = _dec(data.get("avg_cost"), "0")

    p = Product(
        sku=(data.get("sku") or "").strip() or None,
        name=name,
        category=(data.get("category") or "").strip() or None,
        price=price,
        active=True
    )

    # ✅ setea si existen en el modelo
    if hasattr(p, "product_type"):
        p.product_type = product_type
    if hasattr(p, "show_in_pos"):
        p.show_in_pos = show_in_pos
    if hasattr(p, "unit"):
        p.unit = unit

    if hasattr(p, "track_stock"):
        p.track_stock = track_stock
    if hasattr(p, "stock_qty"):
        p.stock_qty = stock_qty
    if hasattr(p, "stock_min_qty"):
        p.stock_min_qty = stock_min_qty
    if hasattr(p, "avg_cost"):
        p.avg_cost = avg_cost

    db.session.add(p)
    db.session.commit()
    return jsonify({"ok": True, "id": p.id}), 201


# =========================================================
# ADMIN API - PRODUCTOS (LISTAR / EDITAR)
# =========================================================
@admin_bp.get("/products")
@login_required
@require_roles("admin")
def list_products_admin():
    from app.models import Product

    q = (request.args.get("q") or "").strip()
    category = (request.args.get("category") or "").strip()
    active = (request.args.get("active") or "").strip()  # "1" / "0" / ""

    query = Product.query

    if q:
        like = f"%{q}%"
        query = query.filter(or_(Product.name.ilike(like), Product.sku.ilike(like)))

    if category:
        query = query.filter(Product.category == category)

    if active in ("0", "1"):
        query = query.filter(Product.active == (active == "1"))

    products = query.order_by(Product.category.asc(), Product.name.asc()).all()

    def _get(p, attr, default=None):
        return getattr(p, attr, default) if hasattr(p, attr) else default

    return jsonify({
        "ok": True,
        "items": [
            {
                "id": p.id,
                "sku": p.sku,
                "name": p.name,
                "category": p.category,
                "price": float(p.price or 0),
                "active": bool(p.active),

                # ✅ nuevos campos para UI (si no existen, default)
                "product_type": _get(p, "product_type", "sale"),
                "show_in_pos": bool(_get(p, "show_in_pos", True)),
                "unit": _get(p, "unit", "UN"),

                # inventario/costo (si existen)
                "track_stock": bool(_get(p, "track_stock", True)),
                "stock_qty": float(_get(p, "stock_qty", 0) or 0),
                "stock_min_qty": float(_get(p, "stock_min_qty", 0) or 0),
                "avg_cost": float(_get(p, "avg_cost", 0) or 0),
            }
            for p in products
        ]
    })


@admin_bp.put("/products/<int:product_id>")
@login_required
@require_roles("admin")
def update_product_admin(product_id):
    from app.models import Product

    p = Product.query.get_or_404(product_id)
    data = request.get_json(force=True) or {}

    if "sku" in data:
        p.sku = (data.get("sku") or "").strip() or None

    if "name" in data:
        name = (data.get("name") or "").strip()
        if not name:
            return jsonify({"ok": False, "error": "name obligatorio"}), 400
        p.name = name

    if "category" in data:
        p.category = (data.get("category") or "").strip() or None

    if "price" in data:
        try:
            p.price = _dec(data.get("price"), "0")
        except Exception:
            return jsonify({"ok": False, "error": "price inválido"}), 400

    if "active" in data:
        p.active = bool(data.get("active"))

    # ✅ nuevos campos: show_in_pos / product_type / unit (si existen)
    if hasattr(p, "product_type") and "product_type" in data:
    pt = (data.get("product_type") or "sale").strip().lower()
    if pt not in ("sale", "supply"):
        return jsonify({"ok": False, "error": "product_type inválido"}), 400

    p.product_type = pt
        # si es insumo, no debe aparecer en POS
        if hasattr(p, "show_in_pos"):
            if pt == "supply":
                p.show_in_pos = False

    if hasattr(p, "show_in_pos") and "show_in_pos" in data:
        # si el tipo es supply, forzamos false
        if hasattr(p, "product_type") and (getattr(p, "product_type", "sale") == "supply"):
            p.show_in_pos = False
        else:
            p.show_in_pos = bool(data.get("show_in_pos"))

    if hasattr(p, "unit") and "unit" in data:
        p.unit = (data.get("unit") or "UN").strip().upper()[:10] or "UN"

    # inventario / costo (si existen)
    if hasattr(p, "track_stock") and "track_stock" in data:
        p.track_stock = bool(data.get("track_stock"))

    if hasattr(p, "stock_qty") and "stock_qty" in data:
        p.stock_qty = _dec(data.get("stock_qty"), "0")

    if hasattr(p, "stock_min_qty") and "stock_min_qty" in data:
        p.stock_min_qty = _dec(data.get("stock_min_qty"), "0")

    if hasattr(p, "avg_cost") and "avg_cost" in data:
        p.avg_cost = _dec(data.get("avg_cost"), "0")

    db.session.commit()
    return jsonify({"ok": True})


@admin_bp.get("/products/categories")
@login_required
@require_roles("admin")
def list_product_categories_admin():
    from app.models import Product
    cats = (
        db.session.query(Product.category)
        .filter(Product.category.isnot(None))
        .distinct()
        .order_by(Product.category.asc())
        .all()
    )
    return jsonify({"ok": True, "items": [c[0] for c in cats if c[0]]})


# =========================================================
# ADMIN API - COMPRAS
# =========================================================
@admin_bp.get("/purchases")
@login_required
@require_roles("admin")
def list_purchases_admin():
    from app.models import Purchase

    q = (request.args.get("q") or "").strip()
    q_cr = (request.args.get("cash_register_id") or "").strip()
    q_paid = (request.args.get("paid") or "").strip()  # "1"/"0"/""
    limit = int(request.args.get("limit") or 100)

    query = Purchase.query

    if q:
        like = f"%{q}%"
        query = query.filter(or_(Purchase.supplier.ilike(like), Purchase.invoice_ref.ilike(like)))

    if q_cr:
        query = query.filter(Purchase.cash_register_id == int(q_cr))

    if q_paid in ("0", "1"):
        query = query.filter(Purchase.paid == (q_paid == "1"))

    items = query.order_by(Purchase.id.desc()).limit(limit).all()

    return jsonify({
        "ok": True,
        "items": [
            {
                "id": p.id,
                "created_at": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else None,
                "supplier": p.supplier,
                "invoice_ref": p.invoice_ref,
                "payment_method": p.payment_method,
                "paid": bool(p.paid),
                "cash_register_id": p.cash_register_id,
                "total_amount": float(p.total_amount or 0),
            }
            for p in items
        ]
    })


@admin_bp.get("/purchases/<int:purchase_id>")
@login_required
@require_roles("admin")
def get_purchase_admin(purchase_id):
    from app.models import Purchase

    p = Purchase.query.get_or_404(purchase_id)
    return jsonify({
        "ok": True,
        "purchase": {
            "id": p.id,
            "created_at": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else None,
            "supplier": p.supplier,
            "invoice_ref": p.invoice_ref,
            "payment_method": p.payment_method,
            "paid": bool(p.paid),
            "cash_register_id": p.cash_register_id,
            "total_amount": float(p.total_amount or 0),
            "notes": p.notes,
            "items": [
                {
                    "product_id": it.product_id,
                    "product_name": it.product_name,
                    "qty": float(it.qty or 0),
                    "unit_cost": float(it.unit_cost or 0),
                    "line_total": float(it.line_total or 0),
                }
                for it in (p.items or [])
            ]
        }
    })


@admin_bp.post("/purchases")
@login_required
@require_roles("admin")
def create_purchase_admin():
    """
    Crea compra (inversión):
      - suma stock
      - recalcula costo promedio
      - registra stock_moves tipo purchase
    """
    from app.models import Product, Purchase, PurchaseItem, StockMove, StockMoveType

    data = request.get_json(force=True) or {}
    supplier = (data.get("supplier") or "").strip() or None
    invoice_ref = (data.get("invoice_ref") or "").strip() or None
    payment_method = (data.get("payment_method") or "").strip().lower() or None
    paid = bool(data.get("paid", True))
    notes = (data.get("notes") or "").strip() or None

    cash_register_id = data.get("cash_register_id")
    try:
        cash_register_id = int(cash_register_id) if cash_register_id not in (None, "", "null") else None
    except Exception:
        cash_register_id = None

    items_in = data.get("items") or []
    if not items_in:
        return jsonify({"ok": False, "error": "items obligatorio"}), 400

    purchase = Purchase(
        supplier=supplier,
        invoice_ref=invoice_ref,
        payment_method=payment_method,
        paid=paid,
        notes=notes,
        cash_register_id=cash_register_id,
        created_by_id=getattr(current_user, "id", None),
        created_at=datetime.utcnow()
    )

    total = Decimal("0")

    for row in items_in:
        pid = row.get("product_id")
        qty = _dec(row.get("qty"), "0")
        unit_cost = _dec(row.get("unit_cost"), "0")
        if not pid or qty <= 0:
            return jsonify({"ok": False, "error": "Producto/cantidad inválida"}), 400
        if unit_cost < 0:
            return jsonify({"ok": False, "error": "Costo inválido"}), 400

        prod = Product.query.get(int(pid))
        if not prod:
            return jsonify({"ok": False, "error": f"Producto {pid} no existe"}), 400

        line_total = (qty * unit_cost)
        total += line_total

        purchase.items.append(PurchaseItem(
            product_id=prod.id,
            product_name=prod.name,
            qty=qty,
            unit_cost=unit_cost,
            line_total=line_total
        ))

        # aplica stock + costo promedio
        if hasattr(prod, "apply_purchase"):
            prod.apply_purchase(qty, unit_cost)
        else:
            # fallback por si aún no pegaste la función
            if hasattr(prod, "stock_qty"):
                prod.stock_qty = _dec(getattr(prod, "stock_qty", 0), "0") + qty

        # kardex
        db.session.add(StockMove(
            product_id=prod.id,
            move_type=StockMoveType.PURCHASE.value,
            qty_delta=qty,
            unit_cost=unit_cost,
            ref_table="purchases",
            ref_id=None,  # se setea luego si quieres, no es crítico
            cash_register_id=cash_register_id,
            created_by_id=getattr(current_user, "id", None),
            created_at=datetime.utcnow(),
        ))

    purchase.total_amount = total

    db.session.add(purchase)
    db.session.commit()

    return jsonify({"ok": True, "id": purchase.id})


# =========================================================
# ADMIN UI - USUARIOS (HTML)
# =========================================================
@admin_bp.get("/users/ui")
@login_required
@require_roles("admin")
def users_ui():
    business_name = get_setting("business_name", "POS Barra")
    return render_template("admin/users.html", business_name=business_name)


# =========================================================
# ADMIN API - CREAR USUARIO
# =========================================================
@admin_bp.post("/users")
@login_required
@require_roles("admin")
def create_user():
    from app.models import User, Role  # import local

    data = request.get_json(force=True) or {}
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


# =========================================================
# ADMIN API - LISTAR USUARIOS
# =========================================================
@admin_bp.get("/users")
@login_required
@require_roles("admin")
def list_users_admin():
    from app.models import User

    q = (request.args.get("q") or "").strip().lower()
    role = (request.args.get("role") or "").strip().lower()   # admin/cashier/kitchen
    active = (request.args.get("active") or "").strip()       # "1" / "0" / ""

    query = User.query

    if q:
        like = f"%{q}%"
        query = query.filter(
            db.or_(
                User.username.ilike(like),
                User.email.ilike(like)
            )
        )

    if role in ("admin", "cashier", "kitchen"):
        query = query.filter(User.role == role)

    if active in ("0", "1"):
        query = query.filter(User.is_active == (active == "1"))

    users = query.order_by(User.role.asc(), User.username.asc()).all()

    return jsonify({
        "ok": True,
        "items": [
            {
                "id": u.id,
                "username": u.username,
                "email": u.email,
                "role": u.role,
                "is_active": bool(u.is_active),
                "is_me": (u.id == current_user.id),
            }
            for u in users
        ]
    })


# =========================================================
# ADMIN API - ACTUALIZAR USUARIO
# =========================================================
@admin_bp.put("/users/<int:user_id>")
@login_required
@require_roles("admin")
def update_user_admin(user_id):
    from app.models import User, Role

    u = User.query.get_or_404(user_id)
    data = request.get_json(force=True) or {}

    if "email" in data:
        u.email = (data.get("email") or "").strip() or None

    if "role" in data:
        role = (data.get("role") or "").strip().lower()
        valid = (Role.ADMIN.value, Role.CASHIER.value, Role.KITCHEN.value)
        if role not in valid:
            return jsonify({"ok": False, "error": "role inválido"}), 400
        u.role = role

    if "is_active" in data:
        new_active = bool(data.get("is_active"))

        if not new_active:
            if u.id == current_user.id:
                return jsonify({"ok": False, "error": "No puedes desactivarte a ti mismo"}), 400

            if (u.role or "").lower() == Role.ADMIN.value:
                admins_activos = User.query.filter_by(role=Role.ADMIN.value, is_active=True).count()
                if admins_activos <= 1:
                    return jsonify({"ok": False, "error": "No puedes desactivar el último admin"}), 400

        u.is_active = new_active

    db.session.commit()
    return jsonify({"ok": True})


# =========================================================
# ADMIN API - RESET PASSWORD
# =========================================================
@admin_bp.post("/users/<int:user_id>/reset_password")
@login_required
@require_roles("admin")
def reset_password_admin(user_id):
    from app.models import User

    u = User.query.get_or_404(user_id)
    data = request.get_json(force=True) or {}
    new_password = data.get("password") or ""

    if len(new_password) < 4:
        return jsonify({"ok": False, "error": "Password muy corta (mínimo 4)"}), 400

    u.set_password(new_password)
    db.session.commit()

    return jsonify({"ok": True})


# =========================================================
# REPORTES PRO (HTML + API)
# =========================================================
def _parse_date(s: str):
    try:
        return datetime.strptime(s, "%Y-%m-%d")
    except Exception:
        return None


def _payment_label(pm: str) -> str:
    pm = (pm or "").lower().strip()
    if pm in ("cash", "efectivo"):
        return "Efectivo"
    if pm in ("transfer", "transferencia"):
        return "Transferencia"
    if pm in ("card", "tarjeta"):
        return "Tarjeta"
    if not pm:
        return "Sin método"
    return pm


@admin_bp.get("/reportes")
@login_required
@require_roles("admin")
def admin_reportes():
    business_name = get_setting("business_name", "POS Barra")
    return render_template("admin/reportes.html", business_name=business_name)


@admin_bp.get("/api/reportes")
@login_required
@require_roles("admin")
def admin_api_reportes():
    """
    Query params:
      from=YYYY-MM-DD
      to=YYYY-MM-DD
      payment_method=cash|transfer|...
      cash_register_id=#
      user_id=#
    """
    try:
        from app.models import Order, Payment, User, CashRegister, OrderStatus

        q_from = (request.args.get("from") or "").strip()
        q_to = (request.args.get("to") or "").strip()
        q_pm = (request.args.get("payment_method") or "").strip().lower()
        q_cr = (request.args.get("cash_register_id") or "").strip()
        q_user = (request.args.get("user_id") or "").strip()

        d_from = _parse_date(q_from) if q_from else None
        d_to = _parse_date(q_to) if q_to else None

        # default hoy
        if not d_from and not d_to:
            now = datetime.now()
            d_from = datetime(now.year, now.month, now.day)
            d_to = d_from

        if d_from and not d_to:
            d_to = d_from
        if d_to and not d_from:
            d_from = d_to

        start_dt = datetime(d_from.year, d_from.month, d_from.day)
        end_dt = datetime(d_to.year, d_to.month, d_to.day) + timedelta(days=1)

        q_orders = Order.query.filter(
            Order.status == OrderStatus.CLOSED.value,
            Order.created_at >= start_dt,
            Order.created_at < end_dt
        )

        if q_cr:
            q_orders = q_orders.filter(Order.cash_register_id == int(q_cr))

        if q_user:
            q_orders = q_orders.filter(Order.created_by_id == int(q_user))

        if q_pm:
            q_orders = q_orders.join(Payment).filter(func.lower(Payment.method) == q_pm)

        orders = q_orders.order_by(Order.created_at.desc()).all()

        total_sales = 0.0
        orders_count = len(orders)
        sales_by_day_map = {}
        sales_by_payment_map = {}
        top_products_map = {}
        user_totals = {}
        rows = []

        cash_regs = CashRegister.query.order_by(CashRegister.id.desc()).all()
        cash_map = {c.id: f"Caja #{c.id}" for c in cash_regs}
        users = User.query.order_by(User.username.asc()).all()
        user_map = {u.id: (u.username or f"User {u.id}") for u in users}

        for o in orders:
            order_total = float(o.total_amount() or 0)
            total_sales += order_total

            day_label = o.created_at.strftime("%Y-%m-%d") if o.created_at else "—"
            sales_by_day_map[day_label] = sales_by_day_map.get(day_label, 0.0) + order_total

            if o.created_by_id:
                uname = user_map.get(o.created_by_id, f"User {o.created_by_id}")
                user_totals[uname] = user_totals.get(uname, 0.0) + order_total

            if o.payments:
                for p in o.payments:
                    pm = (p.method or "").lower().strip()
                    if q_pm and pm != q_pm:
                        continue
                    sales_by_payment_map[pm] = sales_by_payment_map.get(pm, 0.0) + float(p.amount or 0)

            if o.items:
                for it in o.items:
                    pname = (it.product_name or "—")
                    qty = int(it.quantity or 0)
                    top_products_map[pname] = top_products_map.get(pname, 0) + qty

            pm_label = "Sin método"
            if o.payments and len(o.payments) == 1:
                pm_label = _payment_label(o.payments[0].method)
            elif o.payments and len(o.payments) > 1:
                pm_label = "Mixto"

            cr_label = cash_map.get(o.cash_register_id, f"Caja #{o.cash_register_id}")
            cr_label = f"{cr_label} · #{o.number_in_register}"

            rows.append({
                "date": o.created_at.strftime("%Y-%m-%d %H:%M") if o.created_at else "—",
                "cash_register": cr_label,
                "user": user_map.get(o.created_by_id, "-"),
                "payment_method": pm_label,
                "total": order_total,
            })

        avg_ticket = (total_sales / orders_count) if orders_count else 0.0

        top_user_name = "—"
        top_user_detail = "—"
        if user_totals:
            top_user_name = max(user_totals, key=user_totals.get)
            top_user_detail = f"{user_totals[top_user_name]:,.0f} CLP"

        sales_by_day = [{"label": k, "value": float(v)} for k, v in sorted(sales_by_day_map.items(), key=lambda x: x[0])]

        sales_by_payment = []
        for pm_key in ["cash", "transfer"]:
            if pm_key in sales_by_payment_map:
                sales_by_payment.append({"label": _payment_label(pm_key), "value": float(sales_by_payment_map[pm_key])})
        for pm_key, val in sales_by_payment_map.items():
            if pm_key in ("cash", "transfer"):
                continue
            sales_by_payment.append({"label": _payment_label(pm_key), "value": float(val)})

        top_products = sorted(
            [{"label": k, "value": int(v)} for k, v in top_products_map.items()],
            key=lambda x: x["value"],
            reverse=True
        )[:7]

        filters = {
            "cash_registers": [{"id": c.id, "name": f"Caja #{c.id}"} for c in cash_regs],
            "users": [{"id": u.id, "name": u.username or f"User {u.id}"} for u in users]
        }

        return jsonify({
            "ok": True,
            "kpis": {
                "total_sales": float(total_sales),
                "orders_count": int(orders_count),
                "avg_ticket": float(avg_ticket),
                "top_user": {"name": top_user_name, "detail": top_user_detail},
            },
            "series": {
                "sales_by_day": sales_by_day,
                "sales_by_payment": sales_by_payment,
                "top_products": top_products,
            },
            "rows": rows,
            "filters": filters,
        })

    except Exception as e:
        return jsonify({"ok": False, "message": f"Error reportes: {str(e)}"}), 500


@admin_bp.get("/reportes/export.xlsx")
@login_required
@require_roles("admin")
def admin_reportes_export_xlsx():
    return jsonify({"ok": False, "message": "Export Excel aún no implementado"}), 501