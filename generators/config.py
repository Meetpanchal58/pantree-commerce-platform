"""Configuration for the stateful Pantree ecommerce simulator."""
from pathlib import Path
import os
from datetime import date

SEED = int(os.getenv("PANTREE_SEED", "42"))
N_CUSTOMERS = int(os.getenv("PANTREE_CUSTOMERS", "30000"))
N_PRODUCTS = int(os.getenv("PANTREE_PRODUCTS", "10000"))
N_SELLERS = int(os.getenv("PANTREE_SELLERS", "400"))

DATA_START = os.getenv("PANTREE_DATA_START", "2025-01-01")
# DATA_END is the simulation horizon. By default it is today; PANTREE_TODAY is
# provided as a reproducible testing/CI override.
DATA_END = os.getenv("PANTREE_TODAY", date.today().isoformat())

GEO = {
    "West": {"Maharashtra": ["Mumbai", "Pune", "Nagpur"], "Gujarat": ["Ahmedabad", "Surat"]},
    "North": {"Delhi": ["New Delhi"], "Uttar Pradesh": ["Lucknow", "Noida"], "Punjab": ["Chandigarh"]},
    "South": {"Karnataka": ["Bengaluru", "Mysuru"], "Tamil Nadu": ["Chennai", "Coimbatore"], "Telangana": ["Hyderabad"], "Kerala": ["Kochi"]},
    "East": {"West Bengal": ["Kolkata"], "Odisha": ["Bhubaneswar"], "Bihar": ["Patna"]},
}

ACQUISITION_CHANNELS = ["Organic", "Paid Search", "Social Ads", "Affiliate", "Email/CRM", "Referral", "Direct", "Display/DSP"]
CHANNEL_QUALITY = {"Referral": 1.35, "Organic": 1.25, "Direct": 1.10, "Email/CRM": 1.05, "Paid Search": 0.95, "Affiliate": 0.85, "Display/DSP": 0.70, "Social Ads": 0.65}
ACQ_WEIGHTS = [0.22, 0.15, 0.18, 0.08, 0.07, 0.10, 0.12, 0.08]

AGE_BRACKETS = ["18-24", "25-34", "35-44", "45-54", "55+"]
AGE_WEIGHTS = [0.20, 0.38, 0.24, 0.12, 0.06]
GENDERS = ["M", "F", "Other"]
GENDER_WEIGHTS = [0.55, 0.43, 0.02]

SELLER_TYPES = ["1P", "3P-FBA", "3P-MFN"]
SELLER_TYPE_WEIGHTS = [0.20, 0.45, 0.35]

DEVICES = ["Android App", "iOS App", "Mobile Web", "Desktop Web"]
DEVICE_WEIGHTS = [0.45, 0.22, 0.20, 0.13]
TRAFFIC_SOURCES = ["organic", "paid_search", "social", "email", "direct", "affiliate"]

EVENT_TYPES = ["session_start", "home_view", "category_view", "search", "plp_view", "pdp_view", "filter_apply", "sort_apply", "add_to_cart", "wishlist_add", "remove_from_cart", "checkout_start", "payment_attempt", "payment_failed", "purchase", "return_completed", "replacement_completed", "session_end"]

# Canonical article-type relationships. Only relationships whose target exists in the catalog are used.
COMPLEMENTS = {
    "Tshirts": ["Jeans", "Track Pants", "Casual Shoes", "Caps"],
    "Shirts": ["Jeans", "Trousers", "Belts", "Formal Shoes"],
    "Tops": ["Jeans", "Handbags", "Heels"],
    "Kurtas": ["Leggings", "Sandals", "Earrings"],
    "Kurtis": ["Leggings", "Sandals"],
    "Dresses": ["Heels", "Handbags", "Sunglasses"],
    "Jeans": ["Tshirts", "Shirts", "Belts", "Casual Shoes"],
    "Trousers": ["Shirts", "Formal Shoes", "Belts"],
    "Track Pants": ["Tshirts", "Sports Shoes"],
    "Casual Shoes": ["Socks", "Jeans"],
    "Sports Shoes": ["Socks", "Track Pants"],
    "Formal Shoes": ["Socks", "Belts", "Shirts"],
    "Sandals": ["Kurtas"], "Heels": ["Dresses", "Handbags"],
    "Watches": ["Sunglasses", "Belts", "Wallets"],
    "Handbags": ["Sunglasses", "Wallets"], "Backpacks": ["Caps"],
    "Sunglasses": ["Watches", "Caps"], "Belts": ["Wallets", "Formal Shoes"],
    "Wallets": ["Belts"], "Earrings": ["Kurtas"], "Sarees": ["Handbags", "Earrings"],
    "Perfume and Body Mist": ["Watches", "Handbags"], "Lipstick": ["Handbags"],
}

FESTIVALS = {
    "2024-01": ("New Year", 1.15), "2024-03": ("Holi", 1.12), "2024-08": ("Independence Day", 1.15),
    "2024-10": ("Festive/Diwali", 1.55), "2024-11": ("Festive/Diwali", 1.35), "2024-12": ("Year End", 1.20),
    "2025-01": ("Republic Day", 1.18), "2025-03": ("Holi", 1.12), "2025-04": ("Summer Sale", 1.10),
    "2025-05": ("Summer Sale", 1.18), "2025-06": ("End of Season Sale", 1.30),
}

SHOCK_DATE = "2025-02-01"
