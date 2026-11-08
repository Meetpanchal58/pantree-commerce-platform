from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from recommendations.candidates import collaborative, fbt, popularity
from recommendations.candidates.common import gender_is_compatible
from recommendations.serve import SURFACE_CONFIG, SUB_SURFACES, _similar_fallback


class RecommendationSafetyTests(unittest.TestCase):
    def test_popularity_and_ranker_share_clickstream_load(self):
        from recommendations.candidates import common

        events = pd.DataFrame(
            [
                ("customer-1", "product-1", "pdp_view", "2026-01-01"),
                ("customer-1", "product-2", "add_to_cart", "2026-01-02"),
                ("customer-2", "product-1", "purchase", "2026-01-03"),
                ("customer-2", None, "pdp_view", "2026-01-04"),
            ],
            columns=["customer_id", "product_id", "event_type", "event_timestamp"],
        )
        common.read_clickstream_activity.cache_clear()
        common.read_event_counts.cache_clear()
        popularity._activity_events.cache_clear()
        popularity._recent_product_scores.cache_clear()
        try:
            with patch.object(common.pd, "read_parquet", return_value=events) as read:
                activity = popularity._activity_events()
                customer_counts, product_counts = common.read_event_counts()

            read.assert_called_once()
            self.assertEqual(activity["product_id"].tolist(), ["product-1", "product-2"])
            self.assertEqual(customer_counts.to_dict(), {"customer-1": 2})
            self.assertEqual(product_counts.to_dict(), {"product-1": 1, "product-2": 1})
        finally:
            common.read_clickstream_activity.cache_clear()
            common.read_event_counts.cache_clear()
            popularity._activity_events.cache_clear()
            popularity._recent_product_scores.cache_clear()

    def test_stock_reader_keeps_latest_row_and_handles_missing_dates(self):
        from recommendations.candidates import common

        stock = pd.DataFrame(
            [
                ("2026-01-01", "dated", 1),
                ("2026-01-02", "dated", 2),
                ("2026-01-02", "dated", 3),
                (None, "undated", 4),
                (None, "undated", 5),
            ],
            columns=["inventory_date", "product_id", "closing_stock"],
        )
        common.read_stock.cache_clear()
        try:
            with patch.object(common.pd, "read_parquet", return_value=stock):
                result = common.read_stock()
        finally:
            common.read_stock.cache_clear()

        self.assertEqual(result.to_dict(), {"dated": 3, "undated": 5})

    def test_homepage_warmup_loads_catalog_and_inventory(self):
        from recommendations import serve

        with (
            patch.object(serve, "read_products") as read_products,
            patch.object(serve, "read_stock") as read_stock,
            patch.object(serve.popularity, "warm_popularity") as warm_popularity,
            patch.object(serve, "_load_ranker", return_value=object()),
            patch.object(serve, "read_event_counts") as read_event_counts,
        ):
            serve.warm_homepage_caches()

        read_products.assert_called_once_with()
        read_stock.assert_called_once_with()
        warm_popularity.assert_not_called()
        read_event_counts.assert_not_called()

    def test_cold_homepage_recommendations_skip_clickstream_ranker_load(self):
        from recommendations import serve

        product = pd.DataFrame(
            [
                {
                    "product_id": "product-1",
                    "product_name": "Test shirt",
                    "image_url": None,
                    "h1_category": "Apparel",
                    "h2_category": "Shirts",
                    "h3_category": "Casual shirts",
                    "brand_name": "Test",
                    "current_price": 100,
                    "gender": "Unisex",
                    "is_active": True,
                }
            ]
        ).set_index("product_id", drop=False)
        config = {
            "candidate_sources": [
                (
                    "test",
                    lambda **_: [
                        {"product_id": "product-1", "score": 0.5, "source": "test"}
                    ],
                    1,
                )
            ],
            "fusion_weights": {"test": 1.0},
            "max_candidates": 1,
            "top_k": 1,
            "exclude_purchased": False,
        }
        from recommendations.candidates.common import read_event_counts

        read_event_counts.cache_clear()
        try:
            with (
                patch.object(serve, "read_products", return_value=product),
                patch.object(
                    serve,
                    "read_stock",
                    return_value=pd.Series({"product-1": 2}),
                ),
                patch.object(serve, "_load_ranker") as load_ranker,
            ):
                items = serve._recommend_from_config(
                    None, "homepage", None, {}, config
                )
        finally:
            read_event_counts.cache_clear()

        self.assertEqual(len(items), 1)
        load_ranker.assert_not_called()

    def test_pdp_keeps_homepage_product_actions_on_every_recommendation_card(self):
        template = (
            Path(__file__).resolve().parents[1]
            / "webapp"
            / "templates"
            / "pdp.html"
        ).read_text(encoding="utf-8")
        for expected in (
            'class="wishlist-button',
            'className = "card product-card recommendation-card"',
            'rating.className = "rating"',
            'prices.className = "recommendation-prices"',
            'button.className = "btn add-cart-button recommendation-cart"',
            "applyRecommendationCartState(card, Number(item.cart_quantity || 0))",
        ):
            self.assertIn(expected, template)

    def test_recommendation_cards_do_not_show_explanation_text(self):
        root = Path(__file__).resolve().parents[1]
        for template_path in (
            root / "webapp" / "templates" / "home.html",
            root / "webapp" / "templates" / "pdp.html",
        ):
            template = template_path.read_text(encoding="utf-8")
            self.assertNotIn("recommendation-reason", template)

    def test_pdp_recommendations_return_the_full_candidate_pool(self):
        self.assertEqual(SURFACE_CONFIG["product"]["top_k"], 100)
        self.assertEqual(SUB_SURFACES["pdp-fbt"]["top_k"], 50)
        self.assertEqual(SUB_SURFACES["pdp-fbt"]["max_candidates"], 50)
        self.assertEqual(SUB_SURFACES["pdp-similar"]["top_k"], 100)
        self.assertEqual(SUB_SURFACES["pdp-similar"]["max_candidates"], 100)

    def test_gender_compatibility_rejects_mismatched_audience(self):
        self.assertTrue(gender_is_compatible("Men", "Men"))
        self.assertTrue(gender_is_compatible("Men", "Unisex"))
        self.assertTrue(gender_is_compatible("Men", "All"))
        self.assertFalse(gender_is_compatible("Men", "Women"))
        self.assertFalse(gender_is_compatible("Women", "Men"))

    def test_similar_fallback_keeps_category_gender_and_stock_constraints(self):
        catalog = pd.DataFrame(
            [
                ("anchor", "Night suits", "Men", True, 4.0, 10),
                ("men-item", "Night suits", "Men", True, 4.5, 20),
                ("unisex-item", "Night suits", "Unisex", True, 4.0, 15),
                ("women-item", "Night suits", "Women", True, 5.0, 50),
                ("out-of-stock", "Night suits", "Men", True, 5.0, 50),
                ("other-category", "Shirts", "Men", True, 5.0, 50),
            ],
            columns=[
                "product_id", "h3_category", "gender", "is_active",
                "avg_rating", "review_count",
            ],
        ).set_index("product_id", drop=False)
        catalog["product_name"] = catalog.index
        catalog["image_url"] = None
        catalog["brand_name"] = "Test"
        catalog["current_price"] = 100
        stock = pd.Series(
            {"anchor": 2, "men-item": 3, "unisex-item": 4, "women-item": 5,
             "out-of-stock": 0, "other-category": 2}
        )

        candidates = _similar_fallback(
            "anchor", catalog, stock, {"anchor"}, limit=8
        )

        self.assertEqual(
            {item["product_id"] for item in candidates},
            {"men-item", "unisex-item"},
        )
        self.assertTrue(all(item["source"] == "similar_fallback" for item in candidates))

    def test_live_activity_seed_uses_same_audience_products(self):
        products = pd.DataFrame(
            [
                ("seed", "Night suits", "Men", "Viewed lounge set", 100, 100, 100),
                ("men-item", "Night suits", "Men", "Men's lounge set", 200, 200, 20),
                ("unisex-item", "Night suits", "Unisex", "Unisex lounge set", 180, 180, 15),
                ("women-item", "Night suits", "Women", "Women's lounge set", 150, 100, 500),
            ],
            columns=[
                "product_id", "h3_category", "gender", "product_name",
                "mrp", "current_price", "review_count",
            ],
        ).set_index("product_id", drop=False)
        empty_events = pd.DataFrame(
            columns=["customer_id", "product_id", "event_type", "event_timestamp", "weight"]
        )
        recent_scores = pd.Series(
            {"men-item": 10.0, "unisex-item": 5.0, "women-item": 100.0}
        )
        with (
            patch.object(popularity, "_activity_events", return_value=empty_events),
            patch.object(popularity, "_recent_product_scores", return_value=recent_scores),
            patch.object(popularity, "read_products", return_value=products),
        ):
            candidates = popularity.get_personalized(
                "new-customer", recent_product_ids=["seed"]
            )

        self.assertEqual(
            {item["product_id"] for item in candidates},
            {"men-item", "unisex-item"},
        )
        self.assertTrue(all(item["source"] == "recent_activity" for item in candidates))

    def test_cold_als_lookup_does_not_train_inside_the_request(self):
        with (
            patch.object(collaborative, "_MODEL_READY", False),
            patch.object(collaborative, "_fit_model") as fit_model,
        ):
            self.assertEqual(
                collaborative.get_collaborative("unknown-customer"), []
            )
        fit_model.assert_not_called()


