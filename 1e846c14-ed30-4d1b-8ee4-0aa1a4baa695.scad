//$fn = 64; // Set for higher quality renders, comment out for faster previews
//$vpr = [5.60265, -1.97746, 12.0628];
//$vpt = [2.01243, -4.50893, 2.72144];
//$vpd = 86.8378;

// Global settings
$fn = 64;
eps = 0.01;

// --- Lego base dimensions ---
lego_unit_width = 8;    // Width/depth of a 1x1 stud brick in mm
lego_plate_height = 3.2; // Height of a 1-plate brick in mm
stud_diameter = 4.8;       // Stud diameter in mm
stud_height = 1.8;       // Stud height in mm

// --- Helicopter dimensions (tunable parameters) ---
// Main Body
body_width_units = 4;
body_length_units = 6;
body_height_plates = 6; // Total height of the main body in plates (e.g., 6 plates = 2 bricks high)

// Tail Boom
tail_boom_length_units = 8;
tail_boom_width_units = 2;
tail_boom_height_plates = 2;

// Main Rotor
rotor_mast_height = 25;
rotor_hub_diameter = 12;
rotor_blade_length = 60;
rotor_blade_width = 8;
rotor_blade_thickness = 2;
num_main_rotor_blades = 3;

// Tail Rotor
tail_rotor_mast_height = 8;
tail_rotor_hub_diameter = 8;
tail_rotor_blade_length = 20;
tail_rotor_blade_width = 4;
tail_rotor_blade_thickness = 1.5;
num_tail_rotor_blades = 2;

// Landing Skids
skid_length = 100;
skid_radius = 2.5;
skid_separation_x = 30; // Distance between left and right skid
skid_y_offset = -10; // Offset forward/backward relative to body center
skid_z_offset = -12; // How far below the body bottom

// Cockpit Cutout (relative to main body)
cockpit_cutout_width_offset_units = 2; // Cockpit will be 2 units narrower than body
cockpit_cutout_length_factor = 0.4; // Proportion of body length for cockpit length
cockpit_cutout_height_factor = 0.75; // Proportion of body height for cockpit height
cockpit_cutout_z_start_factor = 0.25; // Proportion of body height to start the cutout from bottom


// --- Helper Modules ---

// Creates a single Lego stud
module lego_stud() {
    cylinder(h = stud_height, r = stud_diameter / 2);
}

// Creates a generic rectangular Lego block with studs on top.
// Origin is at [0,0,0], block extends in +X, +Y, +Z.
module lego_block(width_units, length_units, height_plates) {
    actual_width = width_units * lego_unit_width;
    actual_length = length_units * lego_unit_width;
    actual_height = height_plates * lego_plate_height;

    cube([actual_width, actual_length, actual_height]);

    // Add studs on top
    translate([0, 0, actual_height]) {
        for (x_idx = [0 : width_units - 1]) {
            for (y_idx = [0 : length_units - 1]) {
                translate([
                    x_idx * lego_unit_width + lego_unit_width / 2,
                    y_idx * lego_unit_width + lego_unit_width / 2,
                    0
                ]) {
                    lego_stud();
                }
            }
        }
    }
}

// --- Helicopter Component Modules ---

module main_body() {
    actual_body_width = body_width_units * lego_unit_width;
    actual_body_length = body_length_units * lego_unit_width;
    actual_body_height = body_height_plates * lego_plate_height;

    cockpit_cutout_width = actual_body_width - (cockpit_cutout_width_offset_units * lego_unit_width);
    cockpit_cutout_length = actual_body_length * cockpit_cutout_length_factor;
    cockpit_cutout_height = actual_body_height * cockpit_cutout_height_factor;
    cockpit_cutout_z_start = actual_body_height * cockpit_cutout_z_start_factor;

    difference() {
        // Main block base
        lego_block(body_width_units, body_length_units, body_height_plates);

        // Cockpit cutout (simulated as a simple rectangular hole)
        // Shifted slightly forward and up from the bottom
        translate([
            (actual_body_width - cockpit_cutout_width) / 2, // Center in X
            0, // Start from front edge
            cockpit_cutout_z_start
        ]) {
            cube([cockpit_cutout_width, cockpit_cutout_length, cockpit_cutout_height + eps]); // +eps for clean cut
        }
    }
}

module tail_boom() {
    lego_block(tail_boom_width_units, tail_boom_length_units, tail_boom_height_plates);
}

module main_rotor_assembly() {
    // Rotor mast (vertical)
    cylinder(h = rotor_mast_height, r = rotor_hub_diameter / 4);

    // Rotor hub (disk on top of mast)
    translate([0, 0, rotor_mast_height]) {
        cylinder(h = rotor_blade_thickness + eps, r = rotor_hub_diameter / 2);
    }

