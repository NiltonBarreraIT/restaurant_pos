from decimal import Decimal
from datetime import datetime

from flask import request, jsonify, render_template, redirect, url_for
from flask_login import login_required, current_user

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Order
from app.utils import require_roles
from . import pos_bp


def _dec(v, default="0"):
    try:
        return Decimal(str(v if v is not None else default))
    except Exception:
        return Decimal(default)


# ======================================================
# SETTINGS (para receipt / branding)
# ======================================================
def get_setting(key: str, default: str = "") -> str:
    from app.models import AppSetting
    s = AppSetting.query.get(key)
    return (s.value if s and s.value is not None else default)


# ======================================================
# POS ROOT: /pos -> /pos/ui (evita 404)
# ======================================================
@pos_bp.get("/")
@login_required
def pos_root():
    return redirect(url_for("pos.pos_ui"))


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

    # ✅ primero: si hay una caja abierta, esa manda
    cr = get_open_cash_register()

    # si no hay abierta, muestra la última (para historial)
    if not cr:
        cr = CashRegister.query.order_by(CashRegister.id.desc()).first()

    if not cr:
        return jsonify({"ok": True, "open": False, "cash_register": None})

    is_open = (cr.status or "").strip().lower() == "open"

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
    from app.models import (
        CashRegister, CashRegisterStatus,
        Product, CashRegisterStockCount,
        StockMove, StockMoveType
    )

    data = request.get_json(force=True) or {}
    opening_amount = _dec(data.get("opening_amount"), "0")
    notes = (data.get("notes") or "").strip() or None

    # compat front viejo/nuevo
    counts_open = data.get("counts_open") or data.get("opening_counts") or []

    if get_open_cash_register():
        return jsonify({"ok": False, "error": "Ya existe una caja abierta"}), 400

    cr = CashRegister(
        status=CashRegisterStatus.OPEN.value,
        opened_at=datetime.utcnow(),
        opened_by_id=current_user.id,
        opening_amount=opening_amount,
        notes=notes
    )

    db.session.add(cr)
    db.session.flush()  # ya tenemos cr.id

    # ======================================================
    # ✅ Conteo inicial (open): guarda + sincroniza stock + genera kardex (ajuste)
    # ======================================================
    if counts_open:
        # limpia por si reintentas abrir (evita duplicados)
        CashRegisterStockCount.query.filter_by(
            cash_register_id=cr.id,
            count_type="open"
        ).delete(synchronize_session=False)

        # Tipo de movimiento ajuste (compat)
        adjust_enum = getattr(StockMoveType, "ADJUST", None)
        adjust_type_value = adjust_enum.value if adjust_enum is not None else "adjust"

        for row in counts_open:
            pid = row.get("product_id")
            qty_counted = _dec(row.get("qty"), "0")
            if not pid or qty_counted < 0:
                continue

            prod = Product.query.get(int(pid))
            if not prod:
                continue

            # solo track_stock
            if not bool(getattr(prod, "track_stock", True)):
                continue

            # debe tener stock_qty para sincronizar
            if not hasattr(prod, "stock_qty"):
                continue

            current_qty = _dec(getattr(prod, "stock_qty", 0), "0")
            delta = qty_counted - current_qty  # + entra, - sale

            # ✅ Actualiza stock real al conteo inicial
            prod.stock_qty = qty_counted

            # ✅ Registra conteo inicial
            db.session.add(CashRegisterStockCount(
                cash_register_id=cr.id,
                product_id=prod.id,
                count_type="open",
                qty=qty_counted,
                product_name=prod.name,
                unit=getattr(prod, "unit", None),
                created_by_id=current_user.id,
                created_at=datetime.utcnow()
            ))

            # ✅ Kardex SOLO si hay diferencia
            if delta != 0:
                unit_cost = _dec(getattr(prod, "avg_cost", 0), "0")

                db.session.add(StockMove(
                    product_id=prod.id,
                    move_type=adjust_type_value,
                    qty_delta=delta,
                    unit_cost=unit_cost,
                    ref_table="cash_registers",
                    ref_id=cr.id,
                    cash_register_id=cr.id,
                    created_by_id=current_user.id,
                    created_at=datetime.utcnow(),
                ))

    db.session.commit()
    return jsonify({"ok": True, "cash_register_id": cr.id}), 201