class FrequentlyBoughtTogetherTests(unittest.TestCase):
    @staticmethod
    def product_rows():
        return pd.DataFrame(
            [
                ("anchor", "Apparel", "Night suits", "Men", True, 4.0, 10),
                ("bought-with", "Apparel", "Lounge Pants", "Men", True, 4.5, 20),
                ("pairing", "Apparel", "Lounge Pants", "Men", True, 4.5, 20),
            ],
            columns=[
                "product_id", "h1_category", "h3_category", "gender",
                "is_active", "avg_rating", "review_count",
            ],
        ).set_index("product_id", drop=False)

    def test_same_order_purchase_evidence_takes_priority(self):
        orders = pd.DataFrame(
            [
                ("anchor", "order-1"),
                ("bought-with", "order-1"),
                ("unrelated", "order-2"),
            ],
            columns=["product_id", "order_id"],
        )
        affinity = pd.DataFrame(
            columns=[
                "product_id", "related_product_id", "affinity_type",
                "affinity_score", "co_purchase_count", "co_view_count",
            ]
        )
        with (
            patch.object(fbt, "read_realized_transactions", return_value=orders),
            patch.object(fbt, "realized_transactions_ready", return_value=True),
            patch.object(fbt, "_affinity", return_value=affinity),
            patch.object(fbt, "read_products", return_value=self.product_rows()),
            patch.object(
                fbt,
                "read_stock",
                return_value=pd.Series({"anchor": 2, "bought-with": 3, "pairing": 1}),
            ),
        ):
            candidates = fbt.get_frequently_bought_together(["anchor"])

        self.assertEqual([item["product_id"] for item in candidates], ["bought-with"])
        self.assertEqual(candidates[0]["source"], "fbt")
        self.assertEqual(candidates[0]["reason"], "Related product pairing")

    def test_curated_pairing_fills_missing_purchase_and_coview_edges(self):
        products = self.product_rows().drop(index="bought-with")
        empty_orders = pd.DataFrame(columns=["product_id", "order_id"])
        empty_affinity = pd.DataFrame(
            columns=[
                "product_id", "related_product_id", "affinity_type",
                "affinity_score", "co_purchase_count", "co_view_count",
            ]
        )
        with (
            patch.object(fbt, "read_realized_transactions", return_value=empty_orders),
            patch.object(fbt, "realized_transactions_ready", return_value=False),
            patch.object(fbt, "_affinity", return_value=empty_affinity),
            patch.object(fbt, "read_products", return_value=products),
            patch.object(
                fbt,
                "read_stock",
                return_value=pd.Series({"anchor": 2, "bought-with": 3, "pairing": 1}),
            ),
        ):
            candidates = fbt.get_frequently_bought_together(["anchor"])

        self.assertEqual([item["product_id"] for item in candidates], ["pairing"])
        self.assertEqual(candidates[0]["source"], "curated_complement")

    def test_co_view_pairing_precedes_curated_fallback(self):
        affinity = pd.DataFrame(
            [
                ("anchor", "pairing", "complementary_product", 0.8, 0, 5),
            ],
            columns=[
                "product_id", "related_product_id", "affinity_type",
                "affinity_score", "co_purchase_count", "co_view_count",
            ],
        )
        with (
            patch.object(
                fbt,
                "read_realized_transactions",
                return_value=pd.DataFrame(columns=["product_id", "order_id"]),
            ),
            patch.object(fbt, "realized_transactions_ready", return_value=True),
            patch.object(fbt, "_affinity", return_value=affinity),
            patch.object(fbt, "read_products", return_value=self.product_rows()),
            patch.object(
                fbt,
                "read_stock",
                return_value=pd.Series({"anchor": 2, "bought-with": 3, "pairing": 1}),
            ),
        ):
            candidates = fbt.get_frequently_bought_together(["anchor"])

        self.assertEqual([item["product_id"] for item in candidates], ["pairing"])
        self.assertEqual(candidates[0]["source"], "session_pairing")


if __name__ == "__main__":
    unittest.main()
