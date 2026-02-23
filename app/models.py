from datetime import datetime
from enum import Enum
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from .extensions import db

class Role(str, Enum):
    ADMIN = "admin"
    CASHIER = "cashier"
    KITCHEN = "kitchen"

class OrderStatus(str, Enum):
    PREP = "prep"         # en preparación
    READY = "ready"       # listo para entregar
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    CLOSED = "closed"     # cerrado por cierre de caja

class PaymentMethod(str, Enum):
    CASH = "cash"           # efectivo
    TRANSFER = "transfer"   # transferencia

class User(db.Model, UserMixin):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    email = db.Column(db.String(120), unique=True, nullable=True, index=True)
    role = db.Column(db.String(20), nullable=False, default=Role.CASHIER.value)
    password_hash = db.Column(db.String(255), nullable=False)

    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, raw: str) -> None:
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw: str) -> bool:
        return check_password_hash(self.password_hash, raw)

from decimal import Decimal

class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), unique=True, nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    category = db.Column(db.String(80), nullable=True, index=True)  # churros/empanadas/bebidas...
    price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    active = db.Column(db.Boolean, default=True)
    product_type = db.Column(db.String(20), nullable=False, default="sale")  # sale | supply
    show_in_pos = db.Column(db.Boolean, nullable=False, default=True)
    
    # ====== NUEVO: INVENTARIO / COSTOS ======
    unit = db.Column(db.String(10), nullable=False, default="UN")  # UN / KG / LT
    track_stock = db.Column(db.Boolean, nullable=False, default=True)  # si descuenta stock al vender
    stock_qty = db.Column(db.Numeric(14, 3), nullable=False, default=0)  # admite decimales para KG/LT
    stock_min_qty = db.Column(db.Numeric(14, 3), nullable=False, default=0)

    # costo promedio ponderado (AVCO)
    avg_cost = db.Column(db.Numeric(14, 4), nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def apply_purchase(self, qty, unit_cost):
        """
        Actualiza stock + costo promedio ponderado.
        qty: Decimal (o float convertible)
        unit_cost: Decimal (o float convertible)
        """
        q = Decimal(str(qty or 0))
        c = Decimal(str(unit_cost or 0))

        if q <= 0:
            return

        current_qty = Decimal(str(self.stock_qty or 0))
        current_cost = Decimal(str(self.avg_cost or 0))

        new_qty = current_qty + q
        if new_qty <= 0:
            self.stock_qty = Decimal("0")
            return

        new_cost = ((current_qty * current_cost) + (q * c)) / new_qty

        self.stock_qty = new_qty
        self.avg_cost = new_cost

class Order(db.Model):
    __tablename__ = "orders"

    id = db.Column(db.Integer, primary_key=True)
    reference_name = db.Column(db.String(120), nullable=False, index=True)
    status = db.Column(db.String(20), nullable=False, default=OrderStatus.PREP.value, index=True)

    # 🔗 RELACIÓN CON CAJA
    cash_register_id = db.Column(
        db.Integer,
        db.ForeignKey("cash_registers.id"),
        nullable=False,
        index=True
    )
    cash_register = db.relationship("CashRegister", back_populates="orders")

    # ✅ Correlativo por caja (1..N por cada cash_register)
    number_in_register = db.Column(db.Integer, nullable=False, default=1, index=True)

    __table_args__ = (
        db.UniqueConstraint("cash_register_id", "number_in_register", name="uq_order_register_number"),
    )

    # Auditoría
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    notes = db.Column(db.Text, nullable=True)

    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payments = db.relationship("Payment", back_populates="order", cascade="all, delete-orphan")

    def total_amount(self) -> float:
        """
        Total del pedido calculado desde items.
        Retorna float para facilitar JSON/plantillas.
        """
        return sum(float(it.unit_price or 0) * int(it.quantity or 0) for it in (self.items or []))


class OrderItem(db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)

    product_name = db.Column(db.String(120), nullable=False)  # snapshot
    unit_price = db.Column(db.Numeric(12, 2), nullable=False) # snapshot
    quantity = db.Column(db.Integer, nullable=False, default=1)
    notes = db.Column(db.String(255), nullable=True)

    order = db.relationship("Order", back_populates="items")
    product = db.relationship("Product")

class Payment(db.Model):
    __tablename__ = "payments"

    id = db.Column(db.Integer, primary_key=True)

    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id"),
        nullable=False,
        index=True
    )

    method = db.Column(db.String(20), nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    reference = db.Column(db.String(80), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    order = db.relationship("Order", back_populates="payments")

class CashRegisterStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class CashRegister(db.Model):
    __tablename__ = "cash_registers"

    id = db.Column(db.Integer, primary_key=True)

    # Estado de la caja
    status = db.Column(
        db.String(10),
        nullable=False,
        default=CashRegisterStatus.OPEN.value,
        index=True
    )

    # Fechas
    opened_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    closed_at = db.Column(db.DateTime, nullable=True)

    # Usuarios
    opened_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    closed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    opened_by = db.relationship("User", foreign_keys=[opened_by_id])
    closed_by = db.relationship("User", foreign_keys=[closed_by_id])

    # Montos
    opening_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    closing_amount = db.Column(db.Numeric(12, 2), nullable=True)

    total_cash = db.Column(db.Numeric(12, 2), nullable=True)
    total_transfer = db.Column(db.Numeric(12, 2), nullable=True)
    total_sales = db.Column(db.Numeric(12, 2), nullable=True)

    # Resumen
    total_orders = db.Column(db.Integer, nullable=True)
    total_cancelled = db.Column(db.Integer, nullable=True)

    notes = db.Column(db.String(255), nullable=True)

    # 🔗 RELACIÓN CON PEDIDOS
    orders = db.relationship(
        "Order",
        back_populates="cash_register",
        cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<CashRegister id={self.id} status={self.status}>"

class AppSetting(db.Model):
    __tablename__ = "app_settings"

    key = db.Column(db.String(80), primary_key=True)
    value = db.Column(db.Text, nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Purchase(db.Model):
    __tablename__ = "purchases"

    id = db.Column(db.Integer, primary_key=True)

    # opcional, pero recomendado para reportar por turno:
    cash_register_id = db.Column(db.Integer, db.ForeignKey("cash_registers.id"), nullable=True, index=True)
    cash_register = db.relationship("CashRegister")

    supplier = db.Column(db.String(120), nullable=True)
    invoice_ref = db.Column(db.String(80), nullable=True)

    # cash / transfer / credit (si quieres)
    payment_method = db.Column(db.String(20), nullable=True, index=True)
    paid = db.Column(db.Boolean, default=True)

    total_amount = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    notes = db.Column(db.Text, nullable=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    items = db.relationship("PurchaseItem", back_populates="purchase", cascade="all, delete-orphan")


class PurchaseItem(db.Model):
    __tablename__ = "purchase_items"

    id = db.Column(db.Integer, primary_key=True)
    purchase_id = db.Column(db.Integer, db.ForeignKey("purchases.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)

    product_name = db.Column(db.String(120), nullable=False)  # snapshot
    qty = db.Column(db.Numeric(14, 3), nullable=False, default=0)
    unit_cost = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    line_total = db.Column(db.Numeric(12, 2), nullable=False, default=0)

    purchase = db.relationship("Purchase", back_populates="items")
    product = db.relationship("Product")


class StockMoveType(str, Enum):
    PURCHASE = "purchase"
    SALE = "sale"
    ADJUST = "adjust"
    RETURN = "return"


class StockMove(db.Model):
    """
    Kardex simple: todo movimiento que cambie stock queda registrado.
    """
    __tablename__ = "stock_moves"

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)

    move_type = db.Column(db.String(20), nullable=False, index=True)  # purchase/sale/adjust/return
    qty_delta = db.Column(db.Numeric(14, 3), nullable=False, default=0)  # + entra, - sale

    unit_cost = db.Column(db.Numeric(14, 4), nullable=True)  # costo usado para compras / COGS
    ref_table = db.Column(db.String(40), nullable=True)      # purchases/orders/adjustments
    ref_id = db.Column(db.Integer, nullable=True, index=True)

    cash_register_id = db.Column(db.Integer, db.ForeignKey("cash_registers.id"), nullable=True, index=True)

    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    product = db.relationship("Product")


class CashRegisterInventorySnapshot(db.Model):
    """
    Foto del inventario al cierre de caja (para ver qué quedó y cuánto vale).
    """
    __tablename__ = "cash_register_inventory_snapshots"

    id = db.Column(db.Integer, primary_key=True)
    cash_register_id = db.Column(db.Integer, db.ForeignKey("cash_registers.id"), nullable=False, index=True)

    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)
    product_name = db.Column(db.String(120), nullable=False)

    qty = db.Column(db.Numeric(14, 3), nullable=False, default=0)
    avg_cost = db.Column(db.Numeric(14, 4), nullable=False, default=0)
    stock_value = db.Column(db.Numeric(14, 2), nullable=False, default=0)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        db.UniqueConstraint("cash_register_id", "product_id", name="uq_snapshot_register_product"),
    )

class CashRegisterCountType(str, Enum):
    OPEN = "open"
    CLOSE = "close"

class CashRegisterStockCount(db.Model):
    __tablename__ = "cash_register_stock_counts"

    id = db.Column(db.Integer, primary_key=True)
    cash_register_id = db.Column(db.Integer, db.ForeignKey("cash_registers.id"), nullable=False, index=True)
    product_id = db.Column(db.Integer, db.ForeignKey("products.id"), nullable=False, index=True)

    count_type = db.Column(db.String(10), nullable=False, index=True)  # "open" | "close"
    qty = db.Column(db.Numeric(14, 3), nullable=False, default=0)

    # snapshot para que aunque cambies nombre/unidad, quede el histórico del turno
    product_name = db.Column(db.String(120), nullable=False)
    unit = db.Column(db.String(10), nullable=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)

    __table_args__ = (
        db.UniqueConstraint("cash_register_id", "product_id", "count_type", name="uq_cash_count_once"),
    )