@pos_bp.post("/cash/close")
@login_required
@require_roles("admin", "cashier")
def cash_close():
    """
    Cierre PRO + Conteo final + Consumo manual (Opción B):
      - cierra pedidos pendientes
      - totales pagos
      - consumo manual de insumos (ej harina)
      - guarda conteo final (insumos + bebidas)
      - snapshot inventario
    """
    from sqlalchemy import func
    from app.models import (
        OrderStatus,
        PaymentMethod,
        CashRegisterStatus,
        Product,
        StockMove,
        StockMoveType,
        CashRegisterInventorySnapshot,
        Purchase,
        CashRegisterStockCount
    )

    data = request.get_json(force=True) or {}
    closing_amount = _dec(data.get("closing_amount"), "0")

    # consumptions: [{product_id, qty}] (solo insumos)
    consumptions = data.get("consumptions") or []

    # counts_close: [{product_id, qty}]
    # soporta compatibilidad con front viejo y nuevo
    counts_close = data.get("counts_close") or data.get("closing_counts") or []

    cr = get_open_cash_register()
    if not cr:
        return jsonify({"ok": False, "error": "No hay caja abierta"}), 400

    orders_q = Order.query.filter_by(cash_register_id=cr.id)

    pendientes = orders_q.filter(Order.status.in_([
        OrderStatus.PREP.value,
        OrderStatus.READY.value
    ]))

    pendientes.update(
        {Order.status: OrderStatus.CLOSED.value},
        synchronize_session=False
    )

    orders_ok = orders_q.filter(Order.status != OrderStatus.CANCELLED.value).all()
    orders_cancelled = orders_q.filter(Order.status == OrderStatus.CANCELLED.value).count()

    total_cash = Decimal("0")
    total_transfer = Decimal("0")
    total_sales = Decimal("0")

    for o in orders_ok:
        for pay in o.payments:
            amt = _dec(pay.amount, "0")
            if pay.method == PaymentMethod.CASH.value:
                total_cash += amt
            elif pay.method == PaymentMethod.TRANSFER.value:
                total_transfer += amt
            total_sales += amt

    # ===== COGS del turno (según SALE) =====
    cogs = Decimal("0")
    sale_moves = StockMove.query.filter(
        StockMove.cash_register_id == cr.id,
        StockMove.move_type == StockMoveType.SALE.value
    ).all()

    for mv in sale_moves:
        q = _dec(mv.qty_delta, "0")  # negativo
        uc = _dec(mv.unit_cost, "0")
        if q < 0:
            cogs += (q.copy_abs() * uc)

    # ===== compras ligadas a caja =====
    purchases_total = Decimal("0")
    purchases = Purchase.query.filter(Purchase.cash_register_id == cr.id).all()
    for p in purchases:
        purchases_total += _dec(p.total_amount, "0")

    # ======================================================
    # ✅ Consumo manual de insumos (harina, aceite, etc.)
    # ======================================================
    harina_stock_final = None
    harina_consumed_qty = Decimal("0")
    harina_consumed_cost = Decimal("0")

    if consumptions:
        for row in consumptions:
            pid = row.get("product_id")
            qty_used = _dec(row.get("qty"), "0")
            if not pid or qty_used <= 0:
                continue

            prod = Product.query.get(int(pid))
            if not prod:
                continue

            # Solo track_stock
            if not bool(getattr(prod, "track_stock", True)):
                continue

            # Solo insumos si existe product_type
            if hasattr(prod, "product_type") and (prod.product_type or "sale") != "supply":
                continue

            if not hasattr(prod, "stock_qty"):
                continue

            current_qty = _dec(getattr(prod, "stock_qty", 0), "0")
            if qty_used > current_qty:
                return jsonify({
                    "ok": False,
                    "error": f"Consumo supera stock: {prod.name}. Disponible {float(current_qty)}"
                }), 400

            prod.stock_qty = current_qty - qty_used

            unit_cost = _dec(getattr(prod, "avg_cost", 0), "0")

            # Requiere StockMoveType.CONSUME = "consume"
            move_type_value = getattr(StockMoveType, "CONSUME").value if hasattr(StockMoveType, "CONSUME") else "consume"

            db.session.add(StockMove(
                product_id=prod.id,
                move_type=move_type_value,
                qty_delta=_dec(-qty_used, "0"),
                unit_cost=unit_cost,
                ref_table="cash_registers",
                ref_id=cr.id,
                cash_register_id=cr.id,
                created_by_id=current_user.id,
                created_at=datetime.utcnow(),
            ))

            if (prod.name or "").strip().lower() == "harina":
                harina_consumed_qty += qty_used
                harina_consumed_cost += (qty_used * unit_cost)

    # ======================================================
    # ✅ Guardar CONTEO FINAL (insumos + bebidas)
    # ======================================================
    if counts_close:
        CashRegisterStockCount.query.filter_by(cash_register_id=cr.id, count_type="close").delete(synchronize_session=False)

        for row in counts_close:
            pid = row.get("product_id")
            qty = _dec(row.get("qty"), "0")
            if not pid or qty < 0:
                continue

            prod = Product.query.get(int(pid))
            if not prod:
                continue

            if not bool(getattr(prod, "track_stock", True)):
                continue

            db.session.add(CashRegisterStockCount(
                cash_register_id=cr.id,
                product_id=prod.id,
                count_type="close",
                qty=qty,
                product_name=prod.name,
                unit=getattr(prod, "unit", None),
                created_by_id=current_user.id,
                created_at=datetime.utcnow()
            ))

    # ===== Snapshot inventario (DESPUÉS del consumo) =====
    CashRegisterInventorySnapshot.query.filter_by(cash_register_id=cr.id).delete(synchronize_session=False)

    inventory_value = Decimal("0")
    products = Product.query.order_by(Product.category.asc(), Product.name.asc()).all()

    for p in products:
        qty = _dec(getattr(p, "stock_qty", 0), "0")
        avg_cost = _dec(getattr(p, "avg_cost", 0), "0")
        stock_value = (qty * avg_cost)

        inventory_value += stock_value

        db.session.add(CashRegisterInventorySnapshot(
            cash_register_id=cr.id,
            product_id=p.id,
            product_name=p.name,
            qty=qty,
            avg_cost=avg_cost,
            stock_value=stock_value
        ))

        if (p.name or "").strip().lower() == "harina":
            harina_stock_final = float(qty)

    profit_est = total_sales - cogs

    cr.status = CashRegisterStatus.CLOSED.value
    cr.closed_at = datetime.utcnow()
    cr.closed_by_id = current_user.id
    cr.closing_amount = closing_amount
    cr.total_cash = total_cash
    cr.total_transfer = total_transfer
    cr.total_sales = total_sales
    cr.total_orders = len(orders_ok)
    cr.total_cancelled = orders_cancelled

    db.session.commit()

    return jsonify({
        "ok": True,
        "resume_pro": {
            "total_sales": float(total_sales),
            "cogs": float(cogs),
            "profit_est": float(profit_est),
            "purchases_total": float(purchases_total),
            "inventory_value": float(inventory_value),

            # ✅ harina
            "harina_consumed_qty": float(harina_consumed_qty),
            "harina_consumed_cost": float(harina_consumed_cost),
            "harina_stock_final": harina_stock_final,
        }
    })

