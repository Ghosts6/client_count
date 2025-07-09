# Complete mapping from short form to full building name (from doc/db/building.txt)
SHORT_TO_FULL_BUILDING = {
    "ace": "Accolade Building East",
    "acw": "Accolade Building West",
    "ao": "Archives of Ontario",
    "atk": "Atkinson",
    "bc": "Norman Bethune College",
    "bcss": "Bennett Centre for Student Services",
    "brg": "Bergeron Centre for Engineering Excellence",
    "bsb": "Behavioural Sciences Building",
    "bu": "Burton Auditorium",
    "cb": "Chemistry Building",
    "cc": "Calumet College",
    "cfa": "Joan & Martin Goldfarb Centre for Fine Arts",
    "cft": "Centre for Film and Theatre / Joseph F. Green Studio Theatre",
    "clh": "Curtis Lecture Halls",
    "csq": "Central Square",
    "cub": "Central Utilities Building",
    "db": "Victor Phillip Dahdaleh Building",
    "elc": "Executive Learning Centre",
    "fan": "Founders Annex North",
    "fas": "Founders Annex South",
    "fc": "Founders College",
    "frq": "Farquharson Life Sciences",
    "gh": "Glendon Hall",
    "hc": "Lorna R. Marsden Honour Court & Welcome Centre",
    "hne": "Health, Nursing and Environmental Studies Building",
    "hr": "Hilliard Residence",
    "k": "Kinsmen Building",
    "kt": "Kaneff Tower",
    "las": "Lassonde Building",
    "lmp": "LA&PS @ IBM (Markham campus)",
    "lsb": "Life Sciences Building",
    "lum": "Lumbers Building",
    "mb": "Rob & Cheryl McEwen Graduate Study & Research Building",
    "mc": "McLaughlin College",
    "oc": "Off Campus",
    "osg": "Ignat Kaneff Building (Osgoode Hall Law School)",
    "prb": "Physical Resources Building",
    "pse": "Petrie Science & Engineering Building",
    "ross": "Ross Building",
    "say": "Seneca @ York (Stephen E. Quinlan Building)",
    "sc": "Stong College",
    "scl": "Scott Library",
    "shr": "Sherman Health Science Research Centre",
    "slh": "Stedman Lecture Halls",
    "ssb": "Seymour Schulich Building",
    "ssc": "Second Student Centre",
    "stc": "First Student Centre",
    "stl": "Steacie Science & Engineering Library",
    "tc": "Tennis Canada – Sobeys Stadium",
    "tfc": "Track & Field Centre",
    "tm": "Tait McKenzie Centre",
    "vc": "Vanier College",
    "vh": "Vari Hall",
    "wc": "Winters College",
    "wob": "West Office Building",
    "wsc": "William Small Centre",
    "yh": "York Hall",
    "yl": "York Lanes",
    # Add common campus-specific short forms
    "studc": "Student Centre",
    "beth": "Bethune Residence",
    "as380": "Atkinson",
    "tel": "Victor Phillip Dahdaleh",
    "psci": "Petrie Science and Engineering",
    "scott": "Scott Library",
    "vanier": "Vanier College",
    "winters": "Winters College",
    "lumbers": "Lumbers",
    "life": "Life Sciences",
    "pond": "Pond Road Residence",
    "osgoode": "Osgoode",
    "tait": "Tait Mackenzie",
    "st": "Stong College",
}

# Mapping for floor/area tokens
FLOOR_MAP = {
    "b": "Basement",
    "g": "Ground",
    "f": "Floor",
    "r": "Room",
    "fl": "Floor",
    "bsmt": "Basement",
    "gr": "Ground",
}

# Canonical building names from wireless_count DB (first 55 lines of building.txt)
CANONICAL_BUILDING_NAMES = {
    'Lab',
    'Lassonde',
    'Life Sciences',
    'Lumbers',
    'McEwen',
    'McLaughlin College',
    'Northwest Gate',
    'Osgoode',
    'PSI - Parking Structure I (BCSS)',
    'Passy 10',
    'Passy 12',
    'Passy 14',
    'Passy 16',
    'Passy 18',
    'Passy 2',
    'Passy 4',
    'Passy 6',
    'Passy 8',
    'Petrie Science and Engineering',
    'Physical Resources',
    'Pond Road Residence',
    'Ross',
    'School of Continuing Studies',
    'Scott Library',
    'Scott Religious Centre',
    'Second Student Centre',
    'Seymour Schulich',
    'Sherman',
    'Shoreham',
    'Steacie - DataCentre040',
    'Steacie Lab',
    'Steacie Science and Engineering',
    'Stedman Lecture Halls',
    'Stong College',
    'Stong Residence',
    'Student Centre',
    'Tait Mackenzie',
    'Tatham Residence',
    'Toronto Track and Field Centre',
    'Vanier College',
    'Vanier Residence',
    'Vari Hall',
    'Victor Phillip Dahdaleh',
    'Victor Phillip Dahdaleh - DataCentre5028',
    'West Office',
    'William Small',
    'Winters College',
    'Winters Residence',
    'York Lanes',
    'York Lions Stadium',
    'York Stadium',
    'Anees - Test',
    'Goldfarb Gallery',
    'Keele Campus',
}

def normalize_building_name(name):
    """
    Normalize and map a building name or short form to the canonical name in the DB.
    Returns the canonical name if found, else None.
    """
    if not name or not isinstance(name, str):
        return None
    name = name.strip()
    # Try direct match
    if name in CANONICAL_BUILDING_NAMES:
        return name
    # Try case-insensitive match
    for canon in CANONICAL_BUILDING_NAMES:
        if name.lower() == canon.lower():
            return canon
    # Try mapping from short form
    mapped = SHORT_TO_FULL_BUILDING.get(name.lower())
    if mapped and mapped in CANONICAL_BUILDING_NAMES:
        return mapped
    # Try partial/contains match (for common variants)
    for canon in CANONICAL_BUILDING_NAMES:
        if name.lower() in canon.lower() or canon.lower() in name.lower():
            return canon
    # Try removing common suffixes/variants
    for canon in CANONICAL_BUILDING_NAMES:
        if name.lower().replace('building', '').strip() == canon.lower().replace('building', '').strip():
            return canon
    return None

def parse_ap_name_for_location(ap_name):
    """
    Parse AP name like 'k388-studc-b-1' to infer building and floor/area.
    Returns (building, floor/area, ap_number) or (None, None, None) if not parseable.
    """
    if not ap_name or not isinstance(ap_name, str):
        return None, None, None
    parts = ap_name.lower().split('-')
    if len(parts) < 4:
        return None, None, None
    # Example: k388-studc-b-1
    _, short_building, floor_token, ap_number = parts[:4]
    building = SHORT_TO_FULL_BUILDING.get(short_building, short_building.title())
    floor = FLOOR_MAP.get(floor_token, floor_token.title())
    return building, floor, ap_number 