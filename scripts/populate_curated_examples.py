#!/usr/bin/env python3
"""Delete ALL existing OpenSCAD RAG examples and replace with curated
everyday-object / 3D-printable examples.

Usage:
    python -m scripts.populate_curated_examples [--dry-run]
"""

import argparse
import asyncio
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

STORAGE_BUCKET = "openscad-examples"
STORAGE_FOLDER = "examples"
EMBEDDING_MODEL = "gemini-embedding-001"


# ── Helpers (inlined) ──────────────────────────────────────────────────────


def get_embeddings(texts: list[str], api_key: str) -> list[list[float]]:
    """Generate embeddings using Gemini API (one at a time)."""
    all_embeddings = []
    with httpx.Client(timeout=60.0) as client:
        for t in texts:
            resp = client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{EMBEDDING_MODEL}:embedContent",
                params={"key": api_key},
                headers={"Content-Type": "application/json"},
                json={
                    "model": f"models/{EMBEDDING_MODEL}",
                    "content": {"parts": [{"text": t}]},
                },
            )
            resp.raise_for_status()
            data = resp.json()
            all_embeddings.append(data["embedding"]["values"])
    return all_embeddings


def _storage_headers() -> dict[str, str]:
    return {
        "apikey": settings.supabase_service_role_key,
        "Authorization": f"Bearer {settings.supabase_service_role_key}",
    }


def _storage_url() -> str:
    url = settings.supabase_url
    if not url:
        raise RuntimeError("SUPABASE_URL is not set")
    if not url.startswith("http"):
        url = f"https://{url}"
    return f"{url}/storage/v1"


def ensure_bucket(client: httpx.Client) -> None:
    base = _storage_url()
    resp = client.get(f"{base}/bucket/{STORAGE_BUCKET}", headers=_storage_headers())
    if resp.status_code == 200:
        print(f"       Bucket '{STORAGE_BUCKET}' exists.")
        return
    resp = client.post(
        f"{base}/bucket",
        headers={**_storage_headers(), "Content-Type": "application/json"},
        json={"id": STORAGE_BUCKET, "name": STORAGE_BUCKET, "public": True},
    )
    if resp.status_code in (200, 201):
        print(f"       Created bucket '{STORAGE_BUCKET}'.")


def upload_to_storage(client: httpx.Client, path: str, content: str) -> str:
    base = _storage_url()
    full_path = f"{STORAGE_FOLDER}/{path}"
    url = f"{base}/object/{STORAGE_BUCKET}/{full_path}"
    headers = {**_storage_headers(), "Content-Type": "text/plain", "x-upsert": "true"}
    resp = client.post(url, headers=headers, content=content.encode("utf-8"))
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed for {path}: {resp.status_code} {resp.text}")
    return full_path

SOURCE_NAME = "curated"

# ── Curated examples ──────────────────────────────────────────────────────