    // Rotor blades
    for (i = [0 : num_main_rotor_blades - 1]) {
        rotate([0, 0, i * (360 / num_main_rotor_blades)]) { // Rotate around Z (up/down)
            translate([0, 0, rotor_mast_height + rotor_blade_thickness / 2]) { // Center blade thickness on top of hub
                // Position blade outwards from the hub center
                translate([rotor_hub_diameter / 2 + rotor_blade_length / 2 - rotor_blade_width / 2, 0, 0]) {
                    cube([rotor_blade_length, rotor_blade_width, rotor_blade_thickness], center = true);
                }
            }
        }
    }
}

module tail_rotor_assembly() {
    // Tail rotor mast (horizontal, along X-axis)
    rotate([0, 90, 0]) { // Rotate cylinder (default Z-axis) to be along X-axis
        cylinder(h = tail_rotor_mast_height, r = tail_rotor_hub_diameter / 4);
    }

    // Tail rotor hub (at the end of the mast, flat in Y-Z plane)
    translate([tail_rotor_mast_height, 0, 0]) { // Move along X to the end of the mast
        rotate([0, 90, 0]) { // Orient hub correctly, flat in Y-Z plane
            cylinder(h = tail_rotor_blade_thickness + eps, r = tail_rotor_hub_diameter / 2);
        }
    }

    // Tail rotor blades (rotate around X-axis, extend perpendicular to it)
    for (i = [0 : num_tail_rotor_blades - 1]) {
        rotate([i * (360 / num_tail_rotor_blades), 0, 0]) { // Rotate around X-axis for blades
            translate([tail_rotor_mast_height + tail_rotor_blade_thickness / 2, 0, 0]) { // Position blades on hub (along X-axis)
                // Blade itself: width along Y, thickness along Z, length along X, but shifted for blade length
                // Let's make the blade length along Y, so it sweeps vertically with X axis rotation
                translate([0, tail_rotor_blade_length / 2, 0]) { // Extend blade along Y
                    cube([tail_rotor_blade_thickness, tail_rotor_blade_length, tail_rotor_blade_width], center = true);
                }
            }
        }
    }
}

module landing_skids() {
    // Two main skids (long cylinders)
    translate([-skid_separation_x / 2, skid_y_offset, skid_z_offset]) {
        rotate([90, 0, 0]) { // Rotate to be horizontal along Y
            cylinder(h = skid_length, r = skid_radius);
        }
    }
    translate([skid_separation_x / 2, skid_y_offset, skid_z_offset]) {
        rotate([90, 0, 0]) { // Rotate to be horizontal along Y
            cylinder(h = skid_length, r = skid_radius);
        }
    }

    // Connecting bars (shorter cylinders)
    // Front connector
    translate([0, skid_y_offset + skid_radius * 2, skid_z_offset + skid_radius]) {
        rotate([90, 0, 90]) { // Rotate to be horizontal along X
            cylinder(h = skid_separation_x, r = skid_radius);
        }
    }
    // Rear connector
    translate([0, skid_y_offset + skid_length - skid_radius * 2, skid_z_offset + skid_radius]) {
        rotate([90, 0, 90]) { // Rotate to be horizontal along X
            cylinder(h = skid_separation_x, r = skid_radius);
        }
    }
}

// --- Main Assembly Module ---
module main() {
    // Calculate main body dimensions for placement
    actual_body_width = body_width_units * lego_unit_width;
    actual_body_length = body_length_units * lego_unit_width;
    actual_body_height = body_height_plates * lego_plate_height;

    color("blue") {
        // Main Body: Centered on X and Y, starting at Z=0
        translate([-actual_body_width / 2, -actual_body_length / 2, 0]) {
            main_body();
        }
    }

    color("darkgrey") {
        // Main Rotor Assembly: Placed on top center of the main body
        translate([0, 0, actual_body_height]) {
            main_rotor_assembly();
        }
    }

    color("blue") {
        // Tail Boom: Placed at the back of the main body, roughly centered vertically
        translate([
            -(tail_boom_width_units * lego_unit_width) / 2, // Center on X
            actual_body_length / 2, // Align front of tail boom with back of main body
            actual_body_height / 2 // Roughly center vertically with main body
        ]) {
            tail_boom();
        }
    }

    color("darkgrey") {
        // Tail Rotor Assembly: Placed at the very end of the tail boom
        translate([
            0, // Centered on X (relative to tail boom)
            (actual_body_length / 2) + (tail_boom_length_units * lego_unit_width), // End of tail boom Y
            (actual_body_height / 2) + (tail_boom_height_plates * lego_plate_height) / 2 // Vertical center of tail boom
        ]) {
            tail_rotor_assembly();
        }
    }

    color("darkgrey") {
        // Landing Skids: Placed below the main body, centered on X and slightly forward/backward on Y
        translate([0, 0, 0]) {
            landing_skids();
        }
    }
}

// Render the main model
main();