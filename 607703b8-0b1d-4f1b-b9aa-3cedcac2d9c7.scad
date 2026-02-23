$fn = 60; // Facets for curves smoothness
eps = 0.05; // Epsilon for preventing Z-fighting

// Main Body Parameters
body_length = 80; // mm
body_width = 30; // mm
body_height = 35; // mm
body_nose_radius = 15; // mm

// Cockpit Parameters
cockpit_radius = 15; // mm
cockpit_height = 20; // mm

// Tail Boom Parameters
tail_boom_length = 60; // mm
tail_boom_diameter = 10; // mm
tail_boom_offset_z = 5; // mm

// Tail Fin Parameters
tail_fin_height = 25; // mm
tail_fin_base_width = 10; // mm
tail_fin_tip_width = 5; // mm
tail_fin_thickness = 3; // mm

// Landing Skid Parameters
skid_length = 90; // mm
skid_radius = 2; // mm
skid_gap = 25; // mm (distance between skids)
skid_support_height = 15; // mm
skid_support_thickness = 3; // mm
skid_support_offset_x = 20; // mm

// Main Rotor Parameters
main_rotor_shaft_diameter = 5; // mm
main_rotor_shaft_height = 10; // mm
main_rotor_blade_length = 50; // mm
main_rotor_blade_width = 8; // mm
main_rotor_blade_thickness = 2; // mm
main_rotor_blade_count = 2; // number of blades

// Tail Rotor Parameters
tail_rotor_shaft_diameter = 3; // mm
tail_rotor_shaft_height = 5; // mm
tail_rotor_blade_length = 15; // mm
tail_rotor_blade_width = 4; // mm
tail_rotor_blade_thickness = 1.5; // mm
tail_rotor_blade_count = 2; // number of blades

module main_body() {
    hull() {
        translate([-body_length/2 + body_nose_radius, 0, 0])
            sphere(r=body_nose_radius, center = true);
        translate([body_length/2 - body_nose_radius, 0, 0])
            sphere(r=body_nose_radius, center = true);
    }
    
    linear_extrude(height = body_height, center = true) {
        offset(r = body_width/2 - body_nose_radius) {
            square([body_length - 2*body_nose_radius, body_width - 2*body_nose_radius], center = true);
        }
    }
    
    // Taper the back slightly (optional, but makes it look better)
    translate([body_length/4, 0, 0])
    scale([1.0, 0.8, 1.0])
    cube([body_length/2, body_width, body_height], center = true);
}

module cockpit() {
    color("lightskyblue") {
        translate([-(body_length/2 - cockpit_radius/2), 0, body_height/2 - (cockpit_height/2 - eps)])
        rotate([90, 0, 0])
        cylinder(h = cockpit_height, r = cockpit_radius, center = true);

        // Cut out the interior to make it somewhat hollow/visible
        difference() {
            translate([-(body_length/2 - cockpit_radius/2), 0, body_height/2 - (cockpit_height/2 - eps)])
            rotate([90, 0, 0])
            cylinder(h = cockpit_height, r = cockpit_radius, center = true);
            
            translate([-(body_length/2 - cockpit_radius/2), 0, body_height/2 - (cockpit_height/2 - eps)])
            rotate([90, 0, 0])
            cylinder(h = cockpit_height+2*eps, r = cockpit_radius - 1, center = true);
        }
    }
}

module tail_boom() {
    translate([body_length/2 + tail_boom_length/2 - (body_nose_radius/2), 0, tail_boom_offset_z])
    rotate([0, 90, 0])
    cylinder(h = tail_boom_length, r = tail_boom_diameter/2, center = true);
}

module tail_fin() {
    pos_x = body_length/2 + tail_boom_length - (body_nose_radius/2);
    pos_z = tail_boom_offset_z + tail_boom_diameter/2 + tail_fin_height/2 - 2; // Adjust for boom curve
    
    translate([pos_x, 0, pos_z])
    rotate([0, 0, 0]) // Keep upright
    linear_extrude(height = tail_fin_thickness, center = true) {
        polygon([
            [-tail_fin_base_width/2, -tail_fin_height/2],
            [tail_fin_base_width/2, -tail_fin_height/2],
            [tail_fin_tip_width/2, tail_fin_height/2],
            [-tail_fin_tip_width/2, tail_fin_height/2]
        ]);
    }
}

module landing_skids() {
    // Left Skid
    translate([0, -skid_gap/2, -body_height/2 - skid_support_height - skid_radius])
    hull() {
        cylinder(h=skid_radius*2, r=skid_radius, center=true);
        translate([skid_length, 0, 0])
            cylinder(h=skid_radius*2, r=skid_radius, center=true);
    }

    // Right Skid
    translate([0, skid_gap/2, -body_height/2 - skid_support_height - skid_radius])
    hull() {
        cylinder(h=skid_radius*2, r=skid_radius, center=true);
        translate([skid_length, 0, 0])
            cylinder(h=skid_radius*2, r=skid_radius, center=true);
    }
    
    // Skid Supports
    support_base_z = -body_height/2 - skid_support_height/2;
    skid_top_z = -body_height/2 - skid_support_height - skid_radius + skid_radius*2;
    
    for (i = [-1, 1]) {
        for (j = [-1, 1]) {
            translate([j*skid_support_offset_x + skid_length/2, i*skid_gap/2, support_base_z]) {
                cube([skid_support_thickness, skid_support_thickness, skid_support_height], center = true);
            }
            
            // Connector to skids
            translate([j*skid_support_offset_x + skid_length/2, i*skid_gap/2, support_base_z + skid_support_height/2])
            rotate([j == -1 ? 15 : -15, 0, 0])
            cube([skid_support_thickness, skid_support_thickness, skid_support_height/2], center = true);
        }
    }
}

module rotor_blade(length, width, thickness) {
    cube([length, width, thickness], center = true);
}

module rotor_assembly(shaft_h, shaft_d, blade_length, blade_width, blade_thickness, blade_count) {
    // Rotor shaft
    rotate([90, 0, 0]) // Stand upright
    cylinder(h = shaft_h, r = shaft_d/2, center = true);

    // Blades
    translate([0, 0, shaft_h/2 + blade_thickness/2]) {
        for (i = [0 : blade_count - 1]) {
            rotate([0, 0, i * (360 / blade_count)]) {
                translate([blade_length/2, 0, 0])
                rotor_blade(blade_length, blade_width, blade_thickness);
            }
        }
    }
}

module main_rotor() {
    translate([0, 0, body_height/2 + main_rotor_shaft_height/2 - 1]) // Position above body
    rotor_assembly(
        main_rotor_shaft_height,
        main_rotor_shaft_diameter,
        main_rotor_blade_length,
        main_rotor_blade_width,
        main_rotor_blade_thickness,
        main_rotor_blade_count
    );
}

module tail_rotor() {
    pos_x = body_length/2 + tail_boom_length - (body_nose_radius/2) + tail_fin_thickness/2 + tail_rotor_shaft_height/2;
    pos_z = tail_boom_offset_z + tail_boom_diameter/2 + tail_fin_height/2 - 5;
    
    translate([pos_x, tail_boom_diameter/2 + 2, pos_z]) // Position at end of tail boom
    rotate([0, 90, 0]) // Rotate to face outward
    rotor_assembly(
        tail_rotor_shaft_height,
        tail_rotor_shaft_diameter,
        tail_rotor_blade_length,
        tail_rotor_blade_width,
        tail_rotor_blade_thickness,
        tail_rotor_blade_count
    );
}

module helicopter_assembly() {
    color("dimgray") main_body();
    cockpit();
    color("dimgray") tail_boom();
    color("dimgray") tail_fin();
    color("darkgray") landing_skids();
    color("black") main_rotor();
    color("black") tail_rotor();
}

// Render the full assembly
helicopter_assembly();
