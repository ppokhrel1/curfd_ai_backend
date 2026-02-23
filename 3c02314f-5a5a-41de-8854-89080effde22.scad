$fn = 60;
eps = 0.05;

// --- Fuselage Parameters ---
fuselage_length = 80;
fuselage_width = 25;
fuselage_height = 30;
fuselage_nose_radius = 15;

// --- Main Rotor Parameters ---
main_rotor_mast_height = 20;
main_rotor_mast_radius = 3;
main_rotor_blade_length = 70;
main_rotor_blade_width = 10;
main_rotor_blade_thickness = 2;
main_rotor_count = 2;

// --- Tail Boom Parameters ---
tail_boom_length = 60;
tail_boom_radius = 6;

// --- Tail Rotor Parameters ---
tail_rotor_mast_length = 10;
tail_rotor_mast_radius = 1.5;
tail_rotor_blade_length = 20;
tail_rotor_blade_width = 4;
tail_rotor_blade_thickness = 1.5;
tail_rotor_count = 2;

// --- Landing Skid Parameters ---
skid_length = 90;
skid_height = 25;
skid_radius = 2;
skid_width_offset = 15;
skid_strut_radius = 2;
skid_strut_count = 3;

module create_fuselage() {
    difference() {
        hull() {
            translate([-fuselage_length/2 + fuselage_nose_radius, 0, 0])
                cylinder(h=fuselage_height, r=fuselage_width/2, center=true);
            translate([fuselage_length/2 - fuselage_nose_radius, 0, 0])
                cylinder(h=fuselage_height, r=fuselage_width/2, center=true);
        }
        // Cut a rounded nose/tail for smoother shape
        translate([-fuselage_length/2, 0, 0])
            sphere(r=fuselage_nose_radius, center=true);
        translate([fuselage_length/2, 0, 0])
            sphere(r=fuselage_nose_radius, center=true);
    }
}

module create_main_rotor() {
    // Rotor mast
    cylinder(h=main_rotor_mast_height, r=main_rotor_mast_radius, center=false);

    // Rotor blades
    translate([0, 0, main_rotor_mast_height-eps]) {
        for (i = [0 : main_rotor_count - 1]) {
            rotate([0, 0, i * (360 / main_rotor_count)]) {
                translate([main_rotor_mast_radius + main_rotor_blade_length/2, 0, 0]) {
                    cube([main_rotor_blade_length, main_rotor_blade_width, main_rotor_blade_thickness], center=true);
                }
            }
        }
    }
}

module create_tail_boom() {
    cylinder(h=tail_boom_length, r=tail_boom_radius, center=false);
}

module create_tail_rotor() {
    // Tail rotor mast
    rotate([0, 90, 0])
        cylinder(h=tail_rotor_mast_length, r=tail_rotor_mast_radius, center=false);

    // Tail rotor blades
    translate([0, tail_rotor_mast_radius, tail_rotor_mast_length/2]) {
        rotate([0, 90, 0]) {
            for (i = [0 : tail_rotor_count - 1]) {
                rotate([0, 0, i * (360 / tail_rotor_count)]) {
                    translate([tail_rotor_mast_radius + tail_rotor_blade_length/2, 0, 0]) {
                        cube([tail_rotor_blade_length, tail_rotor_blade_width, tail_rotor_blade_thickness], center=true);
                    }
                }
            }
        }
    }
}

module create_landing_skids() {
    // Left skid
    translate([skid_width_offset, 0, -skid_height]) {
        rotate([90, 0, 0])
            cylinder(h=skid_length, r=skid_radius, center=true);
    }

    // Right skid
    translate([-skid_width_offset, 0, -skid_height]) {
        rotate([90, 0, 0])
            cylinder(h=skid_length, r=skid_radius, center=true);
    }

    // Struts connecting skids to fuselage
    for (i = [0 : skid_strut_count - 1]) {
        translate([0, (i - (skid_strut_count-1)/2) * (skid_length/(skid_strut_count+1)), -skid_height/2]) {
            rotate([0, 90, 0])
                cylinder(h=skid_width_offset*2, r=skid_strut_radius, center=true);
        }
    }
}

module helicopter_assembly() {
    color("darkred") {
        create_fuselage();
    }

    // Main rotor assembly
    translate([0, 0, fuselage_height/2 + main_rotor_mast_height/2]) {
        color("gray") {
            create_main_rotor();
        }
    }

    // Tail boom assembly
    translate([-fuselage_length/2 + tail_boom_radius, 0, fuselage_height/2 - tail_boom_radius]) {
        rotate([0, -90, 0]) {
            color("darkred") {
                create_tail_boom();
            }
        }
    }

    // Tail rotor assembly
    translate([-(fuselage_length/2 + tail_boom_length - tail_rotor_mast_length/2), 0, fuselage_height/2 - tail_boom_radius + tail_rotor_mast_radius]) {
        color("gray") {
            create_tail_rotor();
        }
    }

    // Landing skids assembly
    translate([0, 0, -fuselage_height/2]) {
        color("silver") {
            create_landing_skids();
        }
    }
}

helicopter_assembly();