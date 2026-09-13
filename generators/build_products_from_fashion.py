"""
build_products_from_fashion.py
------------------------------
Builds a REAL, BALANCED 10k-product product_master from the Kaggle fashion dataset
(3 pillars: Apparel, Footwear, Accessories), copies the matching real images, and
writes a fashion complement map (for real Frequently-Bought-Together).

Called by build_final_dataset.py. Point FASHION_DIR at the extracted dataset
(the folder containing styles.csv and images/).
"""
import os, glob, json, shutil, sys
from pathlib import Path
import pandas as pd, numpy as np

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"; DATA.mkdir(exist_ok=True)
IMG_OUT = ROOT / "webapp" / "static" / "products"
SEED = 42

# balance target (≈10k)
TARGET = {"Apparel": 4500, "Footwear": 2500, "Accessories": 3000}
CAP_PER_ARTICLETYPE = 0.08     # no articleType > 8% of catalogue
MIN_PER_ARTICLETYPE = 40       # each kept type has >= this many (density for recs)

# fashion complement map at articleType level (real FBT affinity)
FASHION_COMPLEMENTS = {
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
    "Formal Shoes": ["Socks", "Belts", "Formal Shirts"],
    "Sandals": ["Kurtas"],
    "Heels": ["Dresses", "Handbags"],
    "Watches": ["Sunglasses", "Belts", "Wallets"],
    "Handbags": ["Sunglasses", "Wallets"],
    "Backpacks": ["Caps"],
    "Sunglasses": ["Watches", "Caps"],
    "Belts": ["Wallets", "Formal Shoes"],
    "Wallets": ["Belts"],
    "Earrings": ["Kurtas"],
}

def find(path, name):
    hits = glob.glob(os.path.join(path, "**", name), recursive=True)
    if not hits: sys.exit(f"could not find {name} under {path}")
    return hits[0]

def build(fashion_dir):
    r = np.random.default_rng(SEED)
    styles = find(fashion_dir, "styles.csv")
    imgdir = find(fashion_dir, "images")
    df = pd.read_csv(styles, on_bad_lines="skip").dropna(
        subset=["id","productDisplayName","masterCategory","articleType","baseColour"])
    df["id"] = df["id"].astype(int)
    have = {int(os.path.splitext(f)[0]) for f in os.listdir(imgdir) if f.endswith(".jpg")}
    df = df[df["id"].isin(have)]
    df = df[df["masterCategory"].isin(TARGET)].reset_index(drop=True)

    # ---- balanced sampling per pillar with per-articleType cap + floor ----
    total_target = sum(TARGET.values())
    cap_n = int(CAP_PER_ARTICLETYPE * total_target)
    picked = []
    for pillar, want in TARGET.items():
        pool = df[df.masterCategory == pillar]
        # keep articleTypes with enough products
        vc = pool.articleType.value_counts()
        good_types = vc[vc >= MIN_PER_ARTICLETYPE].index.tolist()
        pool = pool[pool.articleType.isin(good_types)]
        # round-robin across articleTypes so no single type dominates
        per_type = {}
        chosen = []
        types = good_types[:]
        r.shuffle(types)
        i = 0
        while len(chosen) < want and types:
            t = types[i % len(types)]
            avail = pool[(pool.articleType == t)]
            taken = per_type.get(t, 0)
            if taken < min(cap_n, len(avail)):
                row = avail.iloc[taken]
                chosen.append(row); per_type[t] = taken + 1
            else:
                types.remove(t); i -= 1
            i += 1
        picked.append(pd.DataFrame(chosen))
    cat = pd.concat(picked).reset_index(drop=True)
    if len(cat) > total_target:
        cat = cat.sample(total_target, random_state=SEED).reset_index(drop=True)
    n = len(cat)
    print(f"balanced catalogue: {n} products")

    # ---- assign product_ids, realistic prices per articleType, cost, rating ----
    cat["product_id"] = [f"prod_{i:05d}" for i in range(1, n+1)]
    # price bands (INR) by pillar/type — realistic fashion pricing
    def price_for(row):
        base = {"Apparel": 900, "Footwear": 2200, "Accessories": 1500}[row.masterCategory]
        prem = {"Watches": 3.0, "Handbags": 2.2, "Heels": 1.6, "Formal Shoes": 1.8,
                "Sunglasses": 1.7, "Sports Shoes": 1.9, "Jewellery": 2.0}.get(row.articleType, 1.0)
        return base * prem
    anchor = cat.apply(price_for, axis=1).to_numpy()
    mrp = np.round(anchor * r.uniform(0.7, 1.8, n) / 10) * 10
    mrp = np.clip(mrp, 199, 24999)
    cost = np.round(mrp * r.uniform(0.45, 0.70, n), 2)
    mop = np.round(np.maximum(mrp * (1 - r.uniform(0, 0.40, n)), cost * 1.05), 2)
    mop = np.minimum(mop, mrp)

    # ---- brand from productDisplayName first token ----
    brand = cat.productDisplayName.str.split().str[0].fillna("Generic")

    # ---- copy real images renamed to product_id ----
    IMG_OUT.mkdir(parents=True, exist_ok=True)
    for f in IMG_OUT.glob("*.jpg"): f.unlink()
    imgs = []
    for pid, sid in zip(cat.product_id, cat.id):
        src = os.path.join(imgdir, f"{sid}.jpg")
        if os.path.exists(src):
            shutil.copy(src, IMG_OUT / f"{pid}.jpg"); imgs.append(f"/static/products/{pid}.jpg")
        else: imgs.append("")

    # ---- assemble product_master in the SAME column shape as before ----
    attrs = [json.dumps({"colour": c, "gender": g, "type": a, "usage": u, "season": s})
             for c,g,a,u,s in zip(cat.baseColour, cat.gender, cat.articleType,
                                   cat.get("usage","").astype(str), cat.get("season","").astype(str))]
    seller_ids = [f"sell_{r.integers(1,401):04d}" for _ in range(n)]
    pm = pd.DataFrame({
        "product_id": cat.product_id,
        "product_name": cat.productDisplayName.astype(str),
        "h1_category": cat.masterCategory,
        "h2_category": cat.subCategory,
        "h3_category": cat.articleType,
        "h4_colour": cat.baseColour,
        "brand_name": brand,
        "gender": cat.gender,
        "seller_id": seller_ids,
        "mrp": mrp, "current_mop": mop, "unit_cost": cost,
        "weight_grams": r.integers(80, 1500, n),
        "launch_date": pd.to_datetime("2021-01-01") + pd.to_timedelta(r.integers(0,365*4,n), unit="D"),
        "is_prime_eligible": r.choice([True, False], n, p=[0.7,0.3]),
        "avg_rating": np.round(r.normal(4.0,0.5,n).clip(1.0,5.0),1),
        "product_description": [f"{nm} — {col} {art}. {g}'s {us} wear."
            for nm,col,art,g,us in zip(cat.productDisplayName, cat.baseColour, cat.articleType,
                                       cat.gender, cat.get("usage","").astype(str))],
        "product_attributes": attrs,
        "local_image_url": imgs,
        "image_search_url": ["" for _ in range(n)],
        "placeholder_image_url": ["" for _ in range(n)],
    })
    pm.launch_date = pm.launch_date.dt.date
    # plant a little missingness (same as before)
    miss = r.choice(n, int(n*0.03), replace=False); pm.loc[miss,"avg_rating"] = np.nan
    pm.to_parquet(DATA/"product_master.parquet", index=False)

    # complement map resolved to article types actually present
    present = set(pm.h3_category.unique())
    comp = {k:[v for v in vs if v in present] for k,vs in FASHION_COMPLEMENTS.items() if k in present}
    (DATA/"_fashion_complements.json").write_text(json.dumps(comp))

    print(f"product_master: {len(pm)} rows | images copied: {len(list(IMG_OUT.glob('*.jpg')))}")
    print("pillars:", pm.h1_category.value_counts().to_dict())
    print("top article types:", pm.h3_category.value_counts().head(10).to_dict())
    return pm

if __name__ == "__main__":
    fd = sys.argv[1] if len(sys.argv) > 1 else r"PUT_FASHION_DATASET_PATH"
    build(fd)
