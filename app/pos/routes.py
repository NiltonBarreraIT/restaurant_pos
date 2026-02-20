from decimal import Decimal
from datetime import datetime

from flask import request, jsonify, render_template
from flask_login import login_required, current_user

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Order
from app.utils import require_roles
from . import pos_bp


# ======================================================
# CAJA
# ======================================================
def get_open_cash_register():
    from app.models import CashRegister, CashRegisterStatus
    return (
        CashRegister.query
        .filter(CashRegister.status == CashRegisterStatus.OPEN.value)
        .order_by(CashRegister.opened_at.desc())
        .first()
    )


@pos_bp.get("/cash/status")
@login_required
@require_roles("admin", "cashier")
def cash_status():
    from app.models import CashRegister

    cr = CashRegister.query.order_by(CashRegister.id.desc()).first()

    if not cr:
        return jsonify({"ok": True, "open": False, "cash_register": None})

    is_open = (cr.status or "").lower() == "open"

    return jsonify({
        "ok": True,
        "open": is_open,
        "cash_register": {
            "id": cr.id,
            "status": cr.status,
            "opened_at": cr.opened_at.strftime("%Y-%m-%d %H:%M") if cr.opened_at else None,
            "closed_at": cr.closed_at.strftime("%Y-%m-%d %H:%M") if cr.closed_at else None,
            "opening_amount": float(cr.opening_amount or 0),
            "opened_by_id": cr.opened_by_id
        }
    })


@pos_bp.post("/cash/open")
@login_required
@require_roles("admin", "cashier")
def cash_open():
    from app.models import CashRegister, CashRegisterStatus

    data = request.get_json(force=True) or {}
    opening_amount = Decimal(str(data.get("opening_amount") or "0"))
    notes = (data.get("notes") or "").strip() or None

    if get_open_cash_register():
        return jsonify({"ok": False, "error": "Ya existe una caja abierta"}), 400

    cr = CashRegister(
        status=CashRegisterStatus.OPEN.value,  # ✅ string
        opened_at=datetime.utcnow(),
        opened_by_id=current_user.id,
        opening_amount=opening_amount,
        notes=notes
    )

    db.session.add(cr)
    db.session.commit()

    return jsonify({"ok": True, "cash_register_id": cr.id}), 201


@pos_bp.post("/cash/close")
@login_required
@require_roles("admin", "cashier")
def cash_close():
    from app.models import (
        OrderStatus,
        PaymentMethod,
        CashRegisterStatus
    )

    data = request.get_json(force=True) or {}
    closing_amount = Decimal(str(data.get("closing_amount") or "0"))

    cr = get_open_cash_register()
    if not cr:
        return jsonify({"ok": False, "error": "No hay caja abierta"}), 400

    orders_q = Order.query.filter_by(cash_register_id=cr.id)

    # ✅ CERRAR pedidos pendientes de ESTA caja al cerrar caja
    # (prep/ready -> closed)
    pendientes = orders_q.filter(Order.status.in_([
        OrderStatus.PREP.value,
        OrderStatus.READY.value
    ]))

    # Update masivo
    pendientes.update(
        {Order.status: OrderStatus.CLOSED.value},
        synchronize_session=False
    )

    # Para resumen de caja: consideramos ventas todas menos canceladas
    orders_ok = orders_q.filter(Order.status != OrderStatus.CANCELLED.value).all()
    orders_cancelled = orders_q.filter(Order.status == OrderStatus.CANCELLED.value).count()

    total_cash = Decimal("0")
    total_transfer = Decimal("0")
    total_sales = Decimal("0")

    for o in orders_ok:
        for pay in o.payments:
            amt = Decimal(str(pay.amount))
            if pay.method == PaymentMethod.CASH.value:
                total_cash += amt
            elif pay.method == PaymentMethod.TRANSFER.value:
                total_transfer += amt
            total_sales += amt

    cr.status = CashRegisterStatus.CLOSED.value  # ✅ string
    cr.closed_at = datetime.utcnow()
    cr.closed_by_id = current_user.id
    cr.closing_amount = closing_amount
    cr.total_cash = total_cash
    cr.total_transfer = total_transfer
    cr.total_sales = total_sales
    cr.total_orders = len(orders_ok)
    cr.total_cancelled = orders_cancelled

    db.session.commit()
    return jsonify({"ok": True})


# ======================================================
# PRODUCTOS
# ======================================================
@pos_bp.get("/products")
@login_required
def list_products():
    from app.models import Product

    products = (
        Product.query
        .filter_by(active=True)
        .order_by(Product.category.asc(), Product.name.asc())
        .all()
    )

    return jsonify([
        {"id": p.id, "name": p.name, "category": p.category, "price": float(p.price)}
        for p in products
    ])


