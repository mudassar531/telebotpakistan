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

log = logging.getLogger(__name__)

# Create a base class to define all the database subclasses
TableDeclarativeBase = declarative_base()

# Define all the database tables using the sqlalchemy declarative base
class User(DeferredReflection, TableDeclarativeBase):
    """A Telegram user who used the bot at least once."""

    # Telegram data
    user_id: Mapped[BigInteger] = mapped_column(primary_key=True)
    first_name: Mapped[String] = mapped_column(nullable=False)
    last_name: Mapped[String] = mapped_column()
    username: Mapped[String] = mapped_column()
    language: Mapped[String] = mapped_column(nullable=False)

    # Current wallet credit
    credit: Mapped[Integer] = mapped_column(nullable=False)

    # Extra table parameters
    __tablename__ = "users"

    def __init__(self, w: "worker.Worker", **kwargs):
        # Initialize the super
        super().__init__(**kwargs)
        # Get the data from telegram
        self.user_id = w.telegram_user.id
        self.first_name = w.telegram_user.first_name
        self.last_name = w.telegram_user.last_name
        self.username = w.telegram_user.username
        if w.telegram_user.language_code:
            self.language = w.telegram_user.language_code
        else:
            self.language = w.cfg["Language"]["default_language"]
        # The starting wallet value is 0
        self.credit = 0

    def __str__(self):
        """Describe the user in the best way possible given the available data."""
        if self.username is not None:
            return f"@{self.username}"
        elif self.last_name is not None:
            return f"{self.first_name} {self.last_name}"
        else:
            return self.first_name

    def identifiable_str(self):
        """Describe the user in the best way possible, ensuring a way back to the database record exists."""
        return f"user_{self.user_id} ({str(self)})"

    def mention(self):
        """Mention the user in the best way possible given the available data."""
        if self.username is not None:
            return f"@{self.username}"
        else:
            return f"[{self.first_name}](tg://user?id={self.user_id})"

    def recalculate_credit(self):
        """Recalculate the credit for this user by calculating the sum of the values of all their transactions."""
        valid_transactions: List["Transaction"] = [t for t in self.transactions if not t.refunded]
        self.credit = sum(map(lambda t: t.value, valid_transactions))

    @property
    def full_name(self):
        if self.last_name:
            return f"{self.first_name} {self.last_name}"
        else:
            return self.first_name

    def __repr__(self):
        return f"<User {self.mention()} having {self.credit} credit>"

class Product(DeferredReflection, TableDeclarativeBase):
    """A purchasable product."""

    # Product id
    id: Mapped[Integer] = mapped_column(primary_key=True)
    # Product name
    name: Mapped[String] = mapped_column()
    # Product description
    description: Mapped[Text] = mapped_column()
    # Product price, if null product is not for sale
    price: Mapped[Integer] = mapped_column()
    # Image data
    image: Mapped[LargeBinary] = mapped_column()
    # Product has been deleted
    deleted: Mapped[Boolean] = mapped_column(nullable=False)

    # Extra table parameters
    __tablename__ = "products"

    # No __init__ is needed, the default one is sufficient

    def text(self, w: "worker.Worker", *, style: str = "full", cart_qty: int = None):
        """Return the product details formatted with Telegram HTML. The image is omitted."""
        if style == "short":
            return f"{cart_qty}x {utils.telegram_html_escape(self.name)} - {str(w.Price(self.price) * cart_qty)}"
        elif style == "full":
            if cart_qty is not None:
                cart = w.loc.get("in_cart_format_string", quantity=cart_qty)
            else:
                cart = ''
            return w.loc.get("product_format_string", name=utils.telegram_html_escape(self.name),
                             description=utils.telegram_html_escape(self.description),
                             price=str(w.Price(self.price)),
                             cart=cart)
        else:
            raise ValueError("style is not an accepted value")

    def __repr__(self):
        return f"<Product {self.name}>"

    def send_as_message(self, w: "worker.Worker", chat_id: int) -> dict:
        """Send a message containing the product data."""
        if self.image is None:
            r = requests.get(f"https://api.telegram.org/bot{w.cfg['Telegram']['token']}/sendMessage",
                             params={"chat_id": chat_id,
                                     "text": self.text(w),
                                     "parse_mode": "HTML"})
        else:
            r = requests.post(f"https://api.telegram.org/bot{w.cfg['Telegram']['token']}/sendPhoto",
                              files={"photo": self.image},
                              params={"chat_id": chat_id,
                                      "caption": self.text(w),
                                      "parse_mode": "HTML"})
        return r.json()

    def set_image(self, file: telegram.File):
        """Download an image from Telegram and store it in the image column.
        This is a slow blocking function. Try to avoid calling it directly, use a thread if possible."""
        # Download the photo through a get request
        r = requests.get(file.file_path)
        # Store the photo in the database record
        self.image = r.content

