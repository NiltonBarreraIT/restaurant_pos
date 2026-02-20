from flask import Blueprint, render_template, jsonify, request
from flask_login import login_required
from sqlalchemy import func
from app.extensions import db

cocina_bp = Blueprint("cocina", __name__)  # sin url_prefix


# =========================
# Vista principal
# =========================
@cocina_bp.route("/")
@login_required
def panel():
    return render_template("cocina.html")


def _ui_status_from_db(db_status: str) -> str:
    """Mapea status de BD a status para el frontend."""
    s = (db_status or "").strip().lower()
    if s == "prep":
        return "EN_PREPARACION"
    if s == "ready":
        return "LISTO"
    if s == "closed":
        return "ENTREGADO"
    if s == "cancelled":
        return "ANULADO"
    return (db_status or "").upper()


def _db_status_from_ui(ui_status: str) -> str:
    """Mapea status del frontend a status de BD."""
    s = (ui_status or "").strip().upper()
    mapping = {
        "EN_PREPARACION": "prep",
        "LISTO": "ready",         # opcional (si no lo usas, puedes dejarlo como prep)
        "ENTREGADO": "closed",
        "ANULADO": "cancelled",
    }
    return mapping.get(s, "")


# ============================================================
# Helper: caja abierta actual
# ============================================================
def _get_caja_abierta():
    from app.models import CashRegister
    caja = (
        CashRegister.query
        .filter(func.lower(CashRegister.status) == "open")  # ⚠️ si tu campo no se llama status, cámbialo aquí
        .order_by(CashRegister.id.desc())
        .first()
    )
    return caja


# =========================
# API: pedidos activos
# =========================
@cocina_bp.route("/api/pedidos", methods=["GET"])
@login_required
def pedidos_activos():
    from app.models import Order, OrderItem, Product

    # 1) Buscar la caja ABIERTA (última)
    caja = _get_caja_abierta()
    if not caja:
        return jsonify({"ok": True, "pedidos": [], "warning": "No hay caja abierta"}), 200

    # 2) Pedidos activos SOLO de esa caja (cocina)
    orders = (
        Order.query
        .filter(Order.cash_register_id == caja.id)
        .filter(func.lower(Order.status).in_(["prep"]))  # SOLO EN_PREPARACION
        .order_by(Order.created_at.asc())
        .all()
    )

    if not orders:
        return jsonify({"ok": True, "pedidos": []})

    order_ids = [o.id for o in orders]

    # 3) Items + productos
    rows = (
        db.session.query(OrderItem, Product)
        .join(Product, Product.id == OrderItem.product_id)
        .filter(OrderItem.order_id.in_(order_ids))
        .all()
    )

    items_by_order = {}
    for oi, prod in rows:
        items_by_order.setdefault(oi.order_id, []).append({
            "producto": getattr(prod, "name", "") or "",
            "qty": int(getattr(oi, "qty", 1) or 1),
        })

    data = []
    for o in orders:
        data.append({
            "id": o.id,
            "numero": getattr(o, "number_in_register", None) or o.id,
            "cliente": getattr(o, "reference_name", "") or "",
            "estado": _ui_status_from_db(getattr(o, "status", "")),
            "hora": (o.created_at.strftime("%H:%M") if getattr(o, "created_at", None) else ""),
            "pago": "",
            "total": 0,
            "items": items_by_order.get(o.id, [])
        })

    return jsonify({"ok": True, "pedidos": data})


# =========================
# API: resumen producción (SOLO EN_PREPARACION)
# =========================
@cocina_bp.route("/api/resumen", methods=["GET"])
@login_required
def resumen_produccion():
    """
    Devuelve un resumen de productos/cantidades SOLO de pedidos EN_PREPARACION (status='prep')
    y SOLO de la caja abierta actual.
    """
    from app.models import Order, OrderItem, Product
    from sqlalchemy.orm.attributes import InstrumentedAttribute

    def pick_attr(model, candidates):
        """Devuelve el primer atributo SQLAlchemy válido que exista en model (columna)."""
        for name in candidates:
            attr = getattr(model, name, None)
            if isinstance(attr, InstrumentedAttribute):
                return attr, name
        return None, None

    caja = _get_caja_abierta()
    if not caja:
        return jsonify({"ok": True, "items": [], "total_unidades": 0, "warning": "No hay caja abierta"}), 200

    # IDs de pedidos en preparación
    order_ids = (
        db.session.query(Order.id)
        .filter(Order.cash_register_id == caja.id)
        .filter(func.lower(Order.status) == "prep")
        .all()
    )
    order_ids = [x[0] for x in order_ids]

    if not order_ids:
        return jsonify({"ok": True, "items": [], "total_unidades": 0})

    # Detectar columnas reales en tu BD
    qty_col, qty_name = pick_attr(OrderItem, ["qty", "quantity", "cantidad", "cant"])
    prod_name_col, prod_name_name = pick_attr(Product, ["name", "nombre", "producto"])

    if qty_col is None:
        return jsonify({
            "ok": False,
            "error": "No encontré columna de cantidad en OrderItem (probé: qty/quantity/cantidad/cant)"
        }), 500

    if prod_name_col is None:
        return jsonify({
            "ok": False,
            "error": "No encontré columna de nombre en Product (probé: name/nombre/producto)"
        }), 500

    # Sumatoria por producto
    resumen = (
        db.session.query(
            prod_name_col.label("producto"),
            func.coalesce(func.sum(qty_col), 0).label("qty")
        )
        .join(Product, Product.id == OrderItem.product_id)
        .filter(OrderItem.order_id.in_(order_ids))
        .group_by(prod_name_col)
        .order_by(func.sum(qty_col).desc())
        .all()
    )

    items = [{"producto": r.producto or "", "qty": int(r.qty or 0)} for r in resumen]
    total_unidades = sum(i["qty"] for i in items)

    return jsonify({"ok": True, "items": items, "total_unidades": total_unidades})


# =========================
# API: cambiar estado
# =========================
@cocina_bp.route("/api/pedidos/<int:pedido_id>/estado", methods=["POST"])
@login_required
def cambiar_estado(pedido_id):
    from app.models import Order

    body = request.get_json(silent=True) or {}
    ui_estado = (body.get("estado") or "").strip().upper()

    UI_VALIDOS = ["EN_PREPARACION", "LISTO", "ENTREGADO", "ANULADO"]
    if ui_estado not in UI_VALIDOS:
        return jsonify({"ok": False, "error": "Estado inválido"}), 400

    new_db_status = _db_status_from_ui(ui_estado)
    if not new_db_status:
        return jsonify({"ok": False, "error": "No se pudo mapear estado"}), 400

    pedido = Order.query.get_or_404(pedido_id)
    pedido.status = new_db_status

    db.session.commit()
    return jsonify({"ok": True})