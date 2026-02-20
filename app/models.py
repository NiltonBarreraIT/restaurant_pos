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

class Product(db.Model):
    __tablename__ = "products"

    id = db.Column(db.Integer, primary_key=True)
    sku = db.Column(db.String(40), unique=True, nullable=True, index=True)
    name = db.Column(db.String(120), nullable=False, index=True)
    category = db.Column(db.String(80), nullable=True, index=True)  # churros/empanadas/bebidas...
    price = db.Column(db.Numeric(12, 2), nullable=False, default=0)
    active = db.Column(db.Boolean, default=True)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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

    # Auditoría
    created_by_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    created_by = db.relationship("User", foreign_keys=[created_by_id])

    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    notes = db.Column(db.Text, nullable=True)

    items = db.relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")
    payments = db.relationship("Payment", back_populates="order", cascade="all, delete-orphan")

    def total_amount(self):
        return sum(float(it.unit_price) * it.quantity for it in self.items)


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