class Transaction(DeferredReflection, TableDeclarativeBase):
    """A greed wallet transaction.
    Wallet credit ISN'T calculated from these, but they can be used to recalculate it."""

    # The internal transaction ID
    transaction_id: Mapped[Integer] = mapped_column(primary_key=True)
    # The user whose credit is affected by this transaction
    user_id: Mapped[BigInteger] = mapped_column(ForeignKey("users.user_id"), nullable=False)
    user: Mapped["User"] = relationship("User", backref=backref("transactions"))
    # The value of this transaction. Can be both negative and positive.
    value: Mapped[Integer] = mapped_column(nullable=False)
    # Refunded status: if True, ignore the value of this transaction when recalculating
    refunded: Mapped[Boolean] = mapped_column(default=False)
    # Extra notes on the transaction
    notes: Mapped[Text] = mapped_column()

    # Payment provider
    provider: Mapped[String] = mapped_column()
    # Transaction ID supplied by Telegram
    telegram_charge_id: Mapped[String] = mapped_column()
    # Transaction ID supplied by the payment provider
    provider_charge_id: Mapped[String] = mapped_column()
    # Extra transaction data, may be required by the payment provider in case of a dispute
    payment_name: Mapped[String] = mapped_column()
    payment_phone: Mapped[String] = mapped_column()
    payment_email: Mapped[String] = mapped_column()

    # Order ID
    order_id: Mapped[Integer] = mapped_column(ForeignKey("orders.order_id"))
    order: Mapped["Order"] = relationship("Order")

    # Extra table parameters
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

class Admin(DeferredReflection, TableDeclarativeBase):
    """A greed administrator with his permissions."""

    # The telegram id
    user_id: Mapped[BigInteger] = mapped_column(ForeignKey("users.user_id"), primary_key=True)
    user: Mapped["User"] = relationship("User")
    # Permissions
    edit_products: Mapped[Boolean] = mapped_column(default=False)
    receive_orders: Mapped[Boolean] = mapped_column(default=False)
    create_transactions: Mapped[Boolean] = mapped_column(default=False)
    display_on_help: Mapped[Boolean] = mapped_column(default=False)
    is_owner: Mapped[Boolean] = mapped_column(default=False)
    # Live mode enabled
    live_mode: Mapped[Boolean] = mapped_column(default=False)

    # Extra table parameters
    __tablename__ = "admins"

    def __repr__(self):
        return f"<Admin {self.user_id}>"

class Order(DeferredReflection, TableDeclarativeBase):
    """An order which has been placed by a user.
    It may include multiple products, available in the OrderItem table."""

    # The unique order id
    order_id: Mapped[int] = mapped_column(primary_key=True)
    # The user who placed the order
    user_id: Mapped[BigInteger] = mapped_column(ForeignKey("users.user_id"))
    user: Mapped["User"] = relationship("User")
    # Date of creation
    creation_date: Mapped[DateTime] = mapped_column(nullable=False)
    # Date of delivery
    delivery_date: Mapped[DateTime] = mapped_column()
    # Date of refund: if null, product hasn't been refunded
    refund_date: Mapped[DateTime] = mapped_column()
    # Refund reason: if null, product hasn't been refunded
    refund_reason: Mapped[Text] = mapped_column()
    # List of items in the order
    items: Mapped[List["OrderItem"]] = relationship("OrderItem")
    # Extra details specified by the purchasing user
    notes: Mapped[Text] = mapped_column()
    # Linked transaction
    transaction: Mapped["Transaction"] = relationship("Transaction", uselist=False)

    # Extra table parameters
    __tablename__ = "orders"

    def __repr__(self):
        return f"<Order {self.order_id} placed by User {self.user_id}>"

    def text(self, w: "worker.Worker", session, user=False):
        joined_self = session.query(Order).filter_by(order_id=self.order_id).join(Transaction).one()
        items = ""
        for item in self.items:
            items += item.text(w) + "\n"
        if self.delivery_date is not None:
            status_emoji = w.loc.get("emoji_completed")
            status_text = w.loc.get("text_completed")
        elif self.refund_date is not None:
            status_emoji = w.loc.get("emoji_refunded")
            status_text = w.loc.get("text_refunded")
        else:
            status_emoji = w.loc.get("emoji_not_processed")
            status_text = w.loc.get("text_not_processed")
        if user and w.cfg["Appearance"]["full_order_info"] == "no":
            return w.loc.get("user_order_format_string",
                             status_emoji=status_emoji,
                             status_text=status_text,
                             items=items,
                             notes=self.notes,
                             value=str(w.Price(-joined_self.transaction.value))) + \
                   (w.loc.get("refund_reason", reason=self.refund_reason) if self.refund_date is not None else "")
        else:
            return status_emoji + " " + \
                   w.loc.get("order_number", id=self.order_id) + "\n" + \
                   w.loc.get("order_format_string",
                             user=self.user.mention(),
                             date=self.creation_date.isoformat(),
                             items=items,
                             notes=self.notes if self.notes is not None else "",
                             value=str(w.Price(-joined_self.transaction.value))) + \
                   (w.loc.get("refund_reason", reason=self.refund_reason) if self.refund_date is not None else "")

class OrderItem(DeferredReflection, TableDeclarativeBase):
    """An order item in the Order table."""

    # The unique item id
    item_id: Mapped[int] = mapped_column(primary_key=True)
    # The order this item belongs to
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.order_id"))
    order: Mapped["Order"] = relationship("Order", backref="items")
    # The product this item refers to
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    product: Mapped["Product"] = relationship("Product")
    # The quantity of the product in the order
    quantity: Mapped[int] = mapped_column(nullable=False)

    # Extra table parameters
    __tablename__ = "order_items"

    def text(self, w: "worker.Worker"):
        """Return the formatted text for this item."""
        return f"{self.quantity}x {self.product.name} - {w.Price(self.product.price * self.quantity)}"

    def __repr__(self):
        return f"<OrderItem {self.item_id} in Order {self.order_id}>"
