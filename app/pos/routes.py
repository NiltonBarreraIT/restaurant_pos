from decimal import Decimal
from flask import request, jsonify, render_template
from flask_login import login_required, current_user
from ..extensions import db
from ..models import Product, Order, OrderItem, Payment, OrderStatus, PaymentMethod
from ..utils import require_roles
from . import pos_bp

@pos_bp.get("/products")
@login_required
def list_products():
    products = Product.query.filter_by(active=True).order_by(Product.category.asc(), Product.name.asc()).all()
    return jsonify([{
        "id": p.id,
        "name": p.name,
        "category": p.category,
        "price": float(p.price),
    } for p in products])

@pos_bp.post("/orders")
@login_required
@require_roles("admin", "cashier")
def create_order():
    """
    Cobramos ANTES.
    Payload:
    {
      "reference_name": "Juan",
      "notes": "",
      "items": [{"product_id": 1, "qty": 2, "notes": ""}],
      "payment": {"method": "cash"|"transfer", "amount": 4500, "reference": "1234"}
    }
    """
    data = request.get_json(force=True)

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

    # Crear Order
    order = Order(
        reference_name=reference_name,
        status=OrderStatus.PREP.value,
        created_by_id=current_user.id,
        notes=(data.get("notes") or "").strip() or None
    )

    # Items (snapshot de nombre y precio)
    total = Decimal("0.00")
    for it in items_in:
        pid = it.get("product_id")
        qty = int(it.get("qty") or 0)
        if not pid or qty <= 0:
            return jsonify({"ok": False, "error": "Cada item requiere product_id y qty > 0"}), 400

        p = Product.query.get(pid)
        if not p or not p.active:
            return jsonify({"ok": False, "error": f"Producto inválido: {pid}"}), 400

        unit_price = Decimal(str(p.price))
        oi = OrderItem(
            product_id=p.id,
            product_name=p.name,
            unit_price=unit_price,
            quantity=qty,
            notes=(it.get("notes") or "").strip() or None
        )
        order.items.append(oi)
        total += unit_price * qty

    # Pago: por defecto debe coincidir con total (podemos permitir exactitud/ajuste luego)
    amount = Decimal(str(pay.get("amount") or "0"))
    if amount != total:
        return jsonify({"ok": False, "error": f"Monto de pago no coincide con total. total={float(total)}"}), 400

    payment = Payment(
        method=method,
        amount=amount,
        reference=(pay.get("reference") or "").strip() or None
    )
    order.payments.append(payment)

    db.session.add(order)
    db.session.commit()

    return jsonify({"ok": True, "order_id": order.id, "total": float(total)}), 201

@pos_bp.get("/kitchen/queue")
@login_required
@require_roles("admin", "kitchen", "cashier")
def kitchen_queue():
    orders = Order.query.filter(Order.status.in_([OrderStatus.PREP.value, OrderStatus.READY.value])) \
        .order_by(Order.created_at.asc()).all()

    out = []
    for o in orders:
        out.append({
            "id": o.id,
            "reference_name": o.reference_name,
            "status": o.status,
            "created_at": o.created_at.isoformat(),
            "items": [{
                "name": i.product_name,
                "qty": i.quantity,
                "notes": i.notes
            } for i in o.items]
        })
    return jsonify(out)

@pos_bp.post("/orders/<int:order_id>/status")
@login_required
@require_roles("admin", "kitchen", "cashier")
def set_order_status(order_id: int):
    data = request.get_json(force=True)
    status = (data.get("status") or "").strip()

    if status not in (OrderStatus.PREP.value, OrderStatus.READY.value, OrderStatus.DELIVERED.value, OrderStatus.CANCELLED.value):
        return jsonify({"ok": False, "error": "status inválido"}), 400

    o = Order.query.get_or_404(order_id)
    o.status = status
    db.session.commit()
    return jsonify({"ok": True})

@pos_bp.get("/reports/daily")
@login_required
@require_roles("admin", "cashier")
def report_daily():
    """
    Reporte simple por fecha:
    /pos/reports/daily?date=2026-01-05
    """
    from datetime import datetime, timedelta

    date_str = (request.args.get("date") or "").strip()
    if not date_str:
        return jsonify({"ok": False, "error": "date es obligatorio (YYYY-MM-DD)"}), 400

    day = datetime.fromisoformat(date_str)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)

    orders = Order.query.filter(Order.created_at >= start, Order.created_at < end, Order.status != OrderStatus.CANCELLED.value).all()

    total_cash = Decimal("0")
    total_transfer = Decimal("0")
    total_orders = len(orders)
    total_sales = Decimal("0")

    products_counter = {}

    for o in orders:
        for pay in o.payments:
            if pay.method == PaymentMethod.CASH.value:
                total_cash += Decimal(str(pay.amount))
            elif pay.method == PaymentMethod.TRANSFER.value:
                total_transfer += Decimal(str(pay.amount))
            total_sales += Decimal(str(pay.amount))

        for it in o.items:
            products_counter[it.product_name] = products_counter.get(it.product_name, 0) + int(it.quantity)

    top_products = sorted(products_counter.items(), key=lambda x: x[1], reverse=True)

    return jsonify({
        "ok": True,
        "date": date_str,
        "orders": total_orders,
        "cash": float(total_cash),
        "transfer": float(total_transfer),
        "total": float(total_sales),
        "top_products": [{"name": n, "qty": q} for n, q in top_products[:20]]
    })

@pos_bp.get("/ui")
@login_required
def pos_ui():
    return render_template("pos.html")

@pos_bp.get("/orders/history")
@login_required
def orders_history():
    print("HISTORY CALLED")
    limit = request.args.get("limit", 20, type=int)

    orders = (
        Order.query
        .order_by(Order.created_at.desc())
        .limit(limit)
        .all()
    )

    return jsonify([
    {
        "id": o.id,
        "reference_name": o.reference_name,
        "total": float(sum(i.unit_price * i.quantity for i in o.items)),
        "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
        "status": o.status,  # 👈 CLAVE
    }
    for o in orders
])

@pos_bp.get("/orders/<int:order_id>")
@login_required
def order_detail(order_id):
    order = Order.query.get_or_404(order_id)

    return jsonify({
        "id": order.id,
        "reference_name": order.reference_name,
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M"),
        "items": [
            {
                "name": i.product.name,
                "qty": i.quantity,
                "price": float(i.unit_price),
                "subtotal": float(i.unit_price * i.quantity)
            }
            for i in order.items
        ],
        "total": float(sum(i.unit_price * i.quantity for i in order.items))
    })

@pos_bp.post("/orders/<int:order_id>/cancel")
@login_required
def cancel_order(order_id):
    order = Order.query.get_or_404(order_id)

    # 🔁 Si ya está cancelado, no es error
    if order.status == "CANCELLED":
        return jsonify({
            "status": "already_cancelled",
            "message": "Pedido ya estaba anulado"
        }), 200

    # ✅ Anular pedido
    order.status = "CANCELLED"
    db.session.commit()

    return jsonify({
        "status": "cancelled",
        "message": "Pedido anulado correctamente"
    }), 200

@pos_bp.get("/receipt/<int:order_id>")
def receipt(order_id):
    order = Order.query.get_or_404(order_id)
    total = sum(i.unit_price * i.quantity for i in order.items)

    return render_template(
        "receipt.html",
        order=order,
        total=total
    )