# ======================================================
# PRODUCTOS (POS): SOLO VENTA (show_in_pos=True)
# ======================================================
@pos_bp.get("/products")
@login_required
def list_products():
    from app.models import Product

    query = Product.query.filter_by(active=True)

    # ✅ Si existe el campo show_in_pos, filtra por True
    try:
        col = getattr(Product, "show_in_pos", None)
        if col is not None:
            query = query.filter(Product.show_in_pos.is_(True))
    except Exception:
        # si por alguna razón no existe, no rompemos nada
        pass

    products = (
        query
        .order_by(Product.category.asc(), Product.name.asc())
        .all()
    )

    out = []
    for p in products:
        out.append({
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "price": float(p.price or 0),
            # inventario si existe
            "track_stock": bool(getattr(p, "track_stock", True)),
            "stock_qty": float(getattr(p, "stock_qty", 0) or 0),
            "unit": getattr(p, "unit", "UN"),
        })
    return jsonify(out)


# ======================================================
# CREAR PEDIDO (cobra y descuenta stock 1:1)
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
        PaymentMethod,
        StockMove,
        StockMoveType
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

        # ===== Pre-chequeo stock (evita negativo) =====
        to_deduct = []
        for it in items_in:
            p = Product.query.get(it.get("product_id"))
            qty = int(it.get("qty") or 0)
            if not p or qty <= 0:
                return jsonify({"ok": False, "error": "Producto inválido"}), 400

            track = bool(getattr(p, "track_stock", True))
            if track and hasattr(p, "stock_qty"):
                if _dec(p.stock_qty, "0") < _dec(qty, "0"):
                    return jsonify({
                        "ok": False,
                        "error": f"Stock insuficiente: {p.name} (disponible {float(p.stock_qty or 0)})"
                    }), 400
                to_deduct.append((p, qty))

        # ===== Items + total =====
        for it in items_in:
            p = Product.query.get(it.get("product_id"))
            qty = int(it.get("qty") or 0)

            unit_price = _dec(p.price, "0")

            order.items.append(
                OrderItem(
                    product_id=p.id,
                    product_name=p.name,
                    unit_price=unit_price,
                    quantity=qty
                )
            )

            total += unit_price * qty

        amount = _dec(pay.get("amount"), "0")
        if amount != total:
            return jsonify({"ok": False, "error": "Monto incorrecto"}), 400

        order.payments.append(Payment(method=method, amount=amount))

        db.session.add(order)

        # ===== Descontar stock + registrar kardex (SALE) =====
        for p, qty in to_deduct:
            if hasattr(p, "stock_qty"):
                p.stock_qty = _dec(p.stock_qty, "0") - _dec(qty, "0")

            unit_cost = _dec(getattr(p, "avg_cost", 0), "0")

            db.session.add(StockMove(
                product_id=p.id,
                move_type=StockMoveType.SALE.value,
                qty_delta=_dec(-qty, "0"),
                unit_cost=unit_cost,
                ref_table="orders",
                ref_id=None,  # si quieres, lo ponemos con order.id luego (requiere flush)
                cash_register_id=cr.id,
                created_by_id=current_user.id,
                created_at=datetime.utcnow(),
            ))

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


