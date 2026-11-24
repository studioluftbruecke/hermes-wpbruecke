<?php
/**
 * GeneratePress brand staging and the wpbruecke/* abilities.
 *
 * A brand (colours, fonts) is only ever staged here (option wpbruecke_staged_brand). It reaches
 * GeneratePress' global colours when a person with edit_theme_options clicks Apply brand on the
 * review screen. The abilities are registered only when the site has the Abilities API.
 *
 * @package WPBrueckeConnector
 */

if ( ! defined( 'ABSPATH' ) ) {
	exit;
}

/**
 * Whether the active theme is GeneratePress or a child of it.
 *
 * @return bool
 */
function wpbruecke_is_generatepress() {
	return 'generatepress' === get_template();
}

/**
 * A font family name: letters, digits, spaces, dashes, dots and commas, at most 100 characters.
 *
 * @param mixed $value Raw.
 * @return string Empty when invalid.
 */
function wpbruecke_sanitize_font( $value ) {
	$value = trim( sanitize_text_field( is_scalar( $value ) ? (string) $value : '' ) );
	if ( '' === $value || strlen( $value ) > 100 || ! preg_match( '/^[\p{L}\p{N} .,\'"_-]+$/u', $value ) ) {
		return '';
	}
	return $value;
}

/**
 * Validate and clean a brand: {colors[{slug, name, color}], fonts{body, headings}}.
 *
 * @param mixed $brand Raw input.
 * @return array|WP_Error
 */
function wpbruecke_sanitize_brand( $brand ) {
	if ( is_object( $brand ) ) {
		$brand = json_decode( wp_json_encode( $brand ), true );
	}
	if ( ! is_array( $brand ) ) {
		return new WP_Error( 'wpbruecke_bad_brand', __( 'The brand must be an object.', 'wpbruecke-connector' ), array( 'status' => 400 ) );
	}
	$colors = array();
	$raw    = isset( $brand['colors'] ) && is_array( $brand['colors'] ) ? $brand['colors'] : array();
	if ( count( $raw ) > 30 ) {
		return new WP_Error( 'wpbruecke_bad_brand', __( 'At most 30 colours.', 'wpbruecke-connector' ), array( 'status' => 400 ) );
	}
	$seen = array();
	foreach ( $raw as $item ) {
		if ( ! is_array( $item ) ) {
			return new WP_Error( 'wpbruecke_bad_brand', __( 'Each colour is an object {slug, name, color}.', 'wpbruecke-connector' ), array( 'status' => 400 ) );
		}
		$slug  = isset( $item['slug'] ) ? sanitize_title( (string) $item['slug'] ) : '';
		$color = isset( $item['color'] ) ? sanitize_hex_color( (string) $item['color'] ) : '';
		$name  = isset( $item['name'] ) ? sanitize_text_field( (string) $item['name'] ) : '';
		if ( '' === $slug || strlen( $slug ) > 50 || ! $color ) {
			return new WP_Error( 'wpbruecke_bad_brand', __( 'Each colour needs a slug and a hex colour such as #1a2b3c.', 'wpbruecke-connector' ), array( 'status' => 400 ) );
		}
		if ( isset( $seen[ $slug ] ) ) {
			return new WP_Error( 'wpbruecke_bad_brand', __( 'Colour slugs must be unique.', 'wpbruecke-connector' ), array( 'status' => 400 ) );
		}
		$seen[ $slug ] = true;
		$colors[]      = array(
			'slug'  => $slug,
			'name'  => '' === $name ? $slug : substr( $name, 0, 100 ),
			'color' => strtolower( $color ),
		);
	}
	$fonts = array();
	$raw   = isset( $brand['fonts'] ) && is_array( $brand['fonts'] ) ? $brand['fonts'] : array();
	foreach ( array( 'body', 'headings' ) as $key ) {
		if ( isset( $raw[ $key ] ) && '' !== $raw[ $key ] ) {
			$font = wpbruecke_sanitize_font( $raw[ $key ] );
			if ( '' === $font ) {
				return new WP_Error( 'wpbruecke_bad_brand', __( 'A font name may hold letters, digits, spaces and dashes.', 'wpbruecke-connector' ), array( 'status' => 400 ) );
			}
			$fonts[ $key ] = $font;
		}
	}
	if ( ! $colors && ! $fonts ) {
		return new WP_Error( 'wpbruecke_bad_brand', __( 'Nothing to stage: give colours or fonts.', 'wpbruecke-connector' ), array( 'status' => 400 ) );
	}
	return array(
		'colors' => $colors,
		'fonts'  => (object) $fonts,
	);
}

