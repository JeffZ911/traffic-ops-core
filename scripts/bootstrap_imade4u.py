"""Seed the imade4u keyword pool — curated personalized-gift topics across the
6 content pillars, grounded in the store's real product types. Each row's
`notes` encodes how the content pipeline should generate it:
    type=<pillar>|match=<product keywords>|tags=<article tags>

Idempotent: dedupes on (site_id, keyword). Run once to seed; keyword_gardener
tops up later.

Usage: python -m scripts.bootstrap_imade4u
"""
from __future__ import annotations

from dotenv import load_dotenv
from src.db.client import get_db_connection

load_dotenv()

# (topic, product-match keywords, article tags, pillar, priority)
SEEDS: list[tuple[str, str, str, str, int]] = [
    # ── Occasion guides (the QDF / seasonal engine) ──
    ("Personalized Mother's Day Jewelry: Meaningful Custom Gifts for Mom", "necklace,bracelet,jewelry,custom necklace", "mothers-day,gifts-for-mom,jewelry", "occasion_guide", 85),
    ("Personalized Valentine's Day Gifts She'll Treasure Forever", "necklace,bracelet,couple,photo", "valentines-day,gifts-for-her,jewelry", "occasion_guide", 84),
    ("Custom Anniversary Gifts to Celebrate Your Love Story", "necklace,coordinate,photo,custom", "anniversary,gifts-for-her,jewelry", "occasion_guide", 83),
    ("Personalized Christmas Gifts the Whole Family Will Love", "ornament,mug,stocking,custom", "christmas,family-gifts,home-decor", "occasion_guide", 82),
    ("Thoughtful Personalized Father's Day Gifts for Every Dad", "keychain,wallet,mug,wooden sign", "fathers-day,gifts-for-him", "occasion_guide", 80),
    ("Custom Birthday Gifts That Feel Personal and Special", "necklace,mug,keychain,custom", "birthday,personalized-gifts", "occasion_guide", 76),
    ("Personalized Wedding Gifts for the Happy Couple", "couple,coordinate,canvas,custom", "wedding,couples-gifts", "occasion_guide", 74),
    ("Custom Graduation Gifts to Celebrate Their Big Day", "keychain,necklace,frame,custom", "graduation,personalized-gifts", "occasion_guide", 72),
    ("Personalized New Baby Gifts for the Growing Family", "baby,onesie,blanket,custom", "new-baby,baby-shower", "occasion_guide", 70),

    # ── Recipient guides ──
    ("Personalized Gifts for Her: Custom Ideas She'll Adore", "necklace,bracelet,jewelry,custom", "gifts-for-her,jewelry", "recipient_guide", 75),
    ("Personalized Gifts for Him: Custom Ideas with Meaning", "keychain,wallet,mug,wooden sign", "gifts-for-him", "recipient_guide", 73),
    ("Heartfelt Personalized Gifts for Mom for Any Occasion", "necklace,bracelet,mug,custom", "gifts-for-mom,jewelry", "recipient_guide", 72),
    ("Custom Gifts for Couples to Celebrate Their Bond", "couple,coordinate,canvas,custom", "couples-gifts,anniversary", "recipient_guide", 70),
    ("Personalized Gifts for Pet Lovers Who Adore Their Animals", "pet,portrait,dog,cat", "pet-lovers,pet-gifts", "recipient_guide", 74),
    ("Custom Gifts for Grandma That Honor the Family", "necklace,photo,pillow,custom", "gifts-for-grandma,family", "recipient_guide", 68),

    # ── Pet memorial & pet gifts (the standout niche) ──
    ("Pet Memorial Gift Ideas to Honor a Beloved Pet", "pet,memorial,portrait,dog,cat", "pet-memorial,sympathy,pet-gifts", "pet_memorial", 82),
    ("Custom Pet Portrait Gifts That Capture Their Personality", "pet portrait,custom,dog,cat", "pet-portrait,pet-gifts", "pet_memorial", 78),
    ("Dog Memorial Gifts to Remember a Loyal Companion", "dog,memorial,portrait,paw", "pet-memorial,dog-memorial", "pet_memorial", 76),
    ("Cat Memorial Gifts for a Cherished Feline Friend", "cat,memorial,portrait", "pet-memorial,cat-memorial", "pet_memorial", 74),
    ("Sympathy Gifts for the Loss of a Pet: Comfort & Remembrance", "pet,memorial,suncatcher,custom", "pet-memorial,sympathy", "pet_memorial", 75),

    # ── Memorial & sympathy (high-emotion, evergreen) ──
    ("Meaningful Memorial Gifts to Honor a Loved One", "memorial,photo,necklace,custom", "memorial,sympathy", "sympathy_guide", 78),
    ("Thoughtful Sympathy Gifts to Comfort the Grieving", "memorial,photo,pillow,custom", "sympathy,remembrance", "sympathy_guide", 74),
    ("Remembrance Gifts for the Loss of a Mother", "memorial,photo,necklace,jewelry", "memorial,loss-of-mother", "sympathy_guide", 72),
    ("Keepsake Gifts to Remember a Father Who Passed", "memorial,photo,wooden sign,custom", "memorial,loss-of-father", "sympathy_guide", 70),

    # ── Buying guides (bottom-funnel, high intent) ──
    ("Best Personalized Necklaces for a Meaningful Gift", "necklace,custom necklace,name necklace", "buying-guide,jewelry,necklace", "buying_guide", 72),
    ("Best Custom Keychains for a Small but Heartfelt Gift", "keychain", "buying-guide,keychain", "buying_guide", 68),
    ("Best Custom Photo Mugs for a Personal Touch", "mug,custom mug,photo mug", "buying-guide,mug", "buying_guide", 66),
    ("Best Personalized Bracelets for Every Style", "bracelet,custom bracelet", "buying-guide,jewelry,bracelet", "buying_guide", 66),
    ("Best Custom Home Decor Gifts to Personalize Any Space", "pillow,blanket,canvas,wooden sign", "buying-guide,home-decor", "buying_guide", 64),
    ("Best Photo Projection Necklaces for Hidden Memories", "photo,projection,necklace", "buying-guide,jewelry", "buying_guide", 70),

    # ── How-to / personalization tips (top-funnel) ──
    ("How to Personalize Jewelry: Ideas, Tips, and Inspiration", "necklace,bracelet,custom", "how-to,personalization,jewelry", "how_to", 62),
    ("What to Engrave on a Personalized Gift: Meaningful Ideas", "necklace,keychain,bracelet,custom", "how-to,engraving-ideas", "how_to", 60),
    ("Custom Photo Gift Ideas That Turn Memories into Keepsakes", "photo,custom,canvas,pillow", "how-to,photo-gifts", "how_to", 60),
]


def main() -> int:
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("select id from sites where domain='imade4u.com'")
        row = cur.fetchone()
        if not row:
            print("❌ imade4u.com site row missing — create the tenant first"); return 2
        site_id = str(row[0])

    inserted = 0
    with get_db_connection(autocommit=True) as conn, conn.cursor() as cur:
        for topic, match, tags, atype, prio in SEEDS:
            notes = f"type={atype}|match={match}|tags={tags}"
            cur.execute(
                """
                insert into keywords (site_id, keyword, intent, priority_score,
                                      source, notes, status)
                values (%s,%s,'commercial',%s,'imade4u_seed',%s,'planned')
                on conflict (site_id, keyword) do nothing
                """,
                (site_id, topic, prio, notes),
            )
            inserted += cur.rowcount or 0
    print(f"  ✓ imade4u: seeded {inserted} new topics ({len(SEEDS)} total in list)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