EXAMPLES = [

    # ═══════════════════════════════════════════════════════════════════════
    # HOUSEHOLD
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Coffee Mug",
        "category": "household",
        "prompt": "coffee mug with handle",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
body_d = 80;
body_h = 95;
wall = 3;
base_h = 4;
handle_w = 12;
handle_t = 8;
handle_h = 50;
handle_offset_z = 20;

// --- Connection points ---
handle_z = handle_offset_z;
handle_x = body_d / 2 - eps;

// --- Modules ---
module body() {
    difference() {
        cylinder(d = body_d, h = body_h);
        translate([0, 0, base_h])
            cylinder(d = body_d - 2 * wall, h = body_h - base_h + eps);
    }
}

module handle() {
    translate([handle_x, 0, handle_z])
        difference() {
            scale([1, handle_t / handle_w, 1])
                cylinder(d = handle_w, h = handle_h);
            translate([0, 0, -eps])
                scale([1, handle_t / handle_w, 1])
                cylinder(d = handle_w - 2 * wall, h = handle_h + 2 * eps);
            translate([-handle_w, -handle_w, -eps])
                cube([handle_w, 2 * handle_w, handle_h + 2 * eps]);
        }
}

module main() {
    body();
    handle();
}

main();
""",
    },
    {
        "name": "Simple Vase",
        "category": "household",
        "prompt": "flower vase with curved profile",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
base_r = 30;
mid_r = 22;
top_r = 35;
vase_h = 150;
wall = 2.5;
base_h = 4;
mid_h = 60;

// --- Modules ---
module vase_shell() {
    difference() {
        rotate_extrude()
            polygon([
                [0, 0], [base_r, 0],
                [mid_r, mid_h],
                [top_r, vase_h],
                [0, vase_h]
            ]);
        translate([0, 0, base_h])
            rotate_extrude()
                polygon([
                    [0, 0], [base_r - wall, 0],
                    [mid_r - wall, mid_h - base_h],
                    [top_r - wall, vase_h - base_h + eps],
                    [0, vase_h - base_h + eps]
                ]);
    }
}

module main() {
    vase_shell();
}

main();
""",
    },
    {
        "name": "Coaster",
        "category": "household",
        "prompt": "round coaster with drainage grooves",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
coaster_d = 90;
coaster_h = 5;
rim_h = 2;
rim_w = 3;
groove_count = 6;
groove_w = 2;
groove_depth = 1.5;

// --- Modules ---
module base_disc() {
    cylinder(d = coaster_d, h = coaster_h);
}

module rim() {
    difference() {
        cylinder(d = coaster_d, h = coaster_h + rim_h);
        translate([0, 0, -eps])
            cylinder(d = coaster_d - 2 * rim_w, h = coaster_h + rim_h + 2 * eps);
        translate([0, 0, -eps])
            cylinder(d = coaster_d + eps, h = coaster_h);
    }
}

module grooves() {
    for (i = [0 : groove_count - 1])
        rotate([0, 0, i * 360 / groove_count])
            translate([-groove_w / 2, 0, coaster_h - groove_depth])
                cube([groove_w, coaster_d / 2, groove_depth + eps]);
}

module main() {
    union() {
        difference() {
            base_disc();
            grooves();
        }
        rim();
    }
}

main();
""",
    },
    {
        "name": "Soap Dish",
        "category": "household",
        "prompt": "soap dish with drainage slots",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
dish_w = 90;
dish_d = 60;
dish_h = 15;
wall = 3;
base_h = 3;
corner_r = 8;
slot_count = 5;
slot_w = 3;

// --- Modules ---
module dish_outer() {
    minkowski() {
        cube([dish_w - 2 * corner_r, dish_d - 2 * corner_r, dish_h / 2]);
        cylinder(r = corner_r, h = dish_h / 2);
    }
}

module dish_inner() {
    translate([wall, wall, base_h])
        minkowski() {
            cube([dish_w - 2 * wall - 2 * (corner_r - wall), dish_d - 2 * wall - 2 * (corner_r - wall), dish_h]);
            cylinder(r = corner_r - wall, h = eps);
        }
}

module drain_slots() {
    spacing = (dish_w - 2 * wall - 2 * corner_r) / (slot_count + 1);
    for (i = [1 : slot_count])
        translate([wall + corner_r + i * spacing - slot_w / 2, wall + corner_r, -eps])
            cube([slot_w, dish_d - 2 * wall - 2 * corner_r, base_h + 2 * eps]);
}

module main() {
    difference() {
        dish_outer();
        dish_inner();
        drain_slots();
    }
}

main();
""",
    },
    {
        "name": "Toothbrush Holder",
        "category": "household",
        "prompt": "toothbrush holder stand with multiple holes",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
body_d = 50;
body_h = 80;
wall = 3;
base_h = 5;
hole_count = 4;
hole_d = 14;
hole_ring_r = 12;
drain_d = 6;

// --- Modules ---
module body() {
    difference() {
        cylinder(d = body_d, h = body_h);
        translate([0, 0, base_h])
            cylinder(d = body_d - 2 * wall, h = body_h);
    }
}

module brush_holes() {
    for (i = [0 : hole_count - 1])
        rotate([0, 0, i * 360 / hole_count])
            translate([hole_ring_r, 0, -eps])
                cylinder(d = hole_d, h = base_h + 2 * eps);
}

module drain_hole() {
    translate([0, 0, -eps])
        cylinder(d = drain_d, h = base_h + 2 * eps);
}

module main() {
    difference() {
        body();
        brush_holes();
        drain_hole();
    }
}

main();
""",
    },
    {
        "name": "Threaded Jar with Lid",
        "category": "household",
        "prompt": "round jar container with screw-on threaded lid",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
jar_d = 60;
jar_h = 70;
wall = 2.5;
base_h = 3;
thread_pitch = 4;
thread_depth = 1.5;
thread_turns = 2;
lid_h = 15;
lid_clearance = 0.3;
lid_grip_count = 20;
lid_grip_depth = 1;

// --- Derived ---
jar_outer_r = jar_d / 2;
jar_inner_r = jar_outer_r - wall;
lid_inner_r = jar_outer_r + lid_clearance;
lid_outer_r = lid_inner_r + wall;

// --- Modules ---
module jar_body() {
    difference() {
        cylinder(r = jar_outer_r, h = jar_h);
        translate([0, 0, base_h])
            cylinder(r = jar_inner_r, h = jar_h);
    }
}

module jar_thread() {
    thread_h = thread_turns * thread_pitch;
    intersection() {
        translate([0, 0, jar_h - thread_h])
            cylinder(r = jar_outer_r + thread_depth, h = thread_h);
        for (i = [0 : 72])
            rotate([0, 0, i * 5])
                translate([jar_outer_r, 0, jar_h - thread_h + i * thread_pitch / 72])
                    cube([thread_depth * 2, 1.5, thread_pitch / 2]);
    }
}

module lid() {
    translate([jar_d + 20, 0, 0]) {
        cylinder(r = lid_outer_r, h = wall);
        translate([0, 0, wall - eps])
            difference() {
                cylinder(r = lid_outer_r, h = lid_h);
                translate([0, 0, -eps])
                    cylinder(r = lid_inner_r, h = lid_h + 2 * eps);
                for (i = [0 : lid_grip_count - 1])
                    rotate([0, 0, i * 360 / lid_grip_count])
                        translate([lid_outer_r, 0, -eps])
                            cylinder(r = lid_grip_depth, h = lid_h + 2 * eps);
            }
    }
}

module main() {
    jar_body();
    jar_thread();
    lid();
}

main();
""",
    },
    {
        "name": "Lamp Shade",
        "category": "household",
        "prompt": "conical lamp shade with cutout patterns",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
top_d = 40;
bottom_d = 120;
shade_h = 100;
wall = 2;
cutout_rows = 4;
cutout_per_row = 12;
cutout_w = 8;
cutout_h = 15;

// --- Modules ---
module shade_shell() {
    difference() {
        cylinder(d1 = bottom_d, d2 = top_d, h = shade_h);
        translate([0, 0, -eps])
            cylinder(d1 = bottom_d - 2 * wall, d2 = top_d - 2 * wall, h = shade_h + 2 * eps);
    }
}

module cutout_pattern() {
    for (row = [0 : cutout_rows - 1]) {
        row_z = shade_h * 0.15 + row * (shade_h * 0.7 / cutout_rows);
        row_r = (bottom_d / 2 - wall) + (top_d / 2 - bottom_d / 2) * (row_z / shade_h);
        for (i = [0 : cutout_per_row - 1])
            rotate([0, 0, i * 360 / cutout_per_row + row * 15])
                translate([row_r, -cutout_w / 2, row_z])
                    cube([wall + 2 * eps, cutout_w, cutout_h]);
    }
}

module main() {
    difference() {
        shade_shell();
        cutout_pattern();
    }
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # ORGANIZERS & TECH
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Desk Pencil Holder",
        "category": "organizer",
        "prompt": "hexagonal pencil holder cup for desk",
        "code": """\
$fn = 6;
eps = 0.01;

// --- Parameters ---
hex_r = 35;
holder_h = 100;
wall = 3;
base_h = 4;

// --- Modules ---
module hex_cup() {
    difference() {
        cylinder(r = hex_r, h = holder_h);
        translate([0, 0, base_h])
            cylinder(r = hex_r - wall, h = holder_h);
    }
}

module main() {
    hex_cup();
}

main();
""",
    },
    {
        "name": "Phone Stand",
        "category": "organizer",
        "prompt": "angled phone stand holder for desk",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
base_w = 80;
base_d = 60;
base_h = 5;
back_h = 70;
back_t = 5;
angle = 70;
lip_h = 15;
lip_t = 5;
slot_w = 15;
slot_d = 10;
corner_r = 3;

// --- Connection points ---
back_z = base_h - eps;

// --- Modules ---
module base() {
    minkowski() {
        cube([base_w - 2 * corner_r, base_d - 2 * corner_r, base_h / 2]);
        cylinder(r = corner_r, h = base_h / 2);
    }
}

module back_support() {
    translate([0, 0, back_z])
        rotate([90 - angle, 0, 0])
        cube([base_w, back_t, back_h]);
}

module lip() {
    translate([0, base_d - lip_t - 5, back_z])
        cube([base_w, lip_t, lip_h]);
}

module cable_slot() {
    translate([base_w / 2 - slot_w / 2, base_d / 2, -eps])
        cube([slot_w, slot_d, base_h + 2 * eps]);
}

module main() {
    difference() {
        union() {
            base();
            back_support();
            lip();
        }
        cable_slot();
    }
}

main();
""",
    },
    {
        "name": "Stackable Storage Box",
        "category": "organizer",
        "prompt": "stackable box with lid and finger grip",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
box_w = 100;
box_d = 80;
box_h = 50;
wall = 2.5;
base_h = 2.5;
corner_r = 5;
lip_h = 4;
lip_inset = 1.2;
grip_r = 15;

// --- Modules ---
module rounded_box(w, d, h, r) {
    minkowski() {
        cube([w - 2 * r, d - 2 * r, h]);
        cylinder(r = r, h = eps);
    }
}

module box_body() {
    difference() {
        rounded_box(box_w, box_d, box_h, corner_r);
        translate([wall, wall, base_h])
            rounded_box(box_w - 2 * wall, box_d - 2 * wall, box_h, corner_r - wall);
    }
}

module stacking_lip() {
    translate([lip_inset, lip_inset, box_h - eps])
        rounded_box(box_w - 2 * lip_inset, box_d - 2 * lip_inset, lip_h, corner_r - lip_inset);
}

module finger_grip() {
    translate([box_w / 2, -eps, box_h / 2])
        rotate([-90, 0, 0])
        cylinder(r = grip_r, h = wall + 2 * eps);
}

module main() {
    difference() {
        union() {
            box_body();
            stacking_lip();
        }
        finger_grip();
        translate([0, box_d, 0])
            mirror([0, 1, 0])
            finger_grip();
    }
}

main();
""",
    },
    {
        "name": "Cable Clip",
        "category": "organizer",
        "prompt": "adhesive cable management clip for desk",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
cable_d = 6;
clip_wall = 2;
base_w = 18;
base_d = 18;
base_h = 3;
slot_w = 3;
corner_r = 2;

// --- Derived ---
clip_outer_r = cable_d / 2 + clip_wall;
clip_z = base_h - eps;

// --- Modules ---
module base_pad() {
    minkowski() {
        cube([base_w - 2 * corner_r, base_d - 2 * corner_r, base_h / 2]);
        cylinder(r = corner_r, h = base_h / 2);
    }
}

module cable_cradle() {
    translate([base_w / 2, base_d / 2, clip_z])
        difference() {
            cylinder(r = clip_outer_r, h = cable_d);
            translate([0, 0, -eps])
                cylinder(d = cable_d + 0.4, h = cable_d + 2 * eps);
            // Entry slot
            translate([-slot_w / 2, 0, -eps])
                cube([slot_w, clip_outer_r + eps, cable_d + 2 * eps]);
        }
}

module main() {
    base_pad();
    cable_cradle();
}

main();
""",
    },
    {
        "name": "Laptop Stand Riser",
        "category": "organizer",
        "prompt": "laptop stand riser with ventilation holes",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
stand_w = 260;
stand_d = 200;
stand_h = 50;
wall = 4;
top_t = 4;
corner_r = 10;
lip_h = 3;
lip_w = 5;
vent_rows = 3;
vent_cols = 8;
vent_d = 10;

// --- Modules ---
module shell() {
    difference() {
        minkowski() {
            cube([stand_w - 2 * corner_r, stand_d - 2 * corner_r, stand_h / 2]);
            cylinder(r = corner_r, h = stand_h / 2);
        }
        translate([wall, wall, -eps])
            minkowski() {
                cube([stand_w - 2 * wall - 2 * (corner_r - wall), stand_d - 2 * wall - 2 * (corner_r - wall), stand_h / 2]);
                cylinder(r = corner_r - wall, h = stand_h / 2);
            }
    }
}

module anti_slip_lips() {
    // Front and back lips
    translate([corner_r, -eps, stand_h - eps])
        cube([stand_w - 2 * corner_r, lip_w, lip_h]);
    translate([corner_r, stand_d - lip_w + eps, stand_h - eps])
        cube([stand_w - 2 * corner_r, lip_w, lip_h]);
}

module vent_holes() {
    x_spacing = (stand_w - 4 * corner_r) / (vent_cols + 1);
    y_spacing = (stand_d - 4 * corner_r) / (vent_rows + 1);
    for (r = [1 : vent_rows])
        for (c = [1 : vent_cols])
            translate([2 * corner_r + c * x_spacing, 2 * corner_r + r * y_spacing, stand_h - top_t - eps])
                cylinder(d = vent_d, h = top_t + 2 * eps);
}

module main() {
    difference() {
        union() {
            shell();
            anti_slip_lips();
        }
        vent_holes();
    }
}

main();
""",
    },
    {
        "name": "SD Card Holder",
        "category": "organizer",
        "prompt": "SD card storage holder organizer with slots",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
card_w = 24.5;
card_h = 32.5;
card_t = 2.5;
slot_count = 6;
wall = 2;
base_h = 3;
slot_clearance = 0.5;
divider_t = 1.5;

// --- Derived ---
slot_w = card_t + slot_clearance;
total_w = slot_count * slot_w + (slot_count - 1) * divider_t + 2 * wall;
total_d = card_w + 2 * wall;
total_h = card_h * 0.7 + base_h;

// --- Modules ---
module body() {
    cube([total_w, total_d, total_h]);
}

module card_slots() {
    for (i = [0 : slot_count - 1])
        translate([wall + i * (slot_w + divider_t), wall, base_h])
            cube([slot_w, card_w, total_h]);
}

module main() {
    difference() {
        body();
        card_slots();
    }
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # BRACKETS & FUNCTIONAL
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Wall Hook",
        "category": "bracket",
        "prompt": "wall mounted coat hook with screw holes",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
plate_w = 40;
plate_h = 60;
plate_t = 4;
hook_r = 15;
hook_t = 6;
hook_angle = 200;
screw_d = 4.4;
screw_head_d = 8;
screw_head_depth = 2;
screw_spacing = 40;
corner_r = 3;

// --- Connection points ---
hook_z = plate_h - hook_r - 10;

// --- Modules ---
module back_plate() {
    minkowski() {
        cube([plate_w - 2 * corner_r, plate_t / 2, plate_h - 2 * corner_r]);
        cylinder(r = corner_r, h = plate_t / 2);
    }
}

module hook() {
    translate([plate_w / 2, -plate_t + eps, hook_z])
        rotate([90, 0, 0])
        rotate_extrude(angle = hook_angle)
            translate([hook_r, 0])
                circle(d = hook_t);
}

module screw_holes() {
    for (z = [plate_h / 2 - screw_spacing / 2, plate_h / 2 + screw_spacing / 2])
        translate([plate_w / 2, eps, z])
            rotate([90, 0, 0]) {
                cylinder(d = screw_d, h = plate_t + 2 * eps);
                cylinder(d = screw_head_d, h = screw_head_depth + eps);
            }
}

module main() {
    difference() {
        union() {
            back_plate();
            hook();
        }
        screw_holes();
    }
}

main();
""",
    },
    {
        "name": "Shelf Bracket",
        "category": "bracket",
        "prompt": "L-shaped shelf bracket with mounting holes and gusset",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
arm_length = 120;
wall_length = 100;
width = 30;
thickness = 5;
hole_d = 5.4;
hole_margin = 20;
gusset_t = 4;

// --- Modules ---
module wall_plate() {
    cube([width, thickness, wall_length]);
}

module arm_plate() {
    translate([0, 0, wall_length - eps])
        rotate([-90, 0, 0])
        cube([width, thickness, arm_length]);
}

module gusset() {
    translate([width / 2 - gusset_t / 2, thickness - eps, 0])
        rotate([90, 0, 90])
        linear_extrude(height = gusset_t)
            polygon([[0, 0], [0, wall_length], [arm_length * 0.7, wall_length]]);
}

module wall_holes() {
    for (z = [hole_margin, wall_length - hole_margin])
        translate([width / 2, -eps, z])
            rotate([-90, 0, 0])
            cylinder(d = hole_d, h = thickness + 2 * eps);
}

module arm_holes() {
    for (y = [hole_margin, arm_length - hole_margin])
        translate([width / 2, thickness + y, wall_length])
            cylinder(d = hole_d, h = thickness + 2 * eps);
}

module main() {
    difference() {
        union() {
            wall_plate();
            arm_plate();
            gusset();
        }
        wall_holes();
        arm_holes();
    }
}

main();
""",
    },
    {
        "name": "Curtain Rod Bracket",
        "category": "bracket",
        "prompt": "curtain rod wall bracket with U-shaped cradle",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
rod_d = 25;
wall_plate_w = 40;
wall_plate_h = 50;
wall_plate_t = 5;
arm_length = 50;
arm_t = 5;
cradle_wall = 4;
screw_d = 5;
screw_spacing = 30;

// --- Derived ---
cradle_r = rod_d / 2 + cradle_wall;

// --- Modules ---
module wall_plate() {
    cube([wall_plate_w, wall_plate_t, wall_plate_h]);
}

module support_arm() {
    translate([wall_plate_w / 2 - arm_t / 2, wall_plate_t - eps, wall_plate_h / 2 - arm_t / 2])
        cube([arm_t, arm_length, arm_t]);
}

module rod_cradle() {
    translate([wall_plate_w / 2, wall_plate_t + arm_length, wall_plate_h / 2])
        rotate([-90, 0, 0])
        difference() {
            cylinder(r = cradle_r, h = cradle_wall);
            translate([0, 0, -eps])
                cylinder(d = rod_d + 0.5, h = cradle_wall + 2 * eps);
            // Open top
            translate([-cradle_r - eps, 0, -eps])
                cube([cradle_r * 2 + 2 * eps, cradle_r + eps, cradle_wall + 2 * eps]);
        }
}

module screw_holes() {
    for (z = [wall_plate_h / 2 - screw_spacing / 2, wall_plate_h / 2 + screw_spacing / 2])
        translate([wall_plate_w / 2, -eps, z])
            rotate([-90, 0, 0])
            cylinder(d = screw_d, h = wall_plate_t + 2 * eps);
}

module main() {
    difference() {
        union() {
            wall_plate();
            support_arm();
            rod_cradle();
        }
        screw_holes();
    }
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # TOOLS & FUNCTIONAL
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Bottle Opener",
        "category": "tool",
        "prompt": "handheld bottle opener with ergonomic grip",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
handle_l = 100;
handle_w = 28;
handle_h = 10;
handle_r = 5;
opener_l = 30;
opener_t = 4;
slot_w = 22;
slot_l = 6;
grip_count = 6;
grip_r = 4;

// --- Connection points ---
opener_y = handle_l - eps;

// --- Modules ---
module handle() {
    minkowski() {
        cube([handle_w - 2 * handle_r, handle_l - 2 * handle_r, handle_h / 2]);
        cylinder(r = handle_r, h = handle_h / 2);
    }
}

module grip_indents() {
    for (i = [0 : grip_count - 1])
        translate([-1, handle_l * 0.2 + i * (handle_l * 0.5 / grip_count), handle_h / 2])
            rotate([0, 90, 0])
            cylinder(r = grip_r, h = handle_w + 2);
}

module opener_head() {
    translate([0, opener_y, 0])
        difference() {
            minkowski() {
                cube([handle_w - 2 * handle_r, opener_l - handle_r, opener_t / 2]);
                cylinder(r = handle_r, h = opener_t / 2);
            }
            translate([(handle_w - slot_w) / 2, opener_l / 2, -eps])
                hull() {
                    cube([slot_w, slot_l, opener_t + 2 * eps]);
                    translate([2, slot_l + 3, 0])
                        cube([slot_w - 4, eps, opener_t + 2 * eps]);
                }
        }
}

module main() {
    difference() {
        union() {
            handle();
            opener_head();
        }
        grip_indents();
    }
}

main();
""",
    },
    {
        "name": "Wrench",
        "category": "tool",
        "prompt": "open-end wrench tool",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
jaw_size = 14;
jaw_depth = 12;
jaw_t = 5;
handle_l = 120;
handle_w = 18;
handle_t = 5;
handle_r = 3;

// --- Derived ---
jaw_r = jaw_size / 2 + 3;

// --- Modules ---
module handle() {
    minkowski() {
        cube([handle_w - 2 * handle_r, handle_l - 2 * handle_r, handle_t / 2]);
        cylinder(r = handle_r, h = handle_t / 2);
    }
}

module jaw_head() {
    translate([handle_w / 2, handle_l - eps, 0])
        difference() {
            cylinder(r = jaw_r, h = jaw_t);
            // Jaw opening
            translate([-jaw_size / 2, 0, -eps])
                cube([jaw_size, jaw_r + eps, jaw_t + 2 * eps]);
        }
}

module main() {
    handle();
    jaw_head();
}

main();
""",
    },
    {
        "name": "Whistle",
        "category": "tool",
        "prompt": "simple pea-less whistle",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
body_l = 45;
body_w = 16;
body_h = 12;
wall = 2;
mouth_l = 15;
mouth_h = 4;
window_w = 10;
window_l = 3;
lanyard_hole_d = 4;
corner_r = 2;

// --- Modules ---
module outer_shell() {
    minkowski() {
        cube([body_w - 2 * corner_r, body_l - 2 * corner_r, body_h - 2 * corner_r]);
        sphere(r = corner_r);
    }
}

module inner_cavity() {
    translate([wall, wall, wall])
        cube([body_w - 2 * wall, body_l - 2 * wall, body_h - 2 * wall]);
}

module mouthpiece() {
    translate([wall, -mouth_l + eps, wall])
        cube([body_w - 2 * wall, mouth_l, mouth_h]);
}

module sound_window() {
    translate([(body_w - window_w) / 2, wall - eps, body_h - wall - eps])
        cube([window_w, window_l + wall, wall + 2 * eps]);
}

module lanyard_hole() {
    translate([body_w / 2, body_l - corner_r, body_h / 2])
        rotate([-90, 0, 0])
        cylinder(d = lanyard_hole_d, h = corner_r * 2 + eps);
}

module main() {
    difference() {
        outer_shell();
        inner_cavity();
        mouthpiece();
        sound_window();
        lanyard_hole();
    }
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # ENCLOSURES
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Raspberry Pi Case",
        "category": "enclosure",
        "prompt": "raspberry pi case enclosure with ventilation slots",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
pcb_w = 85;
pcb_d = 56;
wall = 2.5;
base_h = 5;
top_clearance = 18;
corner_r = 3;
standoff_h = 3;
standoff_r = 3;
screw_d = 2.7;
vent_count = 6;
vent_slot_w = 2;
vent_slot_l = 20;
vent_spacing = 5;

// --- Derived ---
inner_w = pcb_w + 1;
inner_d = pcb_d + 1;
outer_w = inner_w + 2 * wall;
outer_d = inner_d + 2 * wall;
total_h = base_h + standoff_h + 1.6 + top_clearance;

hole_positions = [[3.5, 3.5], [61.5, 3.5], [3.5, 52.5], [61.5, 52.5]];

// --- Modules ---
module case_shell() {
    difference() {
        minkowski() {
            cube([outer_w - 2 * corner_r, outer_d - 2 * corner_r, total_h / 2]);
            cylinder(r = corner_r, h = total_h / 2);
        }
        translate([wall, wall, base_h])
            cube([inner_w, inner_d, total_h]);
    }
}

module standoffs() {
    for (pos = hole_positions)
        translate([wall + 0.5 + pos[0], wall + 0.5 + pos[1], base_h - eps])
            difference() {
                cylinder(r = standoff_r, h = standoff_h);
                translate([0, 0, -eps])
                    cylinder(d = screw_d, h = standoff_h + 2 * eps);
            }
}

module vent_slots() {
    start_x = outer_w / 2 - (vent_count * (vent_slot_w + vent_spacing)) / 2;
    for (i = [0 : vent_count - 1])
        translate([start_x + i * (vent_slot_w + vent_spacing), -eps, total_h / 2])
            cube([vent_slot_w, wall + 2 * eps, vent_slot_l]);
}

module main() {
    difference() {
        union() {
            case_shell();
            standoffs();
        }
        vent_slots();
    }
}

main();
""",
    },
    {
        "name": "Battery Box",
        "category": "enclosure",
        "prompt": "AA battery holder box with snap lid",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
batt_d = 14.5;
batt_l = 50.5;
batt_count = 4;
wall = 2;
base_h = 2;
clearance = 0.5;
snap_h = 3;
snap_t = 1;

// --- Derived ---
cell_w = batt_d + clearance;
inner_w = batt_count * cell_w;
inner_d = batt_l + clearance;
outer_w = inner_w + 2 * wall;
outer_d = inner_d + 2 * wall;
box_h = batt_d / 2 + base_h + 5;

// --- Modules ---
module box() {
    difference() {
        cube([outer_w, outer_d, box_h]);
        translate([wall, wall, base_h])
            cube([inner_w, inner_d, box_h]);
    }
}

module dividers() {
    for (i = [1 : batt_count - 1])
        translate([wall + i * cell_w - wall / 2, wall, base_h])
            cube([wall, inner_d, box_h - base_h - 3]);
}

module snap_ridges() {
    // Snaps on long sides
    for (y = [wall / 2, outer_d - wall / 2])
        translate([wall, y - snap_t / 2, box_h - snap_h])
            cube([inner_w, snap_t, snap_h]);
}

module main() {
    box();
    dividers();
    snap_ridges();
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # MECHANICAL / HARDWARE
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Spur Gear",
        "category": "gear",
        "prompt": "spur gear with teeth and center bore",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
teeth = 20;
module_val = 2;
gear_h = 10;
bore_d = 8;
hub_d = 16;
hub_h = 5;

// --- Derived ---
pitch_r = teeth * module_val / 2;
outer_r = pitch_r + module_val;
root_r = pitch_r - 1.25 * module_val;
tooth_angle = 360 / teeth;

// --- Modules ---
module tooth() {
    intersection() {
        cylinder(r = outer_r, h = gear_h);
        hull() {
            translate([root_r, -module_val * 0.4, 0])
                cube([module_val * 2.25, module_val * 0.8, gear_h]);
            translate([pitch_r, -module_val * 0.3, 0])
                cube([module_val, module_val * 0.6, gear_h]);
        }
    }
}

module gear_body() {
    cylinder(r = root_r, h = gear_h);
    for (i = [0 : teeth - 1])
        rotate([0, 0, i * tooth_angle])
            tooth();
}

module main() {
    difference() {
        union() {
            gear_body();
            cylinder(d = hub_d, h = gear_h + hub_h);
        }
        translate([0, 0, -eps])
            cylinder(d = bore_d, h = gear_h + hub_h + 2 * eps);
    }
}

main();
""",
    },
    {
        "name": "Hinge",
        "category": "hinge_joint",
        "prompt": "simple pin hinge with two interlocking leaves",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
leaf_w = 40;
leaf_h = 50;
leaf_t = 3;
knuckle_r = 4;
knuckle_count = 3;
pin_d = 3;
pin_clearance = 0.3;
hole_d = 4.4;
hole_margin = 12;

// --- Derived ---
knuckle_h = leaf_h / knuckle_count;

// --- Modules ---
module leaf_plate() {
    cube([leaf_w, leaf_t, leaf_h]);
}

module knuckle_segment(z_offset) {
    translate([0, 0, z_offset])
        cylinder(r = knuckle_r, h = knuckle_h - 0.3);
}

module leaf_with_knuckles(start) {
    translate([knuckle_r, 0, 0])
        leaf_plate();
    for (i = [start : 2 : knuckle_count - 1])
        knuckle_segment(i * knuckle_h);
}

module main() {
    difference() {
        union() {
            leaf_with_knuckles(0);
            rotate([0, 0, 180])
                translate([-knuckle_r * 2, -leaf_t, 0])
                leaf_with_knuckles(1);
        }
        // Pin hole
        translate([0, 0, -eps])
            cylinder(d = pin_d + pin_clearance, h = leaf_h + 2 * eps);
    }
}

main();
""",
    },
    {
        "name": "Bearing Pillow Block",
        "category": "hinge_joint",
        "prompt": "pillow block bearing housing with bolt holes",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
bore_d = 20;
bearing_od = 35;
bearing_h = 12;
base_w = 80;
base_d = 40;
base_h = 8;
bolt_d = 8;
bolt_spacing = 60;

// --- Derived ---
housing_r = bearing_od / 2 + 4;
housing_z = base_h - eps;

// --- Modules ---
module base_plate() {
    translate([-base_w / 2, -base_d / 2, 0])
        cube([base_w, base_d, base_h]);
}

module bearing_housing() {
    translate([0, 0, housing_z])
        cylinder(r = housing_r, h = bearing_h);
}

module bore_hole() {
    translate([0, 0, -eps])
        cylinder(d = bore_d + 0.3, h = base_h + bearing_h + 2 * eps);
}

module bolt_holes() {
    for (x = [-bolt_spacing / 2, bolt_spacing / 2])
        translate([x, 0, -eps])
            cylinder(d = bolt_d, h = base_h + 2 * eps);
}

module main() {
    difference() {
        union() {
            base_plate();
            bearing_housing();
        }
        bore_hole();
        bolt_holes();
    }
}

main();
""",
    },

    {
        "name": "Rocket",
        "category": "vehicle",
        "prompt": "model rocket with nose cone fins and body tube",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
body_d = 30;
body_h = 150;
nose_h = 50;
fin_count = 4;
fin_root = 50;
fin_tip = 20;
fin_height = 35;
fin_t = 3;
fin_sweep = 15;
nozzle_d = 18;
nozzle_h = 10;

// --- Derived ---
body_r = body_d / 2;

// --- Modules ---
module body_tube() {
    difference() {
        cylinder(d = body_d, h = body_h);
        translate([0, 0, -eps])
            cylinder(d = body_d - 4, h = body_h + 2 * eps);
    }
}

module nose_cone() {
    translate([0, 0, body_h - eps])
        cylinder(d1 = body_d, d2 = 0, h = nose_h);
}

module fin() {
    // Single fin as a swept polygon
    linear_extrude(height = fin_t, center = true)
        polygon([
            [body_r, 0],
            [body_r + fin_height, fin_sweep],
            [body_r + fin_height, fin_sweep + fin_tip],
            [body_r, fin_root]
        ]);
}

module fins() {
    for (i = [0 : fin_count - 1])
        rotate([0, 0, i * 360 / fin_count])
            fin();
}

module nozzle() {
    translate([0, 0, -nozzle_h + eps])
        difference() {
            cylinder(d1 = nozzle_d + 4, d2 = body_d, h = nozzle_h);
            translate([0, 0, -eps])
                cylinder(d = nozzle_d, h = nozzle_h + 2 * eps);
        }
}

module main() {
    body_tube();
    nose_cone();
    fins();
    nozzle();
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # JEWELRY & RINGS
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Band Ring",
        "category": "jewelry",
        "prompt": "simple band finger ring with rounded edges",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters ---
ring_inner_d = 18.5;
ring_width = 6;
ring_thickness = 2;
edge_r = 0.8;

// --- Modules ---
module ring_profile() {
    offset(r = edge_r) offset(r = -edge_r)
        square([ring_thickness, ring_width], center = true);
}

module main() {
    rotate_extrude()
        translate([ring_inner_d / 2 + ring_thickness / 2, 0])
            ring_profile();
}

main();
""",
    },
    {
        "name": "Signet Ring",
        "category": "jewelry",
        "prompt": "signet ring with flat oval top for engraving",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters ---
ring_inner_d = 18.5;
ring_thickness = 2;
ring_width = 6;
signet_w = 12;
signet_d = 10;
signet_h = 3;
taper_h = 5;

// --- Derived ---
ring_outer_r = ring_inner_d / 2 + ring_thickness;

// --- Modules ---
module band() {
    rotate_extrude()
        translate([ring_inner_d / 2 + ring_thickness / 2, 0])
            offset(r = 0.5) offset(r = -0.5)
                square([ring_thickness, ring_width], center = true);
}

module signet_top() {
    translate([0, 0, ring_outer_r - eps])
        hull() {
            scale([signet_w / 2, signet_d / 2, 0.5])
                sphere(r = 1);
            translate([0, 0, -taper_h])
                scale([ring_thickness, ring_width / 2, 0.5])
                    sphere(r = 1);
        }
}

module main() {
    band();
    signet_top();
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

// --- Parameters ---
ring_inner_d = 18;
ring_thickness = 2;
band_height = 4;
crown_points = 8;
point_height = 6;
point_width = 3;

// --- Derived ---
ring_center_r = ring_inner_d / 2 + ring_thickness / 2;

// --- Modules ---
module band() {
    difference() {
        cylinder(d = ring_inner_d + 2 * ring_thickness, h = band_height);
        translate([0, 0, -eps])
            cylinder(d = ring_inner_d, h = band_height + 2 * eps);
    }
}

module crown_point() {
    translate([ring_center_r, 0, band_height - eps])
        hull() {
            cube([ring_thickness, point_width, eps], center = true);
            translate([0, 0, point_height])
                sphere(d = 1.2);
        }
}

module main() {
    band();
    for (i = [0 : crown_points - 1])
        rotate([0, 0, i * 360 / crown_points])
            crown_point();
}

main();
""",
    },
    {
        "name": "Solitaire Ring",
        "category": "jewelry",
        "prompt": "solitaire engagement ring with prong-set gemstone",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters ---
ring_inner_d = 17;
ring_width = 4;
ring_thickness = 1.8;
gem_d = 6;
gem_crown_h = 2;
gem_pavilion_h = 4;
prong_count = 6;
prong_w = 1.2;
prong_h = 5;
setting_d = 8;
setting_h = 3;

// --- Derived ---
ring_outer_r = ring_inner_d / 2 + ring_thickness;
gem_r = gem_d / 2;

// --- Modules ---
module band() {
    rotate_extrude()
        translate([ring_inner_d / 2 + ring_thickness / 2, 0])
            offset(r = 0.4) offset(r = -0.4)
                square([ring_thickness, ring_width], center = true);
}

module gemstone() {
    cylinder(r1 = 0, r2 = gem_r, h = gem_pavilion_h, $fn = 16);
    translate([0, 0, gem_pavilion_h - eps])
        cylinder(r1 = gem_r, r2 = gem_r * 0.55, h = gem_crown_h, $fn = 16);
}

module prong_setting() {
    difference() {
        cylinder(d = setting_d, h = setting_h);
        translate([0, 0, -eps])
            cylinder(d = setting_d - 2, h = setting_h + 2 * eps);
    }
    for (i = [0 : prong_count - 1])
        rotate([0, 0, i * 360 / prong_count])
            translate([setting_d / 2 - prong_w / 2, -prong_w / 2, 0])
                cube([prong_w, prong_w, prong_h]);
}

module main() {
    band();
    translate([0, 0, ring_outer_r - setting_h / 2]) {
        prong_setting();
        translate([0, 0, setting_h - gem_pavilion_h + 1])
            gemstone();
    }
}

main();
""",
    },
    {
        "name": "Hoop Earring",
        "category": "jewelry",
        "prompt": "hoop earring with circular cross section",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters ---
hoop_d = 25;
wire_d = 2;
gap_angle = 30;
post_length = 10;
post_d = 0.8;

// --- Derived ---
hoop_r = hoop_d / 2;

// --- Modules ---
module hoop_arc() {
    rotate_extrude(angle = 360 - gap_angle)
        translate([hoop_r - wire_d / 2, 0])
            circle(d = wire_d);
}

module clasp_post() {
    rotate([0, 0, -gap_angle / 2])
        translate([hoop_r - wire_d / 2, 0, 0])
            rotate([0, 90, 0])
            cylinder(d = post_d, h = post_length);
}

module main() {
    rotate([0, 0, gap_angle / 2])
        hoop_arc();
    clasp_post();
}

main();
""",
    },
    {
        "name": "Pendant with Bail",
        "category": "jewelry",
        "prompt": "round pendant with bail loop for chain",
        "code": """\
$fn = 128;
eps = 0.01;

// --- Parameters ---
pendant_d = 20;
pendant_t = 3;
bail_outer_d = 6;
bail_inner_d = 3;
bail_t = 1.5;

// --- Derived ---
bail_z = pendant_t / 2;

// --- Modules ---
module pendant_disc() {
    cylinder(d = pendant_d, h = pendant_t, center = true);
}

module bail() {
    translate([0, pendant_d / 2 - 1, bail_z])
        rotate([90, 0, 0])
        difference() {
            cylinder(d = bail_outer_d, h = bail_t, center = true);
            cylinder(d = bail_inner_d, h = bail_t + 2 * eps, center = true);
            translate([0, -bail_outer_d / 2, 0])
                cube([bail_outer_d + eps, bail_outer_d, bail_t + 2 * eps], center = true);
        }
}

module main() {
    pendant_disc();
    bail();
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

// --- Parameters ---
diamond_d = 8;
crown_h = 3;
pavilion_h = 5;
table_ratio = 0.55;
facets = 16;

// --- Derived ---
diamond_r = diamond_d / 2;
table_r = diamond_r * table_ratio;

// --- Modules ---
module pavilion() {
    cylinder(r1 = 0, r2 = diamond_r, h = pavilion_h, $fn = facets);
}

module crown() {
    translate([0, 0, pavilion_h - eps])
        cylinder(r1 = diamond_r, r2 = table_r, h = crown_h, $fn = facets);
}

module main() {
    pavilion();
    crown();
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # INTRICATE JEWELRY — diamond rings, halo settings, pave bands
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Halo Diamond Engagement Ring",
        "category": "jewelry",
        "prompt": "halo engagement ring with center diamond surrounded by small accent stones and pave band",
        "code": """\
// DESIGN: halo engagement ring — band, gallery basket, center diamond, halo of small stones, pave accent stones on band
// PARTS: band, gallery, center_gem, halo_stones, pave_stones, prongs
// TREE: band → gallery → center_gem + halo_stones + prongs, band → pave_stones
// CONNECTIONS: setting_z, gem_z, halo_z

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
band_width = 2.8;
band_thickness = 1.6;

center_gem_d = 6.5;
center_gem_crown_h = 2;
center_gem_pavilion_h = 4;
gem_facets = 16;

halo_count = 16;
halo_stone_d = 1.4;
halo_ring_r = 5;

pave_count = 12;
pave_stone_d = 1.2;
pave_rows = 2;

prong_count = 6;
prong_w = 0.9;
prong_h = 6;

gallery_d = 11;
gallery_h = 4;
gallery_arch_count = 8;

// --- Derived ---
band_outer_r = band_inner_d / 2 + band_thickness;
setting_z = band_outer_r;
gem_z = setting_z + gallery_h - center_gem_pavilion_h + 1;
halo_z = setting_z + gallery_h - 0.5;

// --- Modules ---

module band() {
    rotate_extrude()
        translate([band_inner_d / 2 + band_thickness / 2, 0])
            offset(r = 0.3) offset(r = -0.3)
            square([band_thickness, band_width], center = true);
}

module faceted_gem(d, crown_h, pav_h) {
    cylinder(r1 = 0, r2 = d / 2, h = pav_h, $fn = gem_facets);
    translate([0, 0, pav_h - eps])
        cylinder(r1 = d / 2, r2 = d / 2 * 0.55, h = crown_h, $fn = gem_facets);
}

module gallery() {
    translate([0, 0, setting_z])
        difference() {
            cylinder(d = gallery_d, h = gallery_h);
            translate([0, 0, -eps])
                cylinder(d = gallery_d - 2.4, h = gallery_h + 2 * eps);
            for (i = [0 : gallery_arch_count - 1])
                rotate([0, 0, i * 360 / gallery_arch_count])
                    translate([gallery_d / 2, 0, gallery_h / 2])
                        scale([1, 1, 1.5])
                        rotate([90, 0, 0])
                        cylinder(d = gallery_h * 0.6, h = 3, center = true, $fn = 32);
        }
}

module prongs() {
    translate([0, 0, setting_z])
        for (i = [0 : prong_count - 1])
            rotate([0, 0, i * 360 / prong_count])
                translate([gallery_d / 2 - prong_w, -prong_w / 2, 0])
                    hull() {
                        cube([prong_w, prong_w, prong_h - 1]);
                        translate([-0.3, 0, prong_h - 0.5])
                            cube([prong_w * 0.6, prong_w, 0.5]);
                    }
}

module halo_stones() {
    translate([0, 0, halo_z])
        for (i = [0 : halo_count - 1])
            rotate([0, 0, i * 360 / halo_count])
                translate([halo_ring_r, 0, 0])
                    sphere(d = halo_stone_d, $fn = 12);
}

module pave_stones() {
    for (row = [0 : pave_rows - 1]) {
        row_offset = (row - (pave_rows - 1) / 2) * pave_stone_d * 1.1;
        for (i = [0 : pave_count - 1]) {
            angle = 50 + i * 260 / (pave_count - 1);
            rotate([0, 0, angle])
                translate([band_outer_r - 0.3, 0, row_offset])
                    sphere(d = pave_stone_d, $fn = 10);
        }
    }
}

module main() {
    band();
    gallery();
    prongs();
    translate([0, 0, gem_z])
        faceted_gem(center_gem_d, center_gem_crown_h, center_gem_pavilion_h);
    halo_stones();
    pave_stones();
}

main();
""",
    },
    {
        "name": "Three Stone Trilogy Ring",
        "category": "jewelry",
        "prompt": "three stone trilogy engagement ring with center diamond flanked by two smaller stones",
        "code": """\
// DESIGN: three-stone ring — band with three prong-set gemstones in a row
// PARTS: band, center_gem, side_gems, center_prongs, side_prongs
// TREE: band → center_gem + center_prongs + side_gems + side_prongs
// CONNECTIONS: setting_z, side_offset

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
band_width = 2.5;
band_thickness = 1.6;

center_d = 6;
center_crown = 1.8;
center_pavilion = 3.5;

side_d = 4;
side_crown = 1.4;
side_pavilion = 2.5;
side_spacing = 6.5;

prong_w = 0.8;
prong_h_center = 5.5;
prong_h_side = 4;
prong_count = 4;

// --- Derived ---
band_outer_r = band_inner_d / 2 + band_thickness;
setting_z = band_outer_r;

// --- Modules ---

module band() {
    rotate_extrude()
        translate([band_inner_d / 2 + band_thickness / 2, 0])
            offset(r = 0.3) offset(r = -0.3)
            square([band_thickness, band_width], center = true);
}

module gem(d, crown_h, pav_h) {
    cylinder(r1 = 0, r2 = d / 2, h = pav_h, $fn = 16);
    translate([0, 0, pav_h - eps])
        cylinder(r1 = d / 2, r2 = d / 2 * 0.55, h = crown_h, $fn = 16);
}

module prong_set(gem_d, ph) {
    for (i = [0 : prong_count - 1])
        rotate([0, 0, i * 360 / prong_count + 45])
            translate([gem_d / 2 - 0.2, -prong_w / 2, 0])
                hull() {
                    cube([prong_w, prong_w, ph - 0.8]);
                    translate([-0.2, 0, ph - 0.5])
                        cube([prong_w * 0.5, prong_w, 0.5]);
                }
}

module main() {
    band();

    // Center stone
    translate([0, 0, setting_z]) {
        translate([0, 0, 1])
            gem(center_d, center_crown, center_pavilion);
        prong_set(center_d, prong_h_center);
    }

    // Side stones — mirror for symmetry
    for (s = [-1, 1])
        translate([0, s * side_spacing, setting_z]) {
            translate([0, 0, 0.5])
                gem(side_d, side_crown, side_pavilion);
            prong_set(side_d, prong_h_side);
        }
}

main();
""",
    },
    {
        "name": "Pave Eternity Band",
        "category": "jewelry",
        "prompt": "full eternity band ring with pave set diamonds all around",
        "code": """\
// DESIGN: eternity band — continuous ring of pave-set diamonds embedded in the band
// PARTS: band_shell, pave_stones
// TREE: band_shell → pave_stones (recessed into band)
// CONNECTIONS: stone_ring_r

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
band_width = 3.5;
band_thickness = 2;
stone_d = 1.6;
stone_rows = 2;
stones_per_row = 28;
stone_depth = 0.4;

// --- Derived ---
band_outer_r = band_inner_d / 2 + band_thickness;
stone_ring_r = band_outer_r - stone_depth;

// --- Modules ---

module band_shell() {
    difference() {
        rotate_extrude()
            translate([band_inner_d / 2 + band_thickness / 2, 0])
                offset(r = 0.4) offset(r = -0.4)
                square([band_thickness, band_width], center = true);
        // Drill holes for each stone
        for (row = [0 : stone_rows - 1]) {
            row_z = (row - (stone_rows - 1) / 2) * stone_d * 1.15;
            offset_angle = row * (360 / stones_per_row / 2);
            for (i = [0 : stones_per_row - 1])
                rotate([0, 0, i * 360 / stones_per_row + offset_angle])
                    translate([stone_ring_r, 0, row_z])
                        sphere(d = stone_d * 1.05, $fn = 12);
        }
    }
}

module pave_stones() {
    for (row = [0 : stone_rows - 1]) {
        row_z = (row - (stone_rows - 1) / 2) * stone_d * 1.15;
        offset_angle = row * (360 / stones_per_row / 2);
        for (i = [0 : stones_per_row - 1])
            rotate([0, 0, i * 360 / stones_per_row + offset_angle])
                translate([stone_ring_r, 0, row_z])
                    sphere(d = stone_d, $fn = 12);
    }
}

module main() {
    band_shell();
    pave_stones();
}

main();
""",
    },
    {
        "name": "Cathedral Setting Diamond Ring",
        "category": "jewelry",
        "prompt": "cathedral setting engagement ring with arched supports holding center diamond",
        "code": """\
// DESIGN: cathedral ring — band rises into arched supports that cradle the center stone
// PARTS: band, cathedral_arches, setting_base, gem, prongs
// TREE: band → cathedral_arches → setting_base → gem + prongs
// CONNECTIONS: arch_peak_z, setting_z

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
band_width = 2.5;
band_thickness = 1.5;

gem_d = 6.5;
gem_crown = 2;
gem_pavilion = 4;

arch_width = 2.5;
arch_thickness = 1.2;
setting_d = 8;
setting_h = 2;

prong_count = 6;
prong_w = 0.8;
prong_h = 5;

// --- Derived ---
band_outer_r = band_inner_d / 2 + band_thickness;
arch_peak_z = band_outer_r + 3;
setting_z = arch_peak_z - 1;

// --- Modules ---

module band() {
    rotate_extrude()
        translate([band_inner_d / 2 + band_thickness / 2, 0])
            offset(r = 0.3) offset(r = -0.3)
            square([band_thickness, band_width], center = true);
}

module cathedral_arch() {
    hull() {
        translate([0, 0, band_outer_r - 1])
            cube([arch_thickness, arch_width, 0.1], center = true);
        translate([0, 0, arch_peak_z])
            cube([arch_thickness, arch_width * 0.7, 0.1], center = true);
    }
}

module cathedral_arches() {
    for (angle = [0, 180])
        rotate([0, 0, angle])
            translate([0, 0, 0])
            cathedral_arch();
}

module setting_base() {
    translate([0, 0, setting_z])
        difference() {
            cylinder(d = setting_d, h = setting_h);
            translate([0, 0, -eps])
                cylinder(d = setting_d - 2, h = setting_h + 2 * eps);
        }
}

module gem() {
    translate([0, 0, setting_z + setting_h - gem_pavilion + 0.5]) {
        cylinder(r1 = 0, r2 = gem_d / 2, h = gem_pavilion, $fn = 16);
        translate([0, 0, gem_pavilion - eps])
            cylinder(r1 = gem_d / 2, r2 = gem_d / 2 * 0.55, h = gem_crown, $fn = 16);
    }
}

module prongs() {
    translate([0, 0, setting_z])
        for (i = [0 : prong_count - 1])
            rotate([0, 0, i * 360 / prong_count])
                translate([setting_d / 2 - prong_w, -prong_w / 2, 0])
                    cube([prong_w, prong_w, prong_h]);
}

module main() {
    band();
    cathedral_arches();
    setting_base();
    gem();
    prongs();
}

main();
""",
    },
    {
        "name": "Split Shank Diamond Ring",
        "category": "jewelry",
        "prompt": "split shank engagement ring where band divides into two strands meeting at the center stone",
        "code": """\
// DESIGN: split shank ring — band splits into two strands that sweep up to cradle the setting
// PARTS: shank_left, shank_right, bridge, setting, gem, prongs
// TREE: shank_left + shank_right → bridge → setting → gem + prongs
// CONNECTIONS: split_start_angle, setting_z

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
band_thickness = 1.5;
band_width = 2.5;
strand_width = 1.2;
split_gap = 2;

gem_d = 6;
gem_crown = 1.8;
gem_pavilion = 3.5;

setting_d = 8;
setting_h = 2;
prong_count = 6;
prong_w = 0.8;
prong_h = 5;

split_angle = 120;

// --- Derived ---
band_outer_r = band_inner_d / 2 + band_thickness;
setting_z = band_outer_r + 1;
half_r = band_inner_d / 2 + band_thickness / 2;

// --- Modules ---

module main_shank() {
    rotate_extrude(angle = 360 - split_angle)
        translate([half_r, 0])
            offset(r = 0.2) offset(r = -0.2)
            square([band_thickness, band_width], center = true);
}

module split_strand(offset_z) {
    rotate([0, 0, 360 - split_angle])
        rotate_extrude(angle = split_angle / 2)
            translate([half_r, offset_z])
                circle(d = strand_width);
}

module bridge() {
    translate([0, 0, setting_z - 1])
        cylinder(d = setting_d + 2, h = 1);
}

module setting() {
    translate([0, 0, setting_z])
        difference() {
            cylinder(d = setting_d, h = setting_h);
            translate([0, 0, -eps])
                cylinder(d = setting_d - 2, h = setting_h + 2 * eps);
        }
}

module gem() {
    translate([0, 0, setting_z + setting_h - gem_pavilion + 0.5]) {
        cylinder(r1 = 0, r2 = gem_d / 2, h = gem_pavilion, $fn = 16);
        translate([0, 0, gem_pavilion - eps])
            cylinder(r1 = gem_d / 2, r2 = gem_d / 2 * 0.55, h = gem_crown, $fn = 16);
    }
}

module prongs() {
    translate([0, 0, setting_z])
        for (i = [0 : prong_count - 1])
            rotate([0, 0, i * 360 / prong_count])
                translate([setting_d / 2 - prong_w, -prong_w / 2, 0])
                    cube([prong_w, prong_w, prong_h]);
}

module main() {
    main_shank();
    split_strand(split_gap / 2);
    split_strand(-split_gap / 2);
    bridge();
    setting();
    gem();
    prongs();
}

main();
""",
    },
    {
        "name": "Twisted Rope Band Diamond Ring",
        "category": "jewelry",
        "prompt": "twisted rope texture band ring with small diamond accent",
        "code": """\
// DESIGN: twisted rope ring — two intertwined wire strands forming the band, small accent gem on top
// PARTS: strand_a, strand_b, accent_gem, accent_setting
// TREE: strand_a + strand_b → accent_setting → accent_gem
// CONNECTIONS: accent_z

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
wire_d = 1.8;
twist_count = 12;
twist_offset = 1.2;
accent_gem_d = 2.5;
accent_crown = 0.8;
accent_pavilion = 1.5;
bezel_wall = 0.6;

// --- Derived ---
band_r = band_inner_d / 2 + wire_d / 2;
accent_z = band_r + wire_d / 2;

// --- Modules ---

module rope_strand(phase) {
    steps = twist_count * 10;
    for (i = [0 : steps - 1]) {
        a1 = i * 360 / steps;
        a2 = (i + 1) * 360 / steps;
        t1 = phase + i * twist_count * 360 / steps;
        t2 = phase + (i + 1) * twist_count * 360 / steps;
        hull() {
            rotate([0, 0, a1])
                translate([band_r + twist_offset * cos(t1), 0, twist_offset * sin(t1)])
                sphere(d = wire_d, $fn = 16);
            rotate([0, 0, a2])
                translate([band_r + twist_offset * cos(t2), 0, twist_offset * sin(t2)])
                sphere(d = wire_d, $fn = 16);
        }
    }
}

module accent_gem() {
    translate([band_r + twist_offset, 0, accent_z]) {
        cylinder(r1 = 0, r2 = accent_gem_d / 2, h = accent_pavilion, $fn = 12);
        translate([0, 0, accent_pavilion - eps])
            cylinder(r1 = accent_gem_d / 2, r2 = accent_gem_d / 2 * 0.5, h = accent_crown, $fn = 12);
    }
}

module accent_bezel() {
    translate([band_r + twist_offset, 0, accent_z - 0.5])
        difference() {
            cylinder(d = accent_gem_d + bezel_wall * 2, h = accent_pavilion * 0.6);
            translate([0, 0, -eps])
                cylinder(d = accent_gem_d + 0.2, h = accent_pavilion + eps);
        }
}

module main() {
    rope_strand(0);
    rope_strand(180);
    accent_bezel();
    accent_gem();
}

main();
""",
    },
    {
        "name": "Vintage Filigree Diamond Ring",
        "category": "jewelry",
        "prompt": "vintage art deco filigree ring with milgrain edges and center diamond",
        "code": """\
// DESIGN: vintage filigree ring — ornate band with milgrain beading, open filigree shoulders, center diamond
// PARTS: band, filigree_shoulders, milgrain_edges, setting, gem, prongs
// TREE: band → filigree_shoulders + milgrain_edges → setting → gem + prongs
// CONNECTIONS: shoulder_z, setting_z

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
band_width = 3;
band_thickness = 1.5;

gem_d = 5.5;
gem_crown = 1.6;
gem_pavilion = 3.5;

milgrain_bead_d = 0.5;
milgrain_count = 60;

filigree_arch_count = 5;
filigree_span_angle = 60;
shoulder_width = 3.5;

setting_d = 7.5;
prong_count = 8;
prong_w = 0.6;
prong_h = 4.5;

// --- Derived ---
band_outer_r = band_inner_d / 2 + band_thickness;
setting_z = band_outer_r;

// --- Modules ---

module band() {
    rotate_extrude()
        translate([band_inner_d / 2 + band_thickness / 2, 0])
            offset(r = 0.2) offset(r = -0.2)
            square([band_thickness, band_width], center = true);
}

module milgrain_edge(z_offset) {
    for (i = [0 : milgrain_count - 1])
        rotate([0, 0, i * 360 / milgrain_count])
            translate([band_outer_r - 0.1, 0, z_offset])
                sphere(d = milgrain_bead_d, $fn = 8);
}

module milgrain_edges() {
    milgrain_edge(band_width / 2 - milgrain_bead_d / 2);
    milgrain_edge(-band_width / 2 + milgrain_bead_d / 2);
}

module filigree_shoulder() {
    arch_span = filigree_span_angle / filigree_arch_count;
    for (i = [0 : filigree_arch_count - 1]) {
        start_a = 90 - filigree_span_angle / 2 + i * arch_span;
        mid_a = start_a + arch_span / 2;
        hull() {
            rotate([0, 0, start_a])
                translate([band_outer_r, 0, 0])
                sphere(d = 0.8, $fn = 8);
            rotate([0, 0, mid_a])
                translate([band_outer_r + 0.5, 0, 1.5])
                sphere(d = 0.8, $fn = 8);
        }
        hull() {
            rotate([0, 0, mid_a])
                translate([band_outer_r + 0.5, 0, 1.5])
                sphere(d = 0.8, $fn = 8);
            rotate([0, 0, start_a + arch_span])
                translate([band_outer_r, 0, 0])
                sphere(d = 0.8, $fn = 8);
        }
    }
}

module filigree_shoulders() {
    filigree_shoulder();
    mirror([1, 0, 0]) filigree_shoulder();
}

module setting() {
    translate([0, 0, setting_z])
        difference() {
            cylinder(d = setting_d, h = 2);
            translate([0, 0, -eps])
                cylinder(d = setting_d - 1.5, h = 2 + 2 * eps);
        }
}

module gem() {
    translate([0, 0, setting_z + 2 - gem_pavilion + 0.5]) {
        cylinder(r1 = 0, r2 = gem_d / 2, h = gem_pavilion, $fn = 16);
        translate([0, 0, gem_pavilion - eps])
            cylinder(r1 = gem_d / 2, r2 = gem_d / 2 * 0.55, h = gem_crown, $fn = 16);
    }
}

module prongs() {
    translate([0, 0, setting_z])
        for (i = [0 : prong_count - 1])
            rotate([0, 0, i * 360 / prong_count])
                translate([setting_d / 2 - prong_w / 2, -prong_w / 2, 0])
                    cube([prong_w, prong_w, prong_h]);
}

module main() {
    band();
    milgrain_edges();
    filigree_shoulders();
    setting();
    gem();
    prongs();
}

main();
""",
    },
    {
        "name": "Cluster Diamond Cocktail Ring",
        "category": "jewelry",
        "prompt": "cluster cocktail ring with multiple diamonds arranged in a flower pattern",
        "code": """\
// DESIGN: cluster cocktail ring — wide band topped with a flower-shaped cluster of diamonds
// PARTS: band, base_plate, center_gem, petal_gems, accent_gems
// TREE: band → base_plate → center_gem + petal_gems + accent_gems
// CONNECTIONS: plate_z, gem_z

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
band_width = 3;
band_thickness = 1.8;

center_d = 4;
petal_d = 3.2;
petal_count = 6;
petal_ring_r = 4.2;

accent_d = 1.8;
accent_count = 12;
accent_ring_r = 7;

plate_d = 16;
plate_h = 1.5;
crown_h = 1.2;
pavilion_h = 2.5;

// --- Derived ---
band_outer_r = band_inner_d / 2 + band_thickness;
plate_z = band_outer_r - 0.5;

// --- Modules ---

module band() {
    rotate_extrude()
        translate([band_inner_d / 2 + band_thickness / 2, 0])
            offset(r = 0.3) offset(r = -0.3)
            square([band_thickness, band_width], center = true);
}

module base_plate() {
    translate([0, 0, plate_z])
        cylinder(d1 = plate_d - 2, d2 = plate_d, h = plate_h);
}

module mini_gem(d) {
    cylinder(r1 = 0, r2 = d / 2, h = d * 0.8, $fn = 12);
    translate([0, 0, d * 0.8 - eps])
        cylinder(r1 = d / 2, r2 = d / 2 * 0.5, h = d * 0.4, $fn = 12);
}

module center_gem() {
    translate([0, 0, plate_z + plate_h])
        mini_gem(center_d);
}

module petal_gems() {
    translate([0, 0, plate_z + plate_h])
        for (i = [0 : petal_count - 1])
            rotate([0, 0, i * 360 / petal_count])
                translate([petal_ring_r, 0, 0])
                    mini_gem(petal_d);
}

module accent_gems() {
    translate([0, 0, plate_z + plate_h - 0.3])
        for (i = [0 : accent_count - 1])
            rotate([0, 0, i * 360 / accent_count + 15])
                translate([accent_ring_r, 0, 0])
                    mini_gem(accent_d);
}

module main() {
    band();
    base_plate();
    center_gem();
    petal_gems();
    accent_gems();
}

main();
""",
    },
    {
        "name": "Bezel Set Diamond Ring",
        "category": "jewelry",
        "prompt": "modern bezel set diamond ring with sleek metal collar holding the stone flush",
        "code": """\
// DESIGN: bezel ring — clean modern band with a metal collar wrapping the gemstone flush
// PARTS: band, bezel_collar, gem
// TREE: band → bezel_collar → gem
// CONNECTIONS: bezel_z

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
band_width = 3;
band_thickness = 2;

gem_d = 6;
gem_crown = 1.5;
gem_pavilion = 3.5;

bezel_wall = 1;
bezel_h = 3;

// --- Derived ---
band_outer_r = band_inner_d / 2 + band_thickness;
bezel_z = band_outer_r - 1;
bezel_outer_d = gem_d + bezel_wall * 2;

// --- Modules ---

module band() {
    rotate_extrude()
        translate([band_inner_d / 2 + band_thickness / 2, 0])
            offset(r = 0.4) offset(r = -0.4)
            square([band_thickness, band_width], center = true);
}

module bezel_collar() {
    translate([0, 0, bezel_z])
        difference() {
            cylinder(d = bezel_outer_d, h = bezel_h);
            translate([0, 0, bezel_h - gem_crown - 0.2])
                cylinder(d = gem_d + 0.2, h = gem_crown + 0.5);
            translate([0, 0, -eps])
                cylinder(d = gem_d - 1, h = bezel_h + 2 * eps);
        }
}

module gem() {
    translate([0, 0, bezel_z + bezel_h - gem_crown - gem_pavilion + 0.5]) {
        cylinder(r1 = 0, r2 = gem_d / 2, h = gem_pavilion, $fn = 16);
        translate([0, 0, gem_pavilion - eps])
            cylinder(r1 = gem_d / 2, r2 = gem_d / 2 * 0.55, h = gem_crown, $fn = 16);
    }
}

module main() {
    band();
    bezel_collar();
    gem();
}

main();
""",
    },
    {
        "name": "Channel Set Diamond Band",
        "category": "jewelry",
        "prompt": "channel set wedding band with row of princess cut diamonds between two rails",
        "code": """\
// DESIGN: channel set band — two raised metal rails with square-cut gems recessed between them
// PARTS: band_base, channel_rails, channel_gems
// TREE: band_base → channel_rails + channel_gems
// CONNECTIONS: rail_z, gem_z

$fn = 128;
eps = 0.01;

// --- Parameters ---
band_inner_d = 17.3;
band_width = 4;
band_thickness = 1.8;
rail_height = 0.8;
rail_width = 0.6;
channel_width = 2.2;
gem_size = 2;
gem_count = 16;
gem_depth = 1.5;

// --- Derived ---
band_outer_r = band_inner_d / 2 + band_thickness;
rail_offset = channel_width / 2 + rail_width / 2;

// --- Modules ---

module band_base() {
    rotate_extrude()
        translate([band_inner_d / 2 + band_thickness / 2, 0])
            offset(r = 0.3) offset(r = -0.3)
            square([band_thickness, band_width], center = true);
}

module channel_rails() {
    for (side = [-1, 1])
        rotate_extrude()
            translate([band_outer_r - 0.2, side * rail_offset])
                square([rail_height, rail_width], center = true);
}

module channel_gems() {
    for (i = [0 : gem_count - 1])
        rotate([0, 0, i * 360 / gem_count])
            translate([band_outer_r - gem_depth / 2, 0, 0])
                cube([gem_depth, gem_size * 0.9, gem_size * 0.9], center = true);
}

module main() {
    band_base();
    channel_rails();
    color("white", 0.9) channel_gems();
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # TOYS & FUN
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Fidget Spinner",
        "category": "general",
        "prompt": "three arm fidget spinner with bearing holes",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
arm_count = 3;
arm_length = 30;
arm_r = 12;
center_r = 14;
spinner_h = 8;
bearing_d = 22.2;
bearing_h = 7;
weight_d = 18;
weight_h = 5;

// --- Modules ---
module arm() {
    hull() {
        cylinder(r = center_r, h = spinner_h);
        translate([arm_length, 0, 0])
            cylinder(r = arm_r, h = spinner_h);
    }
}

module body() {
    for (i = [0 : arm_count - 1])
        rotate([0, 0, i * 360 / arm_count])
            arm();
}

module main() {
    difference() {
        body();
        // Center bearing
        translate([0, 0, (spinner_h - bearing_h) / 2])
            cylinder(d = bearing_d, h = bearing_h + eps);
        // Weight holes
        for (i = [0 : arm_count - 1])
            rotate([0, 0, i * 360 / arm_count])
                translate([arm_length, 0, (spinner_h - weight_h) / 2])
                    cylinder(d = weight_d, h = weight_h + eps);
    }
}

main();
""",
    },
    {
        "name": "Chess Pawn",
        "category": "general",
        "prompt": "chess pawn piece",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
base_r = 12;
base_h = 5;
stem_r = 5;
stem_h = 20;
collar_r = 8;
collar_h = 3;
head_r = 7;
total_h = base_h + stem_h + collar_h + head_r * 2;

// --- Modules ---
module base() {
    cylinder(r1 = base_r, r2 = base_r - 1, h = base_h);
}

module stem() {
    translate([0, 0, base_h - eps])
        cylinder(r1 = stem_r + 1, r2 = stem_r, h = stem_h);
}

module collar() {
    translate([0, 0, base_h + stem_h - eps])
        cylinder(r1 = stem_r, r2 = collar_r, h = collar_h / 2);
    translate([0, 0, base_h + stem_h + collar_h / 2 - 2 * eps])
        cylinder(r1 = collar_r, r2 = stem_r + 1, h = collar_h / 2);
}

module head() {
    translate([0, 0, base_h + stem_h + collar_h - eps])
        sphere(r = head_r);
}

module main() {
    base();
    stem();
    collar();
    head();
}

main();
""",
    },
    {
        "name": "Desk Nameplate",
        "category": "general",
        "prompt": "desk nameplate stand with angled face",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
plate_w = 150;
plate_h = 40;
plate_t = 5;
base_w = 150;
base_d = 35;
base_h = 8;
face_angle = 75;
corner_r = 3;

// --- Connection points ---
plate_z = base_h - eps;

// --- Modules ---
module base() {
    minkowski() {
        cube([base_w - 2 * corner_r, base_d - 2 * corner_r, base_h / 2]);
        cylinder(r = corner_r, h = base_h / 2);
    }
}

module face_plate() {
    translate([0, 0, plate_z])
        rotate([90 - face_angle, 0, 0])
        cube([plate_w, plate_h, plate_t]);
}

module main() {
    base();
    face_plate();
}

main();
""",
    },
    {
        "name": "Keychain Tag",
        "category": "general",
        "prompt": "rounded keychain tag with hole for key ring",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
tag_w = 45;
tag_h = 25;
tag_t = 4;
corner_r = 5;
ring_hole_d = 6;
ring_hole_margin = 8;

// --- Modules ---
module tag_body() {
    minkowski() {
        cube([tag_w - 2 * corner_r, tag_h - 2 * corner_r, tag_t / 2]);
        cylinder(r = corner_r, h = tag_t / 2);
    }
}

module ring_hole() {
    translate([tag_w - ring_hole_margin, tag_h / 2, -eps])
        cylinder(d = ring_hole_d, h = tag_t + 2 * eps);
}

module main() {
    difference() {
        tag_body();
        ring_hole();
    }
}

main();
""",
    },
    {
        "name": "Desk Fan",
        "category": "general",
        "prompt": "desk fan with blades and guard",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
base_r = 40;
base_h = 8;
neck_r = 6;
neck_h = 50;
motor_r = 15;
motor_h = 20;
guard_r = 45;
guard_ring_r = 3;
blade_count = 5;
blade_len = 38;
blade_w = 12;
blade_t = 2;

// --- Connection points ---
neck_top_z = base_h + neck_h - eps;
motor_z = neck_top_z;
guard_z = motor_z + motor_h / 2;

// --- Modules ---
module base() {
    cylinder(r1 = base_r, r2 = base_r - 2, h = base_h);
}

module neck() {
    translate([0, 0, base_h - eps])
        cylinder(r = neck_r, h = neck_h);
}

module motor_housing() {
    translate([0, 0, motor_z])
        rotate([0, 90, 0])
        cylinder(r = motor_r, h = motor_h, center = true);
}

module guard() {
    translate([0, 0, guard_z])
        rotate([0, 90, 0])
        rotate_extrude()
            translate([guard_r, 0])
            circle(r = guard_ring_r);
}

module blade() {
    hull() {
        cylinder(r = blade_w / 2, h = blade_t, center = true);
        translate([0, blade_len, 0])
            scale([0.5, 1, 1])
            cylinder(r = blade_w / 2, h = blade_t, center = true);
    }
}

module blades() {
    translate([0, 0, guard_z])
        rotate([0, 90, 0])
        for (i = [0 : blade_count - 1])
            rotate([0, 0, i * 360 / blade_count])
                blade();
}

module main() {
    base();
    neck();
    motor_housing();
    guard();
    blades();
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # VEHICLES & TRANSPORT
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Simple Car",
        "category": "vehicle",
        "prompt": "simple toy car with wheels",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
body_l = 120;
body_w = 50;
body_h = 20;
cabin_l = 50;
cabin_w = 44;
cabin_h = 22;
cabin_offset_y = 10;
wheel_r = 12;
wheel_t = 8;
axle_r = 2;
wheelbase = 80;
track = body_w + 2;
corner_r = 5;

// --- Connection points ---
cabin_z = body_h - eps;
front_axle_y = body_l / 2 - 20;
rear_axle_y = -body_l / 2 + 20;

// --- Modules ---
module body() {
    translate([0, 0, 0])
        minkowski() {
            translate([-(body_w - 2 * corner_r) / 2, -(body_l - 2 * corner_r) / 2, 0])
                cube([body_w - 2 * corner_r, body_l - 2 * corner_r, body_h / 2]);
            cylinder(r = corner_r, h = body_h / 2);
        }
}

module cabin() {
    translate([0, cabin_offset_y, cabin_z])
        hull() {
            translate([-(cabin_w) / 2, -(cabin_l) / 2, 0])
                cube([cabin_w, cabin_l, eps]);
            translate([-(cabin_w - 8) / 2, -(cabin_l - 10) / 2, cabin_h])
                cube([cabin_w - 8, cabin_l - 10, eps]);
        }
}

module wheel() {
    rotate([0, 90, 0])
        cylinder(r = wheel_r, h = wheel_t, center = true);
}

module wheels() {
    for (y = [front_axle_y, rear_axle_y])
        for (x = [track / 2, -track / 2])
            translate([x, y, 0])
                wheel();
}

module main() {
    translate([0, 0, wheel_r + 2]) {
        body();
        cabin();
        wheels();
    }
}

main();
""",
    },
    {
        "name": "Boat Hull",
        "category": "vehicle",
        "prompt": "simple boat hull with flat bottom",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
hull_l = 200;
hull_w = 60;
hull_h = 30;
wall = 3;
bow_taper = 60;
stern_taper = 30;
keel_h = 5;

// --- Modules ---
module outer_hull() {
    hull() {
        // Bow
        translate([0, hull_l / 2, hull_h / 2])
            scale([hull_w * 0.15, bow_taper, hull_h / 2])
            sphere(r = 1);
        // Mid-section
        translate([0, 0, 0])
            scale([hull_w / 2, hull_l / 3, 1])
            cylinder(r = 1, h = hull_h);
        // Stern
        translate([0, -hull_l / 2 + stern_taper, 0])
            scale([hull_w * 0.4, stern_taper, 1])
            cylinder(r = 1, h = hull_h * 0.8);
    }
}

module inner_cavity() {
    translate([0, 0, wall])
        scale([(hull_w - 2 * wall) / hull_w, (hull_l - 2 * wall) / hull_l, 1])
        hull() {
            translate([0, hull_l / 2, hull_h / 2])
                scale([hull_w * 0.12, bow_taper - wall, hull_h / 2 - wall])
                sphere(r = 1);
            translate([0, 0, 0])
                scale([hull_w / 2 - wall, hull_l / 3, 1])
                cylinder(r = 1, h = hull_h);
            translate([0, -hull_l / 2 + stern_taper + wall, 0])
                scale([hull_w * 0.35, stern_taper - wall, 1])
                cylinder(r = 1, h = hull_h * 0.8 - wall);
        }
}

module main() {
    difference() {
        outer_hull();
        inner_cavity();
    }
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # POPULAR SHAPES / MISCELLANEOUS
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Heart Shape",
        "category": "general",
        "prompt": "3D heart shape love heart",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
heart_size = 40;
heart_h = 15;

// --- Derived ---
r = heart_size / 4;

// --- Modules ---
module heart_2d() {
    union() {
        translate([-r, 0]) circle(r = r);
        translate([r, 0]) circle(r = r);
        rotate([0, 0, 45])
            square([r * 2, r * 2], center = true);
    }
}

module main() {
    linear_extrude(height = heart_h, scale = 0.5)
        heart_2d();
}

main();
""",
    },
    {
        "name": "Star Shape",
        "category": "general",
        "prompt": "five pointed star shape",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
outer_r = 40;
inner_r = 18;
star_h = 10;
points = 5;

// --- Modules ---
module star_2d() {
    polygon([
        for (i = [0 : 2 * points - 1])
            let(angle = i * 180 / points - 90,
                r = i % 2 == 0 ? outer_r : inner_r)
            [r * cos(angle), r * sin(angle)]
    ]);
}

module main() {
    linear_extrude(height = star_h)
        star_2d();
}

main();
""",
    },
    {
        "name": "Pyramid",
        "category": "general",
        "prompt": "square pyramid like Egyptian pyramid",
        "code": """\
$fn = 4;
eps = 0.01;

// --- Parameters ---
base_size = 80;
pyramid_h = 60;

// --- Modules ---
module main() {
    rotate([0, 0, 45])
        cylinder(d1 = base_size * sqrt(2), d2 = 0, h = pyramid_h, $fn = 4);
}

main();
""",
    },
    {
        "name": "Trophy Cup",
        "category": "general",
        "prompt": "trophy cup with handles and base",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
cup_top_r = 35;
cup_bot_r = 15;
cup_h = 60;
cup_wall = 2.5;
stem_r = 6;
stem_h = 30;
base_r = 30;
base_h = 8;
handle_r = 15;
handle_t = 5;

// --- Connection points ---
stem_z = base_h - eps;
cup_z = stem_z + stem_h - eps;

// --- Modules ---
module base() {
    cylinder(r1 = base_r, r2 = base_r - 2, h = base_h);
}

module stem() {
    translate([0, 0, stem_z])
        cylinder(r = stem_r, h = stem_h);
}

module cup() {
    translate([0, 0, cup_z])
        difference() {
            cylinder(r1 = cup_bot_r, r2 = cup_top_r, h = cup_h);
            translate([0, 0, cup_wall])
                cylinder(r1 = cup_bot_r - cup_wall, r2 = cup_top_r - cup_wall, h = cup_h);
        }
}

module cup_handle() {
    translate([0, 0, cup_z + cup_h / 2])
        rotate([0, 90, 0])
        difference() {
            cylinder(r = handle_r, h = handle_t, center = true);
            cylinder(r = handle_r - handle_t, h = handle_t + 2 * eps, center = true);
            translate([0, -handle_r, 0])
                cube([handle_r * 2, handle_r, handle_t + 2 * eps], center = true);
        }
}

module handles() {
    mid_r = (cup_bot_r + cup_top_r) / 2;
    translate([mid_r + handle_r - 2, 0, 0])
        cup_handle();
    translate([-(mid_r + handle_r - 2), 0, 0])
        cup_handle();
}

module main() {
    base();
    stem();
    cup();
    handles();
}

main();
""",
    },
    {
        "name": "Picture Frame",
        "category": "household",
        "prompt": "picture frame with stand",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
frame_w = 130;
frame_h = 100;
frame_t = 8;
border_w = 12;
photo_w = 106;
photo_h = 76;
stand_w = 20;
stand_h = 80;
stand_t = 5;
stand_angle = 70;

// --- Modules ---
module frame() {
    difference() {
        cube([frame_w, frame_h, frame_t]);
        translate([border_w, border_w, -eps])
            cube([photo_w, photo_h, frame_t + 2 * eps]);
    }
}

module stand() {
    translate([frame_w / 2 - stand_w / 2, 0, 0])
        rotate([180 - stand_angle, 0, 0])
        cube([stand_w, stand_h, stand_t]);
}

module main() {
    frame();
    stand();
}

main();
""",
    },
    {
        "name": "Candle Holder",
        "category": "household",
        "prompt": "candle holder with drip tray",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
base_d = 80;
base_h = 6;
stem_d = 14;
stem_h = 40;
tray_d = 60;
tray_h = 4;
tray_wall = 2;
candle_d = 22;
candle_depth = 15;

// --- Connection points ---
stem_z = base_h - eps;
tray_z = stem_z + stem_h - eps;

// --- Modules ---
module base() {
    cylinder(d = base_d, h = base_h);
}

module stem() {
    translate([0, 0, stem_z])
        cylinder(d = stem_d, h = stem_h);
}

module drip_tray() {
    translate([0, 0, tray_z])
        difference() {
            cylinder(d = tray_d, h = tray_h);
            translate([0, 0, tray_wall])
                cylinder(d = tray_d - 2 * tray_wall, h = tray_h);
        }
}

module candle_cup() {
    translate([0, 0, tray_z + tray_h - eps])
        difference() {
            cylinder(d = candle_d + 2 * tray_wall, h = candle_depth);
            translate([0, 0, tray_wall])
                cylinder(d = candle_d + 0.5, h = candle_depth);
        }
}

module main() {
    base();
    stem();
    drip_tray();
    candle_cup();
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # INDUSTRIAL / MECHANICAL / ENGINEERING
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Pipe Flange",
        "category": "pipe_fitting",
        "prompt": "pipe flange with bolt holes",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
pipe_od = 50;
pipe_id = 44;
flange_d = 100;
flange_t = 12;
bolt_count = 6;
bolt_d = 10;
bolt_circle_d = 78;
raised_face_d = 62;
raised_face_h = 2;

// --- Modules ---
module flange_disc() {
    cylinder(d = flange_d, h = flange_t);
}

module pipe_bore() {
    translate([0, 0, -eps])
        cylinder(d = pipe_id, h = flange_t + 2 * eps);
}

module bolt_holes() {
    for (i = [0 : bolt_count - 1])
        rotate([0, 0, i * 360 / bolt_count])
            translate([bolt_circle_d / 2, 0, -eps])
                cylinder(d = bolt_d, h = flange_t + 2 * eps);
}

module raised_face() {
    translate([0, 0, flange_t - eps])
        cylinder(d = raised_face_d, h = raised_face_h);
}

module main() {
    difference() {
        union() {
            flange_disc();
            raised_face();
        }
        pipe_bore();
        bolt_holes();
    }
}

main();
""",
    },
    {
        "name": "T-Slot Extrusion",
        "category": "structural",
        "prompt": "20x20 aluminum T-slot extrusion profile cross section",
        "code": """\
$fn = 32;
eps = 0.01;

// --- Parameters ---
size = 20;
slot_w = 6;
slot_depth = 6;
wall = 2;
center_bore_d = 5;
length = 50;
corner_r = 0.5;

// --- Derived ---
half = size / 2;

// --- Modules ---
module profile_2d() {
    difference() {
        // Outer square
        offset(r = corner_r) offset(r = -corner_r)
            square([size, size], center = true);
        // Center hole
        circle(d = center_bore_d);
        // T-slots on all 4 sides
        for (i = [0 : 3])
            rotate([0, 0, i * 90])
                translate([0, half - slot_depth / 2 + eps])
                    square([slot_w, slot_depth + eps], center = true);
        // Corner relief
        for (i = [0 : 3])
            rotate([0, 0, i * 90 + 45])
                translate([0, half - wall])
                    square([size - 2 * wall - 2 * slot_depth, wall * 2], center = true);
    }
}

module main() {
    linear_extrude(height = length)
        profile_2d();
}

main();
""",
    },
    {
        "name": "Motor Mount Bracket",
        "category": "bracket",
        "prompt": "NEMA 17 stepper motor mounting bracket",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
// NEMA 17 dimensions
motor_w = 42.3;
bolt_spacing = 31;
shaft_d = 22;
bolt_d = 3.4;
plate_t = 5;
wall_h = 20;
wall_t = 4;
mount_hole_d = 5;
mount_hole_spacing = 30;
corner_r = 3;

// --- Derived ---
plate_w = motor_w + 10;

// --- Modules ---
module face_plate() {
    difference() {
        // Plate with rounded corners
        minkowski() {
            cube([plate_w - 2 * corner_r, plate_w - 2 * corner_r, plate_t / 2]);
            cylinder(r = corner_r, h = plate_t / 2);
        }
        // Shaft opening
        translate([plate_w / 2, plate_w / 2, -eps])
            cylinder(d = shaft_d, h = plate_t + 2 * eps);
        // Motor bolt holes
        for (dx = [-1, 1])
            for (dy = [-1, 1])
                translate([plate_w / 2 + dx * bolt_spacing / 2, plate_w / 2 + dy * bolt_spacing / 2, -eps])
                    cylinder(d = bolt_d, h = plate_t + 2 * eps);
    }
}

module side_wall() {
    translate([0, -wall_t, plate_t - eps])
        cube([plate_w, wall_t, wall_h]);
}

module mount_holes() {
    for (x = [plate_w / 2 - mount_hole_spacing / 2, plate_w / 2 + mount_hole_spacing / 2])
        translate([x, -wall_t - eps, plate_t + wall_h / 2])
            rotate([-90, 0, 0])
            cylinder(d = mount_hole_d, h = wall_t + 2 * eps);
}

module main() {
    difference() {
        union() {
            face_plate();
            side_wall();
        }
        mount_holes();
    }
}

main();
""",
    },
    {
        "name": "Pulley",
        "category": "gear",
        "prompt": "V-belt pulley with keyway and set screw",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
outer_d = 60;
groove_depth = 8;
groove_angle = 38;
pulley_h = 20;
bore_d = 12;
hub_d = 24;
hub_h = 10;
key_w = 4;
key_depth = 2.5;
set_screw_d = 4;
flange_h = 3;

// --- Derived ---
groove_r = outer_d / 2 - groove_depth;

// --- Modules ---
module pulley_profile() {
    rotate_extrude() {
        // Main body
        difference() {
            polygon([
                [bore_d / 2, 0],
                [outer_d / 2, 0],
                [outer_d / 2, flange_h],
                [groove_r, pulley_h / 2],
                [outer_d / 2, pulley_h - flange_h],
                [outer_d / 2, pulley_h],
                [bore_d / 2, pulley_h]
            ]);
        }
    }
}

module bore() {
    translate([0, 0, -eps])
        cylinder(d = bore_d, h = pulley_h + hub_h + 2 * eps);
}

module keyway() {
    translate([-key_w / 2, bore_d / 2 - key_depth, -eps])
        cube([key_w, key_depth + eps, pulley_h + hub_h + 2 * eps]);
}

module set_screw_hole() {
    translate([0, 0, pulley_h + hub_h / 2])
        rotate([90, 0, 0])
        cylinder(d = set_screw_d, h = hub_d / 2 + eps);
}

module hub() {
    translate([0, 0, pulley_h - eps])
        cylinder(d = hub_d, h = hub_h);
}

module main() {
    difference() {
        union() {
            pulley_profile();
            hub();
        }
        bore();
        keyway();
        set_screw_hole();
    }
}

main();
""",
    },
    {
        "name": "Pipe Elbow 90",
        "category": "pipe_fitting",
        "prompt": "90 degree pipe elbow fitting",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
pipe_od = 30;
pipe_id = 25;
bend_r = 40;

// --- Modules ---
module elbow() {
    difference() {
        rotate_extrude(angle = 90)
            translate([bend_r, 0])
                circle(d = pipe_od);
        rotate_extrude(angle = 90)
            translate([bend_r, 0])
                circle(d = pipe_id);
    }
}

module main() {
    elbow();
}

main();
""",
    },
    {
        "name": "DIN Rail Clip",
        "category": "bracket",
        "prompt": "DIN rail mounting clip for electronics modules",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
rail_w = 35;
rail_h = 7.5;
clip_w = 40;
clip_d = 20;
clip_h = 15;
wall = 2;
hook_depth = 3;
hook_h = 5;
spring_gap = 1;

// --- Modules ---
module clip_body() {
    cube([clip_w, clip_d, wall]);
    // Left hook (fixed)
    translate([0, 0, 0])
        cube([wall, clip_d, clip_h]);
    translate([0, 0, clip_h - hook_h])
        cube([wall + hook_depth, clip_d, hook_h]);
    // Right hook (spring)
    translate([clip_w - wall, 0, 0])
        cube([wall, clip_d, clip_h]);
    translate([clip_w - wall - hook_depth, 0, clip_h - hook_h])
        cube([wall + hook_depth, clip_d, hook_h]);
}

module rail_slot() {
    translate([wall + hook_depth, -eps, wall])
        cube([rail_w + spring_gap, clip_d + 2 * eps, rail_h + 1]);
}

module main() {
    difference() {
        clip_body();
        rail_slot();
    }
}

main();
""",
    },
    {
        "name": "Shaft Coupling",
        "category": "hinge_joint",
        "prompt": "rigid shaft coupling with set screws for two shafts",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
shaft_d = 8;
coupling_od = 22;
coupling_length = 30;
set_screw_d = 4;
gap_w = 1;
clamp_bolt_d = 3.5;

// --- Derived ---
half_l = coupling_length / 2;

// --- Modules ---
module coupling_body() {
    cylinder(d = coupling_od, h = coupling_length);
}

module shaft_bore() {
    translate([0, 0, -eps])
        cylinder(d = shaft_d + 0.3, h = coupling_length + 2 * eps);
}

module clamping_gap() {
    translate([-gap_w / 2, 0, -eps])
        cube([gap_w, coupling_od / 2 + eps, coupling_length + 2 * eps]);
}

module set_screws() {
    for (z = [half_l / 2, half_l + half_l / 2])
        translate([0, 0, z])
            rotate([90, 0, 90])
            cylinder(d = set_screw_d, h = coupling_od / 2 + eps);
}

module clamp_bolts() {
    for (z = [half_l / 2, half_l + half_l / 2])
        translate([coupling_od / 2, 0, z])
            rotate([0, 90, 0])
            cylinder(d = clamp_bolt_d, h = coupling_od);
}

module main() {
    difference() {
        coupling_body();
        shaft_bore();
        clamping_gap();
        set_screws();
    }
}

main();
""",
    },
    {
        "name": "Linear Bearing Housing",
        "category": "hinge_joint",
        "prompt": "linear bearing block housing with mounting holes",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
bearing_od = 22;
bearing_id = 12;
bearing_l = 30;
block_w = 40;
block_h = 30;
block_l = 42;
bolt_d = 5;
bolt_spacing_w = 32;
bolt_spacing_l = 28;

// --- Derived ---
bearing_z = block_h / 2;

// --- Modules ---
module block() {
    translate([-block_w / 2, -block_l / 2, 0])
        cube([block_w, block_l, block_h]);
}

module bearing_bore() {
    translate([0, 0, bearing_z])
        rotate([-90, 0, 0])
        translate([0, 0, -block_l / 2 - eps])
        cylinder(d = bearing_od + 0.3, h = block_l + 2 * eps);
}

module shaft_through() {
    translate([0, 0, bearing_z])
        rotate([-90, 0, 0])
        translate([0, 0, -block_l / 2 - eps])
        cylinder(d = bearing_id + 0.5, h = block_l + 2 * eps);
}

module bolt_holes() {
    for (dx = [-1, 1])
        for (dy = [-1, 1])
            translate([dx * bolt_spacing_w / 2, dy * bolt_spacing_l / 2, -eps])
                cylinder(d = bolt_d, h = block_h + 2 * eps);
}

module main() {
    difference() {
        block();
        bearing_bore();
        shaft_through();
        bolt_holes();
    }
}

main();
""",
    },
    {
        "name": "Sprocket",
        "category": "gear",
        "prompt": "chain sprocket with teeth and hub",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
teeth = 16;
roller_d = 8;
pitch = 12.7;
sprocket_h = 6;
bore_d = 10;
hub_d = 20;
hub_h = 8;
key_w = 3;
key_depth = 2;

// --- Derived ---
pitch_r = pitch / (2 * sin(180 / teeth));
outer_r = pitch_r + roller_d / 3;
root_r = pitch_r - roller_d / 2;
tooth_angle = 360 / teeth;

// --- Modules ---
module tooth_valley() {
    rotate([0, 0, 0])
        translate([pitch_r, 0, -eps])
            cylinder(d = roller_d + 0.5, h = sprocket_h + 2 * eps);
}

module sprocket_blank() {
    cylinder(r = outer_r, h = sprocket_h);
}

module tooth_valleys() {
    for (i = [0 : teeth - 1])
        rotate([0, 0, i * tooth_angle])
            tooth_valley();
}

module bore() {
    translate([0, 0, -eps])
        cylinder(d = bore_d, h = sprocket_h + hub_h + 2 * eps);
}

module keyway() {
    translate([-key_w / 2, bore_d / 2 - key_depth, -eps])
        cube([key_w, key_depth + eps, sprocket_h + hub_h + 2 * eps]);
}

module hub() {
    translate([0, 0, sprocket_h - eps])
        cylinder(d = hub_d, h = hub_h);
}

module main() {
    difference() {
        union() {
            sprocket_blank();
            hub();
        }
        tooth_valleys();
        bore();
        keyway();
    }
}

main();
""",
    },
    {
        "name": "Cable Gland",
        "category": "pipe_fitting",
        "prompt": "cable gland with threaded body and compression nut",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
cable_d = 8;
body_od = 18;
thread_od = 16;
body_l = 20;
hex_size = 22;
hex_h = 8;
nut_od = 18;
nut_h = 10;
grip_count = 16;
grip_depth = 0.8;

// --- Derived ---
cable_r = cable_d / 2;

// --- Modules ---
module hex_head() {
    cylinder(d = hex_size, h = hex_h, $fn = 6);
}

module threaded_body() {
    translate([0, 0, hex_h - eps])
        cylinder(d = thread_od, h = body_l);
}

module cable_bore() {
    translate([0, 0, -eps])
        cylinder(d = cable_d + 0.5, h = hex_h + body_l + 2 * eps);
}

module compression_nut() {
    translate([body_od + 10, 0, 0])
        difference() {
            cylinder(d = nut_od, h = nut_h);
            translate([0, 0, -eps])
                cylinder(d = thread_od + 0.3, h = nut_h + 2 * eps);
            // Grip ridges
            for (i = [0 : grip_count - 1])
                rotate([0, 0, i * 360 / grip_count])
                    translate([nut_od / 2, 0, -eps])
                        cylinder(r = grip_depth, h = nut_h + 2 * eps);
        }
}

module main() {
    difference() {
        union() {
            hex_head();
            threaded_body();
        }
        cable_bore();
    }
    compression_nut();
}

main();
""",
    },
    {
        "name": "Clamp",
        "category": "tool",
        "prompt": "C-clamp with threaded screw",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
frame_depth = 60;
frame_height = 80;
frame_t = 10;
frame_w = 20;
jaw_w = 30;
jaw_t = 8;
screw_d = 12;
handle_d = 8;
handle_l = 30;
pad_d = 16;
pad_h = 5;

// --- Modules ---
module c_frame() {
    // Bottom jaw
    cube([jaw_w, frame_t, jaw_t]);
    // Left side
    cube([frame_w, frame_t, frame_height]);
    // Top arm
    translate([0, 0, frame_height - jaw_t])
        cube([jaw_w + 10, frame_t, jaw_t]);
}

module screw_hole() {
    translate([jaw_w / 2, frame_t / 2, frame_height - jaw_t - eps])
        cylinder(d = screw_d, h = jaw_t + 2 * eps);
}

module screw_body() {
    // Screw extending down from top
    translate([jaw_w / 2, frame_t / 2, jaw_t + 10])
        cylinder(d = screw_d - 1, h = frame_height - 2 * jaw_t - 10);
}

module handle() {
    translate([jaw_w / 2, frame_t / 2, frame_height])
        rotate([0, 90, 0])
        cylinder(d = handle_d, h = handle_l, center = true);
}

module swivel_pad() {
    translate([jaw_w / 2, frame_t / 2, jaw_t + 10])
        cylinder(d = pad_d, h = pad_h);
}

module main() {
    difference() {
        c_frame();
        screw_hole();
    }
    screw_body();
    handle();
    swivel_pad();
}

main();
""",
    },

    # ═══════════════════════════════════════════════════════════════════════
    # MORE EVERYDAY & POPULAR OBJECTS
    # ═══════════════════════════════════════════════════════════════════════

    {
        "name": "Bookend",
        "category": "household",
        "prompt": "L-shaped bookend with anti-slip base",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
base_w = 100;
base_d = 80;
base_h = 4;
back_h = 130;
back_t = 5;
corner_r = 3;
grip_count = 4;
grip_d = 8;
grip_h = 1.5;

// --- Modules ---
module base() {
    minkowski() {
        cube([base_w - 2 * corner_r, base_d - 2 * corner_r, base_h / 2]);
        cylinder(r = corner_r, h = base_h / 2);
    }
}

module back_wall() {
    translate([0, 0, base_h - eps])
        cube([base_w, back_t, back_h]);
}

module grip_bumps() {
    for (x = [base_w * 0.2, base_w * 0.8])
        for (y = [base_d * 0.3, base_d * 0.7])
            translate([x, y, 0])
                cylinder(d = grip_d, h = grip_h);
}

module main() {
    base();
    back_wall();
    grip_bumps();
}

main();
""",
    },
    {
        "name": "Planter Pot",
        "category": "household",
        "prompt": "plant pot with drainage holes and saucer",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
pot_top_d = 100;
pot_bot_d = 60;
pot_h = 90;
wall = 3;
base_h = 4;
drain_d = 8;
drain_count = 4;
drain_ring_r = 15;
saucer_d = 110;
saucer_h = 15;
saucer_wall = 2.5;

// --- Modules ---
module pot() {
    difference() {
        cylinder(d1 = pot_bot_d, d2 = pot_top_d, h = pot_h);
        translate([0, 0, base_h])
            cylinder(d1 = pot_bot_d - 2 * wall, d2 = pot_top_d - 2 * wall, h = pot_h);
    }
}

module drain_holes() {
    for (i = [0 : drain_count - 1])
        rotate([0, 0, i * 360 / drain_count])
            translate([drain_ring_r, 0, -eps])
                cylinder(d = drain_d, h = base_h + 2 * eps);
}

module saucer() {
    translate([pot_top_d + 20, 0, 0])
        difference() {
            cylinder(d = saucer_d, h = saucer_h);
            translate([0, 0, saucer_wall])
                cylinder(d = saucer_d - 2 * saucer_wall, h = saucer_h);
        }
}

module main() {
    difference() {
        pot();
        drain_holes();
    }
    saucer();
}

main();
""",
    },
    {
        "name": "Spoon",
        "category": "household",
        "prompt": "spoon with oval bowl and rounded handle",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
handle_l = 120;
handle_w = 12;
handle_t = 5;
bowl_w = 40;
bowl_d = 30;
bowl_depth = 8;
bowl_t = 2;
handle_r = 3;

// --- Connection points ---
bowl_y = handle_l - eps;

// --- Modules ---
module handle() {
    minkowski() {
        cube([handle_w - 2 * handle_r, handle_l - 2 * handle_r, handle_t / 2]);
        cylinder(r = handle_r, h = handle_t / 2);
    }
}

module bowl() {
    translate([handle_w / 2, bowl_y + bowl_d / 2, handle_t / 2])
        difference() {
            scale([bowl_w / 2, bowl_d / 2, bowl_depth])
                sphere(r = 1);
            scale([bowl_w / 2 - bowl_t, bowl_d / 2 - bowl_t, bowl_depth - bowl_t])
                sphere(r = 1);
            translate([0, 0, -bowl_depth])
                cube([bowl_w + eps, bowl_d + eps, bowl_depth * 2], center = true);
        }
}

module main() {
    handle();
    bowl();
}

main();
""",
    },
    {
        "name": "Glasses Frame",
        "category": "general",
        "prompt": "eyeglasses frame with round lenses",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
lens_d = 44;
lens_spacing = 8;
frame_t = 3;
frame_w = 4;
temple_l = 120;
temple_w = 4;
temple_t = 3;
bridge_w = 12;
bridge_h = 6;
nose_pad_d = 8;

// --- Derived ---
lens_r = lens_d / 2;
center_x = lens_r + lens_spacing / 2;

// --- Modules ---
module lens_ring() {
    difference() {
        cylinder(d = lens_d + 2 * frame_w, h = frame_t);
        translate([0, 0, -eps])
            cylinder(d = lens_d, h = frame_t + 2 * eps);
    }
}

module front_frame() {
    // Left lens
    translate([-center_x, 0, 0]) lens_ring();
    // Right lens — mirror
    translate([center_x, 0, 0]) lens_ring();
    // Bridge
    translate([-lens_spacing / 2, -bridge_h / 2, 0])
        cube([lens_spacing, bridge_h, frame_t]);
}

module temple_arm() {
    translate([center_x + lens_r + frame_w - eps, -temple_w / 2, 0])
        cube([temple_l, temple_w, temple_t]);
}

module temples() {
    temple_arm();
    mirror([1, 0, 0]) temple_arm();
}

module main() {
    front_frame();
    temples();
}

main();
""",
    },
    {
        "name": "Helmet",
        "category": "general",
        "prompt": "safety helmet hard hat",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
head_d = 200;
shell_t = 4;
brim_w = 15;
brim_t = 3;
vent_count = 4;
vent_w = 30;
vent_h = 5;

// --- Derived ---
head_r = head_d / 2;

// --- Modules ---
module shell() {
    difference() {
        sphere(r = head_r + shell_t);
        sphere(r = head_r);
        // Cut bottom half
        translate([0, 0, -(head_r + shell_t + eps)])
            cube([(head_r + shell_t + eps) * 2, (head_r + shell_t + eps) * 2, (head_r + shell_t + eps) * 2], center = true);
        // Cut slightly above equator for head opening
        translate([0, 0, -20])
            cylinder(r = head_r + shell_t + eps, h = 20 + eps);
    }
}

module brim() {
    translate([0, 0, 0])
        difference() {
            cylinder(r = head_r + shell_t + brim_w, h = brim_t);
            translate([0, 0, -eps])
                cylinder(r = head_r, h = brim_t + 2 * eps);
            // Only front brim
            translate([0, brim_w, -eps])
                cube([(head_r + shell_t + brim_w + eps) * 2, (head_r + shell_t + brim_w + eps) * 2, brim_t + 2 * eps], center = true);
        }
}

module vents() {
    for (i = [0 : vent_count - 1])
        rotate([0, 0, i * 360 / vent_count])
            translate([0, 0, head_r * 0.6])
                rotate([45, 0, 0])
                cube([vent_w, shell_t + 2 * eps, vent_h], center = true);
}

module main() {
    difference() {
        union() {
            shell();
            brim();
        }
        vents();
    }
}

main();
""",
    },
    {
        "name": "Dice",
        "category": "general",
        "prompt": "six sided die with rounded edges and pip dots",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
die_size = 16;
corner_r = 2;
pip_d = 3;
pip_depth = 1;
pip_offset = 4;

// --- Derived ---
s = die_size / 2;
p = pip_offset;

// --- Modules ---
module die_body() {
    minkowski() {
        cube([die_size - 2 * corner_r, die_size - 2 * corner_r, die_size - 2 * corner_r], center = true);
        sphere(r = corner_r);
    }
}

module pip(x, y, z, rx, ry) {
    translate([x, y, z])
        rotate([rx, ry, 0])
        cylinder(d = pip_d, h = pip_depth + eps);
}

module pips() {
    // Face 1 (top, +Z): 1 pip center
    pip(0, 0, s - pip_depth, 0, 0);
    // Face 6 (bottom, -Z): 6 pips
    for (dx = [-p, 0, p])
        for (dy = [-p, p])
            pip(dx, dy, -s - eps, 0, 0);
    // Face 2 (+X): 2 pips diagonal
    pip(s - pip_depth, -p, p, 0, 90);
    pip(s - pip_depth, p, -p, 0, 90);
    // Face 5 (-X): 5 pips
    for (pos = [[-p, -p], [-p, p], [0, 0], [p, -p], [p, p]])
        pip(-s - eps, pos[0], pos[1], 0, -90);
    // Face 3 (+Y): 3 pips diagonal
    for (dz = [-p, 0, p])
        pip(dz, s - pip_depth, -dz, 90, 0);
    // Face 4 (-Y): 4 pips corners
    for (dx = [-p, p])
        for (dz = [-p, p])
            pip(dx, -s - eps, dz, -90, 0);
}

module main() {
    difference() {
        die_body();
        pips();
    }
}

main();
""",
    },
    {
        "name": "Screwdriver Handle",
        "category": "tool",
        "prompt": "screwdriver handle with ergonomic grip ridges",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
handle_d = 30;
handle_l = 100;
shaft_d = 6;
shaft_insert_depth = 25;
grip_count = 8;
grip_depth = 2;
taper_l = 20;
end_cap_r = 2;

// --- Modules ---
module handle_body() {
    // Main grip
    cylinder(d = handle_d, h = handle_l);
    // Rounded end cap
    translate([0, 0, handle_l - eps])
        scale([1, 1, 0.3])
        sphere(d = handle_d);
}

module taper() {
    translate([0, 0, -taper_l + eps])
        cylinder(d1 = shaft_d + 4, d2 = handle_d, h = taper_l);
}

module grip_ridges() {
    for (i = [0 : grip_count - 1])
        rotate([0, 0, i * 360 / grip_count])
            translate([handle_d / 2, 0, taper_l])
                cylinder(r = grip_depth, h = handle_l - taper_l - 10);
}

module shaft_hole() {
    translate([0, 0, -taper_l - eps])
        cylinder(d = shaft_d + 0.3, h = shaft_insert_depth + taper_l + 2 * eps);
}

module main() {
    difference() {
        union() {
            handle_body();
            taper();
        }
        grip_ridges();
        shaft_hole();
    }
}

main();
""",
    },
    {
        "name": "Wall Outlet Cover",
        "category": "enclosure",
        "prompt": "electrical wall outlet cover plate",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
plate_w = 70;
plate_h = 115;
plate_t = 4;
corner_r = 5;
outlet_w = 26;
outlet_h = 34;
outlet_spacing = 42;
screw_d = 4;
screw_spacing = 83;
bevel = 0.8;

// --- Modules ---
module plate() {
    minkowski() {
        cube([plate_w - 2 * corner_r, plate_h - 2 * corner_r, plate_t / 2]);
        cylinder(r = corner_r, h = plate_t / 2);
    }
}

module outlet_cutout(y_pos) {
    translate([plate_w / 2 - outlet_w / 2, y_pos - outlet_h / 2, -eps])
        minkowski() {
            cube([outlet_w - 4, outlet_h - 4, plate_t / 2 + eps]);
            cylinder(r = 2, h = plate_t / 2 + eps);
        }
}

module screw_holes() {
    for (y = [plate_h / 2 - screw_spacing / 2, plate_h / 2 + screw_spacing / 2])
        translate([plate_w / 2, y, -eps])
            cylinder(d = screw_d, h = plate_t + 2 * eps);
}

module main() {
    difference() {
        plate();
        outlet_cutout(plate_h / 2 - outlet_spacing / 2);
        outlet_cutout(plate_h / 2 + outlet_spacing / 2);
        screw_holes();
    }
}

main();
""",
    },
    {
        "name": "Guitar Pick",
        "category": "general",
        "prompt": "guitar pick plectrum with textured grip",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
pick_w = 26;
pick_h = 30;
pick_t = 1.2;
tip_r = 2;
corner_r = 8;
grip_dot_d = 1.5;
grip_dot_h = 0.4;
grip_rows = 3;
grip_cols = 3;

// --- Modules ---
module pick_outline() {
    hull() {
        // Tip
        translate([pick_w / 2, tip_r, 0])
            circle(r = tip_r);
        // Left shoulder
        translate([corner_r, pick_h - corner_r, 0])
            circle(r = corner_r);
        // Right shoulder
        translate([pick_w - corner_r, pick_h - corner_r, 0])
            circle(r = corner_r);
    }
}

module grip_dots() {
    for (r = [0 : grip_rows - 1])
        for (c = [0 : grip_cols - 1])
            translate([pick_w / 2 - (grip_cols - 1) * 3 / 2 + c * 3,
                       pick_h * 0.5 + r * 3,
                       pick_t - eps])
                cylinder(d = grip_dot_d, h = grip_dot_h);
}

module main() {
    linear_extrude(height = pick_t)
        pick_outline();
    grip_dots();
}

main();
""",
    },
    {
        "name": "Desk Organizer Tray",
        "category": "organizer",
        "prompt": "multi-compartment desk organizer tray",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
tray_w = 200;
tray_d = 120;
tray_h = 30;
wall = 2.5;
base_h = 2.5;
corner_r = 4;
// Compartment dividers
div1_x = 70;
div2_x = 140;
div_y = 60;

// --- Modules ---
module outer_shell() {
    difference() {
        minkowski() {
            cube([tray_w - 2 * corner_r, tray_d - 2 * corner_r, tray_h / 2]);
            cylinder(r = corner_r, h = tray_h / 2);
        }
        translate([wall, wall, base_h])
            cube([tray_w - 2 * wall, tray_d - 2 * wall, tray_h]);
    }
}

module dividers() {
    // Vertical divider 1
    translate([div1_x, wall, base_h])
        cube([wall, tray_d - 2 * wall, tray_h - base_h]);
    // Vertical divider 2
    translate([div2_x, wall, base_h])
        cube([wall, tray_d - 2 * wall, tray_h - base_h]);
    // Horizontal divider (in right section only)
    translate([div2_x + wall, div_y, base_h])
        cube([tray_w - div2_x - 2 * wall, wall, tray_h - base_h]);
}

module main() {
    outer_shell();
    dividers();
}

main();
""",
    },
    {
        "name": "Bike Phone Mount",
        "category": "bracket",
        "prompt": "bicycle handlebar phone mount clamp",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
bar_d = 25.4;
clamp_wall = 4;
clamp_h = 20;
arm_l = 40;
arm_w = 15;
arm_t = 5;
cradle_w = 75;
cradle_d = 10;
cradle_h = 12;
cradle_wall = 3;
bolt_d = 5;
slit_w = 2;

// --- Derived ---
clamp_od = bar_d + 2 * clamp_wall;

// --- Modules ---
module bar_clamp() {
    difference() {
        cylinder(d = clamp_od, h = clamp_h);
        translate([0, 0, -eps])
            cylinder(d = bar_d + 0.3, h = clamp_h + 2 * eps);
        // Clamping slit
        translate([-slit_w / 2, 0, -eps])
            cube([slit_w, clamp_od, clamp_h + 2 * eps]);
        // Bolt hole
        translate([0, clamp_od / 2 - 2, clamp_h / 2])
            rotate([0, 90, 0])
            cylinder(d = bolt_d, h = clamp_od, center = true);
    }
}

module arm() {
    translate([-arm_w / 2, -clamp_od / 2 - arm_l + eps, (clamp_h - arm_t) / 2])
        cube([arm_w, arm_l, arm_t]);
}

module phone_cradle() {
    translate([0, -clamp_od / 2 - arm_l, 0])
        difference() {
            translate([-cradle_w / 2, -cradle_d, 0])
                cube([cradle_w, cradle_d, cradle_h]);
            translate([-(cradle_w - 2 * cradle_wall) / 2, -cradle_d + cradle_wall, -eps])
                cube([cradle_w - 2 * cradle_wall, cradle_d, cradle_h + 2 * eps]);
        }
}

module main() {
    bar_clamp();
    arm();
    phone_cradle();
}

main();
""",
    },
    {
        "name": "Funnel",
        "category": "household",
        "prompt": "kitchen funnel with wide mouth and narrow spout",
        "code": """\
$fn = 64;
eps = 0.01;

// --- Parameters ---
mouth_d = 100;
spout_d = 15;
funnel_h = 80;
spout_h = 30;
wall = 2;

// --- Modules ---
module funnel_body() {
    difference() {
        union() {
            // Cone
            cylinder(d1 = spout_d, d2 = mouth_d, h = funnel_h);
            // Spout
            translate([0, 0, -spout_h + eps])
                cylinder(d = spout_d, h = spout_h);
        }
        // Inner cone
        translate([0, 0, -eps])
            cylinder(d1 = spout_d - 2 * wall, d2 = mouth_d - 2 * wall, h = funnel_h + 2 * eps);
        // Inner spout
        translate([0, 0, -spout_h])
            cylinder(d = spout_d - 2 * wall, h = spout_h + funnel_h + eps);
    }
}

module main() {
    translate([0, 0, spout_h])
        funnel_body();
}

main();
""",
    },
]


# ── Main ────────────────────────────────────────────────────────────────────


async def populate_curated(dry_run: bool = False) -> None:
    gemini_key = settings.gemini_api_key
    if not gemini_key and not dry_run:
        raise RuntimeError("GEMINI_API_KEY is required")

    total = len(EXAMPLES)
    print(f"\n{'='*60}")
    print(f"  Curated OpenSCAD Examples — {total} objects")
    print(f"{'='*60}\n")

    if dry_run:
        for ex in EXAMPLES:
            print(f"  [{ex['category']:12}] {ex['name']} ({len(ex['code'])} chars)")
        return

    # ── Step 1: Delete ALL existing examples ──
    print("[1/4] Deleting all existing examples from DB...")
    async with SessionLocal() as db:
        result = await db.execute(select(func.count(OpenscadExample.id)))
        old_count = result.scalar()
        await db.execute(delete(OpenscadExample))
        await db.commit()
        print(f"       Deleted {old_count} rows.\n")

    # ── Step 2: Generate embeddings ──
    print(f"[2/4] Generating embeddings for {total} examples...")
    texts = [f"{ex['name']}. {ex['category']}. {ex['prompt']}" for ex in EXAMPLES]
    all_embeddings = get_embeddings(texts, gemini_key)
    print(f"       Got {len(all_embeddings)} embeddings.\n")

    # ── Step 3 & 4: Upload to Storage + Insert DB ──
    print(f"[3/4] Uploading .scad files to Supabase Storage + inserting DB rows...")
    with httpx.Client(timeout=30.0) as http:
        ensure_bucket(http)

        async with SessionLocal() as db:
            uploaded = 0
            failed = 0

            for i, ex in enumerate(EXAMPLES):
                example_id = str(uuid.uuid4())
                safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", ex["name"])[:80]
                file_path = f"{SOURCE_NAME}/{ex['category']}/{safe_name}_{example_id[:8]}.scad"

                try:
                    storage_path = upload_to_storage(http, file_path, ex["code"])
                except Exception as e:
                    print(f"  FAIL  {ex['name']}: {e}")
                    failed += 1
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
                pct = int((i + 1) / total * 100)
                print(f"  [{pct:3d}%] {ex['name']:30s}  ({ex['category']})")

            await db.commit()

            result = await db.execute(select(func.count(OpenscadExample.id)))
            final_count = result.scalar()

    print(f"\n{'='*60}")
    print(f"  Done! {uploaded} uploaded, {failed} failed, {final_count} total in DB")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Replace all OpenSCAD RAG examples with curated set")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(populate_curated(dry_run=args.dry_run))