/**
 * Stage a brand (never applies it).
 *
 * @param mixed $brand Raw input.
 * @return array|WP_Error {staged: true, review_url}
 */
function wpbruecke_stage_brand( $brand ) {
	$clean = wpbruecke_sanitize_brand( $brand );
	if ( is_wp_error( $clean ) ) {
		return $clean;
	}
	$clean['fonts']     = (array) $clean['fonts'];
	$clean['staged_at'] = gmdate( 'c' );
	$clean['staged_by'] = get_current_user_id();
	delete_option( 'wpbruecke_staged_brand' );
	add_option( 'wpbruecke_staged_brand', $clean, '', false );
	return array(
		'staged'     => true,
		'review_url' => wpbruecke_review_url(),
	);
}

/**
 * The staged brand or null.
 *
 * @return array|null
 */
function wpbruecke_staged_brand() {
	$staged = get_option( 'wpbruecke_staged_brand' );
	return is_array( $staged ) ? $staged : null;
}

/**
 * GeneratePress' global colours as stored, falling back to the theme defaults.
 *
 * @return array[]
 */
function wpbruecke_gp_global_colors() {
	$settings = get_option( 'generate_settings' );
	if ( is_array( $settings ) && isset( $settings['global_colors'] ) && is_array( $settings['global_colors'] ) ) {
		return $settings['global_colors'];
	}
	if ( function_exists( 'generate_get_defaults' ) ) {
		$defaults = generate_get_defaults();
		if ( isset( $defaults['global_colors'] ) && is_array( $defaults['global_colors'] ) ) {
			return $defaults['global_colors'];
		}
	}
	return array();
}

/**
 * Current brand plus the staged one.
 *
 * @return array
 */
function wpbruecke_gp_brand_get() {
	$current = array();
	if ( wpbruecke_is_generatepress() ) {
		foreach ( wpbruecke_gp_global_colors() as $item ) {
			if ( is_array( $item ) && isset( $item['slug'], $item['color'] ) ) {
				$current[] = array(
					'slug'  => (string) $item['slug'],
					'name'  => isset( $item['name'] ) ? (string) $item['name'] : (string) $item['slug'],
					'color' => (string) $item['color'],
				);
			}
		}
	}
	return array(
		'generatepress' => wpbruecke_is_generatepress(),
		'colors'        => $current,
		'staged'        => wpbruecke_staged_brand(),
		'review_url'    => wpbruecke_review_url(),
	);
}

/**
 * Write the staged colours into GeneratePress' global colours (caller checks rights and nonce).
 *
 * @return array|WP_Error {colors: int}
 */
function wpbruecke_apply_staged_brand() {
	if ( ! wpbruecke_is_generatepress() ) {
		return new WP_Error( 'wpbruecke_not_generatepress', __( 'The active theme is not GeneratePress; the staged brand was not applied.', 'wpbruecke-connector' ) );
	}
	$staged = wpbruecke_staged_brand();
	if ( ! $staged || empty( $staged['colors'] ) ) {
		return new WP_Error( 'wpbruecke_nothing_staged', __( 'No staged colours to apply.', 'wpbruecke-connector' ) );
	}
	$colors = wpbruecke_gp_global_colors();
	$index  = array();
	foreach ( $colors as $i => $item ) {
		if ( is_array( $item ) && isset( $item['slug'] ) ) {
			$index[ (string) $item['slug'] ] = $i;
		}
	}
	foreach ( $staged['colors'] as $item ) {
		$color = sanitize_hex_color( $item['color'] );
		$slug  = sanitize_title( $item['slug'] );
		if ( ! $color || '' === $slug ) {
			continue;
		}
		$entry = array(
			'name'  => sanitize_text_field( $item['name'] ),
			'slug'  => $slug,
			'color' => $color,
		);
		if ( isset( $index[ $slug ] ) ) {
			$colors[ $index[ $slug ] ] = $entry;
		} else {
			$colors[] = $entry;
		}
	}
	$settings = get_option( 'generate_settings' );
	if ( ! is_array( $settings ) ) {
		$settings = array();
	}
	$settings['global_colors'] = array_values( $colors );
	update_option( 'generate_settings', $settings );
	// GeneratePress caches its generated CSS; drop the cache so the new colours show.
	delete_option( 'generate_dynamic_css_output' );
	delete_option( 'generate_dynamic_css_cached_version' );
	delete_option( 'wpbruecke_staged_brand' );
	return array( 'colors' => count( $staged['colors'] ) );
}