# ======================================================
# CREAR PEDIDO
# ======================================================
@pos_bp.post("/orders")
@login_required
@require_roles("admin", "cashier")
def create_order():
    from app.models import (
        Product,
        OrderItem,
        Payment,
        OrderStatus,
        PaymentMethod
    )

    data = request.get_json(force=True) or {}

    reference_name = (data.get("reference_name") or "").strip()
    if not reference_name:
        return jsonify({"ok": False, "error": "reference_name es obligatorio"}), 400

    items_in = data.get("items") or []
    if not items_in:
        return jsonify({"ok": False, "error": "items es obligatorio"}), 400

    pay = data.get("payment") or {}
    method = (pay.get("method") or "").strip()
    if method not in (PaymentMethod.CASH.value, PaymentMethod.TRANSFER.value):
        return jsonify({"ok": False, "error": "payment.method inválido"}), 400

    cr = get_open_cash_register()
    if not cr:
        return jsonify({"ok": False, "error": "Caja cerrada"}), 400

    # ------------------------------------------------------
    # ✅ ASIGNAR CORRELATIVO POR CAJA (number_in_register)
    # Maneja posible colisión del UNIQUE con reintento.
    # ------------------------------------------------------
    tries = 0
    while True:
        tries += 1

        last_num = (
            db.session.query(func.max(Order.number_in_register))
            .filter(Order.cash_register_id == cr.id)
            .scalar()
        )
        next_num = int(last_num or 0) + 1

        order = Order(
            reference_name=reference_name,
            status=OrderStatus.PREP.value,
            created_by_id=current_user.id,
            cash_register_id=cr.id,
            number_in_register=next_num
        )

        total = Decimal("0.00")

        for it in items_in:
            p = Product.query.get(it.get("product_id"))
            qty = int(it.get("qty") or 0)

            if not p or qty <= 0:
                return jsonify({"ok": False, "error": "Producto inválido"}), 400

            unit_price = Decimal(str(p.price))

            order.items.append(
                OrderItem(
                    product_id=p.id,
                    product_name=p.name,
                    unit_price=unit_price,
                    quantity=qty
                )
            )

            total += unit_price * qty

        amount = Decimal(str(pay.get("amount") or "0"))
        if amount != total:
            return jsonify({"ok": False, "error": "Monto incorrecto"}), 400

        order.payments.append(Payment(method=method, amount=amount))

        db.session.add(order)

        try:
            db.session.commit()
            return jsonify({
                "ok": True,
                "order_id": order.id,
                "order_number": order.number_in_register,
                "cash_register_id": cr.id
            })
        except IntegrityError:
            db.session.rollback()
            if tries >= 2:
                return jsonify({"ok": False, "error": "No se pudo asignar correlativo, reintenta"}), 409
            # reintenta calculando el next_num nuevamente


@pos_bp.get("/orders/history")
@login_required
def orders_history():
    """
    Por defecto muestra SOLO pedidos de la caja abierta (si existe),
    para que al abrir nueva caja el historial quede "en blanco".
    Si quieres ver todo, usa ?all=1
    """
    limit = int(request.args.get("limit", 50))
    show_all = (request.args.get("all") or "").strip() == "1"

    q = Order.query

    if not show_all:
        cr = get_open_cash_register()
        if cr:
            q = q.filter(Order.cash_register_id == cr.id)
        else:
            return jsonify([])

    orders = q.order_by(Order.id.desc()).limit(limit).all()

    out = []
    for o in orders:
        out.append({
            "id": o.id,
            "number_in_register": int(o.number_in_register or 0),
            "cash_register_id": o.cash_register_id,
            "created_at": o.created_at.strftime("%Y-%m-%d %H:%M") if o.created_at else "",
            "status": str(o.status or ""),
            "total": float(o.total_amount()),
            "items": [
                {"name": it.product_name, "qty": int(it.quantity)}
                for it in (o.items or [])
            ]
        })

    return jsonify(out)


@pos_bp.get("/orders/<int:order_id>")
@login_required
def get_order_detail(order_id):
    order = Order.query.get_or_404(order_id)

    return jsonify({
        "id": order.id,
        "number_in_register": int(order.number_in_register or 0),
        "cash_register_id": order.cash_register_id,
        "reference_name": order.reference_name,
        "status": order.status,
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M") if order.created_at else "",
        "total": float(order.total_amount()),
        "items": [
            {
                "name": item.product_name,
                "quantity": int(item.quantity),
                "unit_price": float(item.unit_price),
                "subtotal": float(Decimal(str(item.unit_price)) * Decimal(str(item.quantity)))
            }
            for item in (order.items or [])
        ]
    })


@pos_bp.post("/orders/<int:order_id>/cancel")
@login_required
@require_roles("admin", "cashier")
def cancel_order(order_id):
    from app.models import OrderStatus

    cr = get_open_cash_register()
    if not cr:
        return jsonify({"ok": False, "error": "No hay caja abierta"}), 400

    order = Order.query.get_or_404(order_id)

    # Solo permitir anular pedidos de la caja actualmente abierta
    if order.cash_register_id != cr.id:
        return jsonify({"ok": False, "error": "Solo puedes anular pedidos de la caja abierta"}), 400

    st = (order.status or "").lower()
    if st == OrderStatus.CANCELLED.value:
        return jsonify({"ok": True, "status": order.status, "message": "Ya estaba anulado"})

    # Bloquear anulación cuando ya fue entregado/cerrado:
    if st in (OrderStatus.DELIVERED.value, OrderStatus.CLOSED.value):
        return jsonify({"ok": False, "error": "No puedes anular un pedido entregado/cerrado"}), 400

    reason = (request.get_json(silent=True) or {}).get("reason")
    if reason:
        reason = str(reason).strip()
        if reason:
            prev = (order.notes or "").strip()
            order.notes = (prev + "\n" if prev else "") + f"[ANULADO] {reason}"

    order.status = OrderStatus.CANCELLED.value
    db.session.commit()

    return jsonify({"ok": True, "status": order.status})


@pos_bp.get("/receipt/<int:order_id>")
@login_required
def receipt(order_id):
    order = Order.query.get_or_404(order_id)
    total = order.total_amount()
    return render_template("receipt.html", order=order, total=total)


@pos_bp.get("/q/order/<int:order_id>")
def qr_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("qr_status.html", order=order)


# ======================================================
# UI
# ======================================================
@pos_bp.get("/ui")
@login_required
def pos_ui():
    return render_template("pos.html")