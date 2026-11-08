import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from webapp.routes import commerce


class OrderStatusTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "storefront.db"
        with sqlite3.connect(self.database) as con:
            con.executescript(
                """
                CREATE TABLE orders (
                    order_id TEXT PRIMARY KEY, customer_id TEXT, order_status TEXT,
                    payment_status TEXT, delivered_timestamp TEXT, updated_at TEXT
                );
                CREATE TABLE transactions (
                    transaction_id TEXT PRIMARY KEY, order_id TEXT, customer_id TEXT,
                    product_id TEXT, order_status TEXT, payment_status TEXT,
                    net_item_value REAL, gross_margin REAL, quantity INTEGER,
                    delivered_timestamp TEXT
                );
                CREATE TABLE inventory (product_id TEXT PRIMARY KEY, current_stock INTEGER);
                CREATE TABLE inventory_movements (
                    movement_id TEXT, movement_timestamp TEXT, product_id TEXT,
                    quantity_delta INTEGER, movement_type TEXT, order_id TEXT,
                    transaction_id TEXT, reason TEXT
                );
                INSERT INTO orders VALUES ('order-1', 'customer-1', 'Placed', 'Success', NULL, NULL);
                INSERT INTO transactions VALUES (
                    'transaction-1', 'order-1', 'customer-1', 'product-1',
                    'Placed', 'Success', 100, 40, 2, NULL
                );
                INSERT INTO inventory VALUES ('product-1', 3);
                """
            )

    def tearDown(self):
        self.tempdir.cleanup()

    def open_database(self):
        con = sqlite3.connect(self.database)
        con.row_factory = sqlite3.Row
        return con

    def status(self, action):
        with (
            patch.object(commerce, "db", side_effect=self.open_database),
            patch.object(
                commerce,
                "get_session",
                return_value={"customer_id": "customer-1", "device_type": "test"},
            ),
            patch.object(commerce, "mirror_inventory"),
            patch.object(commerce, "mirror_inventory_movement"),
            patch.object(commerce, "mirror_order"),
            patch.object(commerce, "mirror_transaction"),
            patch.object(commerce, "log_event") as log_event,
        ):
            response = commerce.update_order_status(
                "order-1", action=action, sid="session-1"
            )
        return response, log_event

    def test_delivery_updates_order_and_transaction(self):
        response, log_event = self.status("deliver")

        with closing(self.open_database()) as con:
            order = con.execute("SELECT * FROM orders").fetchone()
            transaction = con.execute("SELECT * FROM transactions").fetchone()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(order["order_status"], "Delivered")
        self.assertTrue(order["delivered_timestamp"])
        self.assertEqual(transaction["order_status"], "Delivered")
        self.assertTrue(transaction["delivered_timestamp"])
        self.assertEqual(log_event.call_args.args[4], "order_delivered")

    def test_cancellation_refunds_and_restores_stock(self):
        response, log_event = self.status("cancel")

        with closing(self.open_database()) as con:
            order = con.execute("SELECT * FROM orders").fetchone()
            transaction = con.execute("SELECT * FROM transactions").fetchone()
            stock = con.execute("SELECT current_stock FROM inventory").fetchone()[0]
            movement = con.execute("SELECT * FROM inventory_movements").fetchone()
        self.assertEqual(response.status_code, 303)
        self.assertEqual(order["order_status"], "Cancelled")
        self.assertEqual(order["payment_status"], "Refunded")
        self.assertEqual(transaction["order_status"], "Cancelled")
        self.assertEqual(transaction["payment_status"], "Refunded")
        self.assertEqual(transaction["net_item_value"], 0)
        self.assertEqual(stock, 5)
        self.assertEqual(movement["movement_type"], "CANCELLATION")
        self.assertEqual(movement["quantity_delta"], 2)
        self.assertEqual(log_event.call_args.args[4], "order_cancelled")


if __name__ == "__main__":
    unittest.main()