@pos_bp.get("/orders/history")
@login_required
def orders_history():
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
                "subtotal": float(_dec(item.unit_price, "0") * _dec(item.quantity, "0"))
            }
            for item in (order.items or [])
        ]
    })


@pos_bp.post("/orders/<int:order_id>/cancel")
@login_required
@require_roles("admin", "cashier")
def cancel_order(order_id):
    """
    Anula pedido y repone stock (para no perder inventario).
    """
    from app.models import OrderStatus, Product, StockMove, StockMoveType

    cr = get_open_cash_register()
    if not cr:
        return jsonify({"ok": False, "error": "No hay caja abierta"}), 400

    order = Order.query.get_or_404(order_id)

    if order.cash_register_id != cr.id:
        return jsonify({"ok": False, "error": "Solo puedes anular pedidos de la caja abierta"}), 400

    st = (order.status or "").lower()
    if st == OrderStatus.CANCELLED.value:
        return jsonify({"ok": True, "status": order.status, "message": "Ya estaba anulado"})

    if st in (OrderStatus.DELIVERED.value, OrderStatus.CLOSED.value):
        return jsonify({"ok": False, "error": "No puedes anular un pedido entregado/cerrado"}), 400

    # repone stock por items (solo si track_stock=True)
    for it in (order.items or []):
        p = Product.query.get(it.product_id)
        if not p:
            continue
        track = bool(getattr(p, "track_stock", True))
        if track and hasattr(p, "stock_qty"):
            p.stock_qty = _dec(p.stock_qty, "0") + _dec(it.quantity, "0")
            unit_cost = _dec(getattr(p, "avg_cost", 0), "0")
            db.session.add(StockMove(
                product_id=p.id,
                move_type=StockMoveType.RETURN.value,
                qty_delta=_dec(it.quantity, "0"),
                unit_cost=unit_cost,
                ref_table="orders",
                ref_id=order.id,
                cash_register_id=cr.id,
                created_by_id=current_user.id,
                created_at=datetime.utcnow(),
            ))

    reason = (request.get_json(silent=True) or {}).get("reason")
    if reason:
        reason = str(reason).strip()
        if reason:
            prev = (order.notes or "").strip()
            order.notes = (prev + "\n" if prev else "") + f"[ANULADO] {reason}"

    order.status = OrderStatus.CANCELLED.value
    db.session.commit()

    return jsonify({"ok": True, "status": order.status})


