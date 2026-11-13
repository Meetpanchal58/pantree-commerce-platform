"""Cities for the sign-up form: the same 24 cities as the shop's simulated customers.

Kept here so the web app does not need the ecommerce-simulator package at runtime;
tests/test_geo.py checks it still matches ecommerce_sim.reference.CITIES.
"""

GEO = {
    "East": {
        "Assam": ["Guwahati"],
        "Bihar": ["Patna"],
        "Jharkhand": ["Ranchi"],
        "Odisha": ["Bhubaneswar"],
        "West Bengal": ["Kolkata"],
    },
    "North": {
        "Delhi": ["New Delhi"],
        "Punjab": ["Chandigarh"],
        "Rajasthan": ["Jaipur"],
        "Uttar Pradesh": ["Lucknow", "Noida"],
        "Uttarakhand": ["Dehradun"],
    },
    "South": {
        "Andhra Pradesh": ["Vijayawada"],
        "Karnataka": ["Bengaluru", "Mysuru"],
        "Kerala": ["Kochi"],
        "Tamil Nadu": ["Chennai", "Coimbatore"],
        "Telangana": ["Hyderabad"],
    },
    "West": {
        "Gujarat": ["Ahmedabad", "Surat"],
        "Madhya Pradesh": ["Indore"],
        "Maharashtra": ["Mumbai", "Nagpur", "Pune"],
    },
}
