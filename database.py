import logging
import typing
from typing import List

import requests
import telegram
from sqlalchemy import Column, ForeignKey, UniqueConstraint
from sqlalchemy import Integer, BigInteger, String, Text, LargeBinary, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base, DeferredReflection
from sqlalchemy.orm import relationship, backref, Mapped, mapped_column

import utils

if typing.TYPE_CHECKING:
    import worker

from sqlalchemy import Column, ForeignKey, Integer, BigInteger, String, Text, LargeBinary, DateTime, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Mapped, mapped_column, relationship

TableDeclarativeBase = declarative_base()

class User(TableDeclarativeBase):
    """A Telegram user who used the bot at least once."""

    user_id: Mapped[BigInteger] = mapped_column(primary_key=True)
    first_name: Mapped[str] = mapped_column(String, nullable=False)
    last_name: Mapped[str] = mapped_column(String)
    username: Mapped[str] = mapped_column(String)
    language: Mapped[str] = mapped_column(String, nullable=False)
    credit: Mapped[int] = mapped_column(Integer, nullable=False)

    __tablename__ = "users"

    def __init__(self, w: "worker.Worker", **kwargs):
        super().__init__(**kwargs)
        self.user_id = w.telegram_user.id
        self.first_name = w.telegram_user.first_name
        self.last_name = w.telegram_user.last_name
        self.username = w.telegram_user.username
        self.language = w.telegram_user.language_code or w.cfg["Language"]["default_language"]
        self.credit = 0

    def __str__(self):
        return f"@{self.username}" if self.username else (f"{self.first_name} {self.last_name}" if self.last_name else self.first_name)

    def identifiable_str(self):
        return f"user_{self.user_id} ({str(self)})"

    def mention(self):
        return f"@{self.username}" if self.username else f"[{self.first_name}](tg://user?id={self.user_id})"

    def recalculate_credit(self):
        valid_transactions = [t for t in self.transactions if not t.refunded]
        self.credit = sum(t.value for t in valid_transactions)

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}" if self.last_name else self.first_name

    def __repr__(self):
        return f"<User {self.mention()} having {self.credit} credit>"

class Product(TableDeclarativeBase):
    """A purchasable product."""

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(Text)
    price: Mapped[int] = mapped_column(Integer)
    image: Mapped[LargeBinary] = mapped_column()
    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False)

    __tablename__ = "products"

    def text(self, w: "worker.Worker", *, style: str = "full", cart_qty: int = None):
        if style == "short":
            return f"{cart_qty}x {utils.telegram_html_escape(self.name)} - {str(w.Price(self.price) * cart_qty)}"
        elif style == "full":
            cart = w.loc.get("in_cart_format_string", quantity=cart_qty) if cart_qty is not None else ''
            return w.loc.get("product_format_string", name=utils.telegram_html_escape(self.name),
                             description=utils.telegram_html_escape(self.description),
                             price=str(w.Price(self.price)),
                             cart=cart)
        else:
            raise ValueError("style is not an accepted value")

    def __repr__(self):
        return f"<Product {self.name}>"

    def send_as_message(self, w: "worker.Worker", chat_id: int) -> dict:
        url = f"https://api.telegram.org/bot{w.cfg['Telegram']['token']}/sendMessage" if self.image is None else f"https://api.telegram.org/bot{w.cfg['Telegram']['token']}/sendPhoto"
        params = {"chat_id": chat_id, "text": self.text(w), "parse_mode": "HTML"} if self.image is None else {"chat_id": chat_id, "caption": self.text(w), "parse_mode": "HTML"}
        files = {"photo": self.image} if self.image is not None else None
        r = requests.post(url, params=params, files=files)
        return r.json()

    def set_image(self, file: telegram.File):
        r = requests.get(file.file_path)
        self.image = r.content

class Transaction(TableDeclarativeBase):
    """A greed wallet transaction."""

    transaction_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[BigInteger] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    user: Mapped["User"] = relationship("User", backref="transactions")
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    refunded: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text)

    provider: Mapped[str] = mapped_column(String)
    telegram_charge_id: Mapped[str] = mapped_column(String)
    provider_charge_id: Mapped[str] = mapped_column(String)
    payment_name: Mapped[str] = mapped_column(String)
    payment_phone: Mapped[str] = mapped_column(String)
    payment_email: Mapped[str] = mapped_column(String)

    order_id: Mapped[int] = mapped_column(ForeignKey("orders.order_id"))
    order: Mapped["Order"] = relationship("Order")

    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("provider", "provider_charge_id"),)

    def text(self, w: "worker.Worker"):
        string = f"<b>T{self.transaction_id}</b> | {str(self.user)} | {w.Price(self.value)}"
        if self.refunded:
            string += f" | {w.loc.get('emoji_refunded')}"
        if self.provider:
            string += f" | {self.provider}"
        if self.notes:
            string += f" | {self.notes}"
        return string

    def __repr__(self):
        return f"<Transaction {self.transaction_id} for User {self.user_id}>"

class Admin(TableDeclarativeBase):
    """A greed administrator with permissions."""

    user_id: Mapped[BigInteger] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    user: Mapped["User"] = relationship("User")
    edit_products: Mapped[bool] = mapped_column(Boolean, default=False)
    receive_orders: Mapped[bool] = mapped_column(Boolean, default=False)
    create_transactions: Mapped[bool] = mapped_column(Boolean, default=False)
    display_on_help: Mapped[bool] = mapped_column(Boolean, default=False)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
    live_mode: Mapped[bool] = mapped_column(Boolean, default=False)

    __tablename__ = "admins"

    def __repr__(self):
        return f"<Admin {self.user_id}>"

class Order(TableDeclarativeBase):
    """An order which has been placed by a user.
    It may include multiple products, available in the OrderItem table."""

    order_id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[BigInteger] = mapped_column(ForeignKey("users.user_id"))
    user: Mapped["User"] = relationship("User")
    creation_date: Mapped[DateTime] = mapped_column(nullable=False)
    delivery_date: Mapped[DateTime] = mapped_column()
    refund_date: Mapped[DateTime] = mapped_column()
    refund_reason: Mapped[Text] = mapped_column()
    items: Mapped[List["OrderItem"]] = relationship("OrderItem")
    notes: Mapped[Text] = mapped_column()
    transaction: Mapped["Transaction"] = relationship("Transaction", uselist=False)

    __tablename__ = "orders"

    def __repr__(self):
        return f"<Order {self.order_id} placed by User {self.user_id}>"

    def text(self, w: "worker.Worker", session, user=False):
        joined_self = session.query(Order).filter_by(order_id=self.order_id).join(Transaction).one()
        items = "".join(item.text(w) + "\n" for item in self.items)
        status_emoji, status_text = (w.loc.get("emoji_completed"), w.loc.get("text_completed")) if self.delivery_date else (w.loc.get("emoji_refunded"), w.loc.get("text_refunded")) if self.refund_date else (w.loc.get("emoji_not_processed"), w.loc.get("text_not_processed"))
        if user and w.cfg["Appearance"]["full_order_info"] == "no":
            return w.loc.get("user_order_format_string",
                             status_emoji=status_emoji,
                             status_text=status_text,
                             items=items,
                             notes=self.notes,
                             value=str(w.Price(-joined_self.transaction.value))) + \
                   (w.loc.get("refund_reason", reason=self.refund_reason) if self.refund_date else "")
        else:
            return f"{status_emoji} {w.loc.get('order_number', id=self.order_id)}\n{w.loc.get('order_format_string', user=self.user.mention(), date=self.creation_date.isoformat(), items=items, notes=self.notes or '', value=str(w.Price(-joined_self.transaction.value)))}" + \
                   (w.loc.get("refund_reason", reason=self.refund_reason) if self.refund_date else "")

class OrderItem(TableDeclarativeBase):
    """A product that has been purchased as part of an order."""

    item_id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    product: Mapped["Product"] = relationship("Product")
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.order_id"), nullable=False)

    __tablename__ = "orderitems"

    def text(self, w: "worker.Worker"):
        return f"{self.product.name} - {str(w.Price(self.product.price))}"

    def __repr__(self):
        return f"<OrderItem {self.item_id}>"
