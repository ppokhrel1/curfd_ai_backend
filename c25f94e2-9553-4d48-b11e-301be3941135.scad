$fn = 60;
eps = 0.05;

body_length = 80;
body_width = 30;
body_height = 25;
cockpit_radius = 20;
cockpit_offset_y = 10;

main_rotor_diameter = 100;
main_rotor_blade_count = 2;
main_rotor_blade_width = 12;
main_rotor_blade_thickness = 2;
main_rotor_hub_diameter = 10;
main_rotor_post_height = 15;
main_rotor_post_diameter = 8;

tail_boom_length = 70;
tail_boom_diameter = 15;
tail_boom_offset_z = -5;

tail_rotor_diameter = 30;
tail_rotor_blade_count = 2;
tail_rotor_blade_width = 5;
tail_rotor_blade_thickness = 1.5;
tail_rotor_hub_diameter = 5;
tail_rotor_post_length = 8;

skid_length = 70;
skid_height = 20;
skid_width_separation = 25;
skid_radius = 2;
skid_leg_count = 3;

tail_fin_height = 25;
tail_fin_length = 20;
tail_fin_thickness = 2;

module main_body() {
    color("darkblue") {
        // Main body block (rectangular prism)
        cube([body_length, body_width, body_height], center = true);

        // Cockpit (sphere section for a rounded front)
        translate([body_length / 2 - cockpit_radius + cockpit_offset_y, 0, 0]) {
            sphere(r = cockpit_radius, center = true);
        }
    }
}

module main_rotor_blade(length, width, thickness) {
    // A single main rotor blade, centered for easy rotation and positioning
    cube([length, width, thickness], center = true);
}

module main_rotor_assembly() {
    color("grey") {
        // Vertical post for the main rotor
        cylinder(h = main_rotor_post_height, r = main_rotor_post_diameter / 2, center = true);

        // Rotor Hub, placed on top of the post
        translate([0, 0, (main_rotor_post_height / 2) + (main_rotor_blade_thickness / 2)]) {
            cylinder(h = main_rotor_blade_thickness + eps, r = main_rotor_hub_diameter / 2, center = true);

            // Main Rotor Blades
            rotor_blade_length = (main_rotor_diameter / 2) - (main_rotor_hub_diameter / 2);
            for (i = [0 : main_rotor_blade_count - 1]) {
                rotate([0, 0, i * (360 / main_rotor_blade_count)]) {
                    // Position each blade to extend from the hub outwards
                    translate([main_rotor_hub_diameter / 2 + rotor_blade_length / 2, 0, 0]) {
                        main_rotor_blade(rotor_blade_length, main_rotor_blade_width, main_rotor_blade_thickness);
                    }
                }
            }
        }
    }
}

module tail_boom_and_fin() {
    color("darkblue") {
        // Main tail boom cylinder
        cylinder(h = tail_boom_length, r = tail_boom_diameter / 2, center = true);

        // Vertical tail fin, placed at the end of the boom and extending upwards
        translate([tail_boom_length / 2 - tail_fin_length / 2, 0, tail_boom_diameter / 2 + tail_fin_height / 2 - tail_fin_thickness/2]) {
            cube([tail_fin_length, tail_fin_thickness, tail_fin_height], center = true);
        }
        // Small horizontal stabilizer at the top-rear of the boom
        translate([tail_boom_length / 2 - tail_fin_length / 2, 0, tail_boom_diameter / 2 + tail_fin_height - tail_fin_thickness]) {
            cube([tail_fin_length * 0.75, tail_fin_length, tail_fin_thickness], center = true);
        }
    }
}

module tail_rotor_blade(length, width, thickness) {
    // A single tail rotor blade
    cube([length, width, thickness], center = true);
}

module tail_rotor_assembly() {
    color("grey") {
        // Post connecting the tail rotor to the fin/boom. Rotated to be horizontal (along Y).
        rotate([90, 0, 0]) {
            cylinder(h = tail_rotor_post_length, r = tail_rotor_hub_diameter / 2, center = true);
        }

        // Tail Rotor Hub, positioned at the end of the post
        translate([0, tail_rotor_post_length / 2 + tail_rotor_blade_thickness/2, 0]) {
            rotate([90, 0, 0]) { // Rotate to make blades spin in the Y-Z plane relative to their module origin
                cylinder(h = tail_rotor_blade_thickness + eps, r = tail_rotor_hub_diameter / 2, center = true);

                // Tail Rotor Blades
                rotor_blade_length = (tail_rotor_diameter / 2) - (tail_rotor_hub_diameter / 2);
                for (i = [0 : tail_rotor_blade_count - 1]) {
                    rotate([0, 0, i * (360 / tail_rotor_blade_count)]) { // Blades rotate around Z within their rotated plane
                        translate([tail_rotor_hub_diameter / 2 + rotor_blade_length / 2, 0, 0]) {
                            tail_rotor_blade(rotor_blade_length, tail_rotor_blade_width, tail_rotor_blade_thickness);
                        }
                    }
                }
            }
        }
    }
}

module landing_skids() {
    color("dimgrey") {
        // Calculate even spacing for vertical support legs
        leg_span = skid_length / (skid_leg_count + 1);

        for (i = [0 : skid_leg_count - 1]) {
            // Vertical supports for the first skid rail
            translate([-skid_length / 2 + (i + 1) * leg_span, skid_width_separation / 2 + skid_radius, -skid_height / 2]) {
                cylinder(h = skid_height, r = skid_radius, center = true);
            }
            // Vertical supports for the second skid rail
            translate([-skid_length / 2 + (i + 1) * leg_span, -(skid_width_separation / 2 + skid_radius), -skid_height / 2]) {
                cylinder(h = skid_height, r = skid_radius, center = true);
            }
        }

        // Horizontal skid rails. Rotated to run along the X-axis.
        rotate([0, 90, 0]) { // Rotate cylinders (default Z-axis) to be along X-axis
            translate([0, skid_width_separation / 2 + skid_radius, -skid_height]) {
                cylinder(h = skid_length, r = skid_radius, center = true);
            }
            translate([0, -(skid_width_separation / 2 + skid_radius), -skid_height]) {
                cylinder(h = skid_length, r = skid_radius, center = true);
            }
        }
    }
}

module main_assembly() {
    // Assemble the main body
    main_body();

    // Position the main rotor assembly on top of the body, behind the cockpit
    translate([body_length / 2 - cockpit_radius - 5, 0, body_height / 2 + main_rotor_post_height / 2]) {
        main_rotor_assembly();
    }

    // Position the tail boom and fin assembly at the back of the main body
    // The boom is rotated to run along the X-axis, and its front is attached to the body's back
    translate([-body_length / 2 - tail_boom_length / 2 + 5, 0, tail_boom_offset_z]) {
        rotate([0, 90, 0]) { // Rotate the boom (default Z-axis) to be along the X-axis
            tail_boom_and_fin();
        }
    }

    // Position the tail rotor assembly on the side of the tail fin
    // Calculated to be at the rear of the boom, offset from the fin, and elevated
    translate([-body_length / 2 - tail_boom_length + tail_fin_length/2 + 5,
               (tail_boom_diameter / 2 + tail_fin_thickness / 2 + tail_rotor_post_length/2 + 2),
               tail_boom_offset_z + tail_boom_diameter / 2 + tail_fin_height - tail_rotor_hub_diameter/2]) {
        rotate([0, 0, 0]) { // Default orientation as defined in its module
             tail_rotor_assembly();
        }
    }

    // Position the landing skids below the main body
    translate([0, 0, -body_height / 2 - skid_height / 2 + skid_radius]) {
        landing_skids();
    }
}

// Render the complete helicopter assembly
main_assembly();