# ======================================================
# RECEIPT
# ======================================================
@pos_bp.get("/receipt/<int:order_id>")
@login_required
def receipt(order_id):
    order = Order.query.get_or_404(order_id)
    total = order.total_amount()

    business_name = get_setting("business_name", "POS Barra")
    receipt_footer = get_setting("receipt_footer", "Gracias por su compra")
    receipt_autoprint = get_setting("receipt_autoprint", "1")
    qr_size = get_setting("qr_size", "120")

    try:
        n = int(qr_size)
        if n < 80:
            n = 80
        if n > 400:
            n = 400
        qr_size = str(n)
    except Exception:
        qr_size = "120"

    return render_template(
        "receipt.html",
        order=order,
        total=total,
        business_name=business_name,
        receipt_footer=receipt_footer,
        receipt_autoprint=receipt_autoprint,
        qr_size=qr_size
    )


@pos_bp.get("/q/order/<int:order_id>")
def qr_order_status(order_id):
    order = Order.query.get_or_404(order_id)
    return render_template("qr_status.html", order=order)


@pos_bp.get("/cash/summary")
@login_required
@require_roles("admin", "cashier")
def cash_summary():
    from app.models import OrderStatus, PaymentMethod

    cr = get_open_cash_register()
    if not cr:
        return jsonify({"ok": True, "open": False, "summary": None})

    q = Order.query.filter_by(cash_register_id=cr.id)

    total_orders = q.count()
    cancelled = q.filter(Order.status == OrderStatus.CANCELLED.value).count()
    pending = q.filter(Order.status.in_([OrderStatus.PREP.value, OrderStatus.READY.value])).count()
    delivered = q.filter(Order.status == OrderStatus.DELIVERED.value).count()
    closed = q.filter(Order.status == OrderStatus.CLOSED.value).count()

    orders_ok = q.filter(Order.status != OrderStatus.CANCELLED.value).all()

    total_cash = Decimal("0")
    total_transfer = Decimal("0")
    total_sales = Decimal("0")

    for o in orders_ok:
        for pay in (o.payments or []):
            amt = _dec(pay.amount, "0")
            if pay.method == PaymentMethod.CASH.value:
                total_cash += amt
            elif pay.method == PaymentMethod.TRANSFER.value:
                total_transfer += amt
            total_sales += amt

    return jsonify({
        "ok": True,
        "open": True,
        "cash_register_id": cr.id,
        "summary": {
            "total_sales": float(total_sales),
            "total_cash": float(total_cash),
            "total_transfer": float(total_transfer),
            "total_orders": int(total_orders),
            "cancelled": int(cancelled),
            "pending": int(pending),
            "delivered": int(delivered),
            "closed": int(closed),
        }
    })

@pos_bp.get("/inventory/items")
@login_required
@require_roles("admin", "cashier")
def inventory_items():
    """
    Devuelve productos activos con stock (track_stock=True si existe).
    Sirve para armar el formulario de conteo inicial/final.
    """
    from app.models import Product

    products = Product.query.filter_by(active=True).order_by(Product.category.asc(), Product.name.asc()).all()

    out = []
    for p in products:
        track = bool(getattr(p, "track_stock", True))
        # Solo preguntamos por stock a los que realmente controlas
        if not track:
            continue

        out.append({
            "id": p.id,
            "name": p.name,
            "category": p.category,
            "unit": getattr(p, "unit", "UN"),
            "product_type": getattr(p, "product_type", "sale"),  # sale|supply (si existe)
            "stock_qty": float(getattr(p, "stock_qty", 0) or 0),
        })

    return jsonify({"ok": True, "items": out})
# ======================================================
# UI
# ======================================================
@pos_bp.get("/ui")
@login_required
def pos_ui():
    from app.models import Product

    products = (
        Product.query
        .filter_by(active=True)
        .order_by(Product.category.asc(), Product.name.asc())
        .all()
    )
    return render_template("pos.html", products=products)