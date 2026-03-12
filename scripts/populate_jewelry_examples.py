#!/usr/bin/env python3
"""Insert handcrafted jewelry OpenSCAD examples (rings, gems, etc.)
into the RAG database.

Usage:
    python -m scripts.populate_jewelry_examples [--dry-run]
"""

import argparse
import asyncio
import logging
import re
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from sqlalchemy import delete, select, func, text

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.openscad_example import OpenscadExample
from scripts.populate_openscad_examples import (
    get_embeddings,
    ensure_bucket,
    upload_to_storage,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SOURCE_NAME = "handcrafted"

# ── Handcrafted jewelry examples ────────────────────────────────────────────

EXAMPLES = [
    {
        "name": "Simple Band Ring",
        "category": "jewelry",
        "prompt": "simple band finger ring with rounded edges",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters (mm) ---
ring_inner_d = 18.5;    // finger diameter (US size ~8)
ring_width = 6;         // band width
ring_thickness = 2;     // wall thickness
edge_r = 0.8;           // rounded edge radius

// --- Derived ---
ring_outer_d = ring_inner_d + 2 * ring_thickness;

// --- Modules ---
module ring_profile() {
    // Rounded rectangle cross-section
    offset(r=edge_r) offset(r=-edge_r)
        square([ring_thickness, ring_width], center=true);
}

module main() {
    rotate_extrude()
        translate([ring_inner_d/2 + ring_thickness/2, 0])
            ring_profile();
}

main();
""",
    },
    {
        "name": "Signet Ring",
        "category": "jewelry",
        "prompt": "signet ring with flat oval top surface for engraving",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters (mm) ---
ring_inner_d = 18.5;
ring_width = 6;
ring_thickness = 2;
signet_width = 12;
signet_depth = 10;
signet_height = 3;
taper_height = 5;

// --- Derived ---
ring_outer_r = ring_inner_d/2 + ring_thickness;
ring_center_r = ring_inner_d/2 + ring_thickness/2;

// --- Modules ---
module band() {
    rotate_extrude()
        translate([ring_inner_d/2 + ring_thickness/2, 0])
            offset(r=0.5) offset(r=-0.5)
                square([ring_thickness, ring_width], center=true);
}

module signet_top() {
    // Flat oval on top of ring, tapered from band
    translate([0, 0, ring_outer_r + signet_height/2 - eps])
        hull() {
            // Top flat oval
            scale([signet_width/2, signet_depth/2, 0.5])
                sphere(r=1);
            // Base connects to ring
            translate([0, 0, -taper_height])
                scale([ring_thickness, ring_width/2, 0.5])
                    sphere(r=1);
        }
}

module main() {
    union() {
        band();
        // Position signet at top of ring
        translate([0, 0, 0])
            rotate([0, 0, 0])
                signet_top();
    }
}

main();
""",
    },
    {
        "name": "Diamond Gemstone",
        "category": "jewelry",
        "prompt": "brilliant cut diamond gemstone shape",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters (mm) ---
diamond_d = 8;          // girdle diameter (widest point)
crown_height = 3;       // top portion height
pavilion_height = 5;    // bottom cone height
table_ratio = 0.55;     // table width as ratio of diameter
facets = 16;            // number of main facets

// --- Derived ---
diamond_r = diamond_d / 2;
table_r = diamond_r * table_ratio;

// --- Modules ---
module pavilion() {
    // Bottom cone (pointed end)
    cylinder(r1=0, r2=diamond_r, h=pavilion_height, $fn=facets);
}

module crown() {
    // Top faceted dome with flat table
    translate([0, 0, pavilion_height - eps])
        cylinder(r1=diamond_r, r2=table_r, h=crown_height, $fn=facets);
}

module main() {
    union() {
        pavilion();
        crown();
    }
}

main();
""",
    },
    {
        "name": "Solitaire Ring with Gemstone",
        "category": "jewelry",
        "prompt": "solitaire engagement ring with prong-set gemstone diamond",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters (mm) ---
ring_inner_d = 17;      // finger diameter
ring_width = 4;
ring_thickness = 1.8;
gem_d = 6;              // gemstone diameter
gem_crown_h = 2;
gem_pavilion_h = 4;
prong_count = 6;
prong_w = 1.2;
prong_h = 5;
setting_d = 8;
setting_h = 3;

// --- Derived ---
ring_outer_r = ring_inner_d/2 + ring_thickness;
gem_r = gem_d / 2;

// --- Modules ---
module band() {
    rotate_extrude()
        translate([ring_inner_d/2 + ring_thickness/2, 0])
            offset(r=0.4) offset(r=-0.4)
                square([ring_thickness, ring_width], center=true);
}

module gemstone() {
    // Brilliant cut: cone bottom + tapered crown top
    union() {
        // Pavilion (bottom cone)
        cylinder(r1=0, r2=gem_r, h=gem_pavilion_h, $fn=16);
        // Crown (top taper)
        translate([0, 0, gem_pavilion_h - eps])
            cylinder(r1=gem_r, r2=gem_r*0.55, h=gem_crown_h, $fn=16);
    }
}

module prong(angle) {
    rotate([0, 0, angle])
        translate([setting_d/2 - prong_w/2, -prong_w/2, 0])
            cube([prong_w, prong_w, prong_h]);
}

module prong_setting() {
    // Basket setting with prongs
    difference() {
        cylinder(d=setting_d, h=setting_h);
        translate([0, 0, -eps])
            cylinder(d=setting_d - 2, h=setting_h + 2*eps);
    }
    for (i = [0 : prong_count - 1])
        prong(i * 360 / prong_count);
}

module main() {
    union() {
        band();
        // Setting on top of ring
        translate([0, 0, ring_outer_r - setting_h/2]) {
            prong_setting();
            // Gemstone seated in setting
            translate([0, 0, setting_h - gem_pavilion_h + 1])
                gemstone();
        }
    }
}

main();
""",
    },
    {
        "name": "Wedding Band with Channel Set Stones",
        "category": "jewelry",
        "prompt": "wedding band ring with channel-set gemstones around the band",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters (mm) ---
ring_inner_d = 18;
ring_width = 5;
ring_thickness = 2.5;
stone_d = 2;
stone_count = 12;
channel_depth = 1.2;
channel_width = 2.5;

// --- Derived ---
ring_outer_r = ring_inner_d/2 + ring_thickness;
ring_center_r = ring_inner_d/2 + ring_thickness/2;

// --- Modules ---
module band() {
    rotate_extrude()
        translate([ring_inner_d/2 + ring_thickness/2, 0])
            offset(r=0.5) offset(r=-0.5)
                square([ring_thickness, ring_width], center=true);
}

module channel_groove() {
    // Cut a channel around the outer circumference
    rotate_extrude()
        translate([ring_outer_r - channel_depth/2, 0])
            square([channel_depth + eps, channel_width], center=true);
}

module stones() {
    for (i = [0 : stone_count - 1])
        rotate([0, 0, i * 360 / stone_count])
            translate([ring_outer_r - stone_d/2, 0, 0])
                sphere(d=stone_d, $fn=16);
}

module main() {
    union() {
        difference() {
            band();
            channel_groove();
        }
        stones();
    }
}

main();
""",
    },
    {
        "name": "Gold Bar Ingot",
        "category": "jewelry",
        "prompt": "gold bar ingot with beveled edges and stamped text area",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters (mm) ---
bar_length = 50;
bar_width = 28;
bar_height = 12;
bevel = 3;
stamp_depth = 0.5;
stamp_w = 30;
stamp_h = 15;

// --- Modules ---
module beveled_bar() {
    // Trapezoidal cross-section gold bar
    hull() {
        // Bottom face (larger)
        cube([bar_length, bar_width, eps]);
        // Top face (smaller, inset by bevel)
        translate([bevel, bevel, bar_height - eps])
            cube([bar_length - 2*bevel, bar_width - 2*bevel, eps]);
    }
}

module stamp_recess() {
    // Recessed area on top for markings
    translate([bar_length/2 - stamp_w/2, bar_width/2 - stamp_h/2,
               bar_height - stamp_depth])
        cube([stamp_w, stamp_h, stamp_depth + eps]);
}

module main() {
    difference() {
        beveled_bar();
        stamp_recess();
    }
}

main();
""",
    },
    {
        "name": "Pendant Bezel Setting",
        "category": "jewelry",
        "prompt": "pendant bezel setting for cabochon gemstone with bail loop",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters (mm) ---
cab_d = 14;             // cabochon diameter
cab_h = 5;              // cabochon dome height
bezel_wall = 1;
bezel_lip = 0.8;        // lip that holds stone
bail_outer_d = 6;
bail_inner_d = 3;
bail_thickness = 1.5;
back_plate_h = 1;

// --- Derived ---
bezel_outer_d = cab_d + 2*bezel_wall;
bezel_h = cab_h + back_plate_h;

// --- Modules ---
module bezel_cup() {
    difference() {
        // Outer wall
        cylinder(d=bezel_outer_d, h=bezel_h);
        // Stone cavity
        translate([0, 0, back_plate_h])
            cylinder(d=cab_d + 0.3, h=bezel_h);
        // Inner relief (below lip)
        translate([0, 0, back_plate_h])
            cylinder(d=cab_d - 2*bezel_lip + 0.3, h=bezel_h + eps);
    }
}

module bail() {
    // Loop for chain
    translate([0, 0, bezel_h - eps])
        rotate([90, 0, 0])
            difference() {
                cylinder(d=bail_outer_d, h=bail_thickness, center=true);
                cylinder(d=bail_inner_d, h=bail_thickness + 2*eps, center=true);
                // Cut bottom half to make open loop
                translate([0, -bail_outer_d/2, 0])
                    cube([bail_outer_d + eps, bail_outer_d, bail_thickness + 2*eps], center=true);
            }
}

module main() {
    union() {
        bezel_cup();
        bail();
    }
}

main();
""",
    },
    {
        "name": "Hoop Earring",
        "category": "jewelry",
        "prompt": "hoop earring with circular cross section and clasp post",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters (mm) ---
hoop_d = 25;            // outer hoop diameter
wire_d = 2;             // wire thickness
gap_angle = 30;         // opening gap in degrees
post_length = 10;
post_d = 0.8;

// --- Derived ---
hoop_r = hoop_d / 2;

// --- Modules ---
module hoop_arc() {
    // Partial torus — hoop with a gap
    rotate_extrude(angle=360 - gap_angle)
        translate([hoop_r - wire_d/2, 0])
            circle(d=wire_d);
}

module clasp_post() {
    // Straight post that goes through ear
    rotate([0, 0, -gap_angle/2])
        translate([hoop_r - wire_d/2, 0, 0])
            rotate([0, 90, 0])
                cylinder(d=post_d, h=post_length);
}

module main() {
    union() {
        rotate([0, 0, gap_angle/2])
            hoop_arc();
        clasp_post();
    }
}

main();
""",
    },
    {
        "name": "Crown Ring",
        "category": "jewelry",
        "prompt": "crown shaped ring with pointed tips like a tiara",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters (mm) ---
ring_inner_d = 18;
ring_thickness = 2;
band_height = 4;
crown_points = 8;
point_height = 6;
point_width = 3;

// --- Derived ---
ring_outer_r = ring_inner_d/2 + ring_thickness;
ring_center_r = ring_inner_d/2 + ring_thickness/2;

// --- Modules ---
module band() {
    difference() {
        cylinder(d=ring_inner_d + 2*ring_thickness, h=band_height);
        translate([0, 0, -eps])
            cylinder(d=ring_inner_d, h=band_height + 2*eps);
    }
}

module crown_point(angle) {
    rotate([0, 0, angle])
        translate([ring_center_r, 0, band_height - eps])
            hull() {
                // Base
                cube([ring_thickness, point_width, eps], center=true);
                // Pointed tip
                translate([0, 0, point_height])
                    sphere(d=1.2);
            }
}

module main() {
    union() {
        band();
        for (i = [0 : crown_points - 1])
            crown_point(i * 360 / crown_points);
    }
}

main();
""",
    },
    {
        "name": "Braided Ring",
        "category": "jewelry",
        "prompt": "braided twisted rope style finger ring",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters (mm) ---
ring_inner_d = 18;
ring_thickness = 2;
strand_d = 1.5;
strands = 3;
twist_turns = 4;
steps = 200;

// --- Derived ---
ring_center_r = ring_inner_d/2 + ring_thickness/2;
strand_offset = strand_d * 0.6;

// --- Modules ---
module strand(phase) {
    // Single twisted strand following ring path
    union() {
        for (i = [0 : steps - 1]) {
            angle = i * 360 / steps;
            next_angle = (i + 1) * 360 / steps;
            twist = angle * twist_turns + phase;
            next_twist = next_angle * twist_turns + phase;

            hull() {
                rotate([0, 0, angle])
                    translate([ring_center_r, 0, 0])
                        rotate([0, 0, twist])
                            translate([strand_offset, 0, 0])
                                sphere(d=strand_d, $fn=12);
                rotate([0, 0, next_angle])
                    translate([ring_center_r, 0, 0])
                        rotate([0, 0, next_twist])
                            translate([strand_offset, 0, 0])
                                sphere(d=strand_d, $fn=12);
            }
        }
    }
}

module main() {
    for (s = [0 : strands - 1])
        strand(s * 360 / strands);
}

main();
""",
    },
]

# ── Main ────────────────────────────────────────────────────────────────────


async def populate_jewelry(dry_run: bool = False) -> None:
    gemini_key = settings.gemini_api_key
    if not gemini_key and not dry_run:
        raise RuntimeError("GEMINI_API_KEY is required")

    logger.info(f"Processing {len(EXAMPLES)} handcrafted jewelry examples")

    if dry_run:
        for ex in EXAMPLES:
            logger.info(f"  [{ex['category']}] {ex['name']} ({len(ex['code'])} chars)")
        return

    # Generate embeddings
    logger.info("Generating embeddings...")
    texts = [f"{ex['name']}. {ex['category']}. {ex['prompt']}" for ex in EXAMPLES]
    all_embeddings = get_embeddings(texts, gemini_key)
    logger.info(f"Generated {len(all_embeddings)} embeddings")

    with httpx.Client(timeout=30.0) as http:
        ensure_bucket(http)

        async with SessionLocal() as db:
            # Clear previous handcrafted examples
            await db.execute(
                delete(OpenscadExample).where(OpenscadExample.source == SOURCE_NAME)
            )
            await db.commit()

            uploaded = 0
            for i, ex in enumerate(EXAMPLES):
                example_id = str(uuid.uuid4())
                safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", ex["name"])[:80]
                file_path = f"{SOURCE_NAME}/{ex['category']}/{safe_name}_{example_id[:8]}.scad"

                try:
                    storage_path = upload_to_storage(http, file_path, ex["code"])
                except Exception as e:
                    logger.warning(f"Failed to upload {ex['name']}: {e}")
                    continue

                embedding_str = "[" + ",".join(str(v) for v in all_embeddings[i]) + "]"
                await db.execute(
                    text("""
                        INSERT INTO openscad_examples
                            (id, name, category, prompt, storage_path, source, embedding)
                        VALUES
                            (:id, :name, :category, :prompt, :storage_path, :source,
                             cast(:embedding as vector))
                    """),
                    {
                        "id": example_id,
                        "name": ex["name"],
                        "category": ex["category"],
                        "prompt": ex["prompt"],
                        "storage_path": storage_path,
                        "source": SOURCE_NAME,
                        "embedding": embedding_str,
                    },
                )
                uploaded += 1
                logger.info(f"  Uploaded: {ex['name']}")

            await db.commit()

            result = await db.execute(select(func.count(OpenscadExample.id)))
            total = result.scalar()
            logger.info(f"Done! Uploaded {uploaded} jewelry examples, {total} total in DB")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Populate handcrafted jewelry OpenSCAD examples")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(populate_jewelry(dry_run=args.dry_run))