/**
 * Read-only facts for the abilities.
 *
 * @return array
 */
function wpbruecke_site_facts() {
	return array(
		'connector'     => WPBRUECKE_CONNECTOR_VERSION,
		'wp_version'    => (string) get_bloginfo( 'version' ),
		'theme'         => get_stylesheet(),
		'template'      => get_template(),
		'generatepress' => wpbruecke_is_generatepress(),
		'seo_plugin'    => wpbruecke_seo_plugin(),
		'role'          => WPBRUECKE_ROLE,
		'role_present'  => (bool) get_role( WPBRUECKE_ROLE ),
		'review_url'    => wpbruecke_review_url(),
	);
}

/**
 * Abilities permission: the user can edit posts.
 *
 * @return bool
 */
function wpbruecke_ability_permission() {
	return current_user_can( 'edit_posts' );
}

/**
 * Register the category (when the API has categories).
 */
function wpbruecke_register_ability_category() {
	if ( function_exists( 'wp_register_ability_category' ) ) {
		wp_register_ability_category(
			'wpbruecke',
			array(
				'label'       => __( 'WPBrücke', 'wpbruecke-connector' ),
				'description' => __( 'Site facts and GeneratePress brand staging for WPBrücke.', 'wpbruecke-connector' ),
			)
		);
	}
}

/**
 * Register the three abilities.
 */
function wpbruecke_register_abilities() {
	if ( ! function_exists( 'wp_register_ability' ) ) {
		return;
	}
	$brand_schema = array(
		'type'       => 'object',
		'properties' => array(
			'colors' => array(
				'type'  => 'array',
				'items' => array(
					'type'       => 'object',
					'properties' => array(
						'slug'  => array( 'type' => 'string' ),
						'name'  => array( 'type' => 'string' ),
						'color' => array( 'type' => 'string' ),
					),
				),
			),
			'fonts'  => array(
				'type'       => 'object',
				'properties' => array(
					'body'     => array( 'type' => 'string' ),
					'headings' => array( 'type' => 'string' ),
				),
			),
		),
	);
	wp_register_ability(
		'wpbruecke/site-facts',
		array(
			'label'               => __( 'WPBrücke site facts', 'wpbruecke-connector' ),
			'description'         => __( 'Connector version, theme, SEO plugin and review screen of this site. Read-only.', 'wpbruecke-connector' ),
			'category'            => 'wpbruecke',
			'output_schema'       => array( 'type' => 'object' ),
			'execute_callback'    => 'wpbruecke_site_facts',
			'permission_callback' => 'wpbruecke_ability_permission',
			'meta'                => array(
				'show_in_rest' => true,
				'annotations'  => array(
					'readonly'    => true,
					'destructive' => false,
					'idempotent'  => true,
				),
			),
		)
	);
	wp_register_ability(
		'wpbruecke/gp-brand-get',
		array(
			'label'               => __( 'GeneratePress brand', 'wpbruecke-connector' ),
			'description'         => __( 'GeneratePress global colours and the staged brand waiting for review. Read-only.', 'wpbruecke-connector' ),
			'category'            => 'wpbruecke',
			'output_schema'       => array( 'type' => 'object' ),
			'execute_callback'    => 'wpbruecke_gp_brand_get',
			'permission_callback' => 'wpbruecke_ability_permission',
			'meta'                => array(
				'show_in_rest' => true,
				'annotations'  => array(
					'readonly'    => true,
					'destructive' => false,
					'idempotent'  => true,
				),
			),
		)
	);
	wp_register_ability(
		'wpbruecke/gp-brand-stage',
		array(
			'label'               => __( 'Stage a GeneratePress brand', 'wpbruecke-connector' ),
			'description'         => __( 'Stores colours and fonts for review. Never applies them: a person clicks Apply brand on the review screen.', 'wpbruecke-connector' ),
			'category'            => 'wpbruecke',
			'input_schema'        => $brand_schema,
			'output_schema'       => array( 'type' => 'object' ),
			'execute_callback'    => 'wpbruecke_stage_brand',
			'permission_callback' => 'wpbruecke_ability_permission',
			'meta'                => array(
				'show_in_rest' => true,
				'annotations'  => array(
					'readonly'    => false,
					'destructive' => false,
					'idempotent'  => true,
				),
			),
		)
	);
}
add_action( 'wp_abilities_api_categories_init', 'wpbruecke_register_ability_category' );
add_action( 'wp_abilities_api_init', 'wpbruecke_register_abilities' );